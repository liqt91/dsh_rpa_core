"""默认元素名生成器（A3）的**两侧一致**契约。

``suggest_element_name``（Python，``element_editor.py``）与 ``suggestElementName``
（JS，``devserver/static/app.js``）是同一套方案的**两份实现**——零构建双端没法
真共享代码。本文件与 ``scripts/check_element_name.mjs``（node 切片求值）跑
**同一份用例表**（``data/element_name_cases.json``）：任何一侧改动导致输出分叉，
至少一侧判据红。
"""

from __future__ import annotations

import json
from pathlib import Path

from rpa_core.gui.element_editor import normalize_element_hint, suggest_element_name

ROOT = Path(__file__).resolve().parents[2]
CASES = json.loads(
    (ROOT / "tests" / "contract" / "data" / "element_name_cases.json").read_text(
        encoding="utf-8"
    )
)


def test_python_side_matches_shared_case_table():
    for case in CASES["cases"]:
        actual = suggest_element_name(case["existing"], case["hint"])
        assert actual == case["expected"], (
            f"existing={case['existing']!r} hint={case['hint']!r}: "
            f"got {actual!r} want {case['expected']!r}"
        )


def test_js_side_wiring_exists_in_app_js():
    """JS 侧必须真用上生成器（捕获确认框的默认名），切片锚点必须独占一行。

    只测 Python 侧的话，JS 实现漂了（或有人改回时间戳命名）不会有任何红灯——
    这正是「同一件事两处口径」的形状。
    """
    source = (ROOT / "src" / "rpa_core" / "devserver" / "static" / "app.js").read_text(
        encoding="utf-8"
    )
    assert source.count("// [element-name-helpers:start]") == 1
    assert source.count("// [element-name-helpers:end]") == 1
    assert "suggestElementName(existingNames, hint)" in source, (
        "捕获确认框的默认名必须走 suggestElementName（不允许退回时间戳命名）"
    )
    assert "Date.now() % 100000" not in source, (
        "时间戳默认名已废弃（不可读也不可复现），不该再出现"
    )


def test_gui_wiring_passes_existing_names_to_generator():
    """GUI 捕获确认框必须把**既有元素名清单**传给生成器——只换函数不传清单，
    撞名保护形同虚设（连续采集同名元素会反复弹覆盖确认）。"""
    source = (ROOT / "src" / "rpa_core" / "gui" / "app.py").read_text(encoding="utf-8")
    assert "suggest_element_name(existing, hint)" in source
    assert "existing = list(store.list()) if store is not None else []" in source


def test_normalize_element_hint_edge_cases():
    assert normalize_element_hint(None) == "element"
    assert normalize_element_hint("ButtonControl") == "button"
    assert normalize_element_hint("  ") == "element"
