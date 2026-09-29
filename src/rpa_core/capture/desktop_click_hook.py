"""专职 Ctrl+Click 吞钩子子进程（`python -m rpa_core.capture.desktop_click_hook`）。

为什么单独一个进程：WH_MOUSE_LL 回调需要 GIL，而 agent 主进程里 hover 的
UIA 工作（comtypes 包装、树遍历）又重又慢（真机实测首次 UIA 调用 6~16s，
沙箱 0.3s）——2026-09-30 维护者报「开启捕获后鼠标移动特别卡」。把钩子搬进
一个**零 UIA 依赖**的最小进程：它的 GIL 基本空闲，回调永远及时，系统鼠标
输入不再与 agent 的任何 Python 重活耦合。

生命周期：agent 在 hover 开始时 spawn、结束时 terminate；本进程另有两条
自杀保险——父进程死亡与 --timeout 硬上限（同一轮 WaitForSingleObject 等
齐），保证任何异常路径下都不会留下一个吞掉全系统 Ctrl+Click 的僵尸钩子。

吞的手势：Ctrl+左键 DOWN/UP 成对吞掉（DOWN 吞了 UP 也吞，防孤儿 UP）；
浏览器网页内容区**不吞**（hybrid 让位语义：页内 Ctrl+Click 是扩展手势，
页面必须收到真点击）。吞掉的 DOWN 会 SetEvent 通知 agent（兜底触发——
被钩子吞掉的事件在 GetAsyncKeyState 里是否可见官方口径不明，agent 的
轮询与本事件两路都接）。
"""
import argparse
import ctypes
import sys
import threading
import time
from ctypes import wintypes

from rpa_core.capture._trace import trace as _trace

WH_MOUSE_LL = 14
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_QUIT = 0x0012
VK_CONTROL = 0x11
EVENT_MODIFY_STATE = 0x0002
PROCESS_SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0
INFINITE = 0xFFFFFFFF

# 与 desktop_agent._BROWSER_CONTENT_CLASSES 保持一致（刻意复制：本进程刻意
# 不 import agent 模块，保持零 UIA/comtypes 依赖）
_BROWSER_CONTENT_CLASSES = {"Chrome_RenderWidgetHostHWND"}


class _MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


if sys.platform == "win32":
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    # 64 位陷阱：不设 argtypes 时指针参数按 32 位截断（历史踩过 DefWindowProcW）
    user32.CallNextHookEx.argtypes = [
        wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM,
    ]
    user32.CallNextHookEx.restype = ctypes.c_ssize_t
    user32.SetWindowsHookExW.restype = wintypes.HHOOK
    user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
    kernel32.SetEvent.argtypes = [wintypes.HANDLE]
    kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.OpenEventW.restype = wintypes.HANDLE
    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.PostThreadMessageW.argtypes = [
        wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
    ]
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    kernel32.OpenProcess.argtypes = [
        wintypes.DWORD, wintypes.BOOL, wintypes.DWORD,
    ]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    _MOUSEHOOKPROC = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
    )
else:   # 非 Windows 仅 import 替身用，永不安装
    _MOUSEHOOKPROC = None


