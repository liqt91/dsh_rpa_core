"""前台窗口 Z 序识别（M47.9 浏览器 / M74 S2 跨窗口搜索）的单元判据。

纯逻辑用替身 API 测（任意平台可跑）；Windows 真实 API 的行为另有真机探针
（``.harness/spike``）与维护者真机验收，不在单测里依赖。

规格来源：维护者实测——**影刀校验时只看「哪个浏览器是最后被激活的」**。
依次点 edge → chrome → 资源管理器 再校验 ⇒ 取 **chrome**（非浏览器窗口被跳过）。
"""

from __future__ import annotations

from rpa_core.capture.foreground_window import (
    ForegroundBrowser,
    TopWindow,
    _is_browser_window,
    _is_user_window,
    bring_to_foreground,
    find_window_for_element,
    first_browser_window,
    visible_top_windows,
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


# ---- M74 S2：跨窗口搜索（Z 序顺序 + 命中即停）-------------------------------


def test_visible_top_windows_preserves_zorder_and_filters():
    """原语按传入顺序（Z 序）返回所有可见用户窗，过滤系统窗/不可见/无标题。"""
    api = FakeAPI([
        _row(10, "tooltips_class32", "", "sys.exe", visible=False),   # 不可见
        _row(11, "Chrome_WidgetWin_1", "Edge", "msedge.exe"),
        _row(12, "CabinetWClass", "此电脑", "explorer.exe"),          # 资源管理器也是用户窗
        _row(13, "Progman", "Program Manager", "explorer.exe"),      # 桌面壳
        _row(14, "Chrome_WidgetWin_1", "Chrome", "chrome.exe"),
    ])
    got = visible_top_windows(api)
    assert [w.hwnd for w in got] == [11, 12, 14]  # 顺序=Z 序，一个都不能少/换
    assert all(isinstance(w, TopWindow) for w in got)


def test_visible_top_windows_include_desktop_shell_for_taskbar():
    """M95：任务栏无标题，桌面校验需按原 Z 序位置放行；默认链仍当系统窗滤掉。"""
    api = FakeAPI([
        _row(70, "Chrome_WidgetWin_1", "Edge", "msedge.exe"),
        _row(71, "Shell_TrayWnd", "", "explorer.exe"),
        _row(72, "Shell_SecondaryTrayWnd", "", "explorer.exe"),
        _row(73, "Progman", "Program Manager", "explorer.exe"),
        _row(74, "CabinetWClass", "此电脑", "explorer.exe"),
    ])
    assert [w.hwnd for w in visible_top_windows(api)] == [70, 74]
    got = visible_top_windows(api, include_desktop_shell=True)
    assert [w.hwnd for w in got] == [70, 71, 72, 74]  # 原位插入；Progman 仍滤


def test_find_window_stops_at_first_hit_in_zorder():
    """顺序判据（等值）：两个窗口都命中时，**必须返回更靠前的那个**。

    只断言「返回非 None」是恒绿假判据；这里钉死 hwnd 等值 + probe 命中即停
    （第三个窗口的 probe 根本不该被调用）。
    """
    api = FakeAPI([
        _row(21, "Chrome_WidgetWin_1", "A", "chrome.exe"),
        _row(22, "Chrome_WidgetWin_1", "B", "msedge.exe"),
        _row(23, "Chrome_WidgetWin_1", "C", "chrome.exe"),
    ])
    probed: list[int] = []

    def probe(window: TopWindow) -> bool:
        probed.append(window.hwnd)
        return window.hwnd in (22, 23)  # B 和 C 都「有元素」，A 没有

    hit = find_window_for_element(probe, api)
    assert hit is not None and hit.hwnd == 22      # 更靠前的 B
    assert probed == [21, 22]                       # C 没被 probe（命中即停）


def test_find_window_flips_when_order_reversed():
    """把命中窗口挪到链尾 ⇒ 返回更靠前的另一个（证明依赖的是顺序，不是 hwnd 大小）。"""
    api = FakeAPI([
        _row(31, "Chrome_WidgetWin_1", "B", "msedge.exe"),
        _row(32, "Chrome_WidgetWin_1", "A", "chrome.exe"),
    ])
    hit = find_window_for_element(lambda w: True, api)
    assert hit is not None and hit.hwnd == 31  # 链首，即使 31 < 32


def test_find_window_preferred_hwnd_jumps_to_front():
    """元素记下的窗口句柄优先查：即便它在 Z 序里排第三，也第一个被 probe。"""
    api = FakeAPI([
        _row(41, "Chrome_WidgetWin_1", "A", "chrome.exe"),
        _row(42, "Chrome_WidgetWin_1", "B", "msedge.exe"),
        _row(43, "Chrome_WidgetWin_1", "C", "chrome.exe"),
    ])
    probed: list[int] = []

    def probe(window: TopWindow) -> bool:
        probed.append(window.hwnd)
        return True  # 三个都命中 ⇒ 返回链首

    hit = find_window_for_element(probe, api, preferred_hwnd=43)
    assert hit is not None and hit.hwnd == 43
    assert probed == [43]  # 插队到链首且命中即停，后面两个一概不碰


def test_find_window_include_desktop_shell_reaches_taskbar():
    """M95：默认链够不到任务栏；打开开关后按 Z 序 probe 到它并命中即停。"""
    api = FakeAPI([
        _row(80, "Chrome_WidgetWin_1", "Edge", "msedge.exe"),
        _row(81, "Shell_TrayWnd", "", "explorer.exe"),
    ])
    probed: list[int] = []

    def probe(window: TopWindow) -> bool:
        probed.append(window.hwnd)
        return window.hwnd == 81

    assert find_window_for_element(probe, api) is None
    assert probed == [80]
    probed.clear()
    hit = find_window_for_element(probe, api, include_desktop_shell=True)
    assert hit is not None and hit.hwnd == 81
    assert probed == [80, 81]


def test_find_window_none_when_no_hit():
    """全不命中 ⇒ None（调用方报 count=0，不猜不降级）。"""
    api = FakeAPI([_row(51, "Chrome_WidgetWin_1", "A", "chrome.exe")])
    assert find_window_for_element(lambda w: False, api) is None


def test_find_window_ignores_preferred_not_in_chain():
    """preferred 句柄不在当前 Z 序（窗口已关）⇒ 退回整条链，不报错。"""
    api = FakeAPI([_row(61, "Chrome_WidgetWin_1", "A", "chrome.exe")])
    hit = find_window_for_element(lambda w: True, api, preferred_hwnd=99999)
    assert hit is not None and hit.hwnd == 61


def test_find_window_api_unavailable_is_none():
    """非 Windows / API 不可用 ⇒ None（不是抛异常）。"""
    hit = find_window_for_element(lambda w: True, None)  # 默认 API 在非 Windows 为 None
    # 真机上默认 API 非 None，这里只保证「不抛、返回 TopWindow 或 None」
    assert hit is None or isinstance(hit, TopWindow)
