"""API v1 调用方最小示例：运行 → 读证据 → 暂停 → 恢复。

运行方式（仓库根目录）：

    uv run python examples/api-usage/run_workflow.py
"""

import asyncio
import json
from pathlib import Path

from rpa_core.catalog import load_catalog
from rpa_core.compiler import WorkflowCompiler
from rpa_core.executors import ExecutorRegistry, PythonWorkerExecutor
from rpa_core.model.runtime import RunStatus
from rpa_core.model.workflow import Workflow
from rpa_core.runtime import Orchestrator

EXAMPLE_DIR = Path(__file__).resolve().parent
REPO = EXAMPLE_DIR.parents[1]
ARTIFACTS = EXAMPLE_DIR / "artifacts"


async def main() -> int:
    workflow = Workflow.model_validate_json(
        (EXAMPLE_DIR / "workflow.json").read_text(encoding="utf-8")
    )
    catalog = load_catalog(REPO / "commands")
    plan = WorkflowCompiler(catalog).compile(workflow, {"workspace.write"})

    registry = ExecutorRegistry({"python.worker": PythonWorkerExecutor()})
    try:
        orchestrator = Orchestrator(catalog, registry, ARTIFACTS)

        result = await orchestrator.run(plan, inputs={"workspace": str(ARTIFACTS)})
        print("run #1:", result.status.value)
        if result.status is not RunStatus.SUCCEEDED:
            print(json.dumps(result.error, ensure_ascii=False, indent=2))
            return 1

        evidence = json.loads(
            (ARTIFACTS / result.run_id / "result.json").read_text(encoding="utf-8")
        )
        print("evidence status:", evidence["status"])
        print("hello.txt:", (ARTIFACTS / "hello.txt").read_text(encoding="utf-8"))

        handle = orchestrator.start(plan, inputs={"workspace": str(ARTIFACTS)})
        handle.pause()
        paused = await handle.wait()
        print("run #2:", paused.status.value)

        resumed = await orchestrator.resume(plan, paused.run_id).wait()
        print("run #3:", resumed.status.value)
        return 0 if resumed.status is RunStatus.SUCCEEDED else 1
    finally:
        await registry.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
