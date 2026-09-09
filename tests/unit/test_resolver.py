import pytest

from rpa_core.model.workflow import Condition
from rpa_core.runtime.resolver import ReferenceError, evaluate, resolve

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
