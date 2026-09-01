import asyncio
import json
from pathlib import Path

import pytest

from rpa_core.catalog import load_catalog
from rpa_core.compiler import WorkflowCompiler
from rpa_core.executors import ExecutorRegistry
from rpa_core.executors.base import CommandExecutor
from rpa_core.model.command import CommandResult
from rpa_core.model.errors import ErrorCode
from rpa_core.model.workflow import Workflow
from rpa_core.runtime import Orchestrator
from rpa_core.runtime.events import EventWriter, RunPersistenceError


class ScriptedExecutor(CommandExecutor):
    def __init__(self, results=None):
        self.results = list(results or [])
        self.calls = 0
        self.started = asyncio.Event()
        self.finished = asyncio.Event()

    async def execute(self, invocation, cancellation):
        self.calls += 1
        self.started.set()
        if self.results:
            return self.results.pop(0)
        try:
            await asyncio.Event().wait()
        finally:
            self.finished.set()


def manifest(command_id="test.action", *, output_schema=None, retryable=False):
    return {
        "id": command_id,
        "version": "1.0.0",
        "executor": "scripted",
        "kind": "action",
        "risk": "read",
        "capabilities": [],
        "resources": [],
        "stability": "stable",
        "effect": {"kind": "pure", "replay": "safe", "idempotency": "none"},
        "input_schema": {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "additionalProperties": False,
        },
        "output_schema": output_schema or {"type": "object"},
        "errors": [
            "INVALID_INPUT",
            "INVALID_OUTPUT",
            "EXECUTOR_FAILED",
            "TIMEOUT",
            "CANCELLED",
        ],
        "implementation": {"handler": "test:execute"},
        "default_timeout_seconds": 1,
        "retryable": retryable,
    }


def build(tmp_path, raw_workflow, executor, command_manifest=None):
    commands = tmp_path / "commands"
    commands.mkdir()
    data = command_manifest or manifest()
    (commands / "action.json").write_text(json.dumps(data), encoding="utf-8")
    catalog = load_catalog(commands)
    workflow = Workflow.model_validate(raw_workflow)
    plan = WorkflowCompiler(catalog).compile(workflow, set())
    runner = Orchestrator(
        catalog,
        ExecutorRegistry({"scripted": executor}),
        tmp_path / "runs",
    )
    return runner, plan