def _class_name_of(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _window_class_at(x: int, y: int) -> str:
    hwnd = int(user32.WindowFromPoint(wintypes.POINT(x, y)) or 0)
    return _class_name_of(hwnd)


def main() -> int:
    parser = argparse.ArgumentParser(prog="desktop-click-hook")
    parser.add_argument("--event", required=True,
                        help="吞掉 Ctrl+Click 时 SetEvent 的命名事件")
    parser.add_argument("--parent-pid", type=int, required=True,
                        dest="parent_pid")
    parser.add_argument("--timeout", type=float, default=600.0,
                        help="硬上限：超时自动退出（僵尸保险之一）")
    parser.add_argument("--respect-browser-content", action="store_true",
                        dest="respect_browser_content")
    args = parser.parse_args()

    if sys.platform != "win32" or _MOUSEHOOKPROC is None:
        return 1

    # 提权：agent 侧 UIA 冷启动/comtypes 代码会吃满 CPU（实测风暴 6~7s），
    # 普通优先级的钩子泵会被饿到让系统**旁路钩子**（旁路=回调都不进、点击穿透，
    # 2026-09-30 三臂探针实锤）。输入钩子进程的标准做法：高优先级 + 泵线程最高。
    HIGH_PRIORITY_CLASS = 0x0080
    THREAD_PRIORITY_HIGHEST = 2
    kernel32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), HIGH_PRIORITY_CLASS)
    kernel32.SetThreadPriority.argtypes = [wintypes.HANDLE, ctypes.c_int]
    kernel32.SetThreadPriority(kernel32.GetCurrentThread(), THREAD_PRIORITY_HIGHEST)

    state = {
        "swallow_pair": False,
        "respect_browser_content": args.respect_browser_content,
    }
    event = kernel32.OpenEventW(EVENT_MODIFY_STATE, False, args.event)
    if not event:
        # 事件打不开不致命：agent 侧还有 GetAsyncKeyState 轮询这一路
        _trace("hook", "event_open_failed", name=args.event)

    decisions = {"n": 0}

    def _proc(n_code, w_param, l_param):
        try:
            if n_code >= 0 and w_param in (WM_LBUTTONDOWN, WM_LBUTTONUP):
                info = ctypes.cast(
                    l_param, ctypes.POINTER(_MSLLHOOKSTRUCT)
                ).contents
                ctrl = bool(user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)
                over_browser = (
                    ctrl
                    and state["respect_browser_content"]
                    and _window_class_at(info.pt.x, info.pt.y)
                    in _BROWSER_CONTENT_CLASSES
                )
                swallow = False
                if ctrl and not over_browser:
                    if w_param == WM_LBUTTONDOWN:
                        state["swallow_pair"] = True
                        if event:
                            user32.SetEvent(event)
                        swallow = True
                    elif state["swallow_pair"]:
                        swallow = True
                # 前 10 个按键事件逐条取证（吞没吞、判据是什么）——真机排障铁证
                if decisions["n"] < 10:
                    decisions["n"] += 1
                    _trace(
                        "hook", "button", down=(w_param == WM_LBUTTONDOWN),
                        ctrl=ctrl, over_browser=over_browser, swallowed=swallow,
                    )
                if swallow:
                    return 1
        except Exception:
            pass  # 钩子回调绝不能抛（异常会令钩子失效）
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    callback = _MOUSEHOOKPROC(_proc)
    hook = user32.SetWindowsHookExW(WH_MOUSE_LL, callback, None, 0)
    _trace("hook", "installed", handle_ok=bool(hook), event_ok=bool(event))
    if not hook:
        return 1

    # 守护线程：等「父进程死亡」或「超时」，谁先到都退（同一个等待两类保险）
    def _watch():
        parent = kernel32.OpenProcess(PROCESS_SYNCHRONIZE, False, args.parent_pid)
        _trace("hook", "watch_started", parent_handle_ok=bool(parent))
        if parent:
            kernel32.WaitForSingleObject(
                parent, min(int(args.timeout * 1000), INFINITE - 1)
            )
        else:
            time.sleep(args.timeout)
        main_tid = state["main_tid"]
        user32.PostThreadMessageW(main_tid, WM_QUIT, 0, 0)

    state["main_tid"] = kernel32.GetCurrentThreadId()
    threading.Thread(target=_watch, daemon=True).start()

    # 心跳：证明「钩子装着 + 泵在转」贯穿整个捕获窗口——排障时若点击穿透而
    # 心跳连续，即系统旁路了回调（回调一次都没进）；心跳断了即子进程死了。
    def _heartbeat():
        n = 0
        while True:
            time.sleep(2.0)
            n += 1
            _trace("hook", "alive", beat=n)

    threading.Thread(target=_heartbeat, daemon=True).start()

    _trace("hook", "pumping")

    # 阻塞 GetMessageW 泵：低级钩子回调只在安装线程泵消息时被系统调用，
    # 本进程除睡眠的守护线程外无任何 Python 活，回调永远及时
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))

    user32.UnhookWindowsHookEx(hook)
    _trace("hook", "uninstalled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
