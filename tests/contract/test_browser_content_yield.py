"""M84：捕获「让位给扩展」的判据必须是「类名 + 进程名」，不能只看类名。

真机现象（维护者 2026-11-17）：鼠标停在 WorkBuddy 窗口上**连窗口都识别不出来**。
WorkBuddy 是 Electron 应用，渲染子窗类名同样叫 ``Chrome_RenderWidgetHostHWND``；
旧判据只看类名 ⇒ 整片内容区被当成「网页内容区」：高亮被隐藏、Ctrl+Click 被放行，
而它页面里并没有我们的扩展，于是用户侧是「悬停毫无反应」。
"""

from __future__ import annotations

from rpa_core.capture import desktop_agent, foreground_window


class _Api:
    """进程名接缝替身（真实实现走 QueryFullProcessImageNameW）。"""

    def __init__(self, name: str) -> None:
        self.name = name

    def process_name(self, hwnd: int) -> str:  # noqa: ARG002 - 协议要求签名
        return self.name


def test_electron_client_is_not_browser_content() -> None:
    render = "Chrome_RenderWidgetHostHWND"
    # 真机实测的三个 Electron 客户端进程名，都不是「能下发命令的浏览器」
    for name in ("WorkBuddy.exe", "咚咚.exe", "code.exe"):
        assert foreground_window.is_browser_content(render, 1, api=_Api(name)) is False
    # 已知浏览器才让位
    assert foreground_window.is_browser_content(render, 1, api=_Api("chrome.exe")) is True
    assert foreground_window.is_browser_content(render, 1, api=_Api("MSEDGE.EXE")) is True
    # 顶层窗口类名（不是渲染子窗）⇒ 不是内容区，哪怕进程是浏览器
    assert foreground_window.is_browser_content(
        "Chrome_WidgetWin_1", 1, api=_Api("chrome.exe")
    ) is False
    # 取不到进程名 ⇒ 不认（宁可走桌面腿，也不把捕获让给一个不确定的扩展）
    assert foreground_window.is_browser_content(render, 1, api=_Api("")) is False


def test_agent_passes_root_window_to_the_predicate(monkeypatch) -> None:
    """进程名按**顶层**窗口取：传渲染子窗句柄也能收敛到 root，且用 root 判定。"""
    seen: list[tuple[str, int]] = []

    monkeypatch.setattr(desktop_agent, "_root_window_handle", lambda hwnd: 999)
    monkeypatch.setattr(
        desktop_agent,
        "is_browser_content",
        lambda cls, hwnd: (seen.append((cls, hwnd)) or True),
    )
    assert desktop_agent._browser_content_at("Chrome_RenderWidgetHostHWND", 3214206) is True
    assert seen == [("Chrome_RenderWidgetHostHWND", 999)]

    seen.clear()
    # 句柄为 0（命中了虚拟元素/桌面）⇒ 直接不是内容区，不去问进程名
    assert desktop_agent._browser_content_at("Chrome_RenderWidgetHostHWND", 0) is False
    assert seen == []


def test_click_hook_uses_the_same_predicate_source() -> None:
    """点钩子不许自己再抄一份类名名单——它必须走同一份判定。"""
    from rpa_core.capture import desktop_click_hook

    assert desktop_click_hook.is_browser_content is foreground_window.is_browser_content
