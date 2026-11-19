"""元素编辑无 UI 语义层的契约：两个编辑面共用的权威。"""

from __future__ import annotations

import json

from rpa_core.capture_kit import element_editing as ee


def test_name_allows_chinese_and_rejects_disk_breaking_chars():
    assert ee.validate_name("微信-发送消息") is None
    assert ee.validate_name("a b") is None
    for bad in ("", "  a", "a  ", "a/b", "a\\b", "a:b", "a*b", "a?b", 'a"b',
                "a<b", "a>b", "a|b", "a.b", "CON", "nul", "x" * 49, None, 12):
        assert ee.validate_name(bad) is not None, bad
        assert ee.is_valid_name(bad) is False, bad


def test_selector_validation_browser_single_line_only():
    browser = {"kind": "browser", "selector": {"css": "#x"}}
    assert ee.validate_selector(browser, "#x") is None
    assert ee.validate_selector(browser, "") is not None
    assert ee.validate_selector(browser, "#a\n#b") is not None


def test_selector_validation_desktop_requires_locator_dict():
    desktop = {"kind": "desktop", "selector": {"locator": {"kind": "uia"}}}
    assert ee.validate_selector(desktop, '{"locator": {"kind": "uia"}}') is None
    assert ee.validate_selector(desktop, "not json") is not None
    assert ee.validate_selector(desktop, '{"selector": {}}') is not None
    assert ee.validate_selector(desktop, '{"locator": 1}') is not None


def test_apply_selector_returns_new_descriptor_without_mutating_input():
    original = {"kind": "browser", "selector": {"css": "#old", "path": [{"tag": "div"}]}}
    updated = ee.apply_selector(original, "#new")
    assert updated["selector"]["css"] == "#new"
    assert updated["selector"]["path"] == [{"tag": "div"}]
    assert original["selector"]["css"] == "#old"
    assert updated is not original


def test_apply_selector_desktop_replaces_whole_selector():
    original = {"kind": "desktop", "selector": {"locator": {"kind": "uia"}, "stale": 1}}
    updated = ee.apply_selector(original, '{"locator": {"kind": "win32"}}')
    assert updated["selector"] == {"locator": {"kind": "win32"}}
    assert "stale" in original["selector"]


def test_edit_text_for_browser_vs_desktop():
    assert ee.edit_text_for({"kind": "browser", "selector": {"css": "#x"}}) == "#x"
    desktop = {"kind": "desktop", "selector": {"locator": {"kind": "uia"}}}
    assert json.loads(ee.edit_text_for(desktop)) == desktop["selector"]


def test_build_edit_model_browser_with_path_gives_levels_and_composed_css():
    descriptor = {
        "kind": "browser",
        "selector": {
            "css": "div#root > span.item",
            "path": [
                {"tag": "div", "id": "root", "inCss": True},
                {"tag": "span", "classes": ["item"], "inCss": True},
            ],
        },
    }
    model = ee.build_edit_model(descriptor, name="按钮", matches=2, node_id="n1")
    assert model["name"] == "按钮"
    assert model["kind"] == "browser"
    assert "命中 2 个" in model["info"] and "n1" in model["info"]
    assert len(model["levels"]) == 2
    assert ee.compose(model["levels"]) == model["selector"] == "#root > span.item"


def test_build_edit_model_desktop_gives_json_text_without_levels():
    descriptor = {"kind": "desktop", "selector": {"locator": {"kind": "uia"}}}
    model = ee.build_edit_model(descriptor, name="微信按钮")
    assert "levels" not in model
    assert json.loads(model["selector"]) == descriptor["selector"]
    assert "存为资产" in model["info"]


def test_level_composition_is_the_single_authority_for_checked_paths():
    levels = ee.levels_from_path([
        {"tag": "html", "inCss": False},
        {"tag": "div", "id": "root", "inCss": False},
        {"tag": "span", "classes": ["a", "b"], "nthOfType": 2, "inCss": True},
    ])
    assert ee.compose(levels) == "span.a:nth-of-type(2)"
    levels[0]["included"] = True
    assert ee.compose(levels) == "html span.a:nth-of-type(2)"
    levels[1]["included"] = True
    assert ee.compose(levels) == "html > #root > span.a:nth-of-type(2)"


