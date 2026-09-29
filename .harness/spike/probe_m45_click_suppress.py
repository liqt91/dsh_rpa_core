# -*- coding: utf-8 -*-
"""E2E 负向验证：agent hover 期间的 WH_MOUSE_LL 是否真的吞掉 Ctrl+Click。

三臂：
  A 对照（无 agent）：Ctrl+Click 落到探针窗 → 计数 +1（证明探针测的是真点击）；
  B 抑制臂（agent hover 运行中）：Ctrl+Click → agent 捕获成功（descriptor），
    且探针窗计数不变（点击被吞，没穿透）；
  C 卸载臂（agent 已退出）：再点一次 → 计数 +1（钩子随会话结束被卸载）。
"""
import ctypes
import json
import subprocess
import sys
import threading
import time
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
# 不设原型时 ctypes 默认按 32 位 int 传参：64 位指针值的 lparam 直接 OverflowError
user32.DefWindowProcW.argtypes = [
    wintypes.HWND, ctypes.c_uint, ctypes.c_size_t, ctypes.c_void_p,
]
user32.DefWindowProcW.restype = ctypes.c_ssize_t

clicks = {"down": 0}
running = {"pump": True}

WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, wintypes.HWND, ctypes.c_uint, ctypes.c_size_t, ctypes.c_void_p
)


def _proc(hwnd, msg, wp, lp):
    if msg == 0x0201:  # WM_LBUTTONDOWN
        clicks["down"] += 1
    return user32.DefWindowProcW(hwnd, msg, wp, lp)


PROC = WNDPROC(_proc)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HANDLE),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HANDLE),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


def make_window(x, y, w, h, title):
    wc = WNDCLASSW()
    wc.lpfnWndProc = PROC
    wc.lpszClassName = "RpaClickProbe"
    wc.hInstance = kernel32.GetModuleHandleW(None)
    atom = user32.RegisterClassW(ctypes.byref(wc))
    assert atom, "RegisterClassW failed"
    hwnd = user32.CreateWindowExW(
        0, "RpaClickProbe", title, 0x00CF0000,  # WS_OVERLAPPEDWINDOW
        x, y, w, h, None, None, wc.hInstance, None
    )
    user32.ShowWindow(hwnd, 1)
    # 置顶防遮挡：沙箱里控制台窗可能压在目标坐标上，点击会落进别人家
    user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
    return hwnd


def pump_for(duration: float) -> None:
    """主线程内泵消息（窗口消息只投给创建线程——线程化的 GetMessage 收不到）。"""
    end = time.monotonic() + duration
    msg = wintypes.MSG()
    while time.monotonic() < end:
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        time.sleep(0.01)


def send_ctrl_click(x, y):
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.2)
    user32.keybd_event(0x11, 0, 0, 0)        # Ctrl down
    time.sleep(0.08)
    user32.mouse_event(0x0002, 0, 0, 0, 0)   # LDown
    time.sleep(0.08)
    user32.mouse_event(0x0004, 0, 0, 0, 0)   # LUp
    time.sleep(0.08)
    user32.keybd_event(0x11, 0, 2, 0)        # Ctrl up


def read_agent_line(proc):
    line = proc.stdout.readline()
    return json.loads(line.strip()) if line.strip() else None


def main() -> int:
    hwnd = make_window(200, 200, 320, 220, "RpaClickProbe")
    cx, cy = 200 + 160, 200 + 110  # 窗口中心（屏幕坐标）

    ok = True

    # ---- A 对照：无 agent，点击必须穿透到探针窗 ----
    send_ctrl_click(cx, cy)
    pump_for(0.5)
    print(f"A 对照臂: clicks={clicks['down']} (期望 1)", flush=True)
    ok &= clicks["down"] == 1

    # ---- B 抑制臂：agent hover 运行中，点击被吞且捕获成功 ----
    agent_err = open("_probe_agent_err.txt", "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "rpa_core.capture.desktop_agent",
         "--hover", "--timeout", "10"],
        stdout=subprocess.PIPE, stderr=agent_err,
        text=True, encoding="utf-8",
    )
    time.sleep(6.0)  # 等 hover 循环就绪（解释器 + UIA 首调，冷态可达数秒）
    deadline = time.monotonic() + 12
    before = clicks["down"]
    send_ctrl_click(cx, cy)
    # agent 只在结束时输出一行；等它的同时持续泵消息（钩子与窗口都要主线程转）
    reader_done = threading.Event()
    holder = {}

    def _read():
        holder["line"] = proc.stdout.readline()
        reader_done.set()

    threading.Thread(target=_read, daemon=True).start()
    while not reader_done.wait(timeout=0.1):
        pump_for(0.05)
        if time.monotonic() > deadline:
            break
    raw = holder.get("line") or ""
    result = json.loads(raw.strip()) if raw.strip() else None
    time.sleep(0.5)
    pump_for(0.3)
    after = clicks["down"]
    captured = result is not None and result.get("kind") == "desktop"
    print(f"B 抑制臂: captured={captured} result={result} clicks {before}->{after} "
          f"(期望 捕获=True 且计数不变)", flush=True)
    ok &= captured and after == before
    proc.terminate()
    agent_err.close()

    # ---- C 卸载臂：agent 退出后再点，钩子必须已不在 ----
    time.sleep(0.8)
    send_ctrl_click(cx, cy)
    pump_for(0.5)
    print(f"C 卸载臂: clicks={clicks['down']} (期望 B 臂值 +1)", flush=True)
    ok &= clicks["down"] == after + 1

    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
