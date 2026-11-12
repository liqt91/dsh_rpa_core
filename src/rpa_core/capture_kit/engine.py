"""捕获功能包的引擎层：会话工厂 + 手势文案 + 双腿真实状态。

只依赖 ``rpa_core.capture``（无 Qt、无宿主壳），所以任何宿主都能 import：
Qt 桌面、PPX/WebView、CLI、HTTP 服务。UI 见 ``float_window.py``，编排见
``controller.py``。

口径与 main 分支的 Qt GUI 一致（`gui/app.py` 的 `_capture_element`）：
**单入口混合捕获** —— 不分网页/桌面，桌面 hover + 扩展双通道，先回传者胜。
"""

from __future__ import annotations

from typing import Any

from rpa_core.capture.desktop import DesktopCaptureSession
from rpa_core.capture.extension import ExtensionCaptureSession, capture_click_label
from rpa_core.capture.hybrid import HybridCaptureSession

# 桌面 hover 会话没有「无限」参数，传一个极大的秒数等同永不超时（≈100 年）。
NEVER_TIMEOUT = 86400 * 365 * 10


def build_hybrid_session() -> HybridCaptureSession:
    """单入口混合会话：桌面 hover + 扩展双通道。

    ``hover=True`` 让桌面子进程进入框选态；``hybrid`` 由
    ``HybridCaptureSession`` 经 ``setdefault`` 注入（浏览器内容区让位给扩展）。
    """
    return HybridCaptureSession(
        desktop_factory=lambda **kw: DesktopCaptureSession(hover=True, **kw),
        extension_session=ExtensionCaptureSession(),
    )


def web_status(session: Any) -> tuple[str, bool]:
    """网页通道的（一行文案, 是否可用）。

    三种形态分开说：插件**离线**（去装插件）、插件**未响应**（端点在线但扩展
    没确认 arm → 重开浏览器/重装插件）、**就绪**（去页面里框选）。
    """
    click = capture_click_label()
    if getattr(session, "extension_offline", False):
        return "网页：插件离线（先装插件并打开浏览器）", False
    if getattr(session, "extension_unresponsive", False):
        return "网页：插件未响应（重开浏览器或重装插件）", False
    return f"网页：就绪（在页面里 {click} 或右键）", True


def desktop_status(session: Any) -> tuple[str, bool]:
    """桌面通道的（一行文案, 是否可用）。"""
    click = capture_click_label()
    if getattr(session, "desktop_offline", False):
        return "桌面：本平台不可用（仅支持 Windows）", False
    return f"桌面：就绪（F9 或 {click} / 右键）", True


def gesture_hint(session: Any) -> str:
    """与实际手势一致的一行提示。

    ★ 不能写「点一下」：扩展只认 ``Ctrl+Click``（Mac 为 ``⌘+Click``）或右键
    （见 ``extension/content.js`` 的 ``isCaptureModifier`` / ``isSecondaryClick``），
    普通点击不会捕获、还会让页面跳走。桌面腿另有 ``F9``。
    """
    click = capture_click_label()
    desktop_ok = not bool(getattr(session, "desktop_offline", False))
    desktop_tail = "；桌面也可用 F9" if desktop_ok else ""
    return f"移动鼠标框选，{click} 或右键捕获{desktop_tail}，Esc 取消"


def status_snapshot(session: Any) -> dict[str, Any]:
    """给宿主/浮窗用的统一状态快照。"""
    web, web_ok = web_status(session)
    desktop, desktop_ok = desktop_status(session)
    return {
        "gesture": gesture_hint(session),
        "web": web,
        "desktop": desktop,
        "webOk": web_ok,
        "desktopOk": desktop_ok,
        "armed": bool(getattr(session, "armed", False)),
    }
