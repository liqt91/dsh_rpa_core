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

同一个窗还有第二种形态 ``show_notice(title, detail)``：**宿主界面不展示任何提示**，
环境没就绪、这次没成、资产删不掉这类消息全部落到这里（标题 + 折行说明 + 关闭按钮）。
``show()`` 可以把这个窗从提示形态**原地升级**成捕获形态（换按钮文案、换三行状态），
所以「检查环境 → 就绪」不会闪两个窗口。

非 Windows 直接 no-op（引擎层仍可用，只是没有浮窗）。
"""

from __future__ import annotations

import sys
import threading

from . import _win32

# win32con 里没有的私有消息号：跨线程刷新状态
_WM_APP_STATUS = 0x8000 + 1
_BTN_CANCEL = 1001
_TIMER_AVOID = 1
_TIMER_NOTICE = 2

_MODE_CAPTURE = "capture"
_MODE_NOTICE = "notice"

_WIDTH = 560
_HEIGHT = 104
_MARGIN = 24
# 取消按钮固定在右侧；文字区按它扣宽（早先写死 316px，长文案会被省略号截断，
# 用户看到的是「网页：就绪（在页面里 Ctrl+Click 或右键…」这种半截提示）
_BTN_W = 104
_BTN_H = 32
_TEXT_W = _WIDTH - _BTN_W - 44

_CLASS = "RpaCaptureFloat"


class CaptureFloat:
    """一个始终置顶、无焦点、半透明的提示窗。show() 起线程画窗，hide() 关窗。"""

    _CLASS = _CLASS

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
        # 提示形态：标题 + 折行说明；与捕获形态共用同一个窗口
        self._mode = _MODE_CAPTURE
        self._notice: tuple[str, str] = ("", "")
        self._auto_hide_ms = 0
        self._button = None
        self._button_visible = True

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
        """捕获形态。**同一个实例若已在显示提示，就地升级**成捕获窗（不闪窗）。"""
        self._mode = _MODE_CAPTURE
        self._lines = (gesture, web, desktop, bool(web_ok), bool(desktop_ok))
        self._on_cancel = on_cancel
        self._auto_hide_ms = 0
        self._spawn()

    def show_notice(
        self,
        title: str,
        detail: str = "",
        on_close=None,
        auto_hide_ms: int = 0,
    ) -> None:
        """提示形态：宿主界面里不出现任何提示，全部落到这个桌面框。

        ``on_close`` 为 None 时不显示按钮（纯告知，配合 ``auto_hide_ms`` 自动收起）。
        """
        self._mode = _MODE_NOTICE
        self._notice = (title, detail)
        self._on_cancel = on_close
        self._auto_hide_ms = int(auto_hide_ms or 0)
        self._spawn()

    def _spawn(self) -> None:
        if sys.platform != "win32":
            return
        thread = self._thread
        if thread is not None and thread.is_alive():
            # 窗已开着：只换形态（按钮文案 / 自动收起 / 重绘），不重建窗口。
            self._apply_mode()
            return
        self._thread = threading.Thread(target=self._run, name="capture-float", daemon=True)
        self._thread.start()

    def _apply_mode(self) -> None:
        """跨线程换形态：具体动作在窗口线程的 ``_WM_APP_STATUS`` 里做。"""
        hwnd = self._hwnd
        if hwnd is None:
            return
        try:
            import win32gui

            win32gui.PostMessage(hwnd, _WM_APP_STATUS, 0, 0)
        except Exception:  # noqa: BLE001 - 窗已销毁也无妨
            pass

    def _sync_button(self) -> None:
        button = self._button
        if button is None:
            return
        import win32con
        import win32gui

        try:
            if self._mode == _MODE_NOTICE and self._on_cancel is None:
                win32gui.ShowWindow(button, win32con.SW_HIDE)
                self._button_visible = False
                return
            win32gui.ShowWindow(button, win32con.SW_SHOW)
            self._button_visible = True
            win32gui.SetWindowText(
                button, "关闭" if self._mode == _MODE_NOTICE else "取消捕获"
            )
        except Exception:  # noqa: BLE001 - 换文案失败不该影响捕获
            pass

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
        # 窗口类注册/实例分派收在 capture_kit._win32（与编辑框共用同一套）。
        _win32.register_class(self._CLASS)
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
        # 先登记再建子控件：窗口一显示就会来 WM_PAINT，查不到表就是一笔不画。
        _win32._INSTANCES[int(hwnd)] = self
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
        self._hfont = _win32.make_font()
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
        self._button = button
        if button and self._hfont:
            win32gui.SendMessage(button, win32con.WM_SETFONT, self._hfont, True)
        # 建窗时可能已经是提示形态（「检查环境…」先亮出来）：按当前形态定按钮可见性。
        self._sync_button()
        if not _win32.set_timer(hwnd, _TIMER_AVOID, 40):
            # 定时器起不来：浮窗会钉死在一角（可能挡住要点的元素）——留痕别静默
            from rpa_core.capture._trace import trace as _trace

            _trace("float", "set_timer_failed")
        self._arm_notice_timer(hwnd)
        win32gui.InvalidateRect(hwnd, None, True)

    def _arm_notice_timer(self, hwnd) -> None:
        """提示形态的自动收起；切回捕获形态时要把它撤掉，别把捕获窗关了。"""
        _win32.kill_timer(hwnd, _TIMER_NOTICE)
        if self._mode == _MODE_NOTICE and self._auto_hide_ms > 0:
            _win32.set_timer(hwnd, _TIMER_NOTICE, self._auto_hide_ms)

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
        if msg == win32con.WM_TIMER and wparam == _TIMER_NOTICE:
            # 纯告知型提示自动收起（捕获形态永远不自动关：没有「限时任务」这回事）。
            self.hide()
            return 0
        if msg == _WM_APP_STATUS:
            self._sync_button()
            self._arm_notice_timer(hwnd)
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
                    if self._mode == _MODE_NOTICE:
                        title, detail = self._notice
                        width = _TEXT_W if self._button_visible else _WIDTH - 28
                        self._text(hdc, title, (14, 10, width, 22), (30, 30, 30))
                        # 中文没有空格，DrawText 的 DT_WORDBREAK 断不开 ⇒ 自己按字宽折行
                        # （实测：不折行时说明会被右边按钮一刀切掉）。
                        for index, line in enumerate(self._wrap_px(detail, width - 6)[:3]):
                            self._text(
                                hdc, line, (14, 34 + index * 20, width, 18), (90, 90, 90)
                            )
                    else:
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
            _win32.kill_timer(hwnd, _TIMER_AVOID)
            _win32.kill_timer(hwnd, _TIMER_NOTICE)
            _win32._INSTANCES.pop(int(hwnd), None)
            self._button = None
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
    def _wrap_px(text: str, avail: int) -> list[str]:
        """按像素宽折行（实现已下沉 ``capture_kit._win32``，此处保留调用面）。"""
        return _win32.wrap_px(text, avail)

    @staticmethod
    def _text(hdc, text: str, box, rgb: tuple[int, int, int]) -> None:
        """在 ``box=(x, y, w, h)`` 里画一行字（实现已下沉 ``capture_kit._win32``）。"""
        _win32.draw_text(hdc, text, box, rgb)
