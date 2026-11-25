"""文字截断 + 滚动条宽度两条新判据的负向验证。

四条硬规矩：哨兵自检 / 锚点唯一 / try-finally 逐字节还原 / 元验证（关掉新判据
必须回绿）。注入都是**合法语法的精准放宽**（`+ "…"` → 原样返回、`+= 度量` → `+= 0`）。
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIALOG = ROOT / "src" / "rpa_core" / "capture_kit" / "edit_dialog.py"
WIN32 = ROOT / "src" / "rpa_core" / "capture_kit" / "_win32.py"
TEST = ROOT / "tests" / "unit" / "test_edit_dialog_layout.py"
SENTINEL = "M107_CLIP_NEG_SENTINEL"

# 注入 A：不再补省略号（原样返回被硬切的文本）
CLIP_ANCHOR = '    return text[:low] + "…"'
CLIP_INJECT = "    return text"
# 注入 B：不再把滚动条宽度加回外框
SCROLL_ANCHOR = "        width += user32.GetSystemMetrics(2)  # SM_CXVSCROLL"
SCROLL_INJECT = "        width += 0  # SM_CXVSCROLL"

CLIP_TEST = "def test_clip_adds_ellipsis_only_when_too_wide():"
SCROLL_TEST = "def test_scrollbar_width_is_added_back_to_frame():"

PY = ROOT / ".venv" / "Scripts" / "python.exe"


def digest(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def run_pytest() -> tuple[int, str]:
    # 仓库 addopts 已有 -q，命令行再给 -q 会变成 -qq 把汇总行压掉 ⇒ 先清空
    proc = subprocess.run(
        [str(PY), "-m", "pytest", "-o", "addopts=", "-q", "-rf", str(TEST)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False,
    )
    return proc.returncode, proc.stdout + proc.stderr


def counts(text: str) -> tuple[int, int]:
    failed = re.search(r"(\d+) failed", text)
    passed = re.search(r"(\d+) passed", text)
    return int(failed.group(1)) if failed else 0, int(passed.group(1)) if passed else 0


def inject(path: Path, anchor: str, replacement: str, original: bytes) -> None:
    text = original.decode("utf-8")
    if text.count(anchor) != 1:
        raise SystemExit(f"锚点不唯一（{text.count(anchor)}）：{path.name} {anchor!r}")
    path.write_bytes(original.replace(anchor.encode(), replacement.encode()))


def main() -> int:
    files = (DIALOG, WIN32, TEST)
    originals = {p: p.read_bytes() for p in files}
    hashes = {p: digest(p) for p in originals}
    for path, data in originals.items():
        if SENTINEL.encode() in data:
            raise SystemExit(f"哨兵已存在，拒绝叠加注入：{path}")

    rc, out = run_pytest()
    base_failed, base_passed = counts(out)
    print(f"BASE rc={rc} failed={base_failed} passed={base_passed}")
    if rc != 0 or base_failed:
        raise SystemExit("基线不绿，不能做负向验证")

    try:
        # A：去掉省略号 ⇒ 只有截断那条测试该红
        inject(DIALOG, CLIP_ANCHOR, CLIP_INJECT, originals[DIALOG])
        rc, out = run_pytest()
        failed, passed = counts(out)
        print(f"INJECT-CLIP rc={rc} failed={failed} passed={passed}")
        if rc == 0 or failed != 1 or re.search(r"\d+ error", out):
            raise SystemExit("注入 A 没打中截断判据")
        if CLIP_TEST[len("def "):-1] not in out:
            raise SystemExit("注入 A 红的是别的测试，不是截断判据")
        DIALOG.write_bytes(originals[DIALOG])

        # B：去掉滚动条宽度补偿 ⇒ 只有几何那条测试该红
        inject(WIN32, SCROLL_ANCHOR, SCROLL_INJECT, originals[WIN32])
        rc, out = run_pytest()
        failed, passed = counts(out)
        print(f"INJECT-SCROLL rc={rc} failed={failed} passed={passed}")
        if rc == 0 or failed != 1 or re.search(r"\d+ error", out):
            raise SystemExit("注入 B 没打中滚动条宽度判据")
        if SCROLL_TEST[len("def "):-1] not in out:
            raise SystemExit("注入 B 红的是别的测试，不是滚动条判据")
        WIN32.write_bytes(originals[WIN32])

        # 元验证：两个注入都打上，但关掉两条新判据 ⇒ 同一份坏源码必须回绿
        inject(DIALOG, CLIP_ANCHOR, CLIP_INJECT, originals[DIALOG])
        inject(WIN32, SCROLL_ANCHOR, SCROLL_INJECT, originals[WIN32])
        muted = originals[TEST]
        for anchor in (CLIP_TEST, SCROLL_TEST):
            muted = muted.replace(
                anchor.encode(), anchor.replace("def test_", "def _disabled_").encode()
            )
        TEST.write_bytes(muted)
        rc, out = run_pytest()
        failed, passed = counts(out)
        print(f"META rc={rc} failed={failed} passed={passed}")
        if rc != 0 or failed or passed != base_passed - 2:
            raise SystemExit("元验证失败：关掉新判据后坏源码没回绿")
    finally:
        for path, data in originals.items():
            path.write_bytes(data)

    for path, expected in hashes.items():
        actual = digest(path)
        print(f"RESTORED {path.name}: {actual == expected} {actual}")
        if actual != expected:
            raise SystemExit(f"逐字节还原失败：{path}")
    print("EDIT CLIP NEGATIVE PASSED (2/2 + meta)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
