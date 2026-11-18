"""M87 真机探针：测出目标进程「自己把 gate 关回去」的周期。

用法（微信要先开着、停在聊天界面）：
    ./.venv/Scripts/python.exe .harness/spike/probe_m87_gate_flap.py [秒数]

做三件事：
1. 找到微信主窗（win32 类名 Qt*WindowIcon + 标题「微信」）；
2. 读 gate；为 0 就写 1 并 poke_provider（等同于 capture 侧的唤醒）；
3. 之后每 0.5s 读一次 gate，记录每次翻转的时刻，打印间隔。

判读：
- 若 30s 内一直保持 1 ⇒ 一次唤醒就够，用户遇到的失效另有原因；
- 若周期性变回 0 ⇒ 必须做节流保活（``_WAKE_INTERVAL`` 要小于该周期）。
"""
from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes

from rpa_core.capture import accessibility, desktop_agent

_EVERY = 0.5


def main() -> int:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0
    user32 = ctypes.windll.user32

    targets: list[tuple[int, str]] = []

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        name = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(wintypes.HWND(hwnd), name, 64)
        cls = name.value
        if not (cls.startswith("Qt") and cls.endswith("WindowIcon")):
            return True
        title = ctypes.create_unicode_buffer(64)
        user32.GetWindowTextW(wintypes.HWND(hwnd), title, 64)
        if "微信" in title.value or "Weixin" in title.value:
            targets.append((hwnd, f"{cls} / {title.value}"))
        return True

    user32.EnumWindows(
        ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(cb), 0
    )
    if not targets:
        print("[FATAL] 没找到微信主窗口——先把微信打开、停在聊天界面")
        return 2

    hwnd, desc = targets[0]
    pid = accessibility._pid_of_window(hwnd)
    found = accessibility.module_base_and_path(pid, "Weixin.dll")
    scan = accessibility.gate_for_dll(found[1]) if found else None
    print(f"窗口 {hwnd} ({desc}) pid={pid}")
    if not found or scan is None or scan.rva is None:
        print("[FATAL] 找不到 Weixin.dll 或 gate")
        return 2
    address = found[0] + scan.rva
    print(f"base=0x{found[0]:X} rva=0x{scan.rva:X} addr=0x{address:X}")

    value = accessibility.read_byte(pid, address)
    print(f"初始 gate={value}")
    if value == 0:
        ok = accessibility.write_byte(pid, address, 1)
        accessibility.poke_provider(hwnd)
        print(f"写入 1 -> {ok}，2s 后开始观测")

    start = time.monotonic()
    time.sleep(2.0)
    last = accessibility.read_byte(pid, address)
    last_at = time.monotonic() - start
    print(f"t={last_at:.1f}s gate={last}")
    flips: list[tuple[float, int, int]] = []
    while time.monotonic() - start < seconds:
        time.sleep(_EVERY)
        now = accessibility.read_byte(pid, address)
        if now != last:
            at = time.monotonic() - start
            flips.append((at, last, now))
            print(f"t={at:.1f}s gate {last} -> {now}")
            last = now
            last_at = at
        if now is None:
            print("[WARN] 读不到（进程退出？）")
            break

    print(f"\n观测 {seconds:.0f}s，翻转 {len(flips)} 次")
    if flips:
        prev_at = 2.0
        gaps = []
        for at, _before, _after in flips:
            gaps.append(at - prev_at)
            prev_at = at
        print("间隔(s):", [round(g, 1) for g in gaps])
        print("⇒ 节流值必须明显小于最小间隔，当前 _WAKE_INTERVAL =",
              desktop_agent._WAKE_INTERVAL)
    else:
        print("⇒ 未观测到自动回关；用户遇到的失效更可能是「只试一次就不再重试」")
    return 0


if __name__ == "__main__":
    sys.exit(main())
