# -*- coding: utf-8 -*-
"""E2E 延迟探针：agent 的 WH_MOUSE_LL 钩子不得拖慢鼠标移动。

原理：低级钩子回调只在安装线程泵消息时被系统调用。泵若用 Peek+sleep 轮询，
每个鼠标事件要干等最多一个 sleep 间隔；RIT（系统原始输入线程）串行等钩子，
积压直接表现成光标滞后。测量法：高频注入一串绝对移动（在 A/B 两点间来回，
偶数次、终点固定），注入完成后轮询 GetCursorPos，量「光标追平注入流」的滞后。
  A 对照臂（无 agent）：滞后基线；
  B 钩子臂（agent hover 运行中）：滞后不得显著高于基线。
绝对移动（MOUSEEVENTF_ABSOLUTE）不走指针加速度，终点可精确预测。
"""
import ctypes
import subprocess
import sys
import time
from ctypes import wintypes

user32 = ctypes.windll.user32

N_MOVES = 300          # 注入事件数
PT_A = (600, 400)
PT_B = (660, 400)
LAG_TIMEOUT = 8.0      # 光标追平注入流的等待上限（秒）


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG), ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUT_UNION)]


def _abs_xy(x, y):
    w = user32.GetSystemMetrics(0)
    h = user32.GetSystemMetrics(1)
    return int(x * 65535 / (w - 1)), int(y * 65535 / (h - 1))


def send_move_abs(x, y):
    ax, ay = _abs_xy(x, y)
    inp = INPUT()
    inp.type = 0  # INPUT_MOUSE
    inp.u.mi = MOUSEINPUT(ax, ay, 0, 0x0001 | 0x8000, 0, 0)  # MOVE|ABSOLUTE
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def cursor_at():
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return (pt.x, pt.y)


def measure_lag() -> float:
    """注入 N 次绝对移动（终点 PT_B），返回光标追平的滞后秒数；超时返回 inf。"""
    user32.SetCursorPos(*PT_B)
    time.sleep(0.2)
    assert cursor_at() == PT_B, f"SetCursorPos 未到位: {cursor_at()}"
    t0 = time.perf_counter()
    for i in range(N_MOVES):
        send_move_abs(*(PT_A if i % 2 == 0 else PT_B))
    t_done = time.perf_counter()
    inject_wall = t_done - t0
    deadline = t_done + LAG_TIMEOUT
    while time.perf_counter() < deadline:
        if cursor_at() == PT_B:
            lag = time.perf_counter() - t_done
            return inject_wall, lag
        time.sleep(0.001)
    return inject_wall, float("inf")


def main() -> int:
    ok = True

    # ---- A 对照臂：无 agent ----
    wall_a, lag_a = measure_lag()
    print(f"A 对照臂: inject={wall_a*1000:.0f}ms lag={lag_a*1000:.0f}ms", flush=True)

    # ---- B 钩子臂：agent hover 运行中 ----
    agent_err = open("_probe_lat_err.txt", "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "rpa_core.capture.desktop_agent",
         "--hover", "--timeout", "10"],
        stdout=subprocess.DEVNULL, stderr=agent_err,
    )
    time.sleep(6.0)  # 等 hover 循环与钩子就绪（冷态解释器+UIA 首调）
    wall_b, lag_b = measure_lag()
    print(f"B 钩子臂: inject={wall_b*1000:.0f}ms lag={lag_b*1000:.0f}ms", flush=True)
    proc.terminate()
    agent_err.close()

    # 判据（2026-09-30 两轮实测定标）：阻塞 GetMessageW 泵 B=243ms（A+120ms），
    # sleep(5ms) 轮询泵 B=1857ms（A+1734ms，≈5.4ms/事件）——钩子泵一卡，注入流
    # 就被 RIT 串行拖住，这正是「开启捕获后鼠标移动特别卡」的机制。
    # 取 A+600ms：好臂 2 倍余量，坏臂 3 倍在外。
    within = wall_b - wall_a < 0.6
    no_backlog = lag_b < 0.5
    print(f"判据: B 注入墙钟 {wall_b*1000:.0f}ms < A+600ms({(wall_a+0.6)*1000:.0f}ms) -> {within}; "
          f"B 滞后 {lag_b*1000:.0f}ms < 500ms -> {no_backlog}", flush=True)
    ok &= within and no_backlog

    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
