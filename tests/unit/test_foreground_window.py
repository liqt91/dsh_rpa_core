"""M47.9 前台浏览器窗口识别（Z 序）的单元判据。

纯逻辑用替身 API 测（任意平台可跑）；Windows 真实 API 的行为另有真机探针
（``.harness/spike``）与维护者真机验收，不在单测里依赖。

规格来源：维护者实测——**影刀校验时只看「哪个浏览器是最后被激活的」**。
依次点 edge → chrome → 资源管理器 再校验 ⇒ 取 **chrome**（非浏览器窗口被跳过）。
"""

from __future__ import annotations

from rpa_core.capture.foreground_window import (
    ForegroundBrowser,
    _is_browser_window,
    _is_user_window,
    bring_to_foreground,
    first_browser_window,
)


class FakeAPI:
    """替身：直接给出行集，不做真实 win32 调用。"""

    def __init__(self, rows, set_foreground_ok=True, raise_on_set=False):
        self._rows = rows
        self._set_ok = set_foreground_ok
        self._raise_on_set = raise_on_set
        self.set_calls: list[int] = []

    def enumerate_top_windows(self):
        return list(self._rows)

    def foreground(self):
        return 0

    def set_foreground(self, hwnd):
        self.set_calls.append(hwnd)
        if self._raise_on_set:
            raise RuntimeError("foreground lock")
        return self._set_ok


def _row(hwnd, cls, title, proc, visible=True):
    return (hwnd, cls, title, proc, visible)


def test_first_browser_skips_non_browser_windows():
    """维护者例子：中间夹着资源管理器与我们的对话框，仍取最近的**浏览器**。"""
    api = FakeAPI([
        _row(1, "HwndWrapper[ShadowBot.Shell;;x]", "元素编辑器", "ShadowBot.Shell.exe"),
        _row(2, "HwndWrapper[ShadowBot.Shell;;y]", "UISpyOverlay", "ShadowBot.Shell.exe"),
        _row(3, "CabinetWClass", "此电脑", "explorer.exe"),          # 资源管理器（非浏览器）
        _row(4, "Chrome_WidgetWin_1", "x - Google Chrome", "chrome.exe"),
        _row(5, "Chrome_WidgetWin_1", "y", "msedge.exe"),
    ])
    found = first_browser_window(api)
    assert found is not None
    assert found.browser == "chrome" and found.hwnd == 4


def test_first_browser_skips_hidden_and_untitled():
    """不可见 / 无标题的系统窗必须被跳过（实测 GetTopWindow 链里密布这类窗口）。"""
    api = FakeAPI([
        _row(1, "tooltips_class32", "", "explorer.exe", visible=False),
        _row(2, "IME", "Default IME", "explorer.exe", visible=False),
        _row(3, "Chrome_WidgetWin_1", "", "chrome.exe", visible=True),   # 有类无标题
        _row(4, "Chrome_WidgetWin_1", "real", "chrome.exe", visible=True),
    ])
    found = first_browser_window(api)
    assert found is not None and found.hwnd == 4


def test_first_browser_skips_custom_chromium_clients():
    """定制 Chromium 客户端（咚咚/WorkBuddy，类名同为 Chrome_WidgetWin_1）**不是**浏览器。

    实测本机就有这类窗口排在真 Edge 之前——只看类名会误判，必须靠进程名。
    """
    api = FakeAPI([
        _row(1, "Chrome_WidgetWin_1", "咚咚", "咚咚.exe"),
        _row(2, "Chrome_WidgetWin_1", "WorkBuddy", "WorkBuddy.exe"),
        _row(3, "Chrome_WidgetWin_1", "real - Google Chrome", "chrome.exe"),
    ])
    found = first_browser_window(api)
    assert found is not None and found.browser == "chrome" and found.hwnd == 3


def test_first_browser_none_when_no_browser():
    api = FakeAPI([
        _row(1, "CabinetWClass", "此电脑", "explorer.exe"),
        _row(2, "Progman", "Program Manager", "explorer.exe"),
    ])
    assert first_browser_window(api) is None


def test_first_browser_none_when_api_unavailable(monkeypatch):
    """API 不可用（非 Windows）恒 None——调用方据此退回广播。

    ``api=None`` 语义是「用默认实现」，所以这里要打桩**默认实现本身**为 None。
    """
    import rpa_core.capture.foreground_window as fg_mod

    monkeypatch.setattr(fg_mod, "_default_api", lambda: None)
    assert first_browser_window() is None


def test_first_browser_swallows_enumerate_error():
    class BoomAPI(FakeAPI):
        def enumerate_top_windows(self):
            raise OSError("gone")

    assert first_browser_window(BoomAPI([])) is None


def test_browser_identification_by_process_only():
    """判定**只看进程名**：类名对但进程不是已知浏览器 ⇒ 不是浏览器。"""
    assert _is_browser_window("Chrome_WidgetWin_1", "msedge.exe") == "msedge"
    assert _is_browser_window("Chrome_WidgetWin_1", "chrome.exe") == "chrome"
    assert _is_browser_window("MozillaWindowClass", "firefox.exe") == "firefox"
    assert _is_browser_window("Chrome_WidgetWin_1", "咚咚.exe") is None
    assert _is_browser_window("Chrome_WidgetWin_1", "") is None
    assert _is_browser_window("CabinetWClass", "explorer.exe") is None
    # 大小写不敏感
    assert _is_browser_window("Chrome_WidgetWin_1", "MSEDGE.EXE") == "msedge"


def test_user_window_filter():
    assert _is_user_window("Chrome_WidgetWin_1", "t", True) is True
    assert _is_user_window("Chrome_WidgetWin_1", "", True) is False      # 无标题
    assert _is_user_window("Chrome_WidgetWin_1", "t", False) is False    # 不可见
    assert _is_user_window("Progman", "Program Manager", True) is False  # 桌面壳


def test_bring_to_foreground_ok():
    api = FakeAPI([], set_foreground_ok=True)
    assert bring_to_foreground(123, api) is True
    assert api.set_calls == [123]


def test_bring_to_foreground_failure_is_swallowed():
    """Windows 前台锁定失败被吞：返回 False，不抛。"""
    api = FakeAPI([], raise_on_set=True)
    assert bring_to_foreground(123, api) is False


def test_bring_to_foreground_no_hwnd_is_false():
    api = FakeAPI([])
    assert bring_to_foreground(0, api) is False


def test_foreground_browser_is_frozen():
    fb = ForegroundBrowser(hwnd=1, browser="chrome", process="chrome.exe", title="t")
    assert fb.browser == "chrome"
    try:
        fb.hwnd = 2  # type: ignore[misc]
    except Exception as exc:  # noqa: BLE001
        assert "frozen" in str(exc).lower() or isinstance(exc, AttributeError)
    else:  # pragma: no cover - frozen dataclass 必抛
        raise AssertionError("ForegroundBrowser 应为不可变")
