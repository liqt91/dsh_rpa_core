import asyncio

from rpa_core.executors import PythonWorkerExecutor
from rpa_core.model.command import CommandInvocation


def test_python_worker_writes_only_inside_workspace(tmp_path):
    workspace = tmp_path / "中文工作区"
    output = workspace / "结果.json"

    async def run():
        executor = PythonWorkerExecutor()
        invocation = CommandInvocation(
            command_id="data.writeJson",
            command_version="1.0.0",
            run_id="run",
            step_id="save",
            inputs={"workspace": str(workspace), "path": str(output), "data": [1, 2]},
        )
        return await executor.execute(invocation, asyncio.Event())

    result = asyncio.run(run())
    assert result.status == "success"
    assert output.read_text(encoding="utf-8").strip().startswith("[")
    assert len(result.effects) == 1
    effect = result.effects[0]
    assert effect.kind.value == "idempotent-write"
    assert effect.status.value == "committed"
    assert effect.resource == f"file:{output.resolve()}"
    assert effect.idempotency_key is not None
