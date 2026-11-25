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


def move_windows(moves: list[tuple[int, int, int, bool | None]]) -> bool:
    """批量移动子窗口并可顺带显隐（Begin/Defer/EndDeferWindowPos），尺寸不变。

    ★ ``win32gui`` 没有这三个成员（与 ``CreateFont``/``SetTimer``/
    ``IsDialogMessage`` 同款缺成员），只能走 user32。批量提交的意义是把失效
    与重绘合并成一轮：逐个 ``SetWindowPos`` 时每挪一个控件都要重算一次父窗口
    可见区域，重排一屏十几行就是十几轮（M108 实测 30 个控件 16ms → 7ms）。

    ``moves`` 是 ``[(hwnd, x, y, show), ...]``：``show`` 为 ``True`` 带
    ``SWP_SHOWWINDOW``、``False`` 带 ``SWP_HIDEWINDOW``、``None`` 只管位置。
    显隐并进同一批的理由：单独的 ``ShowWindow`` 一次要 0.5~1.3ms（它要发
    ``WM_SHOWWINDOW`` 并重算可见性），而滚动一格总要进出各一行、两个控件。

    成功返回 True；任一步失败返回 False，调用方自己退回逐个 ``SetWindowPos``
    （宁可慢，不能停摆）。
    """
    import win32con

    if not moves:
        return True
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.BeginDeferWindowPos.argtypes = [ctypes.c_int]
    user32.BeginDeferWindowPos.restype = ctypes.c_void_p
    user32.DeferWindowPos.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    user32.DeferWindowPos.restype = ctypes.c_void_p
    user32.EndDeferWindowPos.argtypes = [ctypes.c_void_p]
    user32.EndDeferWindowPos.restype = ctypes.c_bool

    base = win32con.SWP_NOSIZE | win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE
    hdwp = user32.BeginDeferWindowPos(len(moves))
    if not hdwp:
        return False
    for hwnd, x, y, show in moves:
        flags = base
        if show is True:
            flags |= win32con.SWP_SHOWWINDOW
        elif show is False:
            flags |= win32con.SWP_HIDEWINDOW
        hdwp = user32.DeferWindowPos(
            hdwp, ctypes.c_void_p(int(hwnd)), None, int(x), int(y), 0, 0, flags
        )
        if not hdwp:  # 失败时 hdwp 已失效，不能再 End
            return False
    return bool(user32.EndDeferWindowPos(hdwp))


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


def adjust_window_size(
    client_w: int, client_h: int, style: int, ex_style: int
) -> tuple[int, int]:
    """客户区尺寸 → 窗口外框尺寸（把标题栏/边框算回来）。

    ★ ``win32gui`` **没有** ``AdjustWindowRectEx``（同 ``CreateFont``/``SetTimer``/
    ``IsDialogMessage`` 的缺成员坑）。布局按客户区排布时，外框必须经它反推，
    否则标题栏 + 边框会吃掉排在底部的控件（2026-11-13「保存按钮显示不全」）。
    """
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.AdjustWindowRectEx.argtypes = [
        ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_uint,
    ]
    user32.AdjustWindowRectEx.restype = ctypes.c_int
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetSystemMetrics.restype = ctypes.c_int
    rect = _RECT(0, 0, client_w, client_h)
    user32.AdjustWindowRectEx(ctypes.byref(rect), style, False, ex_style)
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    # ★ MSDN 明文：AdjustWindowRectEx **不**把 WS_VSCROLL/WS_HSCROLL 算进客户区。
    # 漏了这两行，滚动条会从右侧吃掉 17px，按满宽摆的属性值框/取消按钮全被
    # 窗口边缘裁掉（2026-11-25 用户截图 + 探针实测：client 763 vs 布局 780）。
    if style & 0x00200000:  # WS_VSCROLL
        width += user32.GetSystemMetrics(2)  # SM_CXVSCROLL
    if style & 0x00100000:  # WS_HSCROLL
        height += user32.GetSystemMetrics(3)  # SM_CYHSCROLL
    return width, height


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


class _SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


def text_width(hdc: int, hfont: int, text: str) -> int:
    """按指定字体量文字像素宽。``win32gui`` 没有这个函数，只能走 gdi32。

    用途：按钮/复选框的文字由系统绘制、**不支持** ``DT_END_ELLIPSIS``，宽度不够
    就硬切（看起来像被右边的灰块压住）。要自己截断就得先能按像素量。
    """
    if not text:
        return 0
    import win32gui

    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    gdi32.GetTextExtentPoint32W.argtypes = [
        ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int, ctypes.POINTER(_SIZE),
    ]
    gdi32.GetTextExtentPoint32W.restype = ctypes.c_int
    old = win32gui.SelectObject(hdc, hfont)
    try:
        size = _SIZE()
        gdi32.GetTextExtentPoint32W(
            ctypes.c_void_p(int(hdc)), text, len(text), ctypes.byref(size)
        )
        return int(size.cx)
    finally:
        win32gui.SelectObject(hdc, old)
