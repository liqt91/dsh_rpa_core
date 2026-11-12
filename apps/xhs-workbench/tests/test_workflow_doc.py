"""流程文档视图单测：摊平顺序、容器下钻、标签回落、真实流程文件的形状。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from src.workflow_doc import command_label, describe, flatten_steps

APP = Path(__file__).resolve().parents[1]
WF = APP / "workflows" / "xhs_product_search.json"


def _action(node_id: str) -> dict[str, Any]:
    return {"type": "action", "id": node_id, "command": "data.log", "with": {}}


def _control_flow_doc() -> dict[str, Any]:
    """真实 AST 形状：if 用 then/else，try 用 children/catch，forEach 用 children。"""
    return {
        "id": "probe",
        "name": "控制流探针",
        "root": {
            "type": "sequence",
            "id": "root",
            "children": [
                {
                    "type": "if",
                    "id": "if1",
                    "condition": {"op": "eq", "left": 1, "right": 1},
                    "then": [_action("then_step")],
                    "else": [_action("else_step")],
                },
                {
                    "type": "try",
                    "id": "try1",
                    "error_var": "err",
                    "children": [_action("try_step")],
                    "catch": [_action("catch_step")],
                },
                {
                    "type": "forEach",
                    "id": "loop1",
                    "items": [1, 2],
                    "item_var": "row",
                    "children": [_action("loop_step")],
                },
                _action("tail"),
            ],
        },
    }


def test_flatten_keeps_source_order_and_indexes() -> None:
    doc = {
        "root": {
            "type": "sequence",
            "id": "root",
            "children": [
                {"type": "action", "id": "a", "command": "browser.navigate", "with": {"url": "u"}},
                {
                    "type": "action",
                    "id": "b",
                    "command": "browser.waitFor",
                    "with": {"selector": "s"},
                    "_exprModes": {"selector": "fx"},
                    "elementRefs": {"selector": "xhs_search_box"},
                },
            ],
        }
    }
    steps = flatten_steps(doc)
    assert [s["id"] for s in steps] == ["a", "b"]
    assert [s["index"] for s in steps] == [1, 2]
    assert steps[0]["label"] == "打开网页"
    assert steps[1]["exprModes"] == {"selector": "fx"}
    assert steps[1]["elementRefs"] == {"selector": "xhs_search_box"}


def test_container_nodes_are_descended_into_not_listed() -> None:
    """if 的分支体在 then/else，不在 children——旧 fixture 写成 children 正好盖住了漏显示。"""
    doc = {
        "root": {
            "type": "sequence",
            "id": "root",
            "children": [
                {
                    "type": "if",
                    "id": "branch",
                    "condition": {"op": "truthy", "left": "${inputs.flag}"},
                    "then": [
                        {"type": "action", "id": "inner", "command": "browser.click", "with": {}},
                    ],
                    "else": [],
                },
                {"type": "action", "id": "after", "command": "browser.close", "with": {}},
            ],
        }
    }
    steps = flatten_steps(doc)
    assert [s["id"] for s in steps] == ["inner", "after"]
    assert steps[0]["depth"] == 1  # 进入一层容器后深度 +1，前端可据此缩进
    assert steps[0]["branch"] == "then"
    assert steps[1]["depth"] == 0


def test_control_flow_branches_are_all_visited() -> None:
    """if 的 then/else、try 的 children/catch、forEach 的 children 一个都不能丢。"""
    steps = flatten_steps(_control_flow_doc())
    assert [s["id"] for s in steps] == [
        "then_step",
        "else_step",
        "try_step",
        "catch_step",
        "loop_step",
        "tail",
    ]
    assert [s["branch"] for s in steps] == [
        "then",
        "else",
        "children",
        "catch",
        "children",
        "children",
    ]
    assert [s["depth"] for s in steps] == [1, 1, 1, 1, 1, 0]
    # 控制流节点自身不是可执行指令，不该占一个步骤
    assert all(s["command"] == "data.log" for s in steps)


def test_control_flow_fixture_is_a_real_workflow_ast() -> None:
    """钉住 fixture 的真实性：谁把它改回 `if.children`，模型先红。"""
    repo_src = APP.parents[1] / "src"
    if str(repo_src) not in sys.path:
        sys.path.insert(0, str(repo_src))
    from rpa_core.model.workflow import Workflow

    model = Workflow.model_validate(_control_flow_doc())
    root = model.root
    assert root.type == "sequence"
    assert [child.type for child in root.children] == ["if", "try", "forEach", "action"]


def test_unknown_command_falls_back_to_command_id() -> None:
    assert command_label("browser.whatever") == "browser.whatever"
    assert command_label("") == ""


def test_describe_real_workflow_exposes_wait_and_shot_steps() -> None:
    doc = describe(WF)
    ids = [s["id"] for s in doc["steps"]]
    assert ids[0] == "openHome"
    assert "waitItems" in ids and "shotResults" in ids
    assert ids.index("waitItems") < ids.index("grabItems")
    assert doc["count"] == len(ids)
    assert doc["inputs"]["keyword"]
    assert "browser.waitFor" in doc["commands"]
    assert doc["raw"].lstrip().startswith("{")


def test_labels_parse_only_the_commands_block(tmp_path: Path) -> None:
    """i18n.js 文本解析的边界：fields 段的「命令.字段」键不能混进命令表。"""
    from src.workflow_doc import load_command_labels

    fixture = tmp_path / "i18n.js"
    fixture.write_text(
        "window.RPA_I18N = {\n"
        "  commands: {\n"
        '    "browser.navigate": "打开网页",\n'
        '    "data.log": "打印日志",\n'
        "  },\n"
        "  fields: {\n"
        '    "browser.navigate.url": "目标网址",\n'
        "  },\n"
        "};\n",
        encoding="utf-8",
    )
    labels = load_command_labels(fixture)
    assert labels == {"browser.navigate": "打开网页", "data.log": "打印日志"}
    assert "browser.navigate.url" not in labels


def test_labels_real_i18n_covers_browser_commands() -> None:
    from src.workflow_doc import load_command_labels

    labels = load_command_labels(APP.parents[1] / "src" / "rpa_core" / "gui" / "i18n.js")
    assert labels["browser.navigate"] == "打开网页"
    assert labels["browser.screenshot"] == "网页截图"
    assert len(labels) >= 60
