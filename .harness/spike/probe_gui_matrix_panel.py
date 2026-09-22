"""M38 S1.2 页签的负向验证探针（保留为可复跑的证据）。

`.harness/spike/` 不进 lint 门禁（见 pyproject 的 ruff exclude），与 `probe_browser_commands.py`
同性质：一次性调研/验证脚手架，留在这里供后人复跑，不进产品代码。

三向注入都必须让 `test_panel_streams_jsonl_into_rows` 转红。**第一版是假绿灯**（注入点在
`_run_matrix`，而当时的用例自己调 `panel._timer.start()`，根本没走到被测分支；即便走真实入口，
`_on_finished` 收尾还会再 poll 一次，「结束后有行」也证明不了轮询）——教训见
`.harness/tasks/M38-command-matrix.md` §4.3。

用法：`.venv/Scripts/python.exe .harness/spike/probe_gui_matrix_panel.py`
输出：`%TEMP%/neg_gui.txt`（注入结果 + 带报告钩子的全量矩阵正向回归）。
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(r"D:\Users\Administrator\Documents\代码\rpa_core")
PY = ROOT / ".venv" / "Scripts" / "python.exe"
PANEL = ROOT / "src" / "rpa_core" / "gui" / "command_matrix.py"
TEST = "tests/contract/test_gui_command_matrix.py::test_panel_streams_jsonl_into_rows"

# (说明, 注入前原文, 注入后原文)
INJECTIONS = [
    (
        "① `_run_matrix` 摘掉轮询点火",
        '        self._start(build_pytest_command(sys.executable), "matrix")\n'
        "        self._timer.start()\n",
        '        self._start(build_pytest_command(sys.executable), "matrix")\n',
    ),
    (
        "② `__init__` 摘掉轮询接线（定时器在响，没人听）",
        "        self._timer.timeout.connect(self._poll_report)\n",
        "",
    ),
    (
        "③ `_run_matrix` 摘掉报告路径注入（子进程拿不到 REPORT_ENV）",
        "        env.insert(REPORT_ENV, str(report_path))\n",
        "",
    ),
]

lines: list[str] = []
env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
original = PANEL.read_text(encoding="utf-8")

for label, before, after in INJECTIONS:
    assert original.count(before) == 1, f"锚点不唯一，注入点变了：{label}"
    PANEL.write_text(original.replace(before, after), encoding="utf-8", newline="\n")
    try:
        proc = subprocess.run(
            [str(PY), "-m", "pytest", TEST, "-p", "no:cacheprovider", "--no-header", "-q"],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", env=env, check=False,
        )
    finally:
        PANEL.write_text(original, encoding="utf-8", newline="\n")
    verdict = "红（符合预期）" if proc.returncode != 0 else "**绿——断言是摆设**"
    lines.append(f"{label} → exit={proc.returncode} {verdict}")
    # 记下「哪个断言被触发」：只说红了不够，要能看出打进的是不是预期的那一条
    for line in proc.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("FAILED ") or re.match(r"^tests[\\/].*\.py:\d+: ", stripped):
            lines.append(f"   {stripped}")
    lines.append(f"   还原一致 = {PANEL.read_text(encoding='utf-8') == original}")

# 正向回归：报告钩子不影响矩阵本身（180 passed / exit 0），且报告是完整 180 case + 1 summary
report = pathlib.Path(os.environ["TEMP"]) / "matrix_full.jsonl"
if report.exists():
    report.unlink()
env2 = {**env, "RPA_COMMAND_MATRIX": "1", "RPA_COMMAND_MATRIX_REPORT": str(report)}
proc2 = subprocess.run(
    [str(PY), "-m", "pytest", "tests/commands", "-p", "no:cacheprovider",
     "--no-header", "-o", "console_output_style=count"],
    cwd=str(ROOT), capture_output=True, text=True,
    encoding="utf-8", errors="replace", env=env2, check=False,
)
lines.append(f"④ 全量矩阵（带报告钩子）exit={proc2.returncode}")
lines.extend(f"   {line}" for line in proc2.stdout.splitlines()[-2:])
records = [
    json.loads(line)
    for line in report.read_text(encoding="utf-8").splitlines()
    if line.strip()
]
cases = [r for r in records if r.get("event") == "case"]
summaries = [r for r in records if r.get("event") == "summary"]
lines.append(f"   报告 case 数={len(cases)} summary 数={len(summaries)}")
lines.append(f"   summary={summaries[0] if summaries else None}")
missing = [r for r in cases if "command" not in r or "variant" not in r]
lines.append(f"   未切出 (command, variant) 的 case 数 = {len(missing)}（期望 0）")

out = pathlib.Path(os.environ["TEMP"]) / "neg_gui.txt"
out.write_text("\n".join(lines), encoding="utf-8", newline="\n")
print("written", out)
sys.exit(0)