def read_events(runs_root: Path, run_id: str):
    lines = (runs_root / run_id / "events.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def action_workflow(**action_overrides):
    action = {"type": "action", "id": "step", "command": "test.action", "with": {}}
    action.update(action_overrides)
    return {"id": "runtime-correctness", "name": "runtime-correctness", "root": action}


def test_reference_and_schema_errors_have_stable_codes_and_context(tmp_path):
    executor = ScriptedExecutor()
    runner, reference_plan = build(
        tmp_path,
        {
            **action_workflow(**{"with": {"value": "${inputs.user.name}"}}),
            "inputs": {"user": {}},
        },
        executor,
    )
    reference_result = asyncio.run(runner.run(reference_plan))
    assert reference_result.error["code"] == "INVALID_REFERENCE"
    assert reference_result.error["details"] == {
        "nodeId": "step",
        "commandId": "test.action",
        "attempt": 0,
    }

    other = tmp_path / "other"
    other.mkdir()
    runner, input_plan = build(
        other,
        action_workflow(**{"with": {"value": 7}}),
        executor,
    )
    input_result = asyncio.run(runner.run(input_plan))
    assert input_result.error["code"] == "INVALID_INPUT"
    assert input_result.error["details"]["nodeId"] == "step"
    assert input_result.error["details"]["commandId"] == "test.action"


def test_invalid_executor_output_has_dedicated_error_and_attempt(tmp_path):
    executor = ScriptedExecutor([CommandResult.success(outputs={"count": "wrong"})])
    output_schema = {
        "type": "object",
        "required": ["count"],
        "properties": {"count": {"type": "integer"}},
    }
    runner, plan = build(
        tmp_path,
        action_workflow(),
        executor,
        manifest(output_schema=output_schema),
    )
    result = asyncio.run(runner.run(plan))
    assert result.error["code"] == "INVALID_OUTPUT"
    assert result.error["details"]["attempt"] == 1
    assert result.error["details"]["nodeId"] == "step"
    events = read_events(tmp_path / "runs", result.run_id)
    failed = [event for event in events if event["type"] == "stepFailed"]
    assert failed[0]["payload"]["error"]["code"] == "INVALID_OUTPUT"


def test_step_timeout_cancels_and_awaits_executor_task(tmp_path):
    executor = ScriptedExecutor()
    runner, plan = build(
        tmp_path,
        action_workflow(timeout_seconds=0.01),
        executor,
    )
    result = asyncio.run(runner.run(plan))
    assert result.error["code"] == "TIMEOUT"
    assert result.error["details"]["attempt"] == 1
    assert executor.finished.is_set()


def test_workflow_timeout_awaits_active_executor_cleanup(tmp_path):
    executor = ScriptedExecutor()
    workflow = action_workflow(timeout_seconds=1)
    workflow["timeout_seconds"] = 0.2
    runner, plan = build(tmp_path, workflow, executor)
    result = asyncio.run(runner.run(plan))
    assert result.error["code"] == "TIMEOUT"
    assert executor.started.is_set()
    assert executor.finished.is_set()


def test_retry_requires_retryable_result_and_manifest(tmp_path):
    retryable_failure = CommandResult.failure(ErrorCode.EXECUTOR_FAILED, "retry", retryable=True)
    executor = ScriptedExecutor([retryable_failure, CommandResult.success()])
    runner, plan = build(
        tmp_path,
        action_workflow(
            retry_count=1,
            retry_backoff_seconds=0,
            retry_backoff_max_seconds=0,
        ),
        executor,
        manifest(retryable=True),
    )
    result = asyncio.run(runner.run(plan))
    assert result.status.value == "succeeded"
    assert executor.calls == 2
    events = read_events(tmp_path / "runs", result.run_id)
    attempts = [event for event in events if event["type"] == "stepAttemptStarted"]
    retries = [event for event in events if event["type"] == "stepRetried"]
    assert [event["payload"]["attempt"] for event in attempts] == [1, 2]
    assert retries[0]["payload"] == {
        "attempt": 1,
        "nextAttempt": 2,
        "backoffSeconds": 0.0,
    }

    other = tmp_path / "other"
    other.mkdir()
    executor = ScriptedExecutor([retryable_failure, CommandResult.success()])
    runner, plan = build(
        other,
        action_workflow(retry_count=1, retry_backoff_seconds=0),
        executor,
        manifest(retryable=False),
    )
    result = asyncio.run(runner.run(plan))
    assert result.status.value == "failed"
    assert executor.calls == 1


def test_retry_backoff_cannot_exceed_workflow_deadline(tmp_path):
    executor = ScriptedExecutor(
        [CommandResult.failure(ErrorCode.EXECUTOR_FAILED, "retry", retryable=True)]
    )
    workflow = action_workflow(
        retry_count=1,
        retry_backoff_seconds=0.2,
        retry_backoff_max_seconds=0.2,
    )
    workflow["timeout_seconds"] = 0.05
    runner, plan = build(tmp_path, workflow, executor, manifest(retryable=True))
    result = asyncio.run(runner.run(plan))
    assert result.error["code"] == "TIMEOUT"
    assert executor.calls == 1


def test_terminal_evidence_failure_never_returns_success(tmp_path, monkeypatch):
    executor = ScriptedExecutor([CommandResult.success()])
    runner, plan = build(tmp_path, action_workflow(), executor)

    async def fail_write_result(run_dir, result):
        raise OSError("disk full")

    monkeypatch.setattr(runner, "_write_result", fail_write_result)
    with pytest.raises(RunPersistenceError) as captured:
        asyncio.run(runner.run(plan))
    assert captured.value.result is not None
    assert captured.value.result.status.value == "failed"
    assert captured.value.result.error["code"] == "PERSISTENCE_FAILED"


def test_event_failure_stops_execution_and_propagates(tmp_path, monkeypatch):
    executor = ScriptedExecutor([CommandResult.success()])
    runner, plan = build(tmp_path, action_workflow(), executor)
    original = EventWriter._append_line
    calls = 0

    def fail_second_event(writer, line):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("event store unavailable")
        original(writer, line)

    monkeypatch.setattr(EventWriter, "_append_line", fail_second_event)
    with pytest.raises(RunPersistenceError):
        asyncio.run(runner.run(plan))
    assert executor.calls == 0


def test_resume_skips_completed_steps_and_reuses_checkpoint(tmp_path):
    executor = ScriptedExecutor([CommandResult.success(outputs={"value": "first"})])
    runner, plan = build(tmp_path, action_workflow(), executor)
    result = asyncio.run(runner.run(plan))
    assert result.status.value == "succeeded"

    checkpoint = tmp_path / "runs" / result.run_id / "checkpoint.json"
    assert checkpoint.exists()

    resumed_executor = ScriptedExecutor([CommandResult.success(outputs={"value": "second"})])
    resumed_runner = Orchestrator(
        runner.catalog,
        ExecutorRegistry({"scripted": resumed_executor}),
        tmp_path / "runs",
    )

    async def resume():
        return await resumed_runner.resume(plan, result.run_id).wait()

    resumed = asyncio.run(resume())
    assert resumed.status.value == "succeeded"
    assert resumed_executor.calls == 0
