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


def test_python_worker_resolves_relative_path_against_workspace(tmp_path):
    workspace = tmp_path / "ws"

    async def run(path):
        executor = PythonWorkerExecutor()
        invocation = CommandInvocation(
            command_id="data.writeJson",
            command_version="1.0.0",
            run_id="run",
            step_id="save",
            inputs={"workspace": str(workspace), "path": path, "data": {"ok": True}},
        )
        return await executor.execute(invocation, asyncio.Event())

    inside = asyncio.run(run("out/report.json"))
    assert inside.status == "success"
    assert (workspace / "out" / "report.json").exists()

    escaping = asyncio.run(run("../escape.txt"))
    assert escaping.status == "error"
    assert escaping.error.code == "CAPABILITY_DENIED"


def test_python_worker_write_text_lines_inside_workspace(tmp_path):
    workspace = tmp_path / "工作区"
    output = workspace / "reports" / "top10.txt"

    async def run():
        executor = PythonWorkerExecutor()
        invocation = CommandInvocation(
            command_id="data.writeText",
            command_version="1.0.0",
            run_id="run",
            step_id="save",
            inputs={
                "workspace": str(workspace),
                "path": str(output),
                "lines": ["新闻", "央视新闻", "澎湃新闻"],
            },
        )
        return await executor.execute(invocation, asyncio.Event())

    result = asyncio.run(run())
    assert result.status == "success"
    content = output.read_text(encoding="utf-8")
    assert content == "新闻\n央视新闻\n澎湃新闻"
    assert result.effects[0].details["operation"] == "writeText"


def test_python_worker_write_text_string_and_rejects_escape(tmp_path):
    workspace = tmp_path / "ws"

    async def run(path, payload):
        executor = PythonWorkerExecutor()
        invocation = CommandInvocation(
            command_id="data.writeText",
            command_version="1.0.0",
            run_id="run",
            step_id="save",
            inputs={"workspace": str(workspace), "path": path, **payload},
        )
        return await executor.execute(invocation, asyncio.Event())

    result = asyncio.run(run("note.txt", {"text": "标题：新闻"}))
    assert result.status == "success"
    assert (workspace / "note.txt").read_text(encoding="utf-8") == "标题：新闻"

    escaping = asyncio.run(run("..\\escape.txt", {"text": "x"}))
    assert escaping.status == "error"
    assert escaping.error.code == "CAPABILITY_DENIED"


def test_python_worker_limit_truncates_and_counts(tmp_path):
    async def run(items, count):
        executor = PythonWorkerExecutor()
        invocation = CommandInvocation(
            command_id="data.limit",
            command_version="1.0.0",
            run_id="run",
            step_id="pick",
            inputs={"items": items, "count": count},
        )
        return await executor.execute(invocation, asyncio.Event())

    result = asyncio.run(run(["a", "b", "c", "d"], 2))
    assert result.status == "success"
    assert result.outputs == {"items": ["a", "b"], "count": 2}
    assert result.value == ["a", "b"]

    empty = asyncio.run(run([], 5))
    assert empty.outputs == {"items": [], "count": 0}

    zero = asyncio.run(run(["a"], 0))
    assert zero.outputs == {"items": [], "count": 0}

    exact = asyncio.run(run(["a", "b"], 10))
    assert exact.outputs == {"items": ["a", "b"], "count": 2}
