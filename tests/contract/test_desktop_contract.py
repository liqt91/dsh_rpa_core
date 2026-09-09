import asyncio
import json
import sys
from pathlib import Path

import pytest

from rpa_core.executors import DesktopExecutor, Win32DesktopExecutor
from rpa_core.model.command import CommandInvocation
from rpa_core.model.desktop import DesktopLocator

ROOT = Path(__file__).resolve().parents[2]

SHARED_DESKTOP_COMMANDS = (
    "attachWindow",
    "findElement",
    "click",
    "input",
    "getText",
    "closeSession",
    "activateWindow",
    "setWindowState",
    "setWindowVisible",
    "moveWindow",
    "resizeWindow",
    "getWindowTitle",
    "getSelectedText",
    "screenshot",
    "select",
    "drag",
    "getWindowList",
)


def invocation(command_id, inputs):
    return CommandInvocation(
        command_id=command_id,
        command_version="1.0.0",
        run_id="run",
        step_id="step",
        inputs=inputs,
    )


def test_desktop_locator_requires_structured_identity():
    locator = DesktopLocator.model_validate({"automationId": "nameInput", "controlType": "Edit"})
    assert locator.backend == "uia"
    assert locator.automation_id == "nameInput"
    assert locator.control_type == "Edit"


def test_win32_locator_requires_win32_identity():
    locator = DesktopLocator.model_validate(
        {"backend": "win32", "title": "打开", "className": "#32770"}
    )
    assert locator.backend == "win32"
    assert locator.title == "打开"
    assert locator.class_name == "#32770"


def test_desktop_missing_session_is_explicit():
    async def run():
        executor = DesktopExecutor()
        return await executor.execute(
            invocation("desktop.getText", {"sessionId": "missing", "elementId": "element"}),
            asyncio.Event(),
        )

    result = asyncio.run(run())
    if sys.platform == "win32":
        assert result.error.code == "SESSION_NOT_FOUND"
    else:
        assert result.error.code == "PLATFORM_UNSUPPORTED"


def test_desktop_close_is_idempotent_for_executor_shutdown():
    async def run():
        executor = DesktopExecutor()
        await executor.close()
        await executor.close()
        return executor.active_session_count

    assert asyncio.run(run()) == 0


def test_win32_executor_close_is_idempotent_for_executor_shutdown():
    async def run():
        executor = Win32DesktopExecutor()
        await executor.close()
        await executor.close()
        return 0

    assert asyncio.run(run()) == 0


def test_desktop_e2e_workflow_fixture_is_present():
    path = "D:/Users/Administrator/Documents/代码/rpa_core/examples/windows-desktop/workflow.json"
    assert path.endswith("workflow.json")


def test_uia_attach_unknown_window_reports_element_not_found():
    async def run():
        executor = DesktopExecutor()
        return await executor.execute(
            invocation(
                "desktop.attachWindow",
                {"title": "no-such-window-rpa-core-probe", "timeoutMs": 200},
            ),
            asyncio.Event(),
        )

    result = asyncio.run(run())
    if sys.platform == "win32":
        assert result.error.code == "ELEMENT_NOT_FOUND"
        assert result.error.details["matchedCount"] == 0
    else:
        assert result.error.code == "PLATFORM_UNSUPPORTED"


def test_win32_attach_timeout_reports_element_not_found():
    async def run():
        executor = Win32DesktopExecutor()
        return await executor.execute(
            invocation(
                "desktop.win32.attachWindow",
                {"title": "no-such-window-rpa-core-probe", "timeoutMs": 200},
            ),
            asyncio.Event(),
        )

    result = asyncio.run(run())
    if sys.platform == "win32":
        assert result.error.code == "ELEMENT_NOT_FOUND"
        assert result.error.details["matchedCount"] == 0
    else:
        assert result.error.code == "PLATFORM_UNSUPPORTED"


