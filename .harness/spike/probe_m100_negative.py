"""M100 负向验证：校验前唤醒 + 树诊断 + poke 送达记录。

做法：对**生产源码**做一次「合法语法的精准放宽」，跑对应判据，断言
「实际失败集 == 期望失败集」，再按原字节写回并核 md5。

纪律（踩过的坑）：
* 锚点整行取、不带尾随换行；每个锚点在文件里必须**恰好出现一次**（自检）。
* 注入必须是**只破坏目标语义**的最小改动——第一版把 `before_probe` 挪到
  `probe()` 后面写成 `if probe(window) and ...`，结果 finder 被调两次，
  连带压红四条本来无关的用例；这属于注入写坏，不是判据松。
* 不接管道（BrokenPipe 会打断还原），子进程一律 capture_output 后自己解析。
* ``try/finally`` 逐条还原；开头扫哨兵，发现上次留下的注入就硬失败。
* 期望失败集**按推理写**，跑完逐条核对——期望本身写错也要当成失败。

用法：``./.venv/Scripts/python.exe .harness/spike/probe_m100_negative.py``
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
DV = ROOT / "src/rpa_core/capture/desktop_verification.py"
FG = ROOT / "src/rpa_core/capture/foreground_window.py"
VF = ROOT / "src/rpa_core/capture/verification.py"
JS = ROOT / "apps/editor/gui/src/main.js"
JS_CHECK = ROOT / "scripts/check_element_library.mjs"

DV_TESTS = ["tests/contract/test_desktop_verification.py"]
ACC_TESTS = ["tests/contract/test_accessibility_gate.py"]

T = "tests/contract/test_desktop_verification.py::"
A = "tests/contract/test_accessibility_gate.py::"

# (名字, 文件, [(原锚点, 注入后)], 期望失败集, 是否要跑 JS 判据)
CASES = [
    (
        # 顺序：把唤醒从「查之前」挪到「查之后」，finder 调用次数不变
        "wake-after-probe",
        DV,
        [
            ("            before_probe=self._wake_before_query,", "            before_probe=None,"),
            (
                "                matches = self._finder(wrapper, locator)",
                "                matches = self._finder(wrapper, locator)\n                self._wake_before_query(window)",
            ),
        ],
        {T + "test_wake_happens_before_each_window_is_queried"},
        False,
    ),
    (
        "wake-not-called",
        DV,
        [("            result = self._waker(window.hwnd)", "            result = None")],
        {
            T + "test_wake_happens_before_each_window_is_queried",
            T + "test_wake_is_not_repeated_when_first_window_hits",
        },
        False,
    ),
    (
        "hint-always-when-zero",
        DV,
        [(
            '            if mode == "flash" and checked and self._tree_missing(checked):',
            '            if mode == "flash" and checked:',
        )],
        {
            T + "test_count_zero_has_no_hint_when_tree_is_alive",
            T + "test_native_window_never_gets_tree_hint",
            T + "test_verify_no_hit_is_count_zero_not_error",
        },
        False,
    ),
    (
        "tree-hint-for-native",
        DV,
        [(
            "            if not accessibility.looks_dormant(window.class_name, 1):\n                continue",
            "            if False:\n                continue",
        )],
        {T + "test_native_window_never_gets_tree_hint"},
        False,
    ),
    (
        "tree-count-ignored",
        DV,
        [("                if self._tree_count(window) > 1:", "                if False:")],
        {T + "test_count_zero_has_no_hint_when_tree_is_alive"},
        False,
    ),
    (
        "hint-not-forwarded",
        VF,
        [(
            '        hint = self.raw.get("hint")\n        if isinstance(hint, str) and hint:\n            out["hint"] = hint',
            '        hint = self.raw.get("hint")\n        if False:\n            out["hint"] = hint',
        )],
        {T + "test_hint_survives_unified_result_shape"},
        False,
    ),
    (
        "poke-result-dropped",
        ACC,
        [(
            "    # 写过字节就必须唤醒 provider——即使调用方说「稳态别打扰」。\n    poked = bool(poke_provider(hwnd))",
            "    # 写过字节就必须唤醒 provider——即使调用方说「稳态别打扰」。\n    poked = None",
        )],
        {
            A + "test_activate_records_poke_delivered",
            A + "test_activate_records_poke_rejected",
        },
        False,
    ),
    (
        "wake-touches-native",
        ACC,
        [(
            "    if not looks_dormant(class_name, 1):\n        return None\n    return activate(hwnd, dll_name=dll_name, poke=poke)",
            "    if False:\n        return None\n    return activate(hwnd, dll_name=dll_name, poke=poke)",
        )],
        {A + "test_wake_if_dormant_ignores_native_windows"},
        False,
    ),
    (
        "js-hint-branch-removed",
        JS,
        [(
            "      meta.textContent = res.hint === 'tree-missing'\n        ? elemText('verifyNoTree')\n        : elemText('verifyNone')",
            "      meta.textContent = elemText('verifyNone')",
        )],
        {"js::命中 0 且带 tree-missing：行内说清是对方没有无障碍树"},
        True,
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


def run_js() -> set[str]:
    proc = subprocess.run(
        ["node", str(JS_CHECK)],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return {
        "js::" + m.group(1).strip()
        for m in re.finditer(r"^FAIL\s+(.+?)(?:\s+—.*)?$", proc.stdout, re.MULTILINE)
    }


def main() -> int:
    # 1. 锚点自检（不改任何文件）
    for name, path, edits, _exp, _js in CASES:
        text = path.read_text(encoding="utf-8")
        if "<<<M100-INJECTED>>>" in text:
            print(f"[哨兵] {path.name} 里还有上次的注入标记，拒绝运行")
            return 2
        for old, _new in edits:
            if text.count(old) != 1:
                print(f"[锚点] {name}: 在 {path.name} 出现 {text.count(old)} 次，必须恰好 1 次")
                return 2

    problems: list[str] = []
    for name, path, edits, expected, need_js in CASES:
        original = path.read_bytes()
        before = md5(path)
        try:
            text = original.decode("utf-8")
            for old, new in edits:
                replace = new + "  # <<<M100-INJECTED>>>" if path.suffix == ".py" else new
                text = text.replace(old, replace, 1)
            path.write_bytes(text.encode("utf-8"))
            got = run_pytest(DV_TESTS if path in (DV, VF, FG) else ACC_TESTS)
            if need_js:
                got |= run_js()
            if got != expected:
                diff = f"缺失(应红未红)={sorted(expected - got)} 多出(不该红)={sorted(got - expected)}"
                problems.append(f"{name}: {diff}")
                print(f"FAIL {name}: {diff}")
            else:
                print(f"OK   {name}: 失败集相等（{len(got)} 条）")
        finally:
            path.write_bytes(original)
            if md5(path) != before:
                print(f"[还原失败] {path}")
                return 3

    if problems:
        print("\n负向验证未通过：")
        for item in problems:
            print(" -", item)
        return 1
    print("\nM100 NEGATIVE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
