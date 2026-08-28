import asyncio
import json

from rpa_core.catalog import load_catalog
from rpa_core.compiler import WorkflowCompiler
from rpa_core.executors import ExecutorRegistry
from rpa_core.executors.base import CommandExecutor
from rpa_core.model.command import CommandResult
from rpa_core.model.workflow import Workflow
from rpa_core.runtime import Orchestrator


class FakeExecutor(CommandExecutor):
    async def execute(self, invocation, cancellation):
        if cancellation.is_set():
            return CommandResult(status="cancelled")
        return CommandResult.success(outputs={"value": invocation.inputs.get("value", "ok")})


def test_orchestrator_writes_terminal_evidence(tmp_path):
    raw = {
        "id": "runtime-test",
        "name": "runtime-test",
        "root": {"type": "action", "id": "step", "command": "data.fake", "with": {"value": "done"}},
    }
    manifest = {
        "id": "data.fake",
        "version": "1.0.0",
        "executor": "fake",
        "kind": "transform",
        "risk": "read",
        "capabilities": [],
        "resources": [],
        "stability": "stable",
        "effect": {"kind": "pure", "replay": "safe", "idempotency": "none"},
        "input_schema": {"type": "object"},
        "output_schema": {
            "type": "object",
            "required": ["value"],
            "properties": {"value": {"type": "string"}},
        },
        "errors": ["EXECUTOR_FAILED"],
        "implementation": {"handler": "fake:execute"},
    }
    commands = tmp_path / "commands"
    commands.mkdir()
    (commands / "fake.json").write_text(json.dumps(manifest), encoding="utf-8")
    catalog = load_catalog(commands)
    plan = WorkflowCompiler(catalog).compile(Workflow.model_validate(raw), set())

    async def run():
        runner = Orchestrator(
            catalog, ExecutorRegistry({"fake": FakeExecutor()}), tmp_path / "runs"
        )
        return await runner.run(plan)

    result = asyncio.run(run())
    assert result.status.value == "succeeded"
    run_dir = tmp_path / "runs" / result.run_id
    assert (run_dir / "events.jsonl").exists()
    assert (run_dir / "result.json").exists()
    assert '"type":"runFinished"' in (run_dir / "events.jsonl").read_text(encoding="utf-8")
