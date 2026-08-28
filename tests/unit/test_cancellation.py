import asyncio
import json

from rpa_core.catalog import load_catalog
from rpa_core.compiler import WorkflowCompiler
from rpa_core.executors import ExecutorRegistry
from rpa_core.executors.base import CommandExecutor
from rpa_core.model.command import CommandResult
from rpa_core.model.workflow import Workflow
from rpa_core.runtime import Orchestrator


class BlockingExecutor(CommandExecutor):
    async def execute(self, invocation, cancellation):
        await cancellation.wait()
        return CommandResult(status="cancelled")


def test_cancelled_run_has_terminal_result(tmp_path):
    manifest = {
        "id": "test.block",
        "version": "1.0.0",
        "executor": "blocking",
        "kind": "action",
        "risk": "read",
        "capabilities": [],
        "resources": [],
        "stability": "stable",
        "effect": {"kind": "pure", "replay": "safe", "idempotency": "none"},
        "input_schema": {"type": "object", "additionalProperties": False},
        "output_schema": {"type": "object"},
        "errors": ["CANCELLED"],
        "implementation": {"handler": "test:block"},
    }
    commands = tmp_path / "commands"
    commands.mkdir()
    (commands / "block.json").write_text(json.dumps(manifest), encoding="utf-8")
    catalog = load_catalog(commands)
    workflow = Workflow.model_validate(
        {
            "id": "cancel-test",
            "name": "cancel-test",
            "root": {
                "type": "action",
                "id": "block",
                "command": "test.block",
                "with": {},
            },
        }
    )
    plan = WorkflowCompiler(catalog).compile(workflow, set())

    async def run():
        runner = Orchestrator(
            catalog,
            ExecutorRegistry({"blocking": BlockingExecutor()}),
            tmp_path / "runs",
        )
        handle = runner.start(plan)
        await asyncio.sleep(0)
        handle.cancel()
        return await handle.wait()

    result = asyncio.run(run())
    assert result.status.value == "cancelled"
    assert (tmp_path / "runs" / result.run_id / "result.json").exists()
