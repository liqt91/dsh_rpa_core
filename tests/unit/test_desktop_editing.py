"""桌面元素编辑语义层契约：模型可读、重组可逆、模式冲突不静默选一个。"""

from __future__ import annotations

import json

import pytest

from rpa_core.capture_kit import desktop_editing as de
from rpa_core.capture_kit import element_editing as ee


def _descriptor() -> dict:
    return {
        "kind": "desktop",
        "selector": {
            "locator": {
                "backend": "uia",
                "controlType": "Button",
                "automationId": "send_btn",
                "name": "发送",
                "className": "QPushButton",
                "path": [
                    {"controlType": "Pane", "name": "聊天区"},
                    {"controlType": "ToolBar", "automationId": "toolbar", "name": "导航"},
                ],
            },
            "candidates": [
                {"kind": "uia", "locator": {"backend": "uia", "controlType": "Button"},
                 "matchedCount": 3, "penalty": 10},
            ],
        },
        "metadata": {
            "windowTitle": "微信", "className": "Qt51514QWindowIcon",
            "rect": {"left": 10, "top": 20, "width": 30, "height": 40},
        },
    }


def test_nodes_from_descriptor_window_is_readonly_and_last_is_target():
    nodes = de.nodes_from_descriptor(_descriptor())
    assert [n["type"] for n in nodes] == ["wnd", "Pane", "ToolBar", "Button"]
    assert nodes[0]["readonly"] is True and nodes[0]["selected"] is False
    assert nodes[0]["attributes"][0]["field"] == "windowTitle"
    assert not nodes[-1].get("readonly")
    assert [a["field"] for a in nodes[-1]["attributes"]] == [
        "controlType", "automationId", "name", "className"
    ]


def test_nodes_map_match_mode_only_to_fuzzy_fields():
    doc = _descriptor()
    doc["selector"]["locator"]["matchMode"] = "contains"
    nodes = de.nodes_from_descriptor(doc)
    target = nodes[-1]
    by_field = {a["field"]: a["operator"] for a in target["attributes"]}
    assert by_field["name"] == "包含"
    assert by_field["automationId"] == "包含"
    assert by_field["controlType"] == "精确"
    assert by_field["className"] == "精确"
    # path 级恒精确，哪怕整条 locator 设了 contains
    assert all(a["operator"] == "精确" for n in nodes[1:-1] for a in n["attributes"])


def test_round_trip_preserves_candidates_and_unknown_fields():
    doc = _descriptor()
    doc["selector"]["locator"]["sourceRect"] = [1, 2, 3, 4]
    result = de.compose_selector(doc, de.nodes_from_descriptor(doc))
    assert result["locator"] == doc["selector"]["locator"]
    assert result["candidates"] == doc["selector"]["candidates"]


def test_compose_honours_attribute_checkboxes():
    doc = _descriptor()
    nodes = de.nodes_from_descriptor(doc)
    for row in nodes[-1]["attributes"]:
        if row["field"] == "name":
            row["required"] = False
    result = de.compose_selector(doc, nodes)
    assert "name" not in result["locator"]
    assert result["locator"]["automationId"] == "send_btn"
    assert result["locator"]["path"] == doc["selector"]["locator"]["path"]


def test_compose_honours_level_checkboxes():
    doc = _descriptor()
    nodes = de.nodes_from_descriptor(doc)
    nodes[1]["selected"] = False
    result = de.compose_selector(doc, nodes)
    assert [step["controlType"] for step in result["locator"]["path"]] == ["ToolBar"]


def test_compose_writes_match_mode_from_fuzzy_rows():
    doc = _descriptor()
    nodes = de.nodes_from_descriptor(doc)
    for row in nodes[-1]["attributes"]:
        if row["field"] in de.FUZZY_FIELDS:
            row["operator"] = "正则"
    result = de.compose_selector(doc, nodes)
    assert result["locator"]["matchMode"] == "regex"


def test_compose_rejects_conflicting_match_modes():
    doc = _descriptor()
    nodes = de.nodes_from_descriptor(doc)
    for row in nodes[-1]["attributes"]:
        if row["field"] == "name":
            row["operator"] = "包含"
        if row["field"] == "automationId":
            row["operator"] = "正则"
    with pytest.raises(ValueError, match="匹配方式要一致"):
        de.compose_selector(doc, nodes)


def test_compose_rejects_empty_target_and_bad_input():
    doc = _descriptor()
    nodes = de.nodes_from_descriptor(doc)
    for row in nodes[-1]["attributes"]:
        row["required"] = False
    with pytest.raises(ValueError, match="至少要勾选一个属性"):
        de.compose_selector(doc, nodes)
    with pytest.raises(ValueError, match="层级为空"):
        de.compose_selector(doc, [])


def test_compose_rejects_unknown_field_and_operator():
    doc = _descriptor()
    nodes = de.nodes_from_descriptor(doc)
    nodes[-1]["attributes"].append(
        {"field": "handle", "label": "句柄", "value": "1", "operator": "精确", "required": True}
    )
    with pytest.raises(ValueError, match="不支持的属性字段"):
        de.compose_selector(doc, nodes)
    nodes[-1]["attributes"].pop()
    nodes[-1]["attributes"][0]["operator"] = "模糊"
    with pytest.raises(ValueError, match="不支持的匹配方式"):
        de.compose_selector(doc, nodes)


def test_build_edit_model_desktop_carries_nodes_and_selector_text():
    doc = _descriptor()
    model = ee.build_edit_model(doc, name="发送按钮", matches=2)
    assert "levels" not in model
    assert [n["type"] for n in model["nodes"]][-1] == "Button"
    assert json.loads(model["selector"]) == doc["selector"]
    validate = ee.validate_selector(doc, model["selector"])
    assert validate is None
    assert ee.apply_selector(doc, model["selector"])["selector"] == doc["selector"]
