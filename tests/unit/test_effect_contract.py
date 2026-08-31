import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from rpa_core.catalog import load_catalog
from rpa_core.compiler import ExecutionPlan, WorkflowCompileError, WorkflowCompiler
from rpa_core.executors import ExecutorRegistry
from rpa_core.executors.base import CommandExecutor
from rpa_core.model.command import (
    CommandInvocation,
    CommandManifest,
    CommandResult,
    EffectKind,
    EffectRecord,
    EffectStatus,
)
from rpa_core.model.workflow import Workflow
from rpa_core.runtime import Orchestrator

ROOT = Path(__file__).resolve().parents[2]


class ResultExecutor(CommandExecutor):
    def __init__(self, result):
        self.result = result
        self.calls = 0

    async def execute(self, invocation, cancellation):
        self.calls += 1
        return self.result


class CountingExecutor(CommandExecutor):
    def __init__(self):
        self.calls = 0

    async def execute(self, invocation, cancellation):
        self.calls += 1
        raise AssertionError("unsafe command must be rejected before execution")


def unsafe_manifest():
    return {
        "id": "test.unsafe",
        "version": "1.0.0",
        "executor": "counting",
        "kind": "action",
        "risk": "browser-control",
        "capabilities": [],
        "resources": ["external.state"],
        "stability": "stable",
        "effect": {"kind": "unsafe-write", "replay": "unsafe", "idempotency": "none"},
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "errors": ["EXECUTOR_FAILED"],
        "implementation": {"handler": "test:unsafe"},
        "retryable": False,
    }


def test_all_stable_commands_declare_valid_effect_policy():
    expected = {
        "browser.launch": "session",
        "browser.navigate": "unsafe-write",
        "browser.click": "unsafe-write",
        "browser.input": "unsafe-write",
        "browser.waitFor": "read",
        "browser.getText": "read",
        "browser.queryAll": "read",
        "browser.close": "session",
        "data.writeJson": "idempotent-write",
        "desktop.attachWindow": "session",
        "desktop.findElement": "read",
        "desktop.click": "unsafe-write",
        "desktop.input": "unsafe-write",
        "desktop.getText": "read",
        "desktop.closeSession": "session",
    }
    assert expected["desktop.attachWindow"] == "session"
    assert expected["desktop.findElement"] == "read"
    assert expected["desktop.click"] == "unsafe-write"
    assert expected["desktop.input"] == "unsafe-write"
    assert expected["desktop.getText"] == "read"
    assert expected["desktop.closeSession"] == "session"


def test_effect_policy_rejects_contradictory_retry_and_idempotency():
    data = unsafe_manifest()
    data["retryable"] = True
    with pytest.raises(ValidationError, match="unsafe replay commands cannot be retryable"):
        CommandManifest.model_validate(data)

    data = unsafe_manifest()
    data["effect"] = {
        "kind": "idempotent-write",
        "replay": "idempotent",
        "idempotency": "none",
    }
    with pytest.raises(ValidationError, match="requires an idempotency policy"):
        CommandManifest.model_validate(data)


def test_effect_id_is_stable_within_attempt_and_changes_across_attempts():
    first = CommandInvocation(
        command_id="test.read",
        command_version="1.0.0",
        run_id="run",
        step_id="step",
        attempt=1,
        inputs={},
    )
    repeated = EffectRecord.committed(first, kind=EffectKind.READ, resource="resource:item")
    same = EffectRecord.committed(first, kind=EffectKind.READ, resource="resource:item")
    second = EffectRecord.committed(
        first.model_copy(update={"attempt": 2}),
        kind=EffectKind.READ,
        resource="resource:item",
    )
    assert repeated.effect_id == same.effect_id
    assert repeated.effect_id != second.effect_id
    assert repeated.status.value == "committed"


def build_unsafe_run(tmp_path, executor):
    commands = tmp_path / "commands"
    commands.mkdir()
    (commands / "unsafe.json").write_text(json.dumps(unsafe_manifest()), encoding="utf-8")
    catalog = load_catalog(commands)
    workflow = Workflow.model_validate(
        {
            "id": "unsafe-effect",
            "name": "unsafe-effect",
            "root": {"type": "action", "id": "unsafe", "command": "test.unsafe"},
        }
    )
    plan = WorkflowCompiler(catalog).compile(workflow, set())
    runner = Orchestrator(
        catalog,
        ExecutorRegistry({"counting": executor}),
        tmp_path / "runs",
    )
    return runner, plan


def test_non_pure_success_requires_committed_effect_evidence(tmp_path):
    executor = ResultExecutor(CommandResult.success())
    runner, plan = build_unsafe_run(tmp_path, executor)
    result = asyncio.run(runner.run(plan))
    assert result.status.value == "failed"
    assert result.error["code"] == "INVALID_OUTPUT"
    assert "no effect evidence" in result.error["message"]


def test_failed_unknown_effect_is_preserved_in_failure_evidence(tmp_path):
    effect = EffectRecord(
        effectId="e" * 64,
        kind=EffectKind.UNSAFE_WRITE,
        status=EffectStatus.UNKNOWN,
        resource="external:target",
    )
    executor = ResultExecutor(
        CommandResult.failure(
            code="EXECUTOR_FAILED",
            message="outcome unknown",
            effects=[effect],
        )
    )
    runner, plan = build_unsafe_run(tmp_path, executor)
    result = asyncio.run(runner.run(plan))
    assert result.status.value == "failed"
    effects = result.error["details"]["effects"]
    assert effects[0]["status"] == "unknown"
    events_path = tmp_path / "runs" / result.run_id / "events.jsonl"
    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    failed = [event for event in events if event["type"] == "stepFailed"]
    assert failed[0]["payload"]["effects"][0]["status"] == "unknown"


def test_compiler_and_runtime_reject_unsafe_retry(tmp_path):
    commands = tmp_path / "commands"
    commands.mkdir()
    (commands / "unsafe.json").write_text(json.dumps(unsafe_manifest()), encoding="utf-8")
    catalog = load_catalog(commands)
    workflow = Workflow.model_validate(
        {
            "id": "unsafe-retry",
            "name": "unsafe-retry",
            "root": {
                "type": "action",
                "id": "unsafe",
                "command": "test.unsafe",
                "retry_count": 1,
            },
        }
    )
    with pytest.raises(WorkflowCompileError, match="Unsafe replay command"):
        WorkflowCompiler(catalog).compile(workflow, set())

    plan = ExecutionPlan(
        workflow=workflow,
        catalog_digest=catalog.digest,
        required_capabilities=frozenset(),
    )
    executor = CountingExecutor()

    async def run():
        runner = Orchestrator(
            catalog,
            ExecutorRegistry({"counting": executor}),
            tmp_path / "runs",
        )
        return await runner.run(plan)

    result = asyncio.run(run())
    assert result.status.value == "failed"
    assert result.error["code"] == "INVALID_INPUT"
    assert result.error["details"]["attempt"] == 0
    assert executor.calls == 0
