import asyncio
import json
from pathlib import Path

import pytest

from rpa_core.catalog import load_catalog
from rpa_core.compiler import WorkflowCompiler
from rpa_core.executors import ExecutorRegistry
from rpa_core.executors.base import CommandExecutor
from rpa_core.model.command import CommandResult, EffectKind, EffectRecord, EffectStatus
from rpa_core.model.errors import ErrorCode
from rpa_core.model.workflow import Workflow
from rpa_core.runtime import Orchestrator
from rpa_core.runtime.checkpoint import CheckpointError, CheckpointStore, RecoveryRequiredError
from rpa_core.runtime.events import EventWriter, RunPersistenceError


class ScriptedExecutor(CommandExecutor):
    def __init__(self, results=None):
        self.results = list(results or [])
        self.calls = 0

    async def execute(self, invocation, cancellation):
        self.calls += 1
        if self.results:
            return self.results.pop(0)
        try:
            await asyncio.Event().wait()
        finally:
            pass


def manifest(*, effect=None, retryable=False):
    return {
        "id": "test.action",
        "version": "1.0.0",
        "executor": "scripted",
        "kind": "action",
        "risk": "read",
        "capabilities": [],
        "resources": [],
        "stability": "stable",
        "effect": effect or {"kind": "pure", "replay": "safe", "idempotency": "none"},
        "input_schema": {"type": "object", "additionalProperties": False},
        "output_schema": {"type": "object"},
        "errors": ["EXECUTOR_FAILED", "TIMEOUT", "CANCELLED"],
        "implementation": {"handler": "test:execute"},
        "default_timeout_seconds": 1,
        "retryable": retryable,
    }


def build(tmp_path, raw_workflow, executor, command_manifest=None):
    commands = tmp_path / "commands"
    commands.mkdir()
    (commands / "action.json").write_text(
        json.dumps(command_manifest or manifest()), encoding="utf-8"
    )
    catalog = load_catalog(commands)
    workflow = Workflow.model_validate(raw_workflow)
    plan = WorkflowCompiler(catalog).compile(workflow, set())
    runner = Orchestrator(
        catalog,
        ExecutorRegistry({"scripted": executor}),
        tmp_path / "runs",
    )
    return runner, plan


def action_workflow():
    return {
        "id": "checkpoint-recovery",
        "name": "checkpoint-recovery",
        "root": {"type": "action", "id": "step", "command": "test.action", "with": {}},
    }


def read_checkpoint(runs_root: Path, run_id: str):
    path = runs_root / run_id / "checkpoint.json"
    return json.loads(path.read_text(encoding="utf-8"))


def read_events(runs_root: Path, run_id: str):
    lines = (runs_root / run_id / "events.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def test_crash_after_checkpoint_before_step_completed_event_skips_step_on_resume(
    tmp_path, monkeypatch
):
    executor = ScriptedExecutor([CommandResult.success()])
    runner, plan = build(tmp_path, action_workflow(), executor)
    original = EventWriter.append

    async def crash_on_step_completed(self, event_type, **kwargs):
        if event_type == "stepCompleted":
            raise RunPersistenceError("injected crash after checkpoint")
        return await original(self, event_type, **kwargs)

    monkeypatch.setattr(EventWriter, "append", crash_on_step_completed)
    with pytest.raises(RunPersistenceError):
        asyncio.run(runner.run(plan))

    run_id = next((tmp_path / "runs").iterdir()).name
    checkpoint = read_checkpoint(tmp_path / "runs", run_id)
    assert checkpoint["completedSteps"] == ["step"]

    resumed_executor = ScriptedExecutor()
    resumed_runner = Orchestrator(
        runner.catalog,
        ExecutorRegistry({"scripted": resumed_executor}),
        tmp_path / "runs",
    )

    async def resume():
        return await resumed_runner.resume(plan, run_id).wait()

    resumed = asyncio.run(resume())
    assert resumed.status.value == "succeeded"
    assert resumed_executor.calls == 0
    events = read_events(tmp_path / "runs", run_id)
    assert [event["type"] for event in events if event["type"].startswith("run")] == [
        "runStarted",
        "runResumed",
        "runFinished",
    ]


