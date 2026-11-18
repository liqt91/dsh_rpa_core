"""图像校验 verifier（M75 S3）：把图像定位结果收口成统一校验回传形状。

接在 M74 ``verification.py`` 门面的 desktop 分支**之后**——只有 locator 显式声明
``driver="image"`` 才会走到这里（S2 守卫判定），语义（UIA/win32）路径一行不改。

回传形状与 ``DesktopElementVerifier`` 完全一致：
``{count, windowHandle, rect}`` / ``{count:0}`` / ``{error}``。
门面的 ``VerificationResult.from_reply`` 无需改动即可归一。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rpa_core.capture.image_guard import image_locator_errors, is_explicit_image
from rpa_core.capture.image_locator import ImageLocatorError, ImageMatch


class ImageElementVerifier:
    """图像校验：显式 driver=image 的 locator → 统一形状的校验回传。

    依赖注入点（跨平台可测，不依赖真窗口/OCR 运行时）：
    * ``shot_fn(hwnd) -> dict|None``：默认 ``screen_shot.grab_window``；
    * ``locate_fn(shot, locator) -> ImageMatch``：默认按 locator 字段选
      ``locate_by_template`` / ``locate_by_text``。
    """

    def __init__(
        self,
        shot_fn: Callable[[int], dict[str, Any] | None] | None = None,
        locate_fn: Callable[[dict[str, Any], dict[str, Any]], ImageMatch] | None = None,
        bring_front: Callable[[int], None] | None = None,
    ) -> None:
        self._shot_fn = shot_fn
        self._locate_fn = locate_fn
        self._bring_front = bring_front

    def _locate(self, shot: dict[str, Any], locator: dict[str, Any]) -> ImageMatch:
        """按 locator 声明的图像定位方式分派（模板 / 文本二选一）。"""
        if self._locate_fn is not None:
            return self._locate_fn(shot, locator)

        # 生产默认：imageTemplate(Base64) → 模板匹配；imageKeyword → 文本定位
        import base64

        from rpa_core.capture.image_locator import locate_by_template, locate_by_text

        if locator.get("imageTemplate"):
            template = base64.b64decode(str(locator["imageTemplate"]), validate=True)
            size = locator.get("templateSize") or [0, 0]
            current_scale = _scale(shot.get("scale"))
            if current_scale is None:
                raise ImageLocatorError("current-dpi-unavailable")

            capture_scale = _scale(locator.get("captureScale"))
            if capture_scale is None:
                # 命令式 desktop.imageLocate 传的是「当前分辨率」模板：没有捕获期
                # provenance 就按当前截图侧算比值（ratio=1），不猜历史 DPI。
                capture_scale = current_scale
            return locate_by_template(
                shot,
                template,
                (int(size[0]), int(size[1])),
                min_confidence=float(locator.get("minConfidence", 0.8)),
                capture_scale=capture_scale,
                current_scale=current_scale,
            )
        if locator.get("imageKeyword"):
            return locate_by_text(shot, str(locator["imageKeyword"]))
        raise ImageLocatorError("locator has no imageTemplate / imageKeyword")

    def _run(self, locator: Any, mode: str, want_shot: bool = False) -> dict[str, Any]:
        if not isinstance(locator, dict):
            return {"error": "invalid-locator"}
        # 显式选择守卫：不是显式 image 就拒绝（禁静默降级，S2 红线）
        if not is_explicit_image(locator):
            return {"error": "image-not-explicit"}
        # M80：V1 模板没有 DPI provenance ⇒ 显式报错重捕获，不做数据迁移。
        # 放在这里而不是只在纯定位函数里，是为了覆盖 verify_element 用户路径。
        shape_errors = image_locator_errors(locator)
        if shape_errors:
            return {"error": shape_errors[0]}
        if mode not in ("flash", "preview", "clear"):
            return {"error": "unsupported-mode"}
        if mode == "clear":
            return {"count": 0}

        hwnd = locator.get("windowHandle")
        if not isinstance(hwnd, int) or isinstance(hwnd, bool) or hwnd <= 0:
            return {"error": "invalid-window-handle"}

        # 图像匹配吃的是「屏幕上的窗口」：被别的窗口盖住时先在 flash 模式把目标抬上来，
        # 否则模板对的是遮挡后的画面。preview 不抢焦，与三 mode 口径一致。
        if mode == "flash":
            bring = self._bring_front
            if bring is None:
                from rpa_core.capture.foreground_window import bring_to_foreground

                bring = bring_to_foreground
            try:
                bring(hwnd)
            except Exception:  # noqa: BLE001 - 前台锁定不该把校验拖成失败
                pass

        shot_fn = self._shot_fn or _default_shot
        shot = shot_fn(hwnd)
        if not shot:
            return {"count": 0}  # 截图失败不是校验失败（同 grab_window 口径）

        try:
            match = self._locate(shot, locator)
        except ImageLocatorError as exc:
            return {"error": str(exc)}

        if match.count == 0:
            return {"count": 0}

        rect = match.as_rect()
        result: dict[str, Any] = {
            "count": match.count,
            "windowHandle": hwnd,
            "rect": rect,
            # usedScale 无条件回：它是「重采样到底生效没有」的核心证据，不能要求调用方
            # 额外索取 --shot 才看得到。语义分支没有缩放概念，所以这是图像腿独有的字段。
            "usedScale": float(match.used_scale),
        }
        if want_shot:
            # 截图回执只在真要时回（体积大），拍不到不影响命中数。
            shot_meta = _shot_from(shot, rect)
            if shot_meta:
                result["shot"] = shot_meta
        return result

    def verify(self, locator: Any, *, want_shot: bool = False) -> dict[str, Any]:
        return self._run(locator, "flash", want_shot)

    def preview(self, locator: Any) -> dict[str, Any]:
        return self._run(locator, "preview")

    def clear_preview(self) -> dict[str, Any]:
        return self._run({}, "clear")


def _scale(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _shot_from(shot: dict[str, Any], rect: dict[str, Any]) -> dict[str, Any] | None:
    """复用「用于匹配的那张截图」当回执，只补一个图内命中框。

    刻意**不重新截图**：二次截图会让 box 与命中矩形来自不同帧，元素一动就对不上。
    """
    try:
        from rpa_core.capture.screen_shot import box_in_window

        origin = shot.get("windowOrigin")
        if not isinstance(origin, (list, tuple)) or len(origin) != 2:
            return None
        meta = dict(shot)
        box = box_in_window(rect, (origin[0], origin[1]))
        if box is not None:
            meta["box"] = box
        return meta
    except Exception:  # noqa: BLE001 - 回执拍不到不影响命中数
        return None


def _default_shot(hwnd: int) -> dict[str, Any] | None:
    from rpa_core.capture.screen_shot import grab_window

    return grab_window(hwnd)
