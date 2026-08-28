import argparse
import asyncio
import json
from pathlib import Path

from rpa_core.catalog import load_catalog
from rpa_core.compiler import WorkflowCompiler
from rpa_core.executors import (
    DesktopExecutor,
    ExecutorRegistry,
    PlaywrightExecutor,
    PythonWorkerExecutor,
)
from rpa_core.model.workflow import Workflow
from rpa_core.runtime import Orchestrator


def _load_workflow(path: Path) -> Workflow:
    return Workflow.model_validate_json(path.read_text(encoding="utf-8"))


def _compile(path: Path):
    root = Path(__file__).resolve().parents[2]
    catalog = load_catalog(root / "commands")
    workflow = _load_workflow(path)
    plan = WorkflowCompiler(catalog).compile(
        workflow,
        {
            "browser.read",
            "browser.control",
            "desktop.control",
            "process.start",
            "workspace.write",
        },
    )
    return root, catalog, plan


def main() -> int:
    parser = argparse.ArgumentParser(prog="rpa-core")
    subparsers = parser.add_subparsers(dest="action", required=True)
    for action in ("validate", "run"):
        sub = subparsers.add_parser(action)
        sub.add_argument("workflow", type=Path)
        sub.add_argument("--artifacts", type=Path, default=Path("run_artifacts"))
    args = parser.parse_args()
    _root, catalog, plan = _compile(args.workflow)
    if args.action == "validate":
        print(json.dumps({"valid": True, "catalogDigest": catalog.digest}, indent=2))
        return 0

    async def run() -> int:
        browser = PlaywrightExecutor()
        registry = ExecutorRegistry(
            {
                "browser.playwright": browser,
                "desktop.uia": DesktopExecutor(),
                "python.worker": PythonWorkerExecutor(),
            }
        )
        try:
            orchestrator = Orchestrator(catalog, registry, args.artifacts)
            result = await orchestrator.run(plan)
            print(result.model_dump_json(indent=2))
            return 0 if result.status.value == "succeeded" else 1
        finally:
            await registry.close()

    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
