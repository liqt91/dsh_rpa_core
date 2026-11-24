"""M104 负向验证：勾选快照与最大化状态的四处**必须真拦得住**的地方。

现场（2026-11-19）：网页详情勾选全丢、桌面取消勾选的层级重开就消失；
捕获/编辑保存后最大化的 RPA 主窗缩成普通大小。

四个注入各自打一条独立断言：

* ``write-side``    保存时不再写 editState ⇒ 网页/桌面往返用例红
* ``read-web``      网页详情不再读快照 ⇒ 网页往返红，桌面仍绿
* ``read-desktop``  桌面详情不再读快照 ⇒ 桌面往返红，网页仍绿
* ``js-drop-state`` 前端保存不带第三参数 ⇒ JS 捕获确认用例红
* ``window-no-max`` 还原时不认最大化状态 ⇒ 窗口状态用例红

每轮断言「实际失败集 == 期望失败集」，按原字节还原并核 md5；带哨兵防叠注入。
"""

from __future__ import annotations

import hashlib
import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
PY = ROOT / ".venv/Scripts/python.exe"
EDITING = ROOT / "src/rpa_core/capture_kit/element_editing.py"
HOST = ROOT / "src/rpa_core/capture_kit/window_host.py"
MAIN = ROOT / "apps/editor/gui/src/main.js"
API = ROOT / "apps/editor/api/api.py"
PYTEST_FILES = ["tests/unit/test_element_editing.py", "tests/unit/test_window_host.py"]
T = "tests/unit/test_element_editing.py::"
W = "tests/unit/test_window_host.py::"
NODE = "scripts/check_element_library.mjs"
PICKUI = "scripts/check_element_pick_ui.mjs"
JS_CASE = "捕获确认保存把勾选快照随定位一起交给后端"
MARKER = "  # <<<M104-INJECTED>>>"
JS_MARKER = "  // <<<M104-INJECTED>>>"

WEB = T + "test_web_edit_state_survives_save_and_reopens_exactly"
DESKTOP = T + "test_desktop_edit_state_keeps_unchecked_levels"
MAX_1 = W + "test_maximized_window_is_restored_as_maximized"
MAX_2 = W + "test_maximized_state_does_not_leak_into_next_cycle"

CASES = [
    {
        "name": "write-side", "file": EDITING,
        "edits": [('    if isinstance(edit_state, dict) and edit_state:\n'
                   '        result["selector"][EDIT_STATE_KEY] = edit_state',
                   "    if False:\n        pass" + MARKER)],
        "pytest": {WEB, DESKTOP}, "node": set(), "pickui": set(),
    },
    {
        "name": "read-web", "file": EDITING,
        "edits": [('        levels = saved_edit_state(descriptor, "levels")',
                   "        levels = None" + MARKER)],
        "pytest": {WEB}, "node": set(), "pickui": set(),
    },
    {
        "name": "read-desktop", "file": EDITING,
        "edits": [('            nodes = saved_edit_state(descriptor, "nodes")',
                   "            nodes = None" + MARKER)],
        "pytest": {DESKTOP}, "node": set(), "pickui": set(),
    },
    {
        "name": "js-drop-state", "file": MAIN,
        "edits": [("    res = await dialogSave(name, selector, editState)",
                   "    res = await dialogSave(name, selector)" + JS_MARKER)],
        "pytest": set(), "node": {JS_CASE}, "pickui": set(),
    },
    {
        "name": "window-no-max", "file": HOST,
        "edits": [("        if self._was_maximized:",
                   "        if False:" + MARKER)],
        "pytest": {MAX_1, MAX_2}, "node": set(), "pickui": set(),
    },
    {
        "name": "on-commit-two-args", "file": API,
        "edits": [(
            "            on_commit=lambda name, selector, edit_state=None: _commit_from_dialog(\n"
            "                entry, name, selector, edit_state),",
            "            on_commit=lambda name, selector: _commit_from_dialog("
            "entry, name, selector)," + MARKER)],
        "pytest": set(), "node": set(),
        "pickui": {"契约层：确认框保存接线收三参并透传 selector 与勾选快照"},
    },
]


def md5(path: pathlib.Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _run(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    return proc.stdout + proc.stderr


def fail_pytest(output: str) -> set[str]:
    return {
        m.group(1).replace("\\", "/")
        for line in output.splitlines()
        if (m := re.match(r"FAILED\s+(\S+)", line.strip()))
    }


def fail_node(output: str) -> set[str]:
    return {
        m.group(1)
        for line in output.splitlines()
        if (m := re.match(r"FAIL\s+(.+?)(?:\s+—.*)?$", line.strip()))
    }


def main() -> int:
    for case in CASES:
        path: pathlib.Path = case["file"]
        text = path.read_text(encoding="utf-8")
        if "<<<M104-INJECTED>>>" in text:
            print(f"[哨兵] {path.name} 里还有上次的注入标记，拒绝运行")
            return 2
        for old, _new in case["edits"]:
            if text.count(old) != 1:
                print(f"[锚点] {case['name']}: {path.name} 出现 {text.count(old)} 次，必须 1 次")
                return 2

    problems: list[str] = []
    for case in CASES:
        path: pathlib.Path = case["file"]
        original = path.read_bytes()
        before = md5(path)
        restore_failed = False
        try:
            body = original.decode("utf-8")
            for old, new in case["edits"]:
                body = body.replace(old, new, 1)
            path.write_bytes(body.encode("utf-8"))
            got_pytest = fail_pytest(
                _run([str(PY), "-m", "pytest", "-q", "--tb=no", "-rf", *PYTEST_FILES]))
            got_node = fail_node(_run(["node", NODE]))
            got_pickui = fail_node(_run(["node", PICKUI]))
        finally:
            path.write_bytes(original)
            if md5(path) != before:
                print(f"[还原失败] {path}")
                restore_failed = True
        if restore_failed:
            return 3
        diffs: list[str] = []
        for key, got in (("pytest", got_pytest), ("node", got_node), ("pickui", got_pickui)):
            want = case[key]
            if got != want:
                diffs.append(f"{key} 缺失={sorted(want - got)} 多出={sorted(got - want)}")
        if diffs:
            problems.append(f"{case['name']}: " + "；".join(diffs))
            print(f"FAIL {case['name']}: " + "；".join(diffs))
        else:
            print(f"OK   {case['name']}: pytest {len(got_pytest)} 条、node {len(got_node)} 条、"
                  f"pickui {len(got_pickui)} 条")
    if problems:
        print("\n负向验证未通过：")
        for item in problems:
            print(" -", item)
        return 1
    print("\nM104 NEGATIVE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
