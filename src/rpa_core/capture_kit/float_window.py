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

_WIDTH = 560
_HEIGHT = 104
_MARGIN = 24
# 取消按钮固定在右侧；文字区按它扣宽（早先写死 316px，长文案会被省略号截断，
# 用户看到的是「网页：就绪（在页面里 Ctrl+Click 或右键…」这种半截提示）
_BTN_W = 104
_BTN_H = 32
_TEXT_W = _WIDTH - _BTN_W - 44


def _set_timer(hwnd: int, timer_id: int, elapse_ms: int) -> int:
    """起一个 WM_TIMER，返回定时器 id（0 = 失败）。

    ★ ``win32gui`` **没有** ``SetTimer`` / ``KillTimer``（实测 ``dir(win32gui)`` 里
    一个 Timer 成员都没有）。早期写成 ``win32gui.SetTimer(...)`` 会在 ``WM_CREATE``
    里抛 ``AttributeError`` ⇒ **躲鼠标的 40ms 定时器从未建立**（浮窗钉死在一角，
    正好挡住要点的元素），销毁时再抛一次让消息循环退不干净。这里走 user32 原生调用。
    """
    import ctypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetTimer.argtypes = [
        ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint, ctypes.c_void_p,
    ]
    user32.SetTimer.restype = ctypes.c_size_t
    return int(user32.SetTimer(ctypes.c_void_p(int(hwnd)), timer_id, elapse_ms, None))


def _kill_timer(hwnd: int, timer_id: int) -> None:
    import ctypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.KillTimer.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    user32.KillTimer.restype = ctypes.c_int
    user32.KillTimer(ctypes.c_void_p(int(hwnd)), timer_id)


