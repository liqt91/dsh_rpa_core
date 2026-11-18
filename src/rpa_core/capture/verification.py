"""统一元素校验编排（M74 S1：一个门面，两端分派）。

**为什么要有这一层**：历史上「校验元素」只有 browser 一条通道
（``capture.verify.ElementVerifier``），桌面元素根本没有等价的活体校验。
本模块是校验的**编排权威**——调用方只认
:func:`verify_element` 一个入口，按元素的 ``kind`` 分派：

* ``browser`` → ``ElementVerifier``（现有 Web 通道，目标选择逻辑一行不改）；
* ``desktop`` → ``DesktopElementVerifier``（M74 S3，复用 ``_find`` 现场查找）；
* 其它/未知 ``kind`` → 结构化 ``error="unsupported-kind"``，**不静默当 browser**。

两端 verifier 都回 ``dict``，门面用 :meth:`VerificationResult.from_reply` 把回传收口成
**同一个形状**（``count`` / ``windowHandle`` / ``rect`` / ``error``），调用方不再各自解析。
``raw`` 保留原始回传的扩展字段（``viewport`` / ``extBuild`` …），供预览页等继续取用。

## 不做什么

* **不复制 Web 的目标选择逻辑**：browser 分支照旧委托 ``ElementVerifier``，它的 Z 序
  自动目标、定点下发、置前三 mode 等判据保持原样（M47/M48 全绿不退化）。
* **不建第二套定位权威**：desktop 分支委托 ``DesktopElementVerifier``，后者复用
  ``DesktopExecutor._find``（M74 S3）。
* **不碰 GUI / 元素模型**：本模块只读 ``ElementDescriptor`` 的 ``kind``/``selector``，不改它。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_MODES = ("flash", "preview", "clear")


@dataclass(frozen=True)
class VerificationResult:
    """两端统一的校验回传形状。

    ``error`` 非空时 ``count`` 恒为 0——「报错」与「命中 0 个」是两回事，调用方靠
    这个字段区分「元素找不到」和「校验通道坏了」（GUI 要展示不同的提示）。
    """

    count: int = 0
    windowHandle: int | None = None
    rect: dict[str, Any] | None = None
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """序列化成前端/调用方易消费的 dict；``error`` 非空时省略命中类字段。"""
        if self.error:
            return {"count": 0, "error": self.error}
        out: dict[str, Any] = {"count": self.count}
        if self.windowHandle is not None:
            out["windowHandle"] = self.windowHandle
        if self.rect is not None:
            out["rect"] = dict(self.rect)
        # --shot 的截图元数据（桌面腿由 shot_for_desktop 产出）只在真要时回传；
        # Web 通道不带这个键，所以既有 CLI/契约输出形状不变。
        shot = self.raw.get("shot")
        if isinstance(shot, dict) and shot:
            out["shot"] = dict(shot)
        # 图像腿的重采样诊断：命中时说明「用的是哪个尺度」，没有就不加键。
        used_scale = self.raw.get("usedScale")
        if isinstance(used_scale, (int, float)) and not isinstance(used_scale, bool):
            out["usedScale"] = float(used_scale)
        # 命中 0 时的**可行动诊断**（M100）：目前只有 ``tree-missing``——目标窗口的
        # 无障碍树没建起来，跟「选择器写错了」是两回事，GUI 要给不同提示。
        hint = self.raw.get("hint")
        if isinstance(hint, str) and hint:
            out["hint"] = hint
        return out

    @classmethod
    def from_reply(cls, reply: Any) -> VerificationResult:
        """把 verifier 的原始回传归一成统一形状。

        任何非 dict、非法 count、非空 error 都走结构化错误，绝不把垃圾当命中数——
        与 ``ElementVerifier._exchange`` 的 bad-reply 口径一致。
        """
        if not isinstance(reply, dict):
            return cls(error="bad-reply")
        error = reply.get("error")
        if error is not None:
            return cls(error=str(error))
        count = reply.get("count")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            return cls(error="bad-reply")
        hwnd = reply.get("windowHandle")
        if not isinstance(hwnd, int) or isinstance(hwnd, bool) or hwnd <= 0:
            hwnd = None
        rect = reply.get("rect")
        return cls(
            count=count,
            windowHandle=hwnd,
            rect=dict(rect) if isinstance(rect, dict) else None,
            raw=dict(reply),
        )


def _unpack(element: Any) -> tuple[Any, dict[str, Any]]:
    """从 ``ElementDescriptor`` 或等价 dict 取 ``(kind, selector)``。

    只取这两个键，不碰元素模型其余字段——门面的职责是分派，不是校验文档。
    """
    if isinstance(element, dict):
        kind = element.get("kind")
        selector = element.get("selector")
    else:
        kind = getattr(element, "kind", None)
        selector = getattr(element, "selector", None)
    return kind, selector if isinstance(selector, dict) else {}


def _expected_hwnd(element: Any) -> int | None:
    """元素捕获时记下的目标窗口句柄；形状不对就当没记，不猜。"""
    metadata = (
        element.get("metadata")
        if isinstance(element, dict)
        else getattr(element, "metadata", None)
    )
    if not isinstance(metadata, dict):
        return None
    hwnd = metadata.get("windowHandle")
    if isinstance(hwnd, int) and not isinstance(hwnd, bool) and hwnd > 0:
        return hwnd
    return None


def _default_desktop_verifier(expected_hwnd: int | None = None) -> Any | None:
    """惰性取桌面 verifier。

    S3 落地前模块不存在 → 返回 None（调用方拿结构化错误，不抛异常给 GUI）；
    落地后正常返回 verifier 实例。非 Windows 也由 verifier 自身处理为结构化错误。

    ``expected_hwnd`` 来自元素捕获时记下的目标窗口：跨窗口搜索先查它，再按 Z 序兜底。
    不传就纯 Z 序——遮挡场景若两个窗口有相同 locator，纯 Z 序可能命中盖子窗口，
    那不是「找到原元素」（M79 真机实锤）。
    """
    try:
        from rpa_core.capture.desktop_verification import DesktopElementVerifier
    except ImportError:
        return None
    return DesktopElementVerifier(expected_hwnd=expected_hwnd)


def _default_image_verifier() -> Any | None:
    """惰性取图像 verifier（M75 S3）。模块缺失 → None（结构化错误，不抛给 GUI）。"""
    try:
        from rpa_core.capture.image_verification import ImageElementVerifier
    except ImportError:
        return None
    return ImageElementVerifier()


def _is_image_locator(selector: dict[str, Any]) -> bool:
    """desktop 元素的 locator 是否显式声明走图像驱动（M75 S2 守卫）。

    **只在显式时为真**——语义 locator（uia/win32）一律走原桌面 verifier，
    空命中也不降级（ADR 0003）。
    """
    from rpa_core.capture.image_guard import is_explicit_image

    locator = selector.get("locator")
    return isinstance(locator, dict) and is_explicit_image(locator)


def _invoke(verifier: Any, mode: str, query: Any, want_shot: bool) -> VerificationResult:
    """把 mode 映射到 verifier 的三方法并归一回传。"""
    if mode == "flash":
        return VerificationResult.from_reply(verifier.verify(query, want_shot=want_shot))
    if mode == "preview":
        return VerificationResult.from_reply(verifier.preview(query))
    if mode == "clear":
        return VerificationResult.from_reply(verifier.clear_preview())
    return VerificationResult(error="unsupported-mode")


def verify_element(
    element: Any,
    *,
    mode: str,
    want_shot: bool = False,
    browser_verifier: Any = None,
    desktop_verifier: Any = None,
    image_verifier: Any = None,
) -> VerificationResult:
    """校验一个元素（按 ``kind`` 分派，回传统一形状）。

    ``mode`` 与 Web 三 mode 对齐：``flash``（点一下闪框）、``preview``（编辑中驻留高亮）、
    ``clear``（清场）。未知 mode / 未知 kind 都是结构化错误，不抛异常。

    ``browser_verifier`` / ``desktop_verifier`` / ``image_verifier`` 是依赖注入点：生产用
    默认（browser 现网通道、desktop S3 实现、image M75 S3 实现），测试塞替身证明
    「分派到了哪一端」——避免只测内部纯函数、不测用户实际走的入口。

    desktop 分支里再按 locator 是否**显式**声明 image driver 二次分派（M75 S2 守卫）：
    语义 locator 走原桌面 verifier，显式 image 才走图像 verifier；空命中不降级。
    """
    kind, selector = _unpack(element)
    if kind == "browser":
        verifier = browser_verifier if browser_verifier is not None else _default_browser_verifier()
        return _invoke(verifier, mode, selector.get("css"), want_shot)
    if kind == "desktop":
        locator = selector.get("locator")
        if _is_image_locator(selector):
            image_ver = image_verifier if image_verifier is not None else _default_image_verifier()
            if image_ver is None:
                return VerificationResult(error="image-verifier-unavailable")
            return _invoke(image_ver, mode, locator, want_shot)
        verifier = (
            desktop_verifier
            if desktop_verifier is not None
            else _default_desktop_verifier(expected_hwnd=_expected_hwnd(element))
        )
        if verifier is None:
            return VerificationResult(error="desktop-verifier-unavailable")
        return _invoke(verifier, mode, locator, want_shot)
    return VerificationResult(error="unsupported-kind")


def _default_browser_verifier() -> Any:
    from rpa_core.capture.verify import ElementVerifier

    return ElementVerifier()
