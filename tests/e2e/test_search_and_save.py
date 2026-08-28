import asyncio
import functools
import json
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from rpa_core.catalog import load_catalog
from rpa_core.compiler import WorkflowCompiler
from rpa_core.executors import ExecutorRegistry, PlaywrightExecutor, PythonWorkerExecutor
from rpa_core.model.workflow import Workflow
from rpa_core.runtime import Orchestrator

ROOT = Path(__file__).resolve().parents[2]


def test_search_and_save_vertical_slice(tmp_path):
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(ROOT / "testsite"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    async def run():
        catalog = load_catalog(ROOT / "commands")
        workflow = Workflow.model_validate_json(
            (ROOT / "examples" / "search-and-save" / "workflow.json").read_text(encoding="utf-8")
        )
        plan = WorkflowCompiler(catalog).compile(
            workflow,
            {"browser.read", "browser.control", "process.start", "workspace.write"},
        )
        browser = PlaywrightExecutor()
        registry = ExecutorRegistry(
            {"browser.playwright": browser, "python.worker": PythonWorkerExecutor()}
        )
        try:
            runner = Orchestrator(catalog, registry, tmp_path / "runs")
            return await runner.run(
                plan,
                {
                    "url": f"http://127.0.0.1:{port}/",
                    "workspace": str(tmp_path),
                    "outputPath": str(tmp_path / "results.json"),
                },
            )
        finally:
            await registry.close()

    try:
        result = asyncio.run(run())
    finally:
        server.shutdown()
        server.server_close()

    assert result.status.value == "succeeded"
    assert result.return_value == [
        "RPA Core: 1. Typed workflow",
        "RPA Core: 2. Explicit browser session",
        "RPA Core: 3. Structured result",
    ]
    assert (
        json.loads((tmp_path / "results.json").read_text(encoding="utf-8")) == result.return_value
    )
    events = (
        (tmp_path / "runs" / result.run_id / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    records = [json.loads(line) for line in events]
    assert [record["seq"] for record in records] == list(range(1, len(records) + 1))
    assert records[-1]["type"] == "runFinished"

    expected_kinds = {
        "launch": "session",
        "navigate": "unsafe-write",
        "input": "unsafe-write",
        "click": "unsafe-write",
        "wait": "read",
        "collect": "read",
        "save": "idempotent-write",
        "close": "session",
    }
    for step_id, kind in expected_kinds.items():
        effects = result.outputs[step_id]["effects"]
        assert len(effects) == 1
        assert effects[0]["kind"] == kind
        assert effects[0]["status"] == "committed"
        assert len(effects[0]["effectId"]) == 64
    assert result.outputs["save"]["effects"][0]["idempotencyKey"] is not None
