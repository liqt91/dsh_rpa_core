"""流程文档视图单测：摊平顺序、容器下钻、标签回落、真实流程文件的形状。"""

from __future__ import annotations

from pathlib import Path

from src.workflow_doc import command_label, describe, flatten_steps

APP = Path(__file__).resolve().parents[1]
WF = APP / "workflows" / "xhs_product_search.json"


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
    doc = {
        "root": {
            "type": "sequence",
            "id": "root",
            "children": [
                {
                    "type": "if",
                    "id": "branch",
                    "children": [
                        {"type": "action", "id": "inner", "command": "browser.click", "with": {}},
                    ],
                },
                {"type": "action", "id": "after", "command": "browser.close", "with": {}},
            ],
        }
    }
    steps = flatten_steps(doc)
    assert [s["id"] for s in steps] == ["inner", "after"]
    assert steps[0]["depth"] == 1  # 进入一层容器后深度 +1，前端可据此缩进


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
