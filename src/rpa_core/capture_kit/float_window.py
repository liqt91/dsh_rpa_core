"""捕获浮窗：桌面捕获期间的系统级置顶提示 + 取消入口（win32，无 Qt）。

为什么需要它：捕获开始时宿主主窗要**让开目标**（Qt GUI 用 `showMinimized()`，
ppx 用 `window.minimize`），此时宿主的状态栏/面板看不见——浮窗是捕获期唯一的
反馈面与取消出口。

行为约定（对齐 main 分支 `gui/capture_float.py` 的规格，但实现换成 win32，
好让 WebView/CLI 等非 Qt 宿主也能复用）：

- 置顶小窗（``WS_EX_TOPMOST|TOOLWINDOW|NOACTIVATE|LAYERED``），默认右下角；
- **三条真实状态**：手势提示 / 网页通道 / 桌面通道，各自一行——用户据此决定
  「去网页里点还是去桌面按 F9」；
- **取消按钮**：捕获期唯一的确定性出口（不依赖任何一条腿响应键盘）；
- **躲鼠标**：25Hz（40ms）翻边，避免挡住要点的元素；
- 不显示倒计时（没有「限时任务」这回事，超时无惩罚）。

非 Windows 直接 no-op（引擎层仍可用，只是没有浮窗）。
"""

from __future__ import annotations

import sys
import threading

# win32con 里没有的私有消息号：跨线程刷新状态
_WM_APP_STATUS = 0x8000 + 1
_BTN_CANCEL = 1001
_TIMER_AVOID = 1

_WIDTH = 440
_HEIGHT = 110
_MARGIN = 24


