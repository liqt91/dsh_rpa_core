"""探针：页签的三个目标真的把对应开关注进子进程了吗（M38 §6 页签集成验收）。

判据不是「按钮存在」，而是「点下去之后子进程的环境里有没有那个变量、
以及被收集的驱动集合对不对」——用**真实入口** `_on_target_clicked`，
只把 argv 组装那一半打桩（换成一段打印环境的脚本），与 S1.2 的假绿灯教训同款：
注入点必须落在被测分支上。
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from PySide6.QtCore import QProcess  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rpa_core.gui import command_matrix as cm  # noqa: E402

REPO = Path(__file__).resolve().parents[2]

# 子进程替身：把它看到的环境变量打出来，供本探针断言
DUMP = """
import os, json
keys = ["RPA_COMMAND_MATRIX", "RPA_DESKTOP_E2E", "RPA_BROWSER_L2", "RPA_COMMAND_MATRIX_REPORT"]
print("PROBE_ENV=" + json.dumps({k: os.environ.get(k) for k in keys}))
"""

app = QApplication.instance() or QApplication([])
panel = cm.CommandMatrixPanel(repo_root=REPO)
# 只打桩 argv 组装那一半：真实入口 _on_target_clicked -> _run_matrix 全走
panel._start = lambda argv, mode: _fake_start(panel, argv, mode)

results: dict[str, dict] = {}
captured: list[dict] = []


def _fake_start(panel, argv, mode):
    """把真 argv 换成环境打印脚本，但**环境由面板自己组装**（这才是被测的东西）。"""
    import json as _json

    env = panel._process.processEnvironment()
    captured.append({k: env.value(k) for k in (
        "RPA_COMMAND_MATRIX", "RPA_DESKTOP_E2E", "RPA_BROWSER_L2", "RPA_COMMAND_MATRIX_REPORT"
    )})
    print("PROBE_ENV=" + _json.dumps(captured[-1]))
    print(f"PROBE_ARGV={argv}")
    print(f"PROBE_PROGRESS_TOTAL={panel.progress.maximum()}")


import json  # noqa: E402

for target in cm.RUN_TARGETS:
    captured.clear()
    panel._target = target
    # 走真实入口：它会 _run_matrix（组装环境、算分母、组 argv）
    panel._on_target_clicked(target.key)
    results[target.key] = dict(captured[-1]) if captured else {}
    results[target.key]["_progressTotal"] = panel.progress.maximum()

print("PROBE_RESULT=" + json.dumps(results, ensure_ascii=False))
