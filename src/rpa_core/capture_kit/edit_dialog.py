"""元素编辑框：win32 原生对话框（M68 后半场——编辑框收进 capture_kit）。

为什么存在：M68 初版把「抓完确认 / 详情改名」放在 web 页面里，于是「捕获 → 确认入库」
强依赖页面生命周期——主窗要还原、iframe 要活着、RPC 桥要通，两个真机事故都死在
「回页面的路上」。现在编辑框与提示浮窗一样长在桌面上，**任何宿主（Qt / CLI / ppx /
工作台）共用同一个**，宿主只剩两个回调：``on_save(name)`` 返回错误文案或 None，
``on_close(saved)`` 收口。

实现要点（全是从浮窗搬来的教训）：

- 窗口类注册 / 实例分派 / 字体 / 定时器全部走 ``capture_kit._win32``，不复制；
- **键盘行为不手写**：消息泵里喂 ``IsDialogMessage``，Enter 触发默认按钮（保存）、
  Esc 转成 IDCANCEL、Tab 在控件间轮换——EDIT 控件的中文 IME 组合态回车不会被
  当成命令（原生行为，web 端要 ``isComposing`` 守卫才有的语义这里免费）；
- meta（选择器全文 / 命中数 / 引用步）用**多行只读 EDIT**：任意长、可选中复制、
  自带滚动，比自绘文本省事且更强；
- 非 Windows 直接不可用（``available is False``，show 为 no-op），宿主回退自己的形态。
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable

from . import _win32

_CLASS = "RpaElementEditDialog"

_WIDTH = 560
_HEIGHT = 344
# 控件 id：保存/取消沿对话框惯例（IsDialogMessage 会把 Enter/Esc 映到这两个 id）
_ID_NAME = 201
_ID_META = 202
_ID_ERROR = 203


class ElementEditDialog:
    """一次性使用的编辑框：show() 起线程弹窗，保存/取消后自行销毁。"""

    def __init__(self) -> None:
        self._hwnd = None
        self._thread: threading.Thread | None = None
        self._hfont = None
        self._font_owned = False
        self._name_edit = None
        self._error_static = None
        self._on_save: Callable[[str], str | None] | None = None
        self._on_close: Callable[[bool], None] | None = None
        self._closed = False
        # 探针可观察的最近一次保存错误（界面文案以外的事实源）
        self.last_error: str | None = None

    @property
    def available(self) -> bool:
        return sys.platform == "win32"

    def show(
        self,
        *,
        title: str,
        name: str = "",
        meta: str = "",
        save_label: str = "保存",
        on_save: Callable[[str], str | None] | None = None,
        on_close: Callable[[bool], None] | None = None,
    ) -> None:
        """弹出编辑框。``on_save`` 返回 None = 入库成功关框；返回字符串 = 错误回显留框。"""
        if not self.available:
            return
        self._title = title
        self._name = name
        self._meta = meta
        self._save_label = save_label
        self._on_save = on_save
        self._on_close = on_close
        self._closed = False
        self._thread = threading.Thread(target=self._run, name="element-edit", daemon=True)
        self._thread.start()

    def close(self) -> None:
        """外部强制收框（宿主取消会话时用），按「未保存」处理。"""
        hwnd = self._hwnd
        if hwnd is None:
            return
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

        _win32.register_class(_CLASS)
        ex_style = win32con.WS_EX_TOPMOST | win32con.WS_EX_DLGMODALFRAME
        hwnd = win32gui.CreateWindowEx(
            ex_style,
            _CLASS,
            self._title,
            win32con.WS_POPUP | win32con.WS_CAPTION | win32con.WS_SYSMENU,
            0,
            0,
            _WIDTH,
            _HEIGHT,
            0,
            0,
            win32gui.GetModuleHandle(None),
            None,
        )
        self._hwnd = hwnd
        _win32._INSTANCES[int(hwnd)] = self
        self._build_children(hwnd)
        self._place(hwnd)
        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:  # noqa: BLE001 - 抢前台是锦上添花，绝不能把窗口线程打死
            # 实测会拿「句柄无效(6)」：前台锁/桌面状态相关的环境性失败。
            # topmost 已保证可见，抢不到前台不该连累整个编辑框。
            pass
        # ★ 消息泵必须喂 IsDialogMessage：Enter→默认按钮、Esc→IDCANCEL、
        # Tab→控件轮换，全是标准对话框语义，不用自己拦键盘（win32gui 没这个成员，
        # 循环在 _win32.pump_dialog 里用 ctypes 手写）。
        _win32.pump_dialog(hwnd)
        self._hwnd = None

    def _build_children(self, hwnd) -> None:
        import win32con
        import win32gui

        hinst = win32gui.GetModuleHandle(None)
        self._hfont = _win32.make_font()
        self._font_owned = bool(self._hfont)
        if not self._hfont:
            self._hfont = win32gui.GetStockObject(win32con.DEFAULT_GUI_FONT)

        def control(cls, text, style, x, y, w, h, cid):
            handle = win32gui.CreateWindowEx(
                0, cls, text,
                style | win32con.WS_CHILD | win32con.WS_VISIBLE | win32con.WS_TABSTOP,
                x, y, w, h, hwnd, cid, hinst, None,
            )
            if handle and self._hfont:
                win32gui.SendMessage(handle, win32con.WM_SETFONT, self._hfont, True)
            return handle

        control("STATIC", "资产名称", 0, 16, 14, 70, 24, 200)
        self._name_edit = control(
            "EDIT", self._name,
            win32con.ES_AUTOHSCROLL | win32con.WS_BORDER,
            90, 12, 454, 26, _ID_NAME,
        )
        # meta：只读多行——选择器全文/命中数/引用步，任意长、可选中复制
        control(
            "EDIT", self._meta,
            win32con.ES_MULTILINE | win32con.ES_READONLY | win32con.WS_VSCROLL
            | win32con.WS_BORDER,
            16, 48, 528, 214, _ID_META,
        )
        self._error_static = control("STATIC", "", 0, 16, 270, 528, 20, _ID_ERROR)
        control(
            "BUTTON", self._save_label, win32con.BS_DEFPUSHBUTTON,
            368, 298, 84, 30, win32con.IDOK,
        )
        control("BUTTON", "取消", 0, 460, 298, 84, 30, win32con.IDCANCEL)
        # 名字默认全选：用户最常见的动作是「直接敲新名字覆盖建议名」
        win32gui.SendMessage(self._name_edit, win32con.EM_SETSEL, 0, -1)
        win32gui.SetFocus(self._name_edit)

    def _place(self, hwnd) -> None:
        """居中到**鼠标所在**的显示器（多屏机器上框出现在用户正看着的屏）。"""
        import win32api
        import win32con
        import win32gui

        try:
            cursor = win32api.GetCursorPos()
            monitor = win32api.MonitorFromPoint(cursor, win32con.MONITOR_DEFAULTTONEAREST)
            left, top, right, bottom = win32api.GetMonitorInfo(monitor)["Work"]
        except Exception:  # noqa: BLE001 - 拿不到就退主屏
            left = top = 0
            right = win32api.GetSystemMetrics(win32con.SM_CXSCREEN)
            bottom = win32api.GetSystemMetrics(win32con.SM_CYSCREEN)
        x = max(left, left + (right - left - _WIDTH) // 2)
        y = max(top, top + (bottom - top - _HEIGHT) // 2)
        win32gui.SetWindowPos(
            hwnd, win32con.HWND_TOPMOST, x, y, _WIDTH, _HEIGHT,
            win32con.SWP_SHOWWINDOW,
        )

    # -- 消息 -----------------------------------------------------------------
    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        import win32con
        import win32gui

        if msg == win32con.WM_COMMAND:
            cid = wparam & 0xFFFF
            notif = (wparam >> 16) & 0xFFFF
            if cid == win32con.IDOK and notif == 0:
                self._try_save()
                return 0
            if cid == win32con.IDCANCEL and notif == 0:
                self._close(saved=False)
                return 0
            return 0
        if msg == win32con.WM_CTLCOLORSTATIC and int(lparam) == int(self._error_static or 0):
            # 错误行画红：返回画刷前先把文字颜色设上
            import win32api

            win32gui.SetTextColor(wparam, win32api.RGB(192, 0, 0))
            win32gui.SetBkMode(wparam, win32con.TRANSPARENT)
            return win32gui.GetStockObject(win32con.WHITE_BRUSH)
        if msg == win32con.WM_CLOSE:
            self._close(saved=False)
            return 0
        if msg == win32con.WM_DESTROY:
            _win32._INSTANCES.pop(int(hwnd), None)
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

    # -- 保存 / 关闭 ------------------------------------------------------------
    def _try_save(self) -> None:
        import win32gui

        name = win32gui.GetWindowText(self._name_edit).strip()
        if not name:
            self._show_error("名字不能为空")
            return
        error = self._on_save(name) if self._on_save is not None else None
        if error:
            self._show_error(str(error))
            return
        self._close(saved=True)

    def _show_error(self, text: str) -> None:
        import win32gui

        self.last_error = text
        win32gui.SetWindowText(self._error_static, text)

    def _close(self, saved: bool) -> None:
        if self._closed:
            return
        self._closed = True
        import win32gui

        on_close = self._on_close
        self._on_close = None
        hwnd = self._hwnd
        if hwnd is not None:
            win32gui.DestroyWindow(hwnd)
        if on_close is not None:
            on_close(saved)
