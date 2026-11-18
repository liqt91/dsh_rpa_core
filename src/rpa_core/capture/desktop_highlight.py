"""桌面元素校验的瞬时高亮框（M78）：给 ``flash`` 一个用户看得见的反馈。

复用捕获期红框的窗口形态（置顶 / 点击穿透 / 只留边框带），但这里是**校验**用途：
只在 ``flash`` 模式画一次、短暂停留后销毁；``preview``/``clear`` 不画、也不抢焦点。
非 Windows 或缺 pywin32 时静默 no-op——观感增强不能把校验拖成失败。
"""

from __future__ import annotations

import time
from typing import Any

_BORDER = 3
_OUTSET = 5
_COLOR = (255, 59, 48)
_FLASH_SECONDS = 1.6
_CLASS_NAME = "RpaVerifyFlashOverlay"


def _build_overlay(win32gui: Any, win32con: Any, win32api: Any) -> Any:
    hinst = win32gui.GetModuleHandle(None)
    wc = win32gui.WNDCLASS()
    wc.lpfnWndProc = {win32con.WM_PAINT: lambda hwnd, msg, w, lp: _paint(
        win32gui, win32api, hwnd
    )}
    wc.lpszClassName = _CLASS_NAME
    wc.hInstance = hinst
    wc.hbrBackground = win32gui.GetStockObject(win32con.NULL_BRUSH)
    # 窗口类注册一次就够；每次 flash 都注册会在**第二次**抛 1410「类已存在」，
    # 被上层的兜底 except 吞掉 ⇒ 用户看到「第一次框、第二次不框」（M96 真机）。
    # 类留着无害（DestroyWindow 只销毁窗口，不注销类），复用同名类即可。
    try:
        win32gui.RegisterClass(wc)
    except Exception:  # noqa: BLE001 - 1410 类已存在 = 复用，其它错误交给外层兜底
        pass
    ex_style = (
        win32con.WS_EX_TOPMOST | win32con.WS_EX_TRANSPARENT
        | win32con.WS_EX_LAYERED | win32con.WS_EX_TOOLWINDOW
        | win32con.WS_EX_NOACTIVATE
    )
    hwnd = win32gui.CreateWindowEx(
        ex_style, "RpaVerifyFlashOverlay", "", win32con.WS_POPUP,
        0, 0, 0, 0, 0, 0, hinst, None,
    )
    win32gui.SetLayeredWindowAttributes(hwnd, 0, 200, win32con.LWA_ALPHA)
    return hwnd


def _paint(win32gui: Any, win32api: Any, hwnd: int) -> int:
    hdc, ps = win32gui.BeginPaint(hwnd)
    brush = win32gui.CreateSolidBrush(win32api.RGB(*_COLOR))
    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    win32gui.FillRect(hdc, (left, top, right, bottom), brush)
    win32gui.DeleteObject(brush)
    win32gui.EndPaint(hwnd, ps)
    return 0


def _show_overlay(win32gui: Any, win32con: Any, hwnd: int, rect: dict[str, Any]) -> bool:
    import ctypes

    left = int(rect["left"]) - _OUTSET
    top = int(rect["top"]) - _OUTSET
    width = int(rect["width"]) + 2 * _OUTSET
    height = int(rect["height"]) + 2 * _OUTSET
    if width <= 0 or height <= 0:
        # 零/负尺寸画不出来：必须回 False，不能「没画还报成功」（假绿灯）。
        return False
    gdi32 = ctypes.windll.gdi32
    outer = gdi32.CreateRectRgn(0, 0, width, height)
    inner = gdi32.CreateRectRgn(
        _BORDER, _BORDER, max(width - _BORDER, _BORDER), max(height - _BORDER, _BORDER)
    )
    gdi32.CombineRgn(outer, outer, inner, win32con.RGN_DIFF)
    gdi32.DeleteObject(inner)
    win32gui.SetWindowRgn(hwnd, outer, True)
    win32gui.SetWindowPos(
        hwnd, win32con.HWND_TOPMOST, left, top, width, height,
        win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW,
    )
    return True


def flash_rect(rect: dict[str, Any] | None, *, duration: float = _FLASH_SECONDS) -> bool:
    """在屏幕绝对矩形外沿画一次红框；画成功返回 True，失败静默 False。"""
    if not isinstance(rect, dict):
        return False
    try:
        import win32api
        import win32con
        import win32gui
    except Exception:  # noqa: BLE001 - 非 Windows / 缺 pywin32 都只看成「没有高亮」
        return False
    hwnd = None
    try:
        hwnd = _build_overlay(win32gui, win32con, win32api)
        if not _show_overlay(win32gui, win32con, hwnd, rect):
            return False
        deadline = time.monotonic() + max(0.0, float(duration))
        while time.monotonic() < deadline:
            win32gui.PumpWaitingMessages()
            time.sleep(0.02)
        return True
    except Exception:  # noqa: BLE001 - 观感层失败绝不影响校验结果
        return False
    finally:
        if hwnd:
            try:
                win32gui.DestroyWindow(hwnd)
            except Exception:  # noqa: BLE001
                pass


__all__ = ["flash_rect"]
