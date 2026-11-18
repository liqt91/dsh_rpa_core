"""校验高亮框：连续多次闪框必须每次都画得出来。

M96 真机：元素第一次校验框住了目标，第二次不再框——根因是 ``RegisterClass``
每次都注册同名窗口类，第二次抛 1410「类已存在」，异常被 ``flash_rect`` 的兜底
``except`` 吞掉，于是静默不画。判据必须**连调两次**：只调一次永远绿。
"""

from __future__ import annotations

import sys
import types

from rpa_core.capture.desktop_highlight import flash_rect

RECT = {"left": 100, "top": 100, "width": 200, "height": 80}


class _FakeWin32Gui:
    """只实现 flash_rect 用到的那几个入口；第二次 RegisterClass 抛 1410。"""

    def __init__(self) -> None:
        self.register_calls = 0
        self.created_windows = 0
        self.destroyed_windows = 0

    def GetModuleHandle(self, _name):  # noqa: N802 - 照抄 win32gui 名字
        return 1

    def WNDCLASS(self):  # noqa: N802
        return types.SimpleNamespace()

    def GetStockObject(self, _value):  # noqa: N802
        return 0

    def RegisterClass(self, _wc):  # noqa: N802
        self.register_calls += 1
        if self.register_calls > 1:
            raise Exception("(1410, 'RegisterClass', '类已存在。')")

    def CreateWindowEx(self, *args):  # noqa: N802
        self.created_windows += 1
        return 5000 + self.created_windows

    def SetLayeredWindowAttributes(self, *args):  # noqa: N802
        return True

    def GetClientRect(self, hwnd):  # noqa: N802
        return (0, 0, 10, 10)

    def BeginPaint(self, hwnd):  # noqa: N802
        return (1, types.SimpleNamespace())

    def EndPaint(self, hwnd, ps):  # noqa: N802
        return True

    def CreateSolidBrush(self, _color):  # noqa: N802
        return 7

    def FillRect(self, *args):  # noqa: N802
        return True

    def DeleteObject(self, _obj):  # noqa: N802
        return True

    def SetWindowRgn(self, *args):  # noqa: N802
        return True

    def SetWindowPos(self, *args):  # noqa: N802
        return True

    def PumpWaitingMessages(self):  # noqa: N802
        return 0

    def DestroyWindow(self, hwnd):  # noqa: N802
        self.destroyed_windows += 1
        return True


def _install_fakes(monkeypatch) -> _FakeWin32Gui:
    fake = _FakeWin32Gui()
    gui = types.ModuleType("win32gui")
    for name in dir(fake):
        if name.startswith("_"):
            continue
        setattr(gui, name, getattr(fake, name))
    con = types.ModuleType("win32con")
    for name, value in {
        "WM_PAINT": 0x000F,
        "NULL_BRUSH": 1,
        "WS_EX_TOPMOST": 0x8,
        "WS_EX_TRANSPARENT": 0x20,
        "WS_EX_LAYERED": 0x80000,
        "WS_EX_TOOLWINDOW": 0x80,
        "WS_EX_NOACTIVATE": 0x8000000,
        "WS_POPUP": 0x80000000,
        "LWA_ALPHA": 0x2,
        "HWND_TOPMOST": -1,
        "SWP_NOACTIVATE": 0x10,
        "SWP_SHOWWINDOW": 0x40,
        "RGN_DIFF": 4,
    }.items():
        setattr(con, name, value)
    api = types.ModuleType("win32api")
    api.RGB = lambda r, g, b: (r & 0xFF) | ((g & 0xFF) << 8) | ((b & 0xFF) << 16)
    monkeypatch.setitem(sys.modules, "win32gui", gui)
    monkeypatch.setitem(sys.modules, "win32con", con)
    monkeypatch.setitem(sys.modules, "win32api", api)
    return fake


def test_flash_rect_survives_repeated_calls(monkeypatch) -> None:
    """真实缺陷：第二次注册同名窗口类失败 ⇒ 静默不画。"""
    fake = _install_fakes(monkeypatch)

    first = flash_rect(RECT, duration=0)
    second = flash_rect(RECT, duration=0)
    third = flash_rect(RECT, duration=0)

    assert (first, second, third) == (True, True, True), (
        "连续闪框必须每次都画：第二次起被 1410 吞掉就是用户看到的『第一次框、第二次不框』"
    )
    assert fake.register_calls == 3, "三次都该尝试注册（重名由内部吞掉，不改调用次数）"
    assert fake.created_windows == 3, "每次都该真的建出 overlay 窗口"
    assert fake.destroyed_windows == 3, "每次结束都要销毁，不能留下常驻框"


def test_flash_rect_noop_on_bad_rect(monkeypatch) -> None:
    """矩形不合法时不画，也不该误报成功。"""
    _install_fakes(monkeypatch)
    assert flash_rect(None, duration=0) is False
    # 尺寸小到连外扩都撑不出正矩形：必须回 False（曾出现「没画还报 True」）
    assert flash_rect({"left": 0, "top": 0, "width": -100, "height": -100},
                      duration=0) is False