def test_web_edit_state_survives_save_and_reopens_exactly():
    """网页勾选快照随 css 一起落盘，重开详情**逐项**还原（含被取消的层级）。

    没有快照时只能从捕获期的 inCss / 首 class 猜，用户在编辑框里取消的
    层级和 class 会全部丢失（2026-11-19 现场：网页元素详情全没勾）。
    """
    descriptor = {
        "kind": "browser",
        "selector": {
            "css": "div.a2 > span.b",
            "path": [
                {"tag": "div", "classes": ["a", "a2"]},
                {"tag": "span", "classes": ["b"]},
            ],
        },
    }
    levels = [
        {
            "tag": "div", "included": True,
            "id": {"on": False, "value": ""},
            "classes": [
                {"on": False, "value": "a", "mode": "精确"},
                {"on": True, "value": "a2", "mode": "精确"},
            ],
            "nth": {"on": False, "value": ""},
        },
        {
            "tag": "span", "included": True,
            "id": {"on": False, "value": ""},
            "classes": [{"on": True, "value": "b", "mode": "精确"}],
            "nth": {"on": False, "value": ""},
        },
    ]
    updated = ee.apply_selector(descriptor, "div.a2 > span.b", {"levels": levels})
    assert updated["selector"]["css"] == "div.a2 > span.b"
    assert updated["selector"]["editState"]["levels"] == levels
    model = ee.build_edit_model(updated, kind="browser")
    assert model["levels"] == levels
    assert model["selector"] == "div.a2 > span.b"


def test_old_asset_without_edit_state_falls_back_to_path_inference():
    """旧资产没有 editState ⇒ 回退现有推断，不因新字段打不开详情。"""
    descriptor = {
        "kind": "browser",
        "selector": {
            "css": "div > span",
            "path": [{"tag": "div", "classes": ["a"]}, {"tag": "span"}],
        },
    }
    model = ee.build_edit_model(descriptor, kind="browser")
    assert [level["included"] for level in model["levels"]] == [True, True]


def test_mismatched_edit_state_is_ignored_instead_of_misaligned():
    """快照层数与 path 对不上时宁可回退推断，别把勾选错位到别的层。"""
    descriptor = {
        "kind": "browser",
        "selector": {
            "css": "div > span",
            "path": [{"tag": "div"}, {"tag": "span"}],
            "editState": {"levels": [{"tag": "div", "included": False}]},
        },
    }
    model = ee.build_edit_model(descriptor, kind="browser")
    assert [level["included"] for level in model["levels"]] == [True, True]


def test_desktop_edit_state_keeps_unchecked_levels():
    """桌面快照整份存 nodes：取消勾选的层级重开详情仍在树里且仍未勾。"""
    descriptor = {
        "kind": "desktop",
        "selector": {
            "locator": {"controlType": "Button", "name": "搜索", "path": [
                {"controlType": "ToolBar", "name": "任务栏"},
            ]},
        },
    }
    nodes = [
        {"type": "wnd", "name": "窗口", "selected": False, "readonly": True,
         "attributes": []},
        {"type": "ToolBar", "name": "任务栏", "selected": False, "readonly": False,
         "attributes": [{"field": "name", "label": "名称", "value": "任务栏",
                         "operator": "精确", "required": False, "readonly": False}]},
        {"type": "Button", "name": "搜索", "selected": True, "readonly": False,
         "attributes": [{"field": "name", "label": "名称", "value": "搜索",
                         "operator": "精确", "required": True, "readonly": False}]},
    ]
    selector_text = json.dumps(descriptor["selector"], ensure_ascii=False)
    updated = ee.apply_selector(descriptor, selector_text, {"nodes": nodes})
    assert updated["selector"]["editState"]["nodes"] == nodes
    model = ee.build_edit_model(updated, kind="desktop")
    assert model["nodes"] == nodes
    assert [node["selected"] for node in model["nodes"]] == [False, False, True]
