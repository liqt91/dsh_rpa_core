"""图像/OCR 定位能力层（M75 S1，ADR 0003 分层第 4 层）。

给「UIA/win32 都拿不到语义的自绘控件」（微信自绘区、DirectX、canvas）补一条
**显式选择**的定位路径：拿窗口截图 + 目标（模板图或文本关键词）→ 算出屏幕绝对矩形。

**为什么不直接调 cv2/ddddocr**：全仓只有 ``workers/ocr_worker`` 一个 OCR 权威，
它已经封装了 ddddocr 的 detect（出框）与 slide（TM_CCOEFF_NORMED 模板匹配）。
本模块**复用这两个能力**，不建第二套匹配实现（M50/M56「第二权威」教训）。
真要换底层库，只改 ``_default_matcher`` / ``_default_detector`` 两个工厂。

**边界**：本模块只做「图 → 矩形」的纯计算 + 注入接缝，**不决定何时调用**——
「UIA 找不到才允许显式选图像」的守卫在 S2，本模块不提供任何静默兜底。
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from typing import Any, Protocol


class ImageLocatorError(Exception):
    """输入不合法或截图失败（结构化错误，调用方转 ``VerificationResult.error``）。"""


class TemplateMatcher(Protocol):
    """模板匹配接缝：背景图 + 模板图 → 命中中心 + 归一化置信度。

    生产实现走 ``ocr_worker.slide_match``（内部 cv2.matchTemplate TM_CCOEFF_NORMED）。
    """

    def match(self, screen: bytes, template: bytes) -> tuple[int, int, float | None]:
        """返回 ``(中心 x, 中心 y, 置信度)``；无匹配概念时由调用方用阈值判。"""


class TextDetector(Protocol):
    """文本检测接缝：图 → ``[{x,y,width,height,text?}]``。

    生产实现走 ``ocr_worker.detect``（ddddocr det）+ 可选 recognize 出文本。
    """

    def boxes(self, screen: bytes) -> list[dict[str, Any]]:
        """返回检测框列表（坐标相对图片左上，形状对齐 browser.getPosition）。"""


class TemplateResampler(Protocol):
    """模板重采样接缝：模板 PNG + 目标缩放比 → ``(PNG 字节, 新尺寸)``。"""

    def resize(self, template: bytes, scale: float) -> tuple[bytes, tuple[int, int]]:
        """按 ``scale`` 缩放模板；生产实现用 Pillow，替身可只记调用。"""


@dataclass(frozen=True)
class ImageMatch:
    """一次图像定位的结果。坐标是**屏幕绝对**（窗口原点 + 图内偏移）。"""

    left: int
    top: int
    width: int
    height: int
    confidence: float | None
    count: int  # 命中数：0=没找到，1=唯一命中，>1=多命中（歧义）
    used_scale: float = 1.0  # M80：实际命中的模板缩放比（相对捕获时尺寸）

    def as_rect(self) -> dict[str, int]:
        """与 Web / 桌面校验统一的 rect 形状。"""
        return {
            "left": self.left,
            "top": self.top,
            "width": self.width,
            "height": self.height,
        }


def _default_matcher() -> TemplateMatcher | None:
    """生产模板匹配器：复用 ocr_worker.slide_match（ddddocr 的 cv2 匹配）。"""
    try:
        from rpa_core.workers import ocr_worker
    except ImportError:
        return None

    class _DdddocrMatcher:
        def match(self, screen: bytes, template: bytes) -> tuple[int, int, float | None]:
            # slide_match 恒回最佳位置（无「找不到」分支，见其文档）⇒ min_confidence=0
            # 信任分数交回本层的 confidence 阈值判，不在这里抛。
            result = ocr_worker.slide_match(
                template, screen, simple_target=False, min_confidence=0.0
            )
            return (
                int(result["x"]),
                int(result["y"]),
                _as_float(result.get("confidence")),
            )

    return _DdddocrMatcher()


def _default_detector() -> TextDetector | None:
    """生产文本检测器：复用 ocr_worker.detect（ddddocr det 出框）。"""
    try:
        from rpa_core.workers import ocr_worker
    except ImportError:
        return None

    class _DdddocrDetector:
        def boxes(self, screen: bytes) -> list[dict[str, Any]]:
            return ocr_worker.detect(screen)["boxes"]

    return _DdddocrDetector()


def _as_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def scale_candidates(ratio: float | None) -> tuple[float, ...]:
    """M80 多尺度候选：期望比值优先，再补 1.0 与 ±10% 邻域。

    ``ratio == 1``（或缺失）时只跑原尺寸——不为「看起来更稳」凭空多跑两次匹配，
    也保持 M75 既有口径逐字节不变。
    """
    if ratio is None or ratio <= 0 or abs(ratio - 1.0) < 1e-6:
        return (1.0,)
    out: list[float] = []
    for value in (ratio, 1.0, ratio * 0.9, ratio * 1.1):
        rounded = round(float(value), 6)
        if rounded > 0 and rounded not in out:
            out.append(rounded)
    return tuple(out)


def _default_resizer(template: bytes, scale: float) -> tuple[bytes, tuple[int, int]]:
    """Pillow 重采样（LANCZOS），输出 PNG 字节与新尺寸。"""
    from PIL import Image

    with Image.open(io.BytesIO(template)) as image:
        width, height = image.size
        size = (max(1, int(round(width * scale))), max(1, int(round(height * scale))))
        resized = image.resize(size, Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        resized.save(buffer, format="PNG")
        return buffer.getvalue(), size


def _window_origin(shot: dict[str, Any] | None) -> tuple[int, int]:
    """从 ``grab_window`` 的返回取窗口屏幕原点；截图失败/形状错 → 抛结构化错误。"""
    if not shot:
        raise ImageLocatorError("window screenshot unavailable")
    origin = shot.get("windowOrigin")
    if (
        not isinstance(origin, (list, tuple))
        or len(origin) != 2
        or not all(isinstance(v, int) for v in origin)
    ):
        raise ImageLocatorError("screenshot has no valid windowOrigin")
    return int(origin[0]), int(origin[1])


def _screen_png(shot: dict[str, Any]) -> bytes:
    """从 ``grab_window`` 的 dataUrl 取 PNG 字节（复用 ocr_worker 的 base64 口径）。"""
    data_url = str(shot.get("dataUrl", ""))
    prefix = "data:image/png;base64,"
    if not data_url.startswith(prefix):
        raise ImageLocatorError("screenshot dataUrl is not PNG base64")
    try:
        return base64.b64decode(data_url[len(prefix):], validate=True)
    except Exception as exc:  # noqa: BLE001 - binascii.Error 等多种
        raise ImageLocatorError(f"screenshot base64 invalid: {exc}") from exc


def locate_by_template(
    shot: dict[str, Any] | None,
    template: bytes,
    template_size: tuple[int, int],
    *,
    min_confidence: float = 0.8,
    matcher: TemplateMatcher | None = None,
    capture_scale: float | None = None,
    current_scale: float | None = None,
    resampler: TemplateResampler | None = None,
) -> ImageMatch:
    """模板匹配定位：在窗口截图里找 ``template``，回**屏幕绝对矩形**。

    - ``shot`` 来自 ``capture.screen_shot.grab_window``（含窗口原点、PNG dataUrl、
      当前 DPI/scale）；``template_size`` 是**捕获时**模板尺寸；
    - ``capture_scale`` / ``current_scale`` 给出两侧 provenance，期望比值
      ``current / capture``；缺任一侧就只跑原尺寸，**不猜 DPI**；
    - 候选尺度由 :func:`scale_candidates` 单点生成：期望比值优先，再补 1.0 与 ±10%。
      每个尺度都复用同一个 ``matcher``，只换模板字节，**不建第二套匹配实现**；
    - 命中矩形用该尺度的实际模板尺寸（当前屏幕像素），``used_scale`` 记录是哪个尺度；
    - ``min_confidence`` 是等值门：置信度 **<** 阈值即零命中（不是截断、不是放宽）。

    ★ 三态分明：零命中（全部候选置信度不足）、唯一命中、低置信度被拒。
    """
    ox, oy = _window_origin(shot)
    png = _screen_png(shot)  # type: ignore[arg-type]
    tw, th = int(template_size[0]), int(template_size[1])
    if tw <= 0 or th <= 0:
        raise ImageLocatorError("template_size must be positive")

    impl = matcher or _default_matcher()
    if impl is None:
        raise ImageLocatorError("template matcher unavailable (missing OCR runtime)")

    cap = _as_float(capture_scale)
    cur = _as_float(current_scale)
    ratio = None
    if cap is not None and cur is not None and cap > 0 and cur > 0:
        ratio = cur / cap

    best: ImageMatch | None = None
    best_conf: float | None = None
    for scale in scale_candidates(ratio):
        if abs(scale - 1.0) < 1e-6:
            candidate_bytes, candidate_size = template, (tw, th)
        else:
            if resampler is not None:
                candidate_bytes, candidate_size = resampler.resize(template, scale)
            else:
                candidate_bytes, candidate_size = _default_resizer(template, scale)
        if candidate_size[0] <= 0 or candidate_size[1] <= 0:
            continue

        cx, cy, confidence = impl.match(png, candidate_bytes)
        if confidence is None:
            continue
        if best_conf is None or confidence > best_conf:
            best_conf = confidence
        if confidence < min_confidence:
            continue
        candidate = ImageMatch(
            left=ox + cx - candidate_size[0] // 2,
            top=oy + cy - candidate_size[1] // 2,
            width=candidate_size[0],
            height=candidate_size[1],
            confidence=confidence,
            count=1,
            used_scale=scale,
        )
        if best is None or confidence > best.confidence:  # type: ignore[operator]
            best = candidate

    if best is not None:
        return best
    return ImageMatch(0, 0, 0, 0, best_conf, 0)


def locate_by_text(
    shot: dict[str, Any] | None,
    keyword: str,
    *,
    detector: TextDetector | None = None,
    min_confidence: float = 0.0,
) -> ImageMatch:
    """文本定位：在窗口截图里检测含 ``keyword`` 的框，回**屏幕绝对矩形**。

    - ``keyword`` 为空 ⇒ 输入错误（不猜）；
    - 命中框的 ``text`` 字段包含 keyword（大小写不敏感）即算命中；
    - 多命中 ⇒ ``count>1`` 且回第一个框（歧义交给上层决策，不静默选一个）；
    - ``min_confidence`` 过滤低于阈值的候选框。

    ★ 注意 ddddocr detect 只出框不出字（见 ocr_worker.detect 文档），
    生产要真正按文本命中需 detector 替身补 ``text``；本层只认带 text 的框，
    没文本字段的框一律不算命中——宁可零命中，不假命中。
    """
    if not keyword or not keyword.strip():
        raise ImageLocatorError("keyword must not be empty")
    ox, oy = _window_origin(shot)
    _screen_png(shot)  # type: ignore[arg-type]  # 校验截图可用（结果由 detector 消费）

    impl = detector or _default_detector()
    if impl is None:
        raise ImageLocatorError("text detector unavailable (missing OCR runtime)")
    png = _screen_png(shot)  # type: ignore[arg-type]

    needle = keyword.strip().casefold()
    hits: list[dict[str, Any]] = []
    for box in impl.boxes(png):
        text = str(box.get("text", "")).casefold()
        conf = _as_float(box.get("confidence"))
        if needle in text and (conf is None or conf >= min_confidence):
            hits.append(box)

    if not hits:
        return ImageMatch(0, 0, 0, 0, None, 0)
    first = hits[0]
    return ImageMatch(
        left=ox + int(first.get("x", 0)),
        top=oy + int(first.get("y", 0)),
        width=int(first.get("width", 0)),
        height=int(first.get("height", 0)),
        confidence=_as_float(first.get("confidence")),
        count=len(hits),
    )
