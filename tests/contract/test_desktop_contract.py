import asyncio
import sys

from rpa_core.executors import DesktopExecutor
from rpa_core.model.command import CommandInvocation
from rpa_core.model.desktop import DesktopLocator


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
    assert locator.automation_id == "nameInput"
    assert locator.control_type == "Edit"


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
