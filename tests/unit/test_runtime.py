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


class EchoExecutor(CommandExecutor):
    """把解析后的输入回显到 outputs，用于断言变量子路径解析贯穿运行时。"""

    async def execute(self, invocation, cancellation):
        inputs = invocation.inputs or {}
        return CommandResult.success(
            outputs={
                "value": inputs.get("ref"),
                "sessionId": inputs.get("sessionId", "made-in-echo"),
                "resourceType": "webPage",
            }
        )


def _manifest(command_id, output_schema):
    return {
        "id": command_id,
        "version": "1.0.0",
        "executor": "echo",
        "kind": "transform",
        "risk": "read",
        "capabilities": [],
        "resources": [],
        "stability": "stable",
        "effect": {"kind": "pure", "replay": "safe", "idempotency": "none"},
        "input_schema": {
            "type": "object",
            "properties": {"ref": {"type": ["string", "object"]}, "sessionId": {"type": "string"}},
        },
        "output_schema": output_schema,
        "errors": ["EXECUTOR_FAILED"],
        "implementation": {"handler": "echo:execute"},
    }


def test_orchestrator_resolves_output_name_subfield_reference(tmp_path):
    """output_name 节点输出存入 variables；后续节点经 ${var.field} 子路径解析到具体字段。"""
    commands = tmp_path / "commands"
    commands.mkdir()
    launch_out = {
        "type": "object",
        "required": ["sessionId", "resourceType"],
        "properties": {
            "sessionId": {"type": "string"},
            "resourceType": {"type": "string"},
        },
    }
    consume_out = {
        "type": "object",
        "required": ["value"],
        "properties": {"value": {"type": ["string", "object", "null"]}},
    }
    (commands / "produce.json").write_text(
        json.dumps(_manifest("data.produce", launch_out)), encoding="utf-8"
    )
    (commands / "consume.json").write_text(
        json.dumps(_manifest("data.consume", consume_out)), encoding="utf-8"
    )
    catalog = load_catalog(commands)

    raw = {
        "id": "var-subfield-test",
        "name": "var-subfield-test",
        "root": {
            "type": "sequence",
            "id": "root",
            "children": [
                {
                    "type": "action",
                    "id": "producer",
                    "command": "data.produce",
                    "output_name": "web_page1",
                    "with": {"sessionId": "sess-abc"},
                },
                {
                    "type": "action",
                    "id": "consumer",
                    "command": "data.consume",
                    "with": {"ref": "${web_page1.sessionId}"},
                },
            ],
        },
    }
    plan = WorkflowCompiler(catalog).compile(Workflow.model_validate(raw), set())

    async def run():
        runner = Orchestrator(
            catalog, ExecutorRegistry({"echo": EchoExecutor()}), tmp_path / "runs"
        )
        return await runner.run(plan)

    result = asyncio.run(run())
    assert result.status.value == "succeeded"
    events_path = tmp_path / "runs" / result.run_id / "events.jsonl"
    raw = events_path.read_text(encoding="utf-8").replace("\n", ",").rstrip(",")
    events = json.loads(f"[{raw}]")
    # 找到 consumer 的 stepCompleted 事件，验证其收到解析后的 sessionId 字符串
    consumer_events = [
        e for e in events if e.get("type") == "stepCompleted" and e.get("node_id") == "consumer"
    ]
    assert consumer_events, "consumer stepCompleted event missing"
    # EchoExecutor 返回 consumer 收到的 ref（=${web_page1.sessionId} 解析值）
    assert consumer_events[-1]["payload"]["outputs"]["value"] == "sess-abc"
