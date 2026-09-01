import asyncio
import json
import sys
from pathlib import Path

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
        attach_outputs = ("sessionId", "processId", "workWindowId")
        if name == "attachWindow":
            assert set(uia["output_schema"]["required"]) == set(attach_outputs)
            assert set(win32["output_schema"]["required"]) == set(attach_outputs)


def test_uia_and_win32_command_sets_diverge_only_in_win32_extras():
    uia = {path.stem for path in (ROOT / "commands" / "desktop").glob("*.json")}
    win32 = {path.stem for path in (ROOT / "commands" / "desktop_win32").glob("*.json")}
    assert uia == set(SHARED_DESKTOP_COMMANDS)
    assert win32 - uia == {"hotkey", "menuSelect"}
