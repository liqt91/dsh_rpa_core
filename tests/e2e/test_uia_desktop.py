import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from rpa_core.catalog import load_catalog
from rpa_core.compiler import WorkflowCompiler
from rpa_core.executors import DesktopExecutor, ExecutorRegistry
from rpa_core.model.workflow import Workflow
from rpa_core.runtime import Orchestrator

ROOT = Path(__file__).resolve().parents[2]
CSC = (
    Path(os.environ.get("WINDIR", r"C:\Windows"))
    / "Microsoft.NET"
    / "Framework64"
    / "v4.0.30319"
    / "csc.exe"
)
APP_TITLE = "RPA Core Desktop Demo"
DIALOG_TITLE = "RPA Core Desktop Dialog"

requires_uia_fixture = pytest.mark.skipif(
    sys.platform != "win32" or not CSC.exists(),
    reason="UIA fixture requires Windows with the .NET Framework compiler",
)


def _compile_demo_app(tmp_path: Path) -> Path:
    exe = tmp_path / "RpaCoreDesktopDemo.exe"
    subprocess.run(
        [
            str(CSC),
            "/nologo",
            "/target:winexe",
            f"/out:{exe}",
            "/r:System.Windows.Forms.dll",
            "/r:System.Drawing.dll",
            str(ROOT / "testapps" / "desktop" / "Program.cs"),
        ],
        check=True,
        capture_output=True,
    )
    return exe


def _wait_for_window(title: str, timeout: float = 15.0) -> None:
    import pythoncom
    from pywinauto import Desktop

    pythoncom.CoInitialize()
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            if Desktop(backend="uia").windows(title=title):
                return
            time.sleep(0.2)
    finally:
        pythoncom.CoUninitialize()
    raise TimeoutError(f"window did not appear: {title}")


def _kill_demo_apps() -> None:
    subprocess.run(
        ["taskkill", "/F", "/IM", "RpaCoreDesktopDemo.exe"],
        capture_output=True,
        check=False,
    )


def _run_uia_workflow(tmp_path: Path, attempts: int = 1):
    fixture = ROOT / "examples" / "uia-desktop" / "workflow.json"
    workflow = Workflow.model_validate_json(fixture.read_text(encoding="utf-8"))
    exe = _compile_demo_app(tmp_path)

    async def run_once(run_id_tag: str):
        proc = await asyncio.to_thread(subprocess.Popen, [str(exe)])
        try:
            await asyncio.to_thread(_wait_for_window, APP_TITLE)
            catalog = load_catalog(ROOT / "commands")
            plan = WorkflowCompiler(catalog).compile(workflow, {"desktop.control"})
            registry = ExecutorRegistry({"desktop.uia": DesktopExecutor()})
            try:
                runner = Orchestrator(catalog, registry, tmp_path / "runs" / run_id_tag)
                return await runner.run(plan)
            finally:
                await registry.close()
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)

    async def run_all():
        _kill_demo_apps()
        try:
            return [await run_once(f"attempt-{index}") for index in range(1, attempts + 1)]
        finally:
            _kill_demo_apps()

    return asyncio.run(run_all()), workflow


@requires_uia_fixture
def test_uia_desktop_vertical_slice(tmp_path):
    results, _workflow = _run_uia_workflow(tmp_path)
    result = results[0]
    assert result.status.value == "succeeded"
    assert result.outputs["readResult"]["outputs"]["value"] == "hello rpa"
    assert result.outputs["readFinal"]["outputs"]["value"] == "dialog:world"
    assert result.return_value == "dialog:world"


@requires_uia_fixture
def test_uia_desktop_slice_is_stable_across_repeated_runs(tmp_path):
    results, _workflow = _run_uia_workflow(tmp_path, attempts=3)
    returns = [result.return_value for result in results]
    assert returns == ["dialog:world", "dialog:world", "dialog:world"]
    assert all(result.status.value == "succeeded" for result in results)


@requires_uia_fixture
def test_uia_workflow_command_sequence_is_deterministic(tmp_path):
    fixture = ROOT / "examples" / "uia-desktop" / "workflow.json"
    workflow = Workflow.model_validate_json(fixture.read_text(encoding="utf-8"))
    commands = [node.command for node in workflow.root.children if getattr(node, "command", None)]
    assert commands == [
        "desktop.attachWindow",
        "desktop.findElement",
        "desktop.input",
        "desktop.findElement",
        "desktop.click",
        "desktop.findElement",
        "desktop.getText",
        "desktop.findElement",
        "desktop.click",
        "desktop.attachWindow",
        "desktop.findElement",
        "desktop.input",
        "desktop.findElement",
        "desktop.click",
        "desktop.closeSession",
        "desktop.findElement",
        "desktop.getText",
        "desktop.closeSession",
    ]
