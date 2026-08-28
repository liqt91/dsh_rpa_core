import asyncio

from rpa_core.executors import PlaywrightExecutor
from rpa_core.model.command import CommandInvocation


def test_browser_command_without_session_fails_explicitly():
    async def run():
        executor = PlaywrightExecutor()
        result = await executor.execute(
            CommandInvocation(
                command_id="browser.click",
                command_version="1.0.0",
                run_id="run",
                step_id="click",
                inputs={"sessionId": "missing", "selector": "#missing"},
            ),
            asyncio.Event(),
        )
        await executor.close()
        return result

    result = asyncio.run(run())
    assert result.status == "error"
    assert result.error is not None
    assert result.error.code == "SESSION_NOT_FOUND"
