import asyncio
import json

import pytest

from rpa_core.catalog import load_catalog
from rpa_core.compiler import WorkflowCompileError, WorkflowCompiler
from rpa_core.executors import ExecutorRegistry
from rpa_core.executors.base import CommandExecutor
from rpa_core.model.command import CommandResult
from rpa_core.model.errors import ErrorCode
from rpa_core.model.workflow import Workflow
from rpa_core.runtime import Orchestrator


class GuardExecutor(CommandExecutor):
    async def execute(self, invocation, cancellation):
        if invocation.command_id == "test.fail":
            return CommandResult.failure(code=ErrorCode.EXECUTOR_FAILED, message="expected failure")
        if invocation.command_id == "test.sleep":
            await asyncio.sleep(1)
        return CommandResult.success(outputs={"value": invocation.inputs.get("value", "ok")})


def make_catalog(tmp_path, commands):
    directory = tmp_path / "commands"
    directory.mkdir()
    for command in commands:
        (directory / f"{command['id'].split('.')[-1]}.json").write_text(
            json.dumps(command), encoding="utf-8"
        )
    return load_catalog(directory)


def manifest(command_id, *, executor="guard", output=None):
    return {
        "id": command_id,
        "version": "1.0.0",
        "executor": executor,
        "kind": "action",
        "risk": "read",
        "capabilities": [],
        "resources": [],
        "stability": "stable",
        "effect": {"kind": "pure", "replay": "safe", "idempotency": "none"},
        "input_schema": {"type": "object"},
        "output_schema": output or {"type": "object"},
        "errors": ["EXECUTOR_FAILED", "TIMEOUT", "CANCELLED"],
        "implementation": {"handler": "guard:execute"},
    }


def test_try_error_variable_is_lexically_scoped(tmp_path):
    catalog = make_catalog(tmp_path, [manifest("test.fail")])
    workflow = Workflow.model_validate(
        {
            "id": "try-scope",
            "name": "try-scope",
            "root": {
                "type": "sequence",
                "id": "root",
                "children": [
                    {
                        "type": "try",
                        "id": "try",
                        "error_var": "caught",
                        "children": [
                            {"type": "action", "id": "fail", "command": "test.fail"},
                        ],
                        "catch": [],
                    },
                    {"type": "return", "id": "return", "value": "${caught}"},
                ],
            },
        }
    )
    with pytest.raises(WorkflowCompileError, match="Unsupported reference root"):
        WorkflowCompiler(catalog).compile(workflow, set())


def test_workflow_timeout_has_terminal_failure(tmp_path):
    catalog = make_catalog(tmp_path, [manifest("test.sleep")])
    workflow = Workflow.model_validate(
        {
            "id": "workflow-timeout",
            "name": "workflow-timeout",
            "timeout_seconds": 0.01,
            "root": {"type": "action", "id": "sleep", "command": "test.sleep"},
        }
    )
    plan = WorkflowCompiler(catalog).compile(workflow, set())

    async def run():
        runner = Orchestrator(
            catalog, ExecutorRegistry({"guard": GuardExecutor()}), tmp_path / "runs"
        )
        return await runner.run(plan)

    result = asyncio.run(run())
    assert result.status.value == "failed"
    assert result.error["code"] == "TIMEOUT"


def test_for_each_accepts_tuple_but_not_string(tmp_path):
    catalog = make_catalog(tmp_path, [manifest("test.echo")])
    workflow = Workflow.model_validate(
        {
            "id": "tuple-loop",
            "name": "tuple-loop",
            "root": {
                "type": "forEach",
                "id": "loop",
                "items": ("a", "b"),
                "children": [
                    {
                        "type": "action",
                        "id": "echo",
                        "command": "test.echo",
                        "with": {"value": "${loop.item}"},
                    }
                ],
            },
        }
    )
    plan = WorkflowCompiler(catalog).compile(workflow, set())

    async def run():
        runner = Orchestrator(
            catalog, ExecutorRegistry({"guard": GuardExecutor()}), tmp_path / "runs"
        )
        return await runner.run(plan)

    result = asyncio.run(run())
    assert result.status.value == "succeeded"
