"""负向验证探针（M38 S3）：证明数据 / 工作流矩阵的断言**真的承重**。

五向，每向都沿**一条真实缺陷的形状**注入，跑完逐字节还原：

1. **表侧**（静态覆盖率校验器）：把用例表里 `data.deletePath.recursive` 的全部出现改名
   → `check_command_matrix.py` 必须红并点名该参数；
2. **参数被静默忽略**：`writeText` 的 `lines` 分支只取第一行 → 只有内容断言那条红；
3. **安全防线被摘**：删掉 `writeText` 的 `_within_workspace` 检查 → 两条逃逸负路径红；
4. **默认值漂移**：`data.log` 的 `level` 默认从 `info` 改成 `debug` → 默认值断言那条红；
5. **安全阀接线**：把一条用例的 workspace 改成越出变体临时目录 → 必须在**执行命令之前**
   被安全阀拦下（失败信息含「安全阀拦下」，且该目录下不会多出任何文件）。

判据是「红在**预期的那几条**上」——只看「有没有红」远远不够（M30 S4 的教训：注入点没落在
被测分支上时，注入照样可能与断言无关。本例第 ①/③/④ 向都能精确到变体名）。

用法：uv run python .harness/spike/probe_data_matrix_negative.py
"""

from __future__ import annotations

import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
PYTHON = str(ROOT / ".venv" / "Scripts" / "python.exe")
CASES = ROOT / "tests" / "commands" / "cases" / "data.json"
WORKER = ROOT / "src" / "rpa_core" / "workers" / "python_worker.py"
CHECKER = ROOT / ".harness" / "scripts" / "check_command_matrix.py"
DRIVER = "tests/commands/test_data_matrix.py"

LINES: list[str] = []


