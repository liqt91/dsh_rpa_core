"""A 档界面改造的负向验证：证明新增的几何判据真能拦住「改回固定高度」。

四条硬规矩：哨兵自检 / 锚点唯一 / try-finally 逐字节还原 / 元验证（关掉新判据
必须回绿）。注入必须是**合法语法的精准放宽**（32px 行高 → 30px），不是删语句。
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "src" / "rpa_core" / "capture_kit" / "edit_dialog.py"
TEST = ROOT / "tests" / "unit" / "test_edit_dialog_layout.py"
SENTINEL = "M110_NEG_SENTINEL"

ANCHOR = "    return (rows - _visible_rows(count, desktop)) * 32"
INJECT = "    return (rows - _visible_rows(count, desktop)) * 30"
NEW_TESTS = [
    "def test_window_height_tracks_visible_rows():",
    "def test_row_count_beyond_page_keeps_full_height():",
]

PY = ROOT / ".venv" / "Scripts" / "python.exe"


def digest(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def run_pytest() -> tuple[int, str]:
    # 仓库 addopts 已有 -q，命令行再给 -q 会变成 -qq 把汇总行压掉 ⇒ 先清空
    proc = subprocess.run(
        [str(PY), "-m", "pytest", "-o", "addopts=", "-q", str(TEST)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False,
    )
    return proc.returncode, proc.stdout + proc.stderr


def counts(text: str) -> tuple[int, int]:
    failed = re.search(r"(\d+) failed", text)
    passed = re.search(r"(\d+) passed", text)
    return int(failed.group(1)) if failed else 0, int(passed.group(1)) if passed else 0


def main() -> int:
    originals = {p: p.read_bytes() for p in (TARGET, TEST)}
    hashes = {p: digest(p) for p in originals}
    for path, data in originals.items():
        if SENTINEL.encode() in data:
            raise SystemExit(f"哨兵已存在，拒绝叠加注入：{path}")
    if originals[TARGET].decode("utf-8").count(ANCHOR) != 1:
        raise SystemExit("锚点不唯一，拒绝注入")
    for anchor in NEW_TESTS:
        if originals[TEST].decode("utf-8").count(anchor) != 1:
            raise SystemExit(f"新判据锚点不唯一：{anchor}")

    rc, out = run_pytest()
    base_failed, base_passed = counts(out)
    print(f"BASE rc={rc} failed={base_failed} passed={base_passed}")
    if rc != 0 or base_failed:
        raise SystemExit("基线不绿，不能做负向验证")

    try:
        TARGET.write_bytes(originals[TARGET].replace(
            ANCHOR.encode(), INJECT.encode()
        ))

        rc, out = run_pytest()
        failed, passed = counts(out)
        print(f"INJECT rc={rc} failed={failed} passed={passed}")
        if rc == 0 or failed != 2 or re.search(r"\d+ error", out):
            raise SystemExit("注入没打中新判据（或出现异常错误），负向验证失败")

        # 元验证：关掉两条新判据，同一份坏源码必须回绿 ⇒ 红确实是新判据报的
        muted = originals[TEST]
        for anchor in NEW_TESTS:
            muted = muted.replace(anchor.encode(), anchor.replace("def test_", "def _disabled_").encode())
        TEST.write_bytes(muted)
        rc, out = run_pytest()
        failed, passed = counts(out)
        print(f"META rc={rc} failed={failed} passed={passed}")
        if rc != 0 or failed or passed != base_passed - 2:
            raise SystemExit("元验证失败：坏源码在关掉新判据后没有回绿")
    finally:
        for path, data in originals.items():
            path.write_bytes(data)

    for path, expected in hashes.items():
        actual = digest(path)
        print(f"RESTORED {path.name}: {actual == expected} {actual}")
        if actual != expected:
            raise SystemExit(f"逐字节还原失败：{path}")
    print("EDIT SKIN NEGATIVE PASSED (2/2 + meta)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