class CaptureFloat:
    """一个始终置顶、无焦点、半透明的提示窗。show() 起线程画窗，hide() 关窗。"""

    _CLASS = "RpaCaptureFloat"

    def __init__(self) -> None:
        self._hwnd = None
        self._thread = None
        self._on_cancel = None
        self._hfont = None
        # 字体句柄是否由本类创建（只有自建的才该 DeleteObject；stock object 不能删）
        self._font_owned = False
        # 子控件（按钮/定时器）是否已建：建窗后与 WM_CREATE 两条路共用
        self._children_ready = False
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

        self._children_ready = False
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
        # ★ 不依赖 WM_CREATE：实测这个 pywin32 版本下 CreateWindowEx 建窗时
        # `WM_CREATE` 根本没派发到 Python 的 WNDPROC（只收到 WM_PAINT/WM_CLOSE/
        # WM_DESTROY），于是 WM_CREATE 里的字体/按钮/定时器全部没建起来 ——
        # 用户看到的就是「空白的提示框 + 不躲鼠标 + 没有取消按钮」。
        # 这里在窗口句柄拿到后**显式**建子控件，WM_CREATE 保留（幂等）作兜底。
        self._build_children(hwnd)
        self._place(hwnd, bottom_right=True)
        try:
            win32gui.SetLayeredWindowAttributes(hwnd, 0, 242, win32con.LWA_ALPHA)
        except Exception:  # noqa: BLE001 - 分层失败仍可显示
            pass
        win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
        win32gui.PumpMessages()

    def _build_children(self, hwnd) -> None:
        """字体 + 取消按钮 + 躲鼠标定时器（幂等；WM_CREATE 与建窗后各调一次）。"""
        if self._children_ready:
            return
        self._children_ready = True
        import win32con
        import win32gui

        hinst = win32gui.GetModuleHandle(None)
        self._hfont = self._font()
        self._font_owned = bool(self._hfont)
        if not self._hfont:
            # 自建失败退回系统 GUI 字体：文字照画，绝不会再出现空白窗
            self._hfont = win32gui.GetStockObject(win32con.DEFAULT_GUI_FONT)
        button = win32gui.CreateWindowEx(
            0,
            "BUTTON",
            "取消捕获",
            win32con.WS_CHILD | win32con.WS_VISIBLE | win32con.BS_PUSHBUTTON,
            _WIDTH - _BTN_W - 16,
            (_HEIGHT - _BTN_H) // 2,
            _BTN_W,
            _BTN_H,
            hwnd,
            _BTN_CANCEL,
            hinst,
            None,
        )
        if button and self._hfont:
            win32gui.SendMessage(button, win32con.WM_SETFONT, self._hfont, True)
        if not _set_timer(hwnd, _TIMER_AVOID, 40):
            # 定时器起不来：浮窗会钉死在一角（可能挡住要点的元素）——留痕别静默
            from rpa_core.capture._trace import trace as _trace

            _trace("float", "set_timer_failed")
        win32gui.InvalidateRect(hwnd, None, True)

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

    def _font(self) -> int:
        """建 UI 字体（HFONT）。

        ★ ``win32gui`` **没有** ``CreateFont``（那是 ``win32ui`` 的成员）。早期写成
        ``win32gui.CreateFont(...)`` 时，``WM_CREATE`` 与每次 ``WM_PAINT`` 都会抛
        ``AttributeError`` ⇒ 按钮与躲鼠标定时器压根没建起来、文字一笔不画，
        用户侧看到的就是**一个空白浮窗**（WNDPROC 报 "Python WNDPROC handler failed"）。
        这里直接调 gdi32，不引入 ``win32ui`` 的 MFC 依赖；失败也返回 0，由调用方退回
        stock 字体，绝不因为字体把整个窗口废掉。
        """
        import ctypes

        try:
            gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
            gdi32.CreateFontW.argtypes = [
                ctypes.c_int,  # cHeight
                ctypes.c_int,  # cWidth
                ctypes.c_int,  # cEscapement
                ctypes.c_int,  # cOrientation
                ctypes.c_int,  # cWeight
                ctypes.c_uint,  # bItalic
                ctypes.c_uint,  # bUnderline
                ctypes.c_uint,  # bStrikeOut
                ctypes.c_uint,  # iCharSet
                ctypes.c_uint,  # iOutPrecision
                ctypes.c_uint,  # iClipPrecision
                ctypes.c_uint,  # iQuality
                ctypes.c_uint,  # iPitchAndFamily
                ctypes.c_wchar_p,  # pszFaceName
            ]
            gdi32.CreateFontW.restype = ctypes.c_void_p
            handle = gdi32.CreateFontW(
                -15, 0, 0, 0, 400, 0, 0, 0,
                1,  # DEFAULT_CHARSET
                0, 0,
                5,  # CLEARTYPE_QUALITY
                0,  # DEFAULT_PITCH | FF_DONTCARE
                "Microsoft YaHei UI",
            )
            return int(handle or 0)
        except Exception:  # noqa: BLE001 - 字体失败不该拖死浮窗
            return 0

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        import win32con
        import win32gui

        if msg == win32con.WM_CREATE:
            # 幂等兜底：正常路径在 `_run` 里建窗后立刻调 `_build_children`，
            # 但某些 pywin32 版本根本不派发 WM_CREATE（实测只到 WM_PAINT），
            # 两条路都留着，谁先到谁建。
            self._build_children(hwnd)
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
                hfont = self._hfont or win32gui.GetStockObject(
                    win32con.DEFAULT_GUI_FONT
                )
                old = win32gui.SelectObject(hdc, hfont) if hfont else None
                try:
                    win32gui.SetBkMode(hdc, win32con.TRANSPARENT)
                    gesture, web, desktop, web_ok, desktop_ok = self._lines
                    self._text(hdc, gesture, (14, 10, _TEXT_W, 26), (30, 30, 30))
                    self._text(hdc, "● " + web, (14, 40, _TEXT_W, 20),
                               (0, 128, 0) if web_ok else (192, 0, 0))
                    self._text(hdc, "● " + desktop, (14, 64, _TEXT_W, 20),
                               (0, 128, 0) if desktop_ok else (192, 0, 0))
                finally:
                    if old:
                        win32gui.SelectObject(hdc, old)
            except Exception:  # noqa: BLE001 - 画失败最多一帧空白，不能打断消息循环
                pass
            finally:
                win32gui.EndPaint(hwnd, ps)
            return 0
        if msg == win32con.WM_CLOSE:
            win32gui.DestroyWindow(hwnd)
            return 0
        if msg == win32con.WM_DESTROY:
            _kill_timer(hwnd, _TIMER_AVOID)
            if self._font_owned and self._hfont is not None:
                try:
                    win32gui.DeleteObject(self._hfont)
                except Exception:  # noqa: BLE001 - 句柄已释放也无妨
                    pass
            self._hfont = None
            self._font_owned = False
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