def _run(command: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        command, cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def run_variants(keyword: str) -> tuple[int, set[str], str]:
    """跑一片变体，返回 `(退出码, 失败的 <命令::变体> 集合, 原始输出)`。"""
    code, output = _run(
        [PYTHON, "-m", "pytest", DRIVER, "-p", "no:cacheprovider", "--no-header",
         "-q", "-k", keyword]
    )
    failed = set()
    for line in output.splitlines():
        match = re.search(r"test_command_variant\[(.+)\]$", line.strip())
        if match:
            failed.add(match.group(1))
    return code, failed, output


def run_checker() -> tuple[int, str]:
    return _run([PYTHON, str(CHECKER)])


def patch(path: pathlib.Path, replacements: list[tuple]) -> str:
    """改文件（占位符 `(旧, 新)` 要求唯一命中；`(旧, 新, 次数)` 用于多处同款锚点）。"""
    original = path.read_text(encoding="utf-8")
    patched = original
    for item in replacements:
        old, new = item[0], item[1]
        expected_count = item[2] if len(item) > 2 else 1
        assert patched.count(old) == expected_count, (
            f"注入锚点命中 {patched.count(old)} 次（期望 {expected_count}）：{old[:50]!r}"
        )
        patched = patched.replace(old, new)
    path.write_text(patched, encoding="utf-8", newline="\n")
    return original


def restore(path: pathlib.Path, original: str) -> bool:
    path.write_text(original, encoding="utf-8", newline="\n")
    return path.read_text(encoding="utf-8") == original


def report(step: str, ok: bool, detail: str, restored: bool) -> None:
    verdict = "红在预期（符合）" if ok else "**未红在预期——断言是摆设**"
    LINES.append(f"{step}\n    → {verdict}；{detail}；还原一致 = {restored}")


def main() -> None:
    # ① 表侧：recursive 参数从用例表消失 → 静态校验器点名
    original = patch(CASES, [
        ('"recursive": false', '"recursiveX": false', 2),
        ('"recursive": true', '"recursiveX": true', 1),
    ])
    try:
        code, output = run_checker()
        hit = "data.deletePath.recursive: 没有任何变体显式设置该参数" in output
    finally:
        restored = restore(CASES, original)
    report("① 表侧：用例表删掉 data.deletePath.recursive", code != 0 and hit,
           f"exit={code}，命中参数名 = {hit}", restored)

    # ② 实现侧：lines 分支只取第一行（参数被消费但行为错）
    original = patch(WORKER, [(
        '            content = "\\n".join(str(line) for line in invocation.inputs["lines"])\n'
        '        output_path.parent.mkdir(parents=True, exist_ok=True)\n'
        '        output_path.write_text(content, encoding="utf-8")',
        '            content = str(list(invocation.inputs["lines"])[0])\n'
        '        output_path.parent.mkdir(parents=True, exist_ok=True)\n'
        '        output_path.write_text(content, encoding="utf-8")',
    )])
    try:
        code, failed, _ = run_variants("writeText")
    finally:
        restored = restore(WORKER, original)
    expected = {"data.writeText::lines-branch-joins-with-newline"}
    report("② 实现侧：writeText 的 lines 只写第一行", code != 0 and failed == expected,
           f"exit={code}，失败集合 = {sorted(failed)}", restored)

    # ③ 实现侧：摘掉 workspace 越权防线
    original = patch(WORKER, [(
        '    if invocation.command_id == "data.writeText":\n'
        "        workspace, output_path = _resolve_output_path(invocation.inputs)\n"
        "        if not _within_workspace(workspace, output_path):\n"
        "            return CommandResult.failure(\n"
        "                ErrorCode.CAPABILITY_DENIED,\n"
        '                "Output path must stay inside workspace",\n'
        "            )\n",
        '    if invocation.command_id == "data.writeText":\n'
        "        workspace, output_path = _resolve_output_path(invocation.inputs)\n"
        "        _ = workspace\n",
    )])
    try:
        code, failed, _ = run_variants("writeText")
    finally:
        restored = restore(WORKER, original)
    expected = {
        "data.writeText::absolute-path-outside-workspace-is-denied",
        "data.writeText::relative-parent-escape-out-of-workspace-is-denied",
    }
    report("③ 实现侧：摘掉 writeText 的 _within_workspace 防线",
           code != 0 and failed == expected,
           f"exit={code}，失败集合 = {sorted(failed)}", restored)

    # ④ 实现侧：data.log 的 level 默认值漂移
    original = patch(WORKER, [(
        '        level = str(invocation.inputs.get("level") or "info")\n',
        '        level = str(invocation.inputs.get("level") or "debug")\n',
    )])
    try:
        code, failed, _ = run_variants("log")
    finally:
        restored = restore(WORKER, original)
    expected = {"data.log::defaults-to-info-level"}
    report("④ 实现侧：data.log 默认 level 改成 debug", code != 0 and failed == expected,
           f"exit={code}，失败集合 = {sorted(failed)}", restored)

    # ⑤ 安全阀接线：workspace 越出变体临时目录 → 执行前被拦
    original = patch(CASES, [(
        '"workspace": "{tmp}/ws",\n          "path": "sub/../ok.txt"',
        '"workspace": "{tmp}/../valve-probe",\n          "path": "sub/../ok.txt"',
    )])
    try:
        code, failed, output = run_variants("relative-path-normalizes-within-workspace")
        # 「执行前被拦」的证据：失败信息里是安全阀文案，**没有**走到命令层的断言失败
        valve_hit = "安全阀拦下" in output
        ran_command = "用例断言失败" in output
    finally:
        restored = restore(CASES, original)
    expected = {"data.writeText::relative-path-normalizes-within-workspace"}
    report("⑤ 安全阀：workspace 越出变体临时目录",
           code != 0 and failed == expected and valve_hit and not ran_command,
           f"exit={code}，失败集合 = {sorted(failed)}，命中安全阀文案 = {valve_hit}，"
           f"跑到了命令层 = {ran_command}", restored)

    print("\n".join(LINES))


if __name__ == "__main__":
    main()
