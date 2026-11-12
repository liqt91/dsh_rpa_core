"""捕获功能包的引擎层：会话工厂 + 手势文案 + 双腿真实状态。

只依赖 ``rpa_core.capture``（无 Qt、无宿主壳），所以任何宿主都能 import：
Qt 桌面、PPX/WebView、CLI、HTTP 服务。UI 见 ``float_window.py``，编排见
``controller.py``。

口径与 main 分支的 Qt GUI 一致（`gui/app.py` 的 `_capture_element`）：
**单入口混合捕获** —— 不分网页/桌面，桌面 hover + 扩展双通道，先回传者胜。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from rpa_core.capture.desktop import DesktopCaptureSession
from rpa_core.capture.extension import ExtensionCaptureSession, capture_click_label
from rpa_core.capture.hybrid import HybridCaptureSession

# 桌面 hover 会话没有「无限」参数，传一个极大的秒数等同永不超时（≈100 年）。
NEVER_TIMEOUT = 86400 * 365 * 10

# 扩展安装状态来自浏览器档案（读盘），轮询里每 500ms 读一次太贵 ⇒ 缓存 5s。
_INSTALLED_TTL = 5.0
_installed_cache: dict[str, Any] = {}


def extension_dir() -> Path | None:
    """源码树里的扩展目录（本包在 ``<repo>/src/rpa_core/capture_kit``）。

    从包位置**向上找**而不是写死相对层级：源码运行时命中 ``<repo>/extension``，
    包装进 PyInstaller / 装进 site-packages 时找不到就返回 None（视为未安装）。
    """
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "extension"
        if (candidate / "manifest.json").is_file():
            return candidate
    return None


def extension_installed() -> dict[str, str]:
    """已加载本扩展的浏览器 → 扩展 ID（只读浏览器档案，带 5s 缓存）。"""
    now = time.monotonic()
    cached = _installed_cache.get("value")
    if cached is not None and now - float(_installed_cache.get("at", 0.0)) < _INSTALLED_TTL:
        return cached

    found: dict[str, str] = {}
    ext = extension_dir()
    if ext is not None:
        from rpa_core.extension_installer import discover_unpacked_extension_id

        for browser in ("edge", "chrome"):
            try:
                extension_id = discover_unpacked_extension_id(browser, ext)
            except Exception:  # noqa: BLE001 - 读档案失败只影响文案，不该拖死捕获
                extension_id = None
            if extension_id:
                found[browser] = extension_id
    _installed_cache["value"] = found
    _installed_cache["at"] = now
    return found


def ensure_extension_host() -> dict[str, Any]:
    """扩展已装但 Native Messaging 白名单过期时**幂等重注册**（自愈）。

    为什么必须自愈：unpacked 扩展的 ID 由**源码目录绝对路径**派生（manifest 里没有
    ``key``），仓库换路径、换机器、换副本后 ID 就变；而 host manifest 的
    ``allowed_origins`` 是写死的白名单。ID 不在名单里时浏览器**直接拒绝**拉起 host
    （``connectNative`` forbidden），用户侧的表现是「插件明明装了、也启用了，网页里
    怎么点都没反应」，而 ``list_extension_endpoints()`` 返回 0 —— 看起来像没装。

    只在扩展腿离线时调用（写注册表这件事不该无条件做）。
    """
    installed = extension_installed()
    ext = extension_dir()
    if not installed or ext is None:
        return {"changed": False, "installed": installed, "refreshed": []}

    from rpa_core.extension_installer import ensure_native_host, native_host_status

    refreshed: list[str] = []
    for browser, extension_id in installed.items():
        try:
            before = set(native_host_status(browser).get("allowedOrigins") or [])
        except Exception:  # noqa: BLE001 - 读不到旧状态就按「需要重写」处理
            before = set()
        try:
            ensure_native_host(browser, ext)
        except Exception:  # noqa: BLE001 - 注册失败交给上层给文案，不在状态里抛
            continue
        if f"chrome-extension://{extension_id}/" not in before:
            refreshed.append(browser)
    return {"changed": bool(refreshed), "installed": installed, "refreshed": refreshed}


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

    三种形态分开说：插件**离线**（分「没装」与「装了没连通」两种处置）、插件
    **未响应**（端点在线但扩展没确认 arm → 重开浏览器/重装插件）、**就绪**。

    「装了没连通」这条是 2026-11-12 维护者报障的直接产物：插件在 Edge 里明明是
    启用状态，但 host 白名单里是旧路径派生的 ID ⇒ 浏览器拒绝拉起 host ⇒ 端点 0 个。
    把它说成「插件离线（先装插件）」，用户只会反复确认自己装过了。
    """
    click = capture_click_label()
    if getattr(session, "extension_offline", False):
        if extension_installed():
            return "网页：插件已装但未连通（重载插件或重开浏览器）", False
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
    普通点击不会捕获、还会让页面跳走。桌面腿的 ``F9`` 只放在「桌面」那一行，
    免得这一行太长在浮窗里被省略号截断。
    """
    click = capture_click_label()
    return f"移动鼠标框选：{click} 或右键捕获，Esc 取消"


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
