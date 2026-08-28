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
