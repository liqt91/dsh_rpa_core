import asyncio
import os
import subprocess
from pathlib import Path

import desktop_fixture
import pytest

from rpa_core.catalog import load_catalog
from rpa_core.compiler import WorkflowCompiler
from rpa_core.executors import DesktopExecutor, ExecutorRegistry
from rpa_core.model.workflow import Workflow
from rpa_core.runtime import Orchestrator

ROOT = Path(__file__).resolve().parents[2]

# 编译 / 启动 / 抢前台 / 清理都是装配，放 tests/e2e/desktop_fixture.py 里三个消费方共用。
_UNAVAILABLE = desktop_fixture.fixture_unavailable_reason()
requires_uia_fixture = pytest.mark.skipif(_UNAVAILABLE is not None, reason=_UNAVAILABLE or "")

# 本文件会启动 WinForms 演示程序并抢前台——弹窗且抢焦点，缺省跳过
# （见 tests/conftest.py）
pytestmark = pytest.mark.skipif(
    os.environ.get("RPA_DESKTOP_E2E") != "1",
    reason="真实桌面 E2E 会弹窗抢焦点，缺省跳过；设 RPA_DESKTOP_E2E=1 启用",
)


def _run_uia_workflow(tmp_path: Path, attempts: int = 1):
    fixture = ROOT / "examples" / "uia-desktop" / "workflow.json"
    workflow = Workflow.model_validate_json(fixture.read_text(encoding="utf-8"))
    exe = desktop_fixture.compile_demo_app(tmp_path)
    title = desktop_fixture.APP_TITLE

    async def run_once(run_id_tag: str):
        proc = await asyncio.to_thread(subprocess.Popen, [str(exe)])
        try:
            await asyncio.to_thread(desktop_fixture.wait_for_window, title)
            # 窗口已出现；先 warm-up UIA，避免 15s 命令超时被 ~60s 首启初始化掐断
            await asyncio.to_thread(desktop_fixture.warmup_uia, title)
            # 抢回前台焦点（全量套件内前置浏览器用例会占用前台，SendInput 需要）
            await asyncio.to_thread(desktop_fixture.force_foreground, title)
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
        desktop_fixture.kill_demo_apps()
        try:
            return [await run_once(f"attempt-{index}") for index in range(1, attempts + 1)]
        finally:
            desktop_fixture.kill_demo_apps()

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
