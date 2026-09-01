import asyncio
import json
from pathlib import Path

import pytest

from rpa_core.catalog import load_catalog
from rpa_core.compiler import WorkflowCompiler
from rpa_core.executors import ExecutorRegistry
from rpa_core.executors.base import CommandExecutor
from rpa_core.model.command import CommandResult
from rpa_core.model.workflow import Workflow
from rpa_core.runtime import Orchestrator
from rpa_core.runtime.checkpoint import CheckpointError


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


def manifest():
    return {
        "id": "test.action",
        "version": "1.0.0",
        "executor": "scripted",
        "kind": "action",
        "risk": "read",
        "capabilities": [],
        "resources": [],
        "stability": "stable",
        "effect": {"kind": "pure", "replay": "safe", "idempotency": "none"},
        "input_schema": {"type": "object", "additionalProperties": False},
        "output_schema": {"type": "object"},
        "errors": ["EXECUTOR_FAILED", "TIMEOUT", "CANCELLED"],
        "implementation": {"handler": "test:execute"},
        "default_timeout_seconds": 1,
        "retryable": False,
    }


def build(tmp_path, raw_workflow, executor):
    commands = tmp_path / "commands"
    commands.mkdir()
    (commands / "action.json").write_text(json.dumps(manifest()), encoding="utf-8")
    catalog = load_catalog(commands)
    workflow = Workflow.model_validate(raw_workflow)
    plan = WorkflowCompiler(catalog).compile(workflow, set())
    runner = Orchestrator(
        catalog,
        ExecutorRegistry({"scripted": executor}),
        tmp_path / "runs",
    )
    return runner, plan


def two_step_workflow(**workflow_overrides):
    workflow = {
        "id": "pause-continue",
        "name": "pause-continue",
        "root": {
            "type": "sequence",
            "id": "root",
            "children": [
                {"type": "action", "id": "stepOne", "command": "test.action", "with": {}},
                {"type": "action", "id": "stepTwo", "command": "test.action", "with": {}},
            ],
        },
    }
    workflow.update(workflow_overrides)
    return workflow


