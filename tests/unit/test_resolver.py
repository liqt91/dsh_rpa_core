import pytest

from rpa_core.model.workflow import Condition
from rpa_core.runtime.resolver import (
    ReferenceError,
    evaluate,
    resolve,
    resolve_tags,
    resolve_with_modes,
)

SCOPES = {
    "inputs": {"user": {"name": "Ada"}, "items": [{"name": "first"}]},
    "steps": {"count": {"outputs": {"value": 3}}},
    "loop": {},
}


def test_resolve_replaces_only_complete_reference_tokens():
    assert resolve("${inputs.user.name}", SCOPES) == "Ada"
    assert resolve("Hello ${inputs.user.name}", SCOPES) == "Hello ${inputs.user.name}"
    assert resolve({"name": "${inputs.user.name}"}, SCOPES) == {"name": "Ada"}


def test_lookup_fails_fast_on_missing_or_non_mapping_segments():
    with pytest.raises(ReferenceError, match="Unknown reference"):
        resolve("${inputs.user.missing}", SCOPES)
    with pytest.raises(ReferenceError, match="Unknown reference"):
        resolve("${inputs.user.name.first}", SCOPES)


def test_numeric_list_index_is_rejected_as_unsupported_reference_syntax():
    with pytest.raises(ReferenceError, match="Invalid reference syntax"):
        resolve("${inputs.items.0.name}", SCOPES)


def test_evaluate_uses_resolved_values_without_dynamic_execution():
    assert evaluate(Condition(op="gt", left="${steps.count.outputs.value}", right=2), SCOPES)
    assert evaluate(Condition(op="contains", left="Ada Lovelace", right="Ada"), SCOPES)
    assert evaluate(Condition(op="truthy", left="${inputs.user}", right=None), SCOPES)


def test_variable_reference_resolves_whole_output():
    """${web_page1} → scopes.variables['web_page1']（整个 outputs dict）。"""
    scopes = {
        "variables": {"web_page1": {"sessionId": "s-1", "resourceType": "webPage"}},
        "steps": {},
    }
    assert resolve("${web_page1}", scopes) == {
        "sessionId": "s-1",
        "resourceType": "webPage",
    }


def test_variable_reference_resolves_subfield():
    """${web_page1.sessionId} → 变量值 dict 的子字段。"""
    scopes = {
        "variables": {"web_page1": {"sessionId": "s-1", "resourceType": "webPage"}},
        "steps": {},
    }
    assert resolve("${web_page1.sessionId}", scopes) == "s-1"


def test_variable_missing_field_raises():
    scopes = {"variables": {"web_page1": {"sessionId": "s-1"}}}
    with pytest.raises(ReferenceError, match="Unknown reference"):
        resolve("${web_page1.missing}", scopes)


def test_undefined_variable_falls_through_to_scope_lookup_and_fails():
    """未定义的裸变量名最终报 Unknown reference（非 steps/inputs/loop）。"""
    with pytest.raises(ReferenceError, match="Unknown reference"):
        resolve("${never_declared}", SCOPES)


def test_variable_takes_precedence_over_equal_rooted_scope_path():
    """变量名与 scope 根同名时优先查 variables。"""
    scopes = {"inputs": {"count": 1}, "variables": {"count": 99}}
    assert resolve("${count}", scopes) == 99


TAG_SCOPES = {
    "inputs": {"keyword": "新闻"},
    "variables": {"webpage1": {"sessionId": "s-1", "url": "https://a.b"}},
}


def test_tag_interpolates_into_surrounding_text():
    """fx 标签可与普通文本混排拼接（${} 全串语法做不到）。"""
    assert resolve_tags("结果是[inputs.keyword]报告", TAG_SCOPES) == "结果是新闻报告"


def test_tag_supports_subpath():
    assert resolve_tags("网址=[webpage1.url]", TAG_SCOPES) == "网址=https://a.b"


def test_tag_whole_variable_renders_as_text():
    """引用整个 dict 时渲染为 JSON 文本，便于拼进字符串。"""
    assert "sessionId" in resolve_tags("[webpage1]", TAG_SCOPES)


def test_tag_without_placeholder_text_is_untouched():
    assert resolve_tags("纯文本没有标签", TAG_SCOPES) == "纯文本没有标签"


def test_unknown_tag_raises_reference_error():
    """未定义标签必须报错，不能静默返回字面量。"""
    with pytest.raises(ReferenceError, match="Unknown reference"):
        resolve_tags("值=[nope]", TAG_SCOPES)


def test_tag_recurses_into_list_and_dict():
    value = {"a": ["x[inputs.keyword]", "y"], "b": {"c": "z[webpage1.sessionId]"}}
    assert resolve_tags(value, TAG_SCOPES) == {"a": ["x新闻", "y"], "b": {"c": "zs-1"}}


def test_tag_mode_still_accepts_whole_dollar_reference():
    """fx 模式下整串 ${...} 仍按既有语义解析，保留原类型。"""
    scopes = {"variables": {"n": 5}}
    assert resolve_tags("${n}", scopes) == 5


def test_resolve_with_modes_dispatches_per_field():
    inputs = {"a": "x[inputs.keyword]y", "b": "${inputs.keyword}"}
    assert resolve_with_modes(inputs, {"a": "fx"}, TAG_SCOPES) == {
        "a": "x新闻y",
        "b": "新闻",
    }


def test_resolve_with_modes_without_modes_keeps_legacy_behaviour():
    assert resolve_with_modes({"a": "${inputs.keyword}"}, None, TAG_SCOPES) == {"a": "新闻"}
