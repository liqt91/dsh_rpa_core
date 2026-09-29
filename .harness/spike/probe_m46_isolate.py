# -*- coding: utf-8 -*-
"""隔离实验：agent 的 UIA 风暴是否会毒死（旁路/移除）钩子子进程。

时序：
  1. spawn 钩子子进程（先装好钩子，心跳每 2s）；
  2. spawn 真 agent（--hover），其首次命中测试 = 6~7s 的 comtypes/UIA 风暴；
  3. 风暴期间（+3s）点击一次 → 看 button 取证；
  4. 风暴结束后（+8s）再点击一次 → 看 button 取证。
若 3 失败 4 成功 ⇒ 风暴窗口内钩子被旁路；若 4 也失败 ⇒ 钩子被永久移除。
"""
import ctypes
import os
import subprocess
import sys
import time

user32 = ctypes.windll.user32
HERE = os.path.dirname(os.path.abspath(__file__))

def spawn_hook():
    return subprocess.Popen(
        [sys.executable, "-m", "rpa_core.capture.desktop_click_hook",
         "--event", "Local\\rpa-debug-isolate",
         "--parent-pid", str(os.getpid()), "--timeout", "40"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

def ctrl_click():
    user32.keybd_event(0x11, 0, 0, 0)
    time.sleep(0.05)
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.05)
    user32.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(0.05)
    user32.keybd_event(0x11, 0, 2, 0)

def main():
    started = time.time()
    hook = spawn_hook()
    time.sleep(3.0)  # 钩子装好
    t0 = time.strftime("%H:%M:%S")
    print(f"钩子已装 ({t0})，spawn agent 制造风暴…", flush=True)

    agent = subprocess.Popen(
        [sys.executable, "-m", "rpa_core.capture.desktop_agent",
         "--hover", "--timeout", "25"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(3.0)
    print("风暴中点击（t+3s）…", flush=True)
    ctrl_click()
    time.sleep(5.5)  # 等风暴结束（总 8.5s > 首测 6~7s）
    print("风暴后点击（t+8.5s）…", flush=True)
    ctrl_click()
    time.sleep(2.0)
    agent.terminate()
    hook.terminate()
    agent.wait(timeout=5)
    hook.wait(timeout=5)

    # 判据：本轮开始后，trace 里必须出现 ≥2 条 hook 的 swallowed=true 按键取证
    # （两次点击 × DOWN/UP；多进程并发写 trace 可能丢个别行，取保守下限）
    import json as _json
    trace = os.path.join(os.environ.get("TEMP", os.environ.get("TMP", "")),
                         "rpa-capture-trace.log")
    swallowed = 0
    with open(trace, encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = _json.loads(line)
            except Exception:
                continue
            if (rec.get("side") == "hook" and rec.get("mark") == "button"
                    and rec.get("swallowed") and rec.get("epoch", 0) > started):
                swallowed += 1
    ok = swallowed >= 2
    print(f"swallowed 取证条数: {swallowed} (期望 >=2)")
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1

if __name__ == "__main__":
    raise SystemExit(main())
