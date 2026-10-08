# -*- coding: utf-8 -*-
"""分辨实验：3 臂探针的 agent 用 stdout=PIPE、隔离实验用 DEVNULL——
复刻 PIPE 版本 + 独立判据（不依赖 trace，直接读 agent 输出 + 计数窗）。"""
import ctypes
import json
import os
import subprocess
import sys
import threading
import time
from ctypes import wintypes

user32 = ctypes.windll.user32

clicks = {"down": 0}
running = {"pump": True}
WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, wintypes.HWND, ctypes.c_uint, ctypes.c_size_t, ctypes.c_void_p
)
user32.DefWindowProcW.argtypes = [
    wintypes.HWND, ctypes.c_uint, ctypes.c_size_t, ctypes.c_void_p,
]
user32.DefWindowProcW.restype = ctypes.c_ssize_t


def _proc(hwnd, msg, wp, lp):
    if msg == 0x0201:
        clicks["down"] += 1
    return user32.DefWindowProcW(hwnd, msg, wp, lp)


PROC = WNDPROC(_proc)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HANDLE),
        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HANDLE),
        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
    ]


def make_window(x, y, w, h, title):
    wc = WNDCLASSW()
    wc.lpfnWndProc = PROC
    wc.lpszClassName = "RpaClickProbe2"
    wc.hInstance = ctypes.windll.kernel32.GetModuleHandleW(None)
    assert user32.RegisterClassW(ctypes.byref(wc))
    hwnd = user32.CreateWindowExW(
        0, "RpaClickProbe2", title, 0x00CF0000, x, y, w, h, None, None,
        wc.hInstance, None,
    )
    user32.ShowWindow(hwnd, 1)
    user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
    return hwnd


def pump_for(duration):
    end = time.monotonic() + duration
    msg = wintypes.MSG()
    while time.monotonic() < end:
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        time.sleep(0.01)


def main():
    hwnd = make_window(200, 200, 320, 220, "RpaPipeProbe")
    cx, cy = 600, 400  # 点在桌面上，不在探针窗（分辨目标窗口影响）

    # 与 3 臂探针完全一致：agent stdout=PIPE
    agent = subprocess.Popen(
        [sys.executable, "-m", "rpa_core.capture.desktop_agent",
         "--hover", "--timeout", "10"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8",
    )
    time.sleep(8.5)  # 等 UIA 风暴结束（沙箱实测 6~7s）
    before = clicks["down"]
    user32.SetCursorPos(cx, cy)
    time.sleep(0.2)
    user32.keybd_event(0x11, 0, 0, 0)
    time.sleep(0.08)
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.08)
    user32.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(0.08)
    user32.keybd_event(0x11, 0, 2, 0)

    holder = {}
    done = threading.Event()

    def _read():
        holder["line"] = agent.stdout.readline()
        done.set()

    threading.Thread(target=_read, daemon=True).start()
    while not done.wait(timeout=0.1):
        pump_for(0.05)
    raw = (holder.get("line") or "").strip()
    result = json.loads(raw) if raw else None
    pump_for(0.3)
    after = clicks["down"]
    agent.terminate()

    print(f"raw result: {result}")
    captured = result is not None and result.get("kind") == "desktop"
    print(f"captured={captured} clicks {before}->{after}")
    if captured and after == before:
        print("RESULT: PASS（吞住 + 捕获）")
        return 0
    if captured and after == before + 1:
        print("RESULT: PARTIAL（捕获成功但点击穿透——钩子没吞）")
        return 2
    print("RESULT: FAIL")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