def test_uia_find_unknown_element_reports_element_not_found():
    async def run():
        executor = DesktopExecutor()
        return await executor.execute(
            invocation(
                "desktop.findElement",
                {
                    "sessionId": "fabricated",
                    "locator": {"automationId": "missing"},
                    "timeoutMs": 200,
                },
            ),
            asyncio.Event(),
        )

    result = asyncio.run(run())
    if sys.platform == "win32":
        assert result.error.code == "SESSION_NOT_FOUND"
    else:
        assert result.error.code == "PLATFORM_UNSUPPORTED"


def test_desktop_backend_manifests_share_lifecycle_contract():
    def load(backend: str, name: str):
        return json.loads(
            (ROOT / "commands" / backend / f"{name}.json").read_text(encoding="utf-8")
        )

    for name in SHARED_DESKTOP_COMMANDS:
        uia = load("desktop", name)
        win32 = load("desktop_win32", name)
        assert uia["id"] == f"desktop.{name}"
        assert win32["id"] == f"desktop.win32.{name}"
        for field in ("version", "kind", "risk", "stability", "effect", "capabilities"):
            assert uia[field] == win32[field], f"{name}: {field} differs between backends"
            attach_outputs = ("sessionId", "resourceType", "processId", "workWindowId")
        if name == "attachWindow":
            assert set(uia["output_schema"]["required"]) == set(attach_outputs)
            assert set(win32["output_schema"]["required"]) == set(attach_outputs)
        if name in ("attachWindow", "findElement"):
            assert "timeoutMs" in uia["input_schema"]["properties"], name
            assert "timeoutMs" in win32["input_schema"]["properties"], name


def test_uia_and_win32_command_sets_diverge_only_in_win32_extras():
    uia = {path.stem for path in (ROOT / "commands" / "desktop").glob("*.json")}
    win32 = {path.stem for path in (ROOT / "commands" / "desktop_win32").glob("*.json")}
    assert uia == set(SHARED_DESKTOP_COMMANDS)
    assert win32 - uia == {"hotkey", "menuSelect"}


def test_uia_attach_does_not_trigger_full_desktop_enumeration():
    """attachWindow exact 路径必须走 FindWindowW → UIAWrapper(handle) 构造，
    禁止调用 Desktop(backend='uia').windows() 全桌面枚举（慢 UIA provider 可达 ~60s）。"""
    if sys.platform != "win32":
        pytest.skip("Windows-only")
    from unittest.mock import MagicMock, patch

    from rpa_core.executors.desktop import DesktopExecutor

    executor = DesktopExecutor()
    desktop_mock = MagicMock()
    desktop_mock.windows.return_value = []

    with patch("rpa_core.executors.desktop.ctypes") as ctypes_mock:
        user32 = MagicMock()
        ctypes_mock.windll.user32 = user32
        user32.FindWindowW.return_value = 0  # 窗口不存在
        ctypes_mock.c_ulong = MagicMock()
        ctypes_mock.byref = MagicMock()

        mocked_modules = {
            "pywinauto": MagicMock(),
            "pywinauto.controls": MagicMock(),
            "pywinauto.controls.uiawrapper": MagicMock(),
            "pywinauto.uia_element_info": MagicMock(),
        }
        with patch.dict("sys.modules", mocked_modules):
            result = asyncio.run(executor.execute(
                invocation("desktop.attachWindow", {"title": "test", "timeoutMs": 100}),
                asyncio.Event(),
            ))

    assert result.error.code == "ELEMENT_NOT_FOUND"
    user32.FindWindowW.assert_called()


def test_uia_window_by_handle_does_not_enumerate_all_windows():
    """_window_by_handle 必须用 UIAWrapper(UIAElementInfo(handle)) 直接构造，
    禁止调用 Desktop(backend='uia').windows() 全桌面枚举。"""
    if sys.platform != "win32":
        pytest.skip("Windows-only")
    from unittest.mock import MagicMock, patch

    mock_wrapper = MagicMock()
    mock_element_info = MagicMock()
    with patch("pywinauto.controls.uiawrapper.UIAWrapper", return_value=mock_wrapper):
        with patch("pywinauto.uia_element_info.UIAElementInfo", return_value=mock_element_info):
            result = DesktopExecutor._window_by_handle(12345)

    assert result is mock_wrapper