def read_events(runs_root: Path, run_id: str):
    lines = (runs_root / run_id / "events.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class PauseOnFirstCallExecutor(CommandExecutor):
    def __init__(self, results=()):
        self.results = list(results)
        self.calls = 0
        self.holder: dict = {}

    async def execute(self, invocation, cancellation):
        self.calls += 1
        if self.calls == 1:
            self.holder["handle"].pause()
        if self.results:
            return self.results.pop(0)
        try:
            await asyncio.Event().wait()
        finally:
            pass


def test_pause_stops_at_next_boundary_and_attempt_completes_naturally(tmp_path):
    executor = PauseOnFirstCallExecutor(results=[CommandResult.success()])
    runner, plan = build(tmp_path, two_step_workflow(), executor)

    async def scenario():
        executor.holder["handle"] = runner.start(plan)
        return await executor.holder["handle"].wait()

    result = asyncio.run(scenario())
    assert result.status.value == "paused"
    assert result.error is None
    assert executor.calls == 1

    events = read_events(tmp_path / "runs", result.run_id)
    types = [event["type"] for event in events]
    node_ids = [event["node_id"] for event in events if event["node_id"]]
    assert "stepCompleted" in types
    assert "stepTwo" not in node_ids
    assert types[-1] == "runFinished"
    paused = [event for event in events if event["type"] == "runPaused"]
    assert paused[0]["payload"]["nodeId"] == "stepTwo"
    finished = [event for event in events if event["type"] == "runFinished"]
    assert finished[0]["payload"]["status"] == "paused"

    assert read_json(tmp_path / "runs" / result.run_id / "result.json")["status"] == "paused"
    assert read_json(tmp_path / "runs" / result.run_id / "checkpoint.json")[
        "completedSteps"
    ] == ["root/stepOne"]


def test_paused_run_resumes_and_skips_completed_step(tmp_path):
    executor = PauseOnFirstCallExecutor(results=[CommandResult.success()])
    runner, plan = build(tmp_path, two_step_workflow(), executor)

    async def scenario():
        executor.holder["handle"] = runner.start(plan)
        return await executor.holder["handle"].wait()

    result = asyncio.run(scenario())
    assert result.status.value == "paused"

    resumed_executor = ScriptedExecutor([CommandResult.success()])
    resumed_runner = Orchestrator(
        runner.catalog,
        ExecutorRegistry({"scripted": resumed_executor}),
        tmp_path / "runs",
    )

    async def resume():
        return await resumed_runner.resume(plan, result.run_id).wait()

    resumed = asyncio.run(resume())
    assert resumed.status.value == "succeeded"
    assert resumed_executor.calls == 1
    assert result.run_id == resumed.run_id
    resumed_events = read_events(tmp_path / "runs", resumed.run_id)
    started_nodes = [
        event["node_id"] for event in resumed_events if event["type"] == "stepStarted"
    ]
    assert started_nodes == ["stepOne", "stepTwo"]
    completed_nodes = [
        event["node_id"] for event in resumed_events if event["type"] == "stepCompleted"
    ]
    assert completed_nodes == ["stepOne", "stepTwo"]


def test_pause_before_first_node_records_no_steps(tmp_path):
    executor = ScriptedExecutor([CommandResult.success()])
    runner, plan = build(tmp_path, two_step_workflow(), executor)

    async def scenario():
        handle = runner.start(plan)
        handle.pause()
        return await handle.wait()

    result = asyncio.run(scenario())
    assert result.status.value == "paused"
    assert executor.calls == 0
    assert read_json(tmp_path / "runs" / result.run_id / "checkpoint.json")[
        "completedSteps"
    ] == []


def test_pause_with_all_steps_completed_still_succeeds(tmp_path):
    first = ScriptedExecutor([CommandResult.success(), CommandResult.success()])
    runner, plan = build(tmp_path, two_step_workflow(), first)
    result = asyncio.run(runner.run(plan))
    assert result.status.value == "succeeded"

    second = ScriptedExecutor()
    resumed_runner = Orchestrator(
        runner.catalog,
        ExecutorRegistry({"scripted": second}),
        tmp_path / "runs",
    )

    async def resume():
        handle = resumed_runner.resume(plan, result.run_id)
        handle.pause()
        return await handle.wait()

    resumed = asyncio.run(resume())
    assert resumed.status.value == "succeeded"
    assert second.calls == 0


def test_cancel_wins_over_pending_pause(tmp_path):
    executor = ScriptedExecutor()
    runner, plan = build(tmp_path, two_step_workflow(), executor)

    async def scenario():
        handle = runner.start(plan)
        handle.pause()
        return await handle.cancel_and_wait()

    result = asyncio.run(scenario())
    assert result.status.value == "cancelled"
    assert executor.calls == 0


def test_workflow_deadline_beats_pending_pause_mid_attempt(tmp_path):
    holder: dict = {}

    class PauseThenSleepExecutor(CommandExecutor):
        def __init__(self):
            self.calls = 0

        async def execute(self, invocation, cancellation):
            self.calls += 1
            holder["handle"].pause()
            await asyncio.sleep(0.3)
            return CommandResult.success()

    slow = PauseThenSleepExecutor()
    runner, plan = build(tmp_path, two_step_workflow(timeout_seconds=0.1), slow)

    async def scenario():
        holder["handle"] = runner.start(plan)
        return await holder["handle"].wait()

    result = asyncio.run(scenario())
    assert result.status.value == "failed"
    assert result.error["code"] == "TIMEOUT"
    assert slow.calls == 1


def test_m3_gates_apply_to_paused_run_checkpoint(tmp_path):
    executor = PauseOnFirstCallExecutor(results=[CommandResult.success()])
    runner, plan = build(tmp_path, two_step_workflow(), executor)

    async def scenario():
        executor.holder["handle"] = runner.start(plan)
        return await executor.holder["handle"].wait()

    result = asyncio.run(scenario())
    assert result.status.value == "paused"

    checkpoint_path = tmp_path / "runs" / result.run_id / "checkpoint.json"
    checkpoint_path.write_text("{corrupted", encoding="utf-8")

    async def resume():
        return runner.resume(plan, result.run_id)

    with pytest.raises(CheckpointError):
        asyncio.run(resume())