def test_crash_before_checkpoint_replays_safe_step_on_resume(tmp_path, monkeypatch):
    executor = ScriptedExecutor([CommandResult.success() for _ in range(3)])
    workflow = {
        "id": "checkpoint-recovery",
        "name": "checkpoint-recovery",
        "root": {
            "type": "sequence",
            "id": "root",
            "children": [
                {"type": "action", "id": "stepOne", "command": "test.action", "with": {}},
                {"type": "action", "id": "stepTwo", "command": "test.action", "with": {}},
            ],
        },
    }
    runner, plan = build(tmp_path, workflow, executor)
    real_write = CheckpointStore.write
    writes = {"count": 0}

    def flaky_write(self, data):
        writes["count"] += 1
        if writes["count"] == 2:
            raise CheckpointError("injected checkpoint persistence failure")
        return real_write(self, data)

    monkeypatch.setattr(CheckpointStore, "write", flaky_write)
    result = asyncio.run(runner.run(plan))
    assert result.status.value == "recovery_required"
    assert result.error["code"] == "PERSISTENCE_FAILED"
    assert executor.calls == 2
    assert read_checkpoint(tmp_path / "runs", result.run_id)["completedSteps"] == [
        "root/stepOne"
    ]

    async def resume():
        return await runner.resume(plan, result.run_id).wait()

    resumed = asyncio.run(resume())
    assert resumed.status.value == "succeeded"
    assert executor.calls == 3
    assert read_checkpoint(tmp_path / "runs", result.run_id)["completedSteps"] == [
        "root/stepOne",
        "root/stepTwo",
    ]


def test_corrupted_checkpoint_fails_resume_explicitly(tmp_path):
    runner, plan = build(tmp_path, action_workflow(), ScriptedExecutor([CommandResult.success()]))
    run_dir = tmp_path / "runs" / "crash-run"
    run_dir.mkdir(parents=True)
    checkpoint = run_dir / "checkpoint.json"

    cases = [
        ("{not json", "Failed to read checkpoint"),
        ("[]", "must be a JSON object"),
        (json.dumps({"version": 99}), "Unsupported checkpoint version"),
        (json.dumps({"version": 1, "workflowId": "x"}), "must be a non-empty string"),
    ]
    for payload, message in cases:
        checkpoint.write_text(payload, encoding="utf-8")
        with pytest.raises(CheckpointError, match=message):
            asyncio.run(runner.resume(plan, "crash-run"))


def test_resume_rejects_catalog_digest_mismatch(tmp_path):
    executor = ScriptedExecutor([CommandResult.success()])
    runner, plan = build(tmp_path, action_workflow(), executor)
    result = asyncio.run(runner.run(plan))

    commands_v2 = tmp_path / "commands-v2"
    commands_v2.mkdir()
    changed = manifest()
    changed["input_schema"] = {
        "type": "object",
        "properties": {"extra": {"type": "string"}},
        "additionalProperties": False,
    }
    (commands_v2 / "action.json").write_text(json.dumps(changed), encoding="utf-8")
    plan_v2 = WorkflowCompiler(load_catalog(commands_v2)).compile(
        Workflow.model_validate(action_workflow()), set()
    )
    assert plan_v2.catalog_digest != plan.catalog_digest

    with pytest.raises(CheckpointError, match="Catalog digest mismatch"):
        asyncio.run(runner.resume(plan_v2, result.run_id))


def unknown_effect_failure():
    return CommandResult.failure(
        code=ErrorCode.EXECUTOR_FAILED,
        message="outcome unknown",
        retryable=True,
        effects=[
            EffectRecord(
                effectId="e" * 64,
                kind=EffectKind.IDEMPOTENT_WRITE,
                status=EffectStatus.UNKNOWN,
                resource="file:out.json",
            )
        ],
    )


