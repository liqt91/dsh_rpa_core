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
    """轮询等待窗口出现。用 Win32 FindWindowW（毫秒级）探测，避免
    pywinauto UIA Desktop 首次初始化的长阻塞（实测可达 60s）拖垮轮询超时。
    窗口 title 唯一已知，FindWindow 精确定位即可；真正的 UIA 交互交给 executor。"""
    import ctypes

    find_window = ctypes.windll.user32.FindWindowW
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if find_window(None, title):
            return
        time.sleep(0.2)
    raise TimeoutError(f"window did not appear: {title}")


def _warmup_uia() -> None:
    """触发一次 pywinauto UIA 初始化并完成一次全桌面枚举。

    DesktopExecutor 每个命令外层有 15s 的 asyncio.wait_for 超时，而 pywinauto
    UIA backend 在进程内首次初始化实测可达 ~60s（慢 provider / 首次 provider 连接）。
    若首启发生在 executor 的命令里会被 15s 掐断 → TIMEOUT。此处提前把首启消耗掉，
    后续同进程内的 UIA 调用走进程级缓存（实测 ~125ms）。
    """
    import pythoncom
    from pywinauto import Desktop

    pythoncom.CoInitialize()
    try:
        # 首启初始化就藏在这里；允许长时间完成，不设 wait_for
        Desktop(backend="uia").windows()
    finally:
        pythoncom.CoUninitialize()


def _force_foreground(title: str) -> None:
    """强制把目标窗口置前台（AttachThreadInput 绕过前台锁）。

    全量门禁内 UIA 用例排在一堆真实 Chromium e2e 之后，前台焦点常被残留窗口
    占用；UIA SetFocus 只设置应用内键盘焦点，type_keys 的 SendInput 会落到前台
    窗口 → 输入丢失（门禁内抖、单跑即过的根因）。运行前显式抢回前台。
    """
    import ctypes

    user32 = ctypes.windll.user32
    hwnd = user32.FindWindowW(None, title)
    if not hwnd:
        return
    foreground = user32.GetForegroundWindow()
    current_tid = ctypes.windll.kernel32.GetCurrentThreadId()
    foreground_tid = user32.GetWindowThreadProcessId(foreground, None)
    target_tid = user32.GetWindowThreadProcessId(hwnd, None)
    user32.AttachThreadInput(current_tid, foreground_tid, True)
    user32.AttachThreadInput(current_tid, target_tid, True)
    try:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        user32.SetActiveWindow(hwnd)
        user32.SetFocus(hwnd)
    finally:
        user32.AttachThreadInput(current_tid, target_tid, False)
        user32.AttachThreadInput(current_tid, foreground_tid, False)


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
            # 窗口已出现；先 warm-up UIA，避免 15s 命令超时被 ~60s 首启初始化掐断
            await asyncio.to_thread(_warmup_uia)
            # 抢回前台焦点（全量套件内前置浏览器用例会占用前台，SendInput 需要）
            await asyncio.to_thread(_force_foreground, APP_TITLE)
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
