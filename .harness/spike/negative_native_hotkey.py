"""负向验证：原生编辑框 F10 热键的三条判据是否真的拦得住。

每条：注入一处「看起来一样能跑」的破法 → 判据必须变红 → 逐字节还原 → 核 md5。
期望的失败集合按推理先写死（不照抄实际输出）。
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "src/rpa_core/capture_kit/edit_dialog.py"
PY = ROOT / ".venv/Scripts/python.exe"
PYTEST = [str(PY), "-m", "pytest", "tests/unit/test_edit_dialog_hotkey.py", "-q"]
PROBE = ".harness/spike/probe_edit_dialog_hotkey.py"


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def run(cmd: list[str]) -> tuple[int, str]:
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


CASES = [
    (
        "按住不放连开校验（去掉边沿判断）",
        "if down and not was:",
        "if down:",
        PYTEST + ["-k", "rising_edge"],
        "test_watch_posts_only_on_rising_edge",
    ),
    (
        "校验中按 F10 又起一轮（去掉 idle 守卫）",
        "            if not self._checking:\n                self._start_check(hwnd)",
        "            self._start_check(hwnd)",
        PYTEST + ["-k", "starts_check_only_when_idle"],
        "test_hotkey_message_starts_check_only_when_idle",
    ),
    (
        "关框不停 watch 线程",
        "        if msg == win32con.WM_DESTROY:\n            self._stop_hotkey_watch()",
        "        if msg == win32con.WM_DESTROY:\n            pass",
        PYTEST + ["-k", "destroy_stops_watch_thread"],
        "test_destroy_stops_watch_thread",
    ),
    (
        "热键线程根本没起",
        "        self._start_hotkey_watch(hwnd)",
        "        pass",
        [str(PY), PROBE],
        "fired=0",
    ),
    (
        "提示行控件没建（用户看不见有 F10）",
        '        self._hotkey_hint = control(\n'
        '            "STATIC", _HOTKEY_HINT_TEXT, 0, 126, 412, 230, 20, _ID_HOTKEY_HINT,\n'
        "        )",
        "        self._hotkey_hint = None",
        [str(PY), PROBE],
        "FAIL",
    ),
]


def main() -> int:
    original = TARGET.read_bytes()
    before = md5(original)
    text = original.decode("utf-8")
    failures = 0
    try:
        for name, old, new, cmd, expected in CASES:
            assert text.count(old) == 1, f"锚点不唯一/不存在：{name} -> {text.count(old)}"
            TARGET.write_bytes(text.replace(old, new, 1).encode("utf-8"))
            try:
                rc, out = run(cmd)
            finally:
                TARGET.write_bytes(original)
                assert md5(TARGET.read_bytes()) == before, f"还原失败：{name}"
            red = rc != 0 and expected in out
            print(f"[{'PASS' if red else 'FAIL'}] {name} → rc={rc} 期望含 {expected!r}")
            if not red:
                failures += 1
                print(out[-600:])
    finally:
        TARGET.write_bytes(original)
    assert md5(TARGET.read_bytes()) == before, "最终还原不一致"
    print(f"NEGATIVE {len(CASES) - failures}/{len(CASES)} 全红，源码 md5 已还原")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
