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


def test_browser_session_id_can_be_omitted_with_single_session():
    """省略 sessionId 时默认作用于唯一（最近激活）会话。"""
    executor = PlaywrightExecutor()
    executor._sessions["s-1"] = (object(), object(), object())
    session_id, browser, _context, _page = executor._session({})
    assert session_id == "s-1"
    assert browser is executor._sessions["s-1"][0]


def test_browser_session_id_omitted_with_ambiguous_sessions_fails():
    """多个会话且无激活记录时省略 sessionId 应报错（不猜测）。"""
    executor = PlaywrightExecutor()
    executor._sessions["s-1"] = (object(), object(), object())
    executor._sessions["s-2"] = (object(), object(), object())
    try:
        executor._session({})
        raised = False
    except LookupError:
        raised = True
    assert raised