def test_unknown_effect_ends_indeterminate_and_never_retries(tmp_path):
    executor = ScriptedExecutor([unknown_effect_failure(), CommandResult.success()])
    workflow = action_workflow()
    workflow["root"]["retry_count"] = 1
    runner, plan = build(
        tmp_path,
        workflow,
        executor,
        manifest(
            effect={
                "kind": "idempotent-write",
                "replay": "idempotent",
                "idempotency": "derived",
            },
            retryable=True,
        ),
    )
    result = asyncio.run(runner.run(plan))
    assert result.status.value == "indeterminate"
    assert result.error["code"] == "EXECUTOR_FAILED"
    assert result.error["details"]["effects"][0]["status"] == "unknown"
    assert executor.calls == 1
    events = read_events(tmp_path / "runs", result.run_id)
    failed = [event for event in events if event["type"] == "stepFailed"]
    assert failed[0]["payload"]["effects"][0]["status"] == "unknown"
    finished = [event for event in events if event["type"] == "runFinished"]
    assert finished[0]["payload"]["status"] == "indeterminate"
    persisted = json.loads(
        (tmp_path / "runs" / result.run_id / "result.json").read_text(encoding="utf-8")
    )
    assert persisted["status"] == "indeterminate"


def test_indeterminate_resume_requires_manual_ack(tmp_path):
    committed = EffectRecord(
        effectId="f" * 64,
        kind=EffectKind.IDEMPOTENT_WRITE,
        status=EffectStatus.COMMITTED,
        resource="file:out.json",
    )
    executor = ScriptedExecutor(
        [unknown_effect_failure(), CommandResult.success(effects=[committed])]
    )
    runner, plan = build(
        tmp_path,
        action_workflow(),
        executor,
        manifest(
            effect={
                "kind": "idempotent-write",
                "replay": "idempotent",
                "idempotency": "derived",
            },
            retryable=True,
        ),
    )
    result = asyncio.run(runner.run(plan))
    assert result.status.value == "indeterminate"

    with pytest.raises(RecoveryRequiredError, match="indeterminate"):
        asyncio.run(runner.resume(plan, result.run_id))

    async def resume():
        return await runner.resume(plan, result.run_id, allow_indeterminate=True).wait()

    resumed = asyncio.run(resume())
    assert resumed.status.value == "succeeded"
    assert executor.calls == 2


def test_foreach_resume_executes_remaining_iterations(tmp_path, monkeypatch):
    executor = ScriptedExecutor([CommandResult.success() for _ in range(4)])
    workflow = {
        "id": "foreach-recovery",
        "name": "foreach-recovery",
        "inputs": {"items": ["a", "b", "c"]},
        "root": {
            "type": "forEach",
            "id": "loop",
            "items": "${inputs.items}",
            "children": [{"type": "action", "id": "step", "command": "test.action", "with": {}}],
        },
    }
    runner, plan = build(tmp_path, workflow, executor)
    real_write = CheckpointStore.write
    writes = {"count": 0}

    def flaky_write(self, data):
        writes["count"] += 1
        if writes["count"] == 2:
            raise CheckpointError("injected checkpoint persistence failure")
        return real_write(self, data)

    monkeypatch.setattr(CheckpointStore, "write", flaky_write)
    result = asyncio.run(runner.run(plan))
    assert result.status.value == "recovery_required"
    assert executor.calls == 2
    assert read_checkpoint(tmp_path / "runs", result.run_id)["completedSteps"] == [
        "loop/#0/step"
    ]

    async def resume():
        return await runner.resume(plan, result.run_id).wait()

    resumed = asyncio.run(resume())
    assert resumed.status.value == "succeeded"
    assert executor.calls == 4
    assert read_checkpoint(tmp_path / "runs", result.run_id)["completedSteps"] == [
        "loop/#0/step",
        "loop/#1/step",
        "loop/#2/step",
    ]
