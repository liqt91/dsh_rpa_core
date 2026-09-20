"""M24 断点与单步契约（S1）：断点执行前停、resume 消费断点、单步只前进一个节点。

复用 `test_pause.py` 的夹具思路（scripted executor + 两/三步流程），不依赖真实浏览器。
"""

import asyncio
import json
from pathlib import Path

from rpa_core.catalog import load_catalog
from rpa_core.compiler import WorkflowCompiler
from rpa_core.executors import ExecutorRegistry
from rpa_core.executors.base import CommandExecutor
from rpa_core.model.command import CommandResult
from rpa_core.model.workflow import Workflow
from rpa_core.runtime import Orchestrator


class ScriptedExecutor(CommandExecutor):
    def __init__(self, results=None):
        self.results = list(results or [])
        self.calls = 0

    async def execute(self, invocation, cancellation):
        self.calls += 1
        if self.results:
            return self.results.pop(0)
        return CommandResult.success()


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


def build(tmp_path, executor, node_ids=("stepOne", "stepTwo", "stepThree")):
    commands = tmp_path / "commands"
    commands.mkdir(exist_ok=True)
    (commands / "action.json").write_text(json.dumps(manifest()), encoding="utf-8")
    catalog = load_catalog(commands)
    workflow = Workflow.model_validate(
        {
            "id": "debug",
            "name": "debug",
            "root": {
                "type": "sequence",
                "id": "root",
                "children": [
                    {"type": "action", "id": node_id, "command": "test.action", "with": {}}
                    for node_id in node_ids
                ],
            },
        }
    )
    plan = WorkflowCompiler(catalog).compile(workflow, set())
    runner = Orchestrator(
        catalog, ExecutorRegistry({"scripted": executor}), tmp_path / "runs"
    )
    return runner, plan


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_events(runs_root: Path, run_id: str) -> list[dict]:
    lines = (runs_root / run_id / "events.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def test_breakpoint_stops_before_node_and_records_reason(tmp_path):
    """断点命中：在节点**执行前**停下（该节点未执行、不在 completedSteps），
    检查点记录 breakpoints / consumedBreakpoints / pauseReason / pausedAtNode。"""
    executor = ScriptedExecutor([CommandResult.success()])
    runner, plan = build(tmp_path, executor)

    async def scenario():
        return await runner.start(plan, breakpoints=["stepTwo"]).wait()

    result = asyncio.run(scenario())
    assert result.status.value == "paused"
    assert executor.calls == 1  # 只有 stepOne 执行了

    checkpoint = read_json(tmp_path / "runs" / result.run_id / "checkpoint.json")
    assert checkpoint["completedSteps"] == ["root/stepOne"]
    assert checkpoint["breakpoints"] == ["stepTwo"]
    assert checkpoint["consumedBreakpoints"] == ["stepTwo"]
    assert checkpoint["pauseReason"] == "breakpoint"
    assert checkpoint["pausedAtNode"] == "stepTwo"

    events = read_events(tmp_path / "runs", result.run_id)
    paused = [event for event in events if event["type"] == "runPaused"][0]
    assert paused["payload"]["nodeId"] == "stepTwo"
    assert paused["payload"]["reason"] == "breakpoint"


def test_resume_consumes_breakpoint_and_continues(tmp_path):
    """resume 后越过已命中的断点（不原地反复暂停），后续节点正常执行。"""
    first = ScriptedExecutor([CommandResult.success()])
    runner, plan = build(tmp_path, first)

    async def scenario():
        return await runner.start(plan, breakpoints=["stepTwo"]).wait()

    paused = asyncio.run(scenario())
    assert paused.status.value == "paused"

    resumed_executor = ScriptedExecutor([CommandResult.success(), CommandResult.success()])
    resumed_runner = Orchestrator(
        runner.catalog,
        ExecutorRegistry({"scripted": resumed_executor}),
        tmp_path / "runs",
    )

    async def resume():
        return await resumed_runner.resume(plan, paused.run_id).wait()

    resumed = asyncio.run(resume())
    assert resumed.status.value == "succeeded"
    assert resumed_executor.calls == 2  # stepTwo + stepThree，stepOne 不重跑

    events = read_events(tmp_path / "runs", resumed.run_id)
    completed = [
        event["node_id"] for event in events if event["type"] == "stepCompleted"
    ]
    assert completed == ["stepOne", "stepTwo", "stepThree"]


def test_second_breakpoint_hits_after_resume(tmp_path):
    """多个断点：第一个停下 → resume 后命中第二个。"""
    first = ScriptedExecutor([CommandResult.success()])
    runner, plan = build(tmp_path, first)

    async def scenario():
        return await runner.start(plan, breakpoints=["stepOne", "stepThree"]).wait()

    paused = asyncio.run(scenario())
    assert paused.status.value == "paused"
    assert first.calls == 0  # stepOne 执行前就停了
    checkpoint = read_json(tmp_path / "runs" / paused.run_id / "checkpoint.json")
    assert checkpoint["pausedAtNode"] == "stepOne"

    second = ScriptedExecutor([CommandResult.success(), CommandResult.success()])
    resumed_runner = Orchestrator(
        runner.catalog, ExecutorRegistry({"scripted": second}), tmp_path / "runs"
    )

    async def resume():
        return await resumed_runner.resume(plan, paused.run_id).wait()

    resumed = asyncio.run(resume())
    assert resumed.status.value == "paused"
    assert second.calls == 2  # stepOne + stepTwo 执行，stepThree 前停
    checkpoint = read_json(tmp_path / "runs" / resumed.run_id / "checkpoint.json")
    assert checkpoint["pausedAtNode"] == "stepThree"
    assert checkpoint["pauseReason"] == "breakpoint"
    assert checkpoint["consumedBreakpoints"] == ["stepOne", "stepThree"]


def test_step_resume_advances_exactly_one_node(tmp_path):
    """单步：resume(step=True) 只执行一个节点，然后在下一个边界再次暂停（reason=step）。"""
    first = ScriptedExecutor([CommandResult.success()])
    runner, plan = build(tmp_path, first)

    async def scenario():
        return await runner.start(plan, breakpoints=["stepOne"]).wait()

    paused = asyncio.run(scenario())
    assert paused.status.value == "paused"
    assert first.calls == 0

    stepping = ScriptedExecutor([CommandResult.success()])
    step_runner = Orchestrator(
        runner.catalog, ExecutorRegistry({"scripted": stepping}), tmp_path / "runs"
    )

    async def step():
        return await step_runner.resume(plan, paused.run_id, step=True).wait()

    stepped = asyncio.run(step())
    assert stepped.status.value == "paused"
    assert stepping.calls == 1  # 只前进一个节点
    checkpoint = read_json(tmp_path / "runs" / stepped.run_id / "checkpoint.json")
    assert checkpoint["completedSteps"] == ["root/stepOne"]
    assert checkpoint["pausedAtNode"] == "stepTwo"
    assert checkpoint["pauseReason"] == "step"


def test_no_breakpoints_runs_to_completion(tmp_path):
    """无断点时行为与既有一致（回归保护）。"""
    executor = ScriptedExecutor([CommandResult.success()] * 3)
    runner, plan = build(tmp_path, executor)
    result = asyncio.run(runner.run(plan))
    assert result.status.value == "succeeded"
    assert executor.calls == 3


def test_legacy_checkpoint_without_breakpoint_fields_resumes(tmp_path):
    """兼容：旧检查点（无 breakpoints/consumedBreakpoints 字段）仍可 resume。"""
    executor = ScriptedExecutor([CommandResult.success()] * 3)
    runner, plan = build(tmp_path, executor)
    result = asyncio.run(runner.run(plan))
    assert result.status.value == "succeeded"

    checkpoint_path = tmp_path / "runs" / result.run_id / "checkpoint.json"
    legacy = read_json(checkpoint_path)
    for field in ("breakpoints", "consumedBreakpoints", "pauseReason", "pausedAtNode"):
        legacy.pop(field, None)
    checkpoint_path.write_text(json.dumps(legacy), encoding="utf-8")

    resumed_executor = ScriptedExecutor()
    resumed_runner = Orchestrator(
        runner.catalog,
        ExecutorRegistry({"scripted": resumed_executor}),
        tmp_path / "runs",
    )

    async def resume():
        return await resumed_runner.resume(plan, result.run_id).wait()

    resumed = asyncio.run(resume())
    assert resumed.status.value == "succeeded"
    assert resumed_executor.calls == 0  # 全部已完成


def test_cli_breakpoints_parsing():
    """CLI `--breakpoints a,b` 解析：容忍空格与空段。"""
    from rpa_core.cli import _parse_breakpoints

    assert _parse_breakpoints(None) == []
    assert _parse_breakpoints("") == []
    assert _parse_breakpoints("a,b") == ["a", "b"]
    assert _parse_breakpoints(" a , ,b ") == ["a", "b"]
