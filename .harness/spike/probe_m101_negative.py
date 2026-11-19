"""M101 负向验证：按 gate 候选开窗找 core 引用点。

做法与 M100 同：对生产源码做一次「合法语法的精准放宽」，跑判据，断言
「实际失败集 == 期望失败集」，再按原字节写回并核 md5。

四个注入各自验证一条机制：
* window-ignored        忽略 rva_windows ⇒ 又变回全段扫（窗口外的不该进来）
* no-dedupe             去掉 set 去重 ⇒ 重叠窗口把同一指令记两次
* boundary-drop         阈值从「大于才丢」改成「大于等于就丢」⇒ 边界那条漏掉
* no-window-margin      窗口不留余量 ⇒ 指令的 REX 字节落在窗口外，被当 6 字节解析

用法：``./.venv/Scripts/python.exe .harness/spike/probe_m101_negative.py``
"""

from __future__ import annotations

import hashlib
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
PY = sys.executable
ACC = ROOT / "src/rpa_core/capture/accessibility.py"
ACC_TESTS = ["tests/contract/test_accessibility_gate.py"]
A = "tests/contract/test_accessibility_gate.py::"

CASES = [
    (
        "window-ignored",
        [
            (
                "        if rva_windows is None:\n            spans = [(seg_start, seg_end)]",
                "        if True:\n            spans = [(seg_start, seg_end)]",
            ),
        ],
        {
            A + "test_rip_xrefs_window_excludes_refs_outside",
            A + "test_rip_xrefs_window_outside_sections_is_empty",
        },
    ),
    (
        "no-dedupe",
        [
            (
                "        return sorted(set(out))\n    return out",
                "        return out\n    return out",
            ),
        ],
        {A + "test_rip_xrefs_overlapping_windows_dedupe"},
    ),
    (
        "boundary-drop",
        [
            (
                "        if distance > _MAX_CORE_DISTANCE:",
                "        if distance >= _MAX_CORE_DISTANCE:",
            ),
        ],
        {A + "test_scan_finds_gate_at_exact_distance_limit"},
    ),
    (
        # 窗口比阈值窄一个字节 ⇒ 距离恰好等于阈值的引用点落在窗口外，边界那条漏
        "window-too-narrow",
        [
            (
                "    radius = _MAX_CORE_DISTANCE",
                "    radius = _MAX_CORE_DISTANCE - 1",
            ),
        ],
        {A + "test_scan_finds_gate_at_exact_distance_limit"},
    ),
]


def md5(path: pathlib.Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def fail_set_from_pytest(output: str) -> set[str]:
    out: set[str] = set()
    for line in output.splitlines():
        match = re.match(r"FAILED\s+(\S+)", line.strip())
        if match:
            out.add(match.group(1).replace("\\", "/"))
    return out


def run_pytest(files: list[str]) -> set[str]:
    proc = subprocess.run(
        [PY, "-m", "pytest", "-q", "--tb=no", "-rf", *files],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return fail_set_from_pytest(proc.stdout + proc.stderr)


def main() -> int:
    for name, edits, _exp in CASES:
        text = ACC.read_text(encoding="utf-8")
        if "<<<M101-INJECTED>>>" in text:
            print(f"[哨兵] {ACC.name} 里还有上次的注入标记，拒绝运行")
            return 2
        for old, _new in edits:
            if text.count(old) != 1:
                print(f"[锚点] {name}: 在 {ACC.name} 出现 {text.count(old)} 次，必须恰好 1 次")
                return 2

    problems: list[str] = []
    for name, edits, expected in CASES:
        original = ACC.read_bytes()
        before = md5(ACC)
        try:
            text = original.decode("utf-8")
            for old, new in edits:
                text = text.replace(old, new + "  # <<<M101-INJECTED>>>", 1)
            ACC.write_bytes(text.encode("utf-8"))
            got = run_pytest(ACC_TESTS)
            if got != expected:
                diff = f"缺失(应红未红)={sorted(expected - got)} 多出(不该红)={sorted(got - expected)}"
                problems.append(f"{name}: {diff}")
                print(f"FAIL {name}: {diff}")
            else:
                print(f"OK   {name}: 失败集相等（{len(got)} 条）")
        finally:
            ACC.write_bytes(original)
            if md5(ACC) != before:
                print(f"[还原失败] {ACC}")
                return 3

    if problems:
        print("\n负向验证未通过：")
        for item in problems:
            print(" -", item)
        return 1
    print("\nM101 NEGATIVE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
