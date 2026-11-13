"""capture_kit 的 win32 共享件：窗口类注册 / 实例分派 / 定时器 / 折行 / 画字 / 字体。

为什么单独成模块：``float_window``（捕获浮窗）与 ``edit_dialog``（元素编辑框）是
两种窗口，但 win32 的坑全是同几个——窗口类按类名只注册一次、``win32gui`` 缺
``SetTimer``/``CreateFont``、``DT_WORDBREAK`` 断不开中文、``DrawText`` 的 rect 是
``(left, top, right, bottom)``。这些教训只该写一遍。
"""

from __future__ import annotations

import ctypes


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _MSG(ctypes.Structure):
    """user32 MSG 结构体（64 位：wParam/lParam 都是 8 字节）。"""

    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("iMessage", ctypes.c_uint),
        ("wParam", ctypes.c_size_t),
        ("lParam", ctypes.c_ssize_t),
        ("time", ctypes.c_uint),
        ("pt", _POINT),
    ]


def pump_dialog(hwnd: int) -> None:
    """对话框式消息泵：``IsDialogMessage`` 优先，直到 WM_QUIT。

    ★ ``win32gui`` **没有** ``IsDialogMessage``（与 ``CreateFont``/``SetTimer`` 同款
    缺成员，2026-11-13 真机实测直接 AttributeError 把窗口线程打死）。而
    ``win32gui.GetMessage`` 返回的是元组，喂不了需要 ``LPMSG`` 的
    ``IsDialogMessageW``——所以整条循环用 ctypes 手写：取消息、对话框管理器
    优先（Enter→默认按钮、Esc→IDCANCEL、Tab→控件轮换）、否则照常派发。
    pywin32 注册的 WNDPROC thunk 与谁调 DispatchMessage 无关，安全。
    """
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.IsDialogMessageW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    user32.IsDialogMessageW.restype = ctypes.c_bool
    user32.GetMessageW.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint,
    ]
    user32.GetMessageW.restype = ctypes.c_int
    msg = _MSG()
    while True:
        rc = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
        if rc <= 0:  # 0 = WM_QUIT；-1 = 错误，同样退
            return
        if user32.IsDialogMessageW(ctypes.c_void_p(int(hwnd)), ctypes.byref(msg)):
            continue
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


# -- 实例分派 ------------------------------------------------------------------
# 窗口类按类名在**进程内**只注册一次，WNDPROC 也就是第一个实例的方法。若直接把
# ``self._wnd_proc`` 交给 RegisterClass，第二次开同类窗口时消息仍会落到**第一个**
# 实例上——它的 hwnd 早已销毁，表现是「窗开着但不刷新、或者干脆空白」，而日志里
# 一行异常都没有。所以 WNDPROC 固定成模块级函数，按 hwnd 查表分派给当前实例。
# 多个窗口类（浮窗 / 编辑框）共用同一张表：hwnd 本身就是唯一键。
_INSTANCES: dict[int, object] = {}


def _dispatch(hwnd, msg, wparam, lparam):
    import win32gui

    instance = _INSTANCES.get(int(hwnd))
    if instance is None:
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)
    return instance._wnd_proc(hwnd, msg, wparam, lparam)


def register_class(class_name: str) -> None:
    """按类名注册窗口类（幂等：同名已注册就复用，WNDPROC 一致）。"""
    import win32con
    import win32gui

    wc = win32gui.WNDCLASS()
    wc.lpfnWndProc = _dispatch
    wc.lpszClassName = class_name
    wc.hInstance = win32gui.GetModuleHandle(None)
    wc.hbrBackground = win32gui.GetStockObject(win32con.WHITE_BRUSH)
    wc.style = win32con.CS_HREDRAW | win32con.CS_VREDRAW
    try:
        win32gui.RegisterClass(wc)
    except win32gui.error:
        pass


# -- 定时器 ----------------------------------------------------------------------
def set_timer(hwnd: int, timer_id: int, elapse_ms: int) -> int:
    """起一个 WM_TIMER，返回定时器 id（0 = 失败）。

    ★ ``win32gui`` **没有** ``SetTimer`` / ``KillTimer``（实测 ``dir(win32gui)`` 里
    一个 Timer 成员都没有）。早期写成 ``win32gui.SetTimer(...)`` 会抛
    ``AttributeError`` ⇒ 定时器从未建立，销毁时再抛一次让消息循环退不干净。
    这里走 user32 原生调用。
    """
    import ctypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetTimer.argtypes = [
        ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint, ctypes.c_void_p,
    ]
    user32.SetTimer.restype = ctypes.c_size_t
    return int(user32.SetTimer(ctypes.c_void_p(int(hwnd)), timer_id, elapse_ms, None))


def kill_timer(hwnd: int, timer_id: int) -> None:
    import ctypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.KillTimer.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    user32.KillTimer.restype = ctypes.c_int
    user32.KillTimer(ctypes.c_void_p(int(hwnd)), timer_id)


# -- 文字 ------------------------------------------------------------------------
def wrap_px(text: str, avail: int) -> list[str]:
    """按**像素宽**折行（CJK 约 15px、ASCII 约 8px，对应 15px 的雅黑）。

    不能交给 DrawText：中文没有空格，``DT_WORDBREAK`` 找不到断点，长说明会被
    右边一刀切掉——用户看到的是半截话。
    """
    if not text:
        return []
    lines: list[str] = []
    current = ""
    width = 0
    for char in text:
        if char == "\n":
            lines.append(current)
            current, width = "", 0
            continue
        char_width = 15 if ord(char) > 0x2E80 else 8
        if current and width + char_width > avail:
            lines.append(current)
            current, width = "", 0
        current += char
        width += char_width
    if current:
        lines.append(current)
    return lines


def draw_text(hdc, text: str, box, rgb: tuple[int, int, int]) -> None:
    """在 ``box=(x, y, w, h)`` 里画一行字（单行、垂直居中、超出省略号）。

    ★ ``DrawText`` 要的是 ``RECT = (left, top, right, bottom)``，**不是**
    ``(left, top, width, height)``。按后者传，第二行起 ``bottom`` 落在 ``top``
    上面，GDI 拿负高度做垂直居中，把几行字叠进同一片区域。换算收进这一个函数，
    调用方只谈 ``(x, y, w, h)``，语义错位不可能再发生。
    """
    import win32api
    import win32con
    import win32gui

    if not text:
        return
    left, top, width, height = box
    rect = (int(left), int(top), int(left + width), int(top + height))
    win32gui.SetTextColor(hdc, win32api.RGB(*rgb))
    flags = (
        win32con.DT_LEFT | win32con.DT_VCENTER | win32con.DT_SINGLELINE
        | win32con.DT_END_ELLIPSIS
    )
    win32gui.DrawText(hdc, text, -1, rect, flags)


def make_font() -> int:
    """建 UI 字体（HFONT，15px 雅黑）；失败返回 0，调用方退回 stock 字体。

    ★ ``win32gui`` **没有** ``CreateFont``（那是 ``win32ui`` 的成员）。这里直接调
    gdi32，不引入 ``win32ui`` 的 MFC 依赖；绝不因为字体把整个窗口废掉。
    """
    import ctypes

    try:
        gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        gdi32.CreateFontW.argtypes = [
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint,
            ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint,
            ctypes.c_uint, ctypes.c_wchar_p,
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
    except Exception:  # noqa: BLE001 - 字体失败不该拖死窗口
        return 0
