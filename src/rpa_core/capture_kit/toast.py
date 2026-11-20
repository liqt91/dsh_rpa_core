"""校验期间的居中提示条（对应影刀的「正在校验元素…」toast）。

为什么单独一个窗口而不是在编辑框上盖一块：影刀校验时是**把编辑框整个收起来**
——目标窗口要置前、要能看见红框。盖在编辑框里的提示会跟着一起消失，用户就只剩
一片空屏不知道该等还是该点。所以它是一个**独立顶层窗口**，不挂在编辑框下面
（``WS_POPUP`` 且不设 owner），编辑框 ``SW_HIDE`` 不会连它一起藏。

三个必须守住的点：

- ``WS_EX_NOACTIVATE``：浮出来不能抢走焦点，否则校验结束后键盘焦点要重设；
- ``WS_EX_TOOLWINDOW``：不准在任务栏留一个条目；
- 圆角靠 ``SetWindowRgn`` 裁，别指望 ``WS_POPUP`` 自带——方形深色块在浅色
  桌面上很扎眼。
"""

from __future__ import annotations

import sys
from typing import Any

from . import _win32

_CLASS = "RpaCheckToast"
# WM_PRINT：抓图/打印时窗口要自己把内容画进 wparam 这个 HDC
_WM_PRINT = 0x0317
_WIDTH = 268
_HEIGHT = 84
_RADIUS = 16
_BG = (43, 43, 43)
_FG = (245, 245, 245)
# 三个点的节拍（毫秒）：慢一点显得从容，太快像进度条在闪
_TICK_MS = 260


class CheckToast:
    """一个可复用实例只能顺序用：``show()`` → ``close()`` → 再 ``show()``。"""

    def __init__(self) -> None:
        self._hwnd: int | None = None
        self._text = "正在校验元素"
        self._tick = 0
        self._timer = 0
        self._hfont = 0

    # -- 对外 ------------------------------------------------------------------
    def show(self, text: str = "正在校验元素") -> None:
        """在当前线程创建并显示；必须在**有消息泵**的线程调用。"""
        if sys.platform != "win32":
            return
        if self._hwnd:
            self.set_text(text)
            return
        import win32con
        import win32gui

        self._text = text
        self._tick = 0
        _win32.register_class(_CLASS)
        self._hfont = _win32.make_font()
        ex_style = (
            win32con.WS_EX_TOPMOST | win32con.WS_EX_TOOLWINDOW
            | win32con.WS_EX_LAYERED | win32con.WS_EX_NOACTIVATE
        )
        left, top = self._center()
        hwnd = win32gui.CreateWindowEx(
            ex_style,
            _CLASS,
            "",
            win32con.WS_POPUP,
            left,
            top,
            _WIDTH,
            _HEIGHT,
            0,
            0,
            win32gui.GetModuleHandle(None),
            None,
        )
        if not hwnd:
            return
        self._hwnd = hwnd
        _win32._INSTANCES[int(hwnd)] = self
        self._clip_round(win32gui, hwnd)
        # 轻微透明：纯黑块太硬，208/255 既有存在感又不糊住下面的目标
        win32gui.SetLayeredWindowAttributes(hwnd, 0, 236, win32con.LWA_ALPHA)
        win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
        win32gui.UpdateWindow(hwnd)
        self._timer = _win32.set_timer(hwnd, 1, _TICK_MS)

    def set_text(self, text: str) -> None:
        self._text = text
        self._invalidate()

    def close(self) -> None:
        hwnd = self._hwnd
        self._hwnd = None
        if not hwnd:
            return
        import win32gui

        if self._timer:
            _win32.kill_timer(hwnd, self._timer)
            self._timer = 0
        _win32._INSTANCES.pop(int(hwnd), None)
        try:
            win32gui.DestroyWindow(hwnd)
        except Exception:  # noqa: BLE001 - 已经没了就算了
            pass
        if self._hfont:
            try:
                win32gui.DeleteObject(self._hfont)
            except Exception:  # noqa: BLE001
                pass
            self._hfont = 0

    @property
    def visible(self) -> bool:
        return bool(self._hwnd)

    # -- 内部 ------------------------------------------------------------------
    def _center(self) -> tuple[int, int]:
        """居中到鼠标所在显示器的工作区（与编辑框同一取舍：用户正看着哪块屏）。"""
        import win32api
        import win32con

        try:
            cursor = win32api.GetCursorPos()
            monitor = win32api.MonitorFromPoint(
                cursor, win32con.MONITOR_DEFAULTTONEAREST
            )
            left, top, right, bottom = win32api.GetMonitorInfo(monitor)["Work"]
        except Exception:  # noqa: BLE001 - 拿不到就退主屏
            left = top = 0
            right = win32api.GetSystemMetrics(win32con.SM_CXSCREEN)
            bottom = win32api.GetSystemMetrics(win32con.SM_CYSCREEN)
        return (
            left + (right - left - _WIDTH) // 2,
            top + (bottom - top - _HEIGHT) // 2,
        )

    def _clip_round(self, win32gui: Any, hwnd: int) -> None:
        import ctypes

        gdi32 = ctypes.windll.gdi32
        region = gdi32.CreateRoundRectRgn(
            0, 0, _WIDTH + 1, _HEIGHT + 1, _RADIUS, _RADIUS
        )
        win32gui.SetWindowRgn(hwnd, region, True)

    def _invalidate(self) -> None:
        if not self._hwnd:
            return
        import win32gui

        try:
            win32gui.InvalidateRect(self._hwnd, None, False)
        except Exception:  # noqa: BLE001
            pass

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        import win32con
        import win32gui

        if msg == win32con.WM_PAINT:
            hdc, paint = win32gui.BeginPaint(hwnd)
            try:
                self._render(hdc, hwnd)
            finally:
                win32gui.EndPaint(hwnd, paint)
            return 0
        if msg == _WM_PRINT:
            # 自绘窗口不处理 WM_PRINT 就画不出内容（抓图/打印会得到一块空背景）
            self._render(wparam, hwnd)
            return 0
        if msg == win32con.WM_ERASEBKGND:
            return 1  # WM_PAINT 里整块刷，别让系统先擦一遍（闪烁）
        if msg == win32con.WM_TIMER and wparam == 1:
            self._tick = (self._tick + 1) % 4
            self._invalidate()
            return 0
        if msg == win32con.WM_DESTROY:
            if self._timer:
                _win32.kill_timer(hwnd, self._timer)
                self._timer = 0
            return 0
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    def _render(self, hdc: Any, hwnd: int) -> None:
        import win32api
        import win32con
        import win32gui

        brush = win32gui.CreateSolidBrush(win32api.RGB(*_BG))
        left, top, right, bottom = win32gui.GetClientRect(hwnd)
        win32gui.FillRect(hdc, (left, top, right, bottom), brush)
        win32gui.DeleteObject(brush)
        if self._hfont:
            win32gui.SelectObject(hdc, self._hfont)
        win32gui.SetBkMode(hdc, win32con.TRANSPARENT)
        win32gui.SetTextColor(hdc, win32api.RGB(*_FG))
        # 文字 + 三个点的节拍：`正在校验元素` / `.` / `..` / `...`
        text = self._text + "." * self._tick
        rect = (left + 8, top, right - 8, bottom)
        win32gui.DrawText(
            hdc, text, -1, rect,
            win32con.DT_CENTER | win32con.DT_VCENTER | win32con.DT_SINGLELINE,
        )


__all__ = ["CheckToast"]
