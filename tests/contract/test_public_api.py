import inspect

from rpa_core.model.runtime import RunStatus
from rpa_core.runtime import Orchestrator, RunHandle
from rpa_core.runtime.checkpoint import CheckpointError, RecoveryRequiredError


def _params(func):
    return [
        (name, parameter.kind, parameter.default)
        for name, parameter in inspect.signature(func).parameters.items()
    ]


def test_api_v1_orchestrator_surface_is_frozen():
    assert _params(Orchestrator.start) == [
        ("self", inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.empty),
        ("plan", inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.empty),
        ("inputs", inspect.Parameter.POSITIONAL_OR_KEYWORD, None),
    ]
    assert _params(Orchestrator.run) == [
        ("self", inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.empty),
        ("plan", inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.empty),
        ("inputs", inspect.Parameter.POSITIONAL_OR_KEYWORD, None),
    ]
    assert _params(Orchestrator.resume) == [
        ("self", inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.empty),
        ("plan", inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.empty),
        ("run_id", inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.empty),
        (
            "allow_indeterminate",
            inspect.Parameter.KEYWORD_ONLY,
            False,
        ),
    ]


def test_api_v1_run_handle_surface_is_frozen():
    for method in (
        RunHandle.cancel,
        RunHandle.pause,
        RunHandle.wait,
        RunHandle.cancel_and_wait,
        RunHandle.pause_and_wait,
    ):
        assert [name for name, _kind, _default in _params(method)] == ["self"]


def test_api_v1_run_status_values_are_stable_and_distinct():
    assert {status.value for status in RunStatus} == {
        "running",
        "succeeded",
        "failed",
        "cancelled",
        "abandoned",
        "recovery_required",
        "indeterminate",
        "paused",
    }


def test_manual_recovery_gate_is_reachable_through_public_api():
    assert issubclass(RecoveryRequiredError, CheckpointError)
    assert issubclass(RecoveryRequiredError, Exception)
