import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

from rpa_core.catalog import load_catalog
from rpa_core.compiler import WorkflowCompiler
from rpa_core.executors import ExecutorRegistry, Win32DesktopExecutor
from rpa_core.model.workflow import Workflow
from rpa_core.runtime import Orchestrator

ROOT = Path(__file__).resolve().parents[2]

# 本文件会真的启动记事本并输入——弹窗且抢焦点，缺省跳过（见 tests/conftest.py）
pytestmark = pytest.mark.skipif(
    os.environ.get("RPA_DESKTOP_E2E") != "1",
    reason="真实桌面 E2E 会弹窗抢焦点，缺省跳过；设 RPA_DESKTOP_E2E=1 启用",
)


def _run_desktop_workflow(tmp_path: Path):
    fixture = ROOT / "examples" / "windows-desktop" / "workflow.json"
    workflow = Workflow.model_validate_json(fixture.read_text(encoding="utf-8"))

    # fixture 的 filePath 默认值是相对文件名（仓库不能硬编码某台机器的绝对路径），
    # 而记事本「打开」对话框按记事本自己的工作目录解析相对路径 → 这里注入仓库内
    # test.txt 的绝对路径，保证这个切片在任何 Windows 机器上都能跑通。
    inputs = {"filePath": str(ROOT / "test.txt")}

    def _kill_notepad() -> None:
        subprocess.run(
            ["taskkill", "/F", "/IM", "notepad.exe"],
            capture_output=True,
            check=False,
        )

    async def run():
        _kill_notepad()
        proc = await asyncio.to_thread(subprocess.Popen, ["notepad.exe"])
        try:
            catalog = load_catalog(ROOT / "commands")
            plan = WorkflowCompiler(catalog).compile(workflow, {"desktop.control"})
            registry = ExecutorRegistry({"desktop.win32": Win32DesktopExecutor()})
            try:
                runner = Orchestrator(catalog, registry, tmp_path / "runs")
                return await runner.run(plan, inputs)
            finally:
                await registry.close()
        finally:
            proc.terminate()
            proc.wait(timeout=10)
            _kill_notepad()

    return asyncio.run(run()), workflow


@pytest.mark.skipif(sys.platform != "win32", reason="Windows desktop fixture requires Windows")
def test_windows_desktop_vertical_slice(tmp_path):
    result, workflow = _run_desktop_workflow(tmp_path)
    assert result.status.value == "succeeded"
    assert result.return_value is not None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows desktop fixture requires Windows")
def test_windows_desktop_fixture_definition_is_deterministic():
    fixture = ROOT / "examples" / "windows-desktop" / "workflow.json"
    workflow = Workflow.model_validate_json(fixture.read_text(encoding="utf-8"))
    commands = [node.command for node in workflow.root.children if getattr(node, "command", None)]
    assert commands == [
        "desktop.win32.attachWindow",
        "desktop.win32.menuSelect",
        "desktop.win32.attachWindow",
        "desktop.win32.findElement",
        "desktop.win32.input",
        "desktop.win32.hotkey",
        "desktop.win32.attachWindow",
        "desktop.win32.closeSession",
        "desktop.win32.closeSession",
        "desktop.win32.closeSession",
    ]
