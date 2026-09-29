# -*- coding: utf-8 -*-
"""M46 架构判据：钩子子进程（desktop_click_hook）不得拖慢鼠标移动，且必须
免疫宿主进程的 GIL 风暴（agent 的 UIA/comtypes 重活再慢也不波及系统输入）。

两臂对照：
  A 空闲基线：无钩子，注入 300 次绝对移动，量注入墙钟；
  B 风暴臂：本进程跑一条持 GIL 的忙线程（模拟 agent 的 comtypes/UIA 重活，
    真机实测该路径慢一个量级）+ **子进程钩子**运行中，同量注入。
判据（2026-09-30 实测定标）：B 不得慢于 A+600ms 且光标零积压。
（历史对照：sleep(5ms) 轮询泵的进程内钩子为 A+1734ms——被本判据拦住。）
"""
import ctypes
import subprocess
import sys
import threading
import time
from ctypes import wintypes

user32 = ctypes.windll.user32
N_MOVES = 300
PT_A = (600, 400)
PT_B = (660, 400)
LAG_TIMEOUT = 8.0


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG), ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t),
    ]


class _U(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def send_move_abs(x, y):
    w = user32.GetSystemMetrics(0)
    h = user32.GetSystemMetrics(1)
    inp = INPUT()
    inp.type = 0
    inp.u.mi = MOUSEINPUT(
        int(x * 65535 / (w - 1)), int(y * 65535 / (h - 1)),
        0, 0x0001 | 0x8000, 0, 0,
    )
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def cursor_at():
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return (pt.x, pt.y)


def measure():
    user32.SetCursorPos(*PT_B)
    time.sleep(0.2)
    assert cursor_at() == PT_B
    t0 = time.perf_counter()
    for i in range(N_MOVES):
        send_move_abs(*(PT_A if i % 2 == 0 else PT_B))
    t_done = time.perf_counter()
    wall = t_done - t0
    deadline = t_done + LAG_TIMEOUT
    while time.perf_counter() < deadline:
        if cursor_at() == PT_B:
            return wall, time.perf_counter() - t_done
        time.sleep(0.001)
    return wall, float("inf")


def main() -> int:
    ok = True

    # ---- A 空闲基线 ----
    wall_a, lag_a = measure()
    print(f"A 空闲基线: inject={wall_a*1000:.0f}ms lag={lag_a*1000:.0f}ms", flush=True)

    # ---- B 风暴臂：本进程 GIL 忙线程 + 子进程钩子 ----
    stop = threading.Event()
    counter = {"n": 0}

    def busy():
        # 模拟 comtypes/UIA 包装特征：GIL 长段持有（10-20ms 纯 Python 块），
        # 块间短暂让出（对应 COM 调用释放 GIL 的间隙）
        while not stop.is_set():
            for _ in range(2):
                counter["n"] = sum(range(60000))
            time.sleep(0.001)

    t = threading.Thread(target=busy, daemon=True)
    t.start()

    hook_err = open("_probe_hook_err.txt", "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "rpa_core.capture.desktop_click_hook",
         "--event", "Local\\rpa-probe-nonexistent-event",
         "--parent-pid", str(__import__("os").getpid()),
         "--timeout", "30"],
        stdout=subprocess.DEVNULL, stderr=hook_err,
    )
    time.sleep(5.0)  # 等子进程解释器+钩子就绪
    wall_b, lag_b = measure()
    print(f"B 风暴臂  : inject={wall_b*1000:.0f}ms lag={lag_b*1000:.0f}ms", flush=True)
    proc.terminate()
    hook_err.close()
    stop.set()
    t.join(timeout=2)

    within = wall_b - wall_a < 0.6
    no_backlog = lag_b < 0.5
    print(f"判据: B {wall_b*1000:.0f}ms < A+600ms({(wall_a+0.6)*1000:.0f}ms) -> {within}; "
          f"lag {lag_b*1000:.0f}ms < 500ms -> {no_backlog}", flush=True)
    ok &= within and no_backlog

    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
