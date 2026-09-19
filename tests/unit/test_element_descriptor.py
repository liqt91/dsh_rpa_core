"""元素描述符契约：``selector.candidates`` 与语义 metadata 的保留/校验语义。

背景：捕获端（``extension/content.js``）升级后会在 ``selector.candidates`` 里多存一组
备选定位，并在 ``metadata`` 里多存语义特征。本文件锁定三条约束：

1. 老文档（只有 ``css``）必须继续合法——这是「加法兼容」的落点；
2. ``selector`` / ``metadata`` 内部的额外键必须原样落盘，而描述符**顶层**的未知键
   会被 pydantic 默认的 ``extra="ignore"`` 静默丢弃——新字段只能放前者；
3. ``candidates`` 出现时必须结构正确，否则 ``rpa-core elements verify`` 要报出来。
"""

import pytest

from rpa_core.model.capture import selector_errors, validate_element_document

_MATCHED_PATH = "selector.candidates[0].matchedCount"


def _browser_document(**overrides) -> dict:
    document = {
        "kind": "browser",
        "selector": {"css": "#sb_form_q"},
        "verifyCount": 1,
        "metadata": {"tag": "textarea"},
    }
    document.update(overrides)
    return document


def test_element_without_candidates_stays_valid():
    """升级前的元素文档（只有 css）不受新增字段影响。"""
    element = validate_element_document(_browser_document())
    assert selector_errors(element) == []


def test_valid_candidates_pass_validation():
    element = validate_element_document(
        _browser_document(
            selector={
                "css": "#sb_form_q",
                "candidates": [
                    {"kind": "id", "selector": "#sb_form_q", "matchedCount": 1},
                    {"kind": "attribute", "selector": 'input[name="q"]', "matchedCount": 1},
                ],
            }
        )
    )
    assert selector_errors(element) == []


def test_candidates_matched_count_is_optional():
    element = validate_element_document(
        _browser_document(
            selector={"css": "#a", "candidates": [{"kind": "css", "selector": "#b"}]}
        )
    )
    assert selector_errors(element) == []


@pytest.mark.parametrize(
    ("candidates", "expected_path"),
    [
        ("not-a-list", "selector.candidates"),
        ([["nope"]], "selector.candidates[0]"),
        ([{"selector": "#a"}], "selector.candidates[0].kind"),
        ([{"kind": "   ", "selector": "#a"}], "selector.candidates[0].kind"),
        ([{"kind": "css"}], "selector.candidates[0].selector"),
        ([{"kind": "css", "selector": ""}], "selector.candidates[0].selector"),
        ([{"kind": "css", "selector": "#a", "matchedCount": 0}], _MATCHED_PATH),
        ([{"kind": "css", "selector": "#a", "matchedCount": "1"}], _MATCHED_PATH),
        ([{"kind": "css", "selector": "#a", "matchedCount": True}], _MATCHED_PATH),
        ([{"kind": "css", "selector": "#a"}, {"kind": "css"}], "selector.candidates[1].selector"),
    ],
)
def test_malformed_candidates_are_reported(candidates, expected_path):
    element = validate_element_document(
        _browser_document(selector={"css": "#a", "candidates": candidates})
    )
    paths = [error["path"] for error in selector_errors(element)]
    assert expected_path in paths


def test_selector_internal_extra_keys_survive_roundtrip():
    """selector 是自由字典：额外键必须原样落盘（candidates 就靠这个）。"""
    document = validate_element_document(
        _browser_document(
            selector={"css": "#a", "candidates": [{"kind": "css", "selector": "#b"}]}
        )
    ).document()
    assert document["selector"]["candidates"] == [{"kind": "css", "selector": "#b"}]


def test_top_level_unknown_keys_are_dropped():
    """顶层未知键会被静默丢弃——这正是新字段不能放顶层的原因。

    扩展曾长期回传顶层 ``url``，而它从未落盘（本测试即把这一事实钉住）。
    """
    document = validate_element_document(
        _browser_document(url="https://example.com/")
    ).document()
    assert "url" not in document


def test_semantic_metadata_survives_roundtrip():
    document = validate_element_document(
        _browser_document(
            metadata={
                "tag": "textarea",
                "role": "searchbox",
                "accessibleName": "搜索",
                "placeholder": "输入搜索内容",
                "label": "搜索框",
                "containerText": "主页 搜索",
                "url": "https://example.com/",
                "title": "示例站点",
            }
        )
    ).document()
    metadata = document["metadata"]
    assert metadata["role"] == "searchbox"
    assert metadata["accessibleName"] == "搜索"
    assert metadata["placeholder"] == "输入搜索内容"
    assert metadata["label"] == "搜索框"
    assert metadata["containerText"] == "主页 搜索"
    assert metadata["url"] == "https://example.com/"  # 以前丢失的页面指纹
    assert metadata["title"] == "示例站点"


def test_desktop_selector_ignores_candidates_validation():
    """desktop 走 locator 分支：不把 browser 的 candidates 规则套上去。"""
    element = validate_element_document(
        {
            "kind": "desktop",
            "selector": {"locator": {"backend": "uia", "controlType": "Button"}},
            "verifyCount": 0,
            "metadata": {},
        }
    )
    assert selector_errors(element) == []
