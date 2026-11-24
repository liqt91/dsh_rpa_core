"""F10 全局校验热键的真机自检（不经 UI）。

为什么要有它：单元测试 monkeypatch 掉了 `_verify_hotkey_pressed`，只证了「计数逻辑」，
证不了「这个进程真能看见 F10」。用户按 F10 没反应时，先分清是
（a）按键根本读不到，还是（b）读到了但前端没接火。

跑法：
    ./.venv/Scripts/python.exe .harness/spike/probe_verify_hotkey.py

做三件事，每条都打印实测值，不打印结论：
1. 起会话 → 用 SendInput 造一次 F10 按下/抬起 → 累计次数应 +1（真实输入队列，不是 mock）；
2. 按住 200ms 不放 → 只算一次（上升沿）；
3. 换一个前台窗口（记事本）再按 → 仍然算数（证明「焦点在别的窗口」也收得到）。
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes as wt
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "xhs-workbench"))

from src import ppx_bridge as pb  # noqa: E402

VK_F10 = 0x79
KEYEVENTF_KEYUP = 0x0002
user32 = ctypes.WinDLL("user32", use_last_error=True)


def call(method: str, **params) -> dict:
    env = pb.dispatch(method, params)
    assert env["ok"] is True, env
    return env["data"]


def press(key: int = VK_F10, hold: float = 0.05) -> None:
    user32.keybd_event(key, 0, 0, 0)
    time.sleep(hold)
    user32.keybd_event(key, 0, KEYEVENTF_KEYUP, 0)


def fired() -> int:
    return int(call("rpa.pollVerifyHotkey")["fired"])


def wait_until(want: int, timeout: float = 1.5) -> int:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        got = fired()
        if got >= want:
            return got
        time.sleep(0.03)
    return fired()


def bring_notepad_front() -> str:
    """把记事本拉到前台：模拟「鼠标悬停在别的页面、PPX 不是前台」。"""
    import subprocess

    subprocess.Popen(["notepad.exe"])
    user32.FindWindowW.restype = wt.HWND
    hwnd = None
    for _ in range(40):
        hwnd = user32.FindWindowW(None, "无标题 - 记事本") or user32.FindWindowW(None, "Untitled - Notepad")
        if hwnd:
            break
        time.sleep(0.05)
    if hwnd:
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.25)
    return "notepad" if hwnd else ""


def main() -> int:
    fails: list[str] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        print(f"[{'PASS' if ok else 'FAIL'}] {label}  {detail}".rstrip(), flush=True)
        if not ok:
            fails.append(label)

    call("rpa.stopVerifyHotkey")
    started = call("rpa.startVerifyHotkey")
    check("start 起会话", started.get("active") is True and bool(started.get("sessionId")), str(started))

    base = fired()
    press()
    got = wait_until(base + 1)
    check("一次 F10 = 次数 +1", got == base + 1, f"base={base} got={got}")

    before = fired()
    press(hold=0.2)
    time.sleep(0.2)
    held = fired()
    check("按住不放只算一次", held == before + 1, f"before={before} held={held}")

    nb = ""
    try:
        nb = bring_notepad_front()
        base2 = fired()
        press()
        got2 = wait_until(base2 + 1)
        check("前台换成别的窗口也收得到", bool(nb) and got2 == base2 + 1, f"front={nb or '未起记事本'} base={base2} got={got2}")
    finally:
        import subprocess

        subprocess.run(["taskkill", "/IM", "notepad.exe", "/F"], capture_output=True)
        call("rpa.stopVerifyHotkey")

    after = call("rpa.pollVerifyHotkey")
    check("stop 后清空", after["active"] is False and after["fired"] == 0, str(after))

    print("\nRESULT:", "ALL PASS" if not fails else f"{len(fails)} FAILED -> {fails}")
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