class CaptureFloat:
    """一个始终置顶、无焦点、半透明的提示窗。show() 起线程画窗，hide() 关窗。"""

    _CLASS = "RpaCaptureFloat"

    def __init__(self) -> None:
        self._hwnd = None
        self._thread = None
        self._on_cancel = None
        # (gesture, web, desktop, web_ok, desktop_ok)：整体替换，跨线程读安全
        self._lines: tuple[str, str, str, bool, bool] = ("", "", "", False, False)

    # -- 对外 ----------------------------------------------------------------
    def show(
        self,
        gesture: str,
        web: str,
        desktop: str,
        web_ok: bool,
        desktop_ok: bool,
        on_cancel=None,
    ) -> None:
        self._lines = (gesture, web, desktop, bool(web_ok), bool(desktop_ok))
        self._on_cancel = on_cancel
        if sys.platform != "win32":
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="capture-float", daemon=True)
        self._thread.start()

    def set_status(
        self, web: str, desktop: str, web_ok: bool, desktop_ok: bool
    ) -> None:
        """跨线程刷新两条腿的状态（手势文案不变）。"""
        gesture = self._lines[0]
        self._lines = (gesture, web, desktop, bool(web_ok), bool(desktop_ok))
        hwnd = self._hwnd
        if hwnd is None:
            return
        try:
            import win32gui

            win32gui.PostMessage(hwnd, _WM_APP_STATUS, 0, 0)
        except Exception:  # noqa: BLE001 - 窗已销毁也无妨
            pass

    def hide(self) -> None:
        hwnd = self._hwnd
        if hwnd is None:
            return
        self._hwnd = None
        try:
            import win32con
            import win32gui

            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        except Exception:  # noqa: BLE001 - 窗已销毁也无妨
            pass

    # -- 窗口线程 ------------------------------------------------------------
    def _run(self) -> None:
        import win32con
        import win32gui

        hinst = win32gui.GetModuleHandle(None)
        wc = win32gui.WNDCLASS()
        wc.lpfnWndProc = self._wnd_proc
        wc.lpszClassName = self._CLASS
        wc.hInstance = hinst
        wc.hbrBackground = win32gui.GetStockObject(win32con.WHITE_BRUSH)
        wc.style = win32con.CS_HREDRAW | win32con.CS_VREDRAW
        try:
            win32gui.RegisterClass(wc)
        except win32gui.error:
            # 同名类已存在（没有 UnregisterClass）：WNDPROC 一致，复用即可。
            pass
        ex_style = (
            win32con.WS_EX_TOPMOST
            | win32con.WS_EX_TOOLWINDOW
            | win32con.WS_EX_NOACTIVATE
            | win32con.WS_EX_LAYERED
        )
        hwnd = win32gui.CreateWindowEx(
            ex_style,
            self._CLASS,
            "",
            win32con.WS_POPUP | win32con.WS_BORDER,
            0,
            0,
            _WIDTH,
            _HEIGHT,
            0,
            0,
            hinst,
            None,
        )
        self._hwnd = hwnd
        self._place(hwnd, bottom_right=True)
        try:
            win32gui.SetLayeredWindowAttributes(hwnd, 0, 242, win32con.LWA_ALPHA)
        except Exception:  # noqa: BLE001 - 分层失败仍可显示
            pass
        win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
        win32gui.PumpMessages()

    def _place(self, hwnd, *, bottom_right: bool) -> None:
        import win32api
        import win32con
        import win32gui

        cx = win32api.GetSystemMetrics(win32con.SM_CXSCREEN)
        cy = win32api.GetSystemMetrics(win32con.SM_CYSCREEN)
        if bottom_right:
            x = max(0, cx - _WIDTH - _MARGIN)
            y = max(0, cy - _HEIGHT - 56)
        else:
            x = _MARGIN
            y = _MARGIN + 24
        win32gui.SetWindowPos(
            hwnd,
            win32con.HWND_TOPMOST,
            x,
            y,
            _WIDTH,
            _HEIGHT,
            win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW,
        )

    def _avoid_cursor(self, hwnd) -> None:
        """鼠标靠近就翻到另一角（对齐 Qt 版 40ms 的避让节拍）。"""
        import win32api
        import win32con
        import win32gui

        try:
            cx, cy = win32api.GetCursorPos()
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        except Exception:  # noqa: BLE001
            return
        pad = 16
        if left - pad <= cx <= right + pad and top - pad <= cy <= bottom + pad:
            screen_h = win32api.GetSystemMetrics(win32con.SM_CYSCREEN)
            at_bottom = top > screen_h // 2
            self._place(hwnd, bottom_right=not at_bottom)

    def _font(self):
        import win32con
        import win32gui

        return win32gui.CreateFont(
            -15, 0, 0, 0, win32con.FW_NORMAL, 0, 0, 0,
            win32con.DEFAULT_CHARSET,
            win32con.OUT_DEFAULT_PRECIS,
            win32con.CLIP_DEFAULT_PRECIS,
            win32con.CLEARTYPE_QUALITY,
            win32con.DEFAULT_PITCH | win32con.FF_DONTCARE,
            "Microsoft YaHei UI",
        )

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        import win32con
        import win32gui

        if msg == win32con.WM_CREATE:
            hinst = win32gui.GetModuleHandle(None)
            hfont = self._font()
            win32gui.SendMessage(hwnd, win32con.WM_SETFONT, hfont, True)
            win32gui.CreateWindowEx(
                0,
                "BUTTON",
                "取消捕获",
                win32con.WS_CHILD | win32con.WS_VISIBLE | win32con.BS_PUSHBUTTON,
                322, 40, 104, 32,
                hwnd,
                _BTN_CANCEL,
                hinst,
                None,
            )
            win32gui.SetTimer(hwnd, _TIMER_AVOID, 40, None)
            return 0
        if msg == win32con.WM_TIMER and wparam == _TIMER_AVOID:
            self._avoid_cursor(hwnd)
            return 0
        if msg == _WM_APP_STATUS:
            win32gui.InvalidateRect(hwnd, None, True)
            return 0
        if msg == win32con.WM_COMMAND:
            if (wparam & 0xFFFF) == _BTN_CANCEL and ((wparam >> 16) & 0xFFFF) == 0:
                callback = self._on_cancel
                if callback is not None:
                    # 别在窗口线程里跑可能阻塞的取消逻辑（disarm / 杀子进程）。
                    threading.Thread(target=callback, daemon=True).start()
            return 0
        if msg == win32con.WM_PAINT:
            hdc, ps = win32gui.BeginPaint(hwnd)
            try:
                hfont = self._font()
                old = win32gui.SelectObject(hdc, hfont)
                win32gui.SetBkMode(hdc, win32con.TRANSPARENT)
                gesture, web, desktop, web_ok, desktop_ok = self._lines
                self._text(hdc, gesture, (14, 10, 316, 26), (30, 30, 30))
                self._text(hdc, "● " + web, (14, 40, 316, 20),
                           (0, 128, 0) if web_ok else (192, 0, 0))
                self._text(hdc, "● " + desktop, (14, 62, 316, 20),
                           (0, 128, 0) if desktop_ok else (192, 0, 0))
                win32gui.SelectObject(hdc, old)
            finally:
                win32gui.EndPaint(hwnd, ps)
            return 0
        if msg == win32con.WM_CLOSE:
            win32gui.DestroyWindow(hwnd)
            return 0
        if msg == win32con.WM_DESTROY:
            win32gui.KillTimer(hwnd, _TIMER_AVOID)
            win32gui.PostQuitMessage(0)
            return 0
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    @staticmethod
    def _text(hdc, text: str, rect, rgb: tuple[int, int, int]) -> None:
        import win32api
        import win32con
        import win32gui

        if not text:
            return
        win32gui.SetTextColor(hdc, win32api.RGB(*rgb))
        win32gui.DrawText(
            hdc,
            text,
            -1,
            rect,
            win32con.DT_LEFT | win32con.DT_VCENTER | win32con.DT_SINGLELINE
            | win32con.DT_END_ELLIPSIS,
        )
