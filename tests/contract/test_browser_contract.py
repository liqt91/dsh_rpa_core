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


def test_browser_session_commands_fail_explicitly_without_session():
    """新增浏览器命令（cookie/导航/多标签/拖拽组）在会话缺失时必须显式报 SESSION_NOT_FOUND。"""
    commands = [
        ("browser.waitLoad", {}),
        ("browser.scroll", {"position": "bottom"}),
        ("browser.check", {"selector": "#cb"}),
        ("browser.cookieSet", {"cookies": [{"name": "k", "value": "v", "url": "http://x"}]}),
        ("browser.cookieGetAll", {}),
        ("browser.cookieGet", {"name": "k"}),
        ("browser.cookieRemove", {}),
        ("browser.listPages", {}),
        ("browser.drag", {"selector": "#a", "targetSelector": "#b"}),
    ]
    for command_id, extra in commands:
        async def run(command_id=command_id, extra=extra):
            executor = PlaywrightExecutor()
            result = await executor.execute(
                CommandInvocation(
                    command_id=command_id,
                    command_version="1.0.0",
                    run_id="run",
                    step_id="step",
                    inputs={"sessionId": "missing", **extra},
                ),
                asyncio.Event(),
            )
            await executor.close()
            return result

        result = asyncio.run(run())
        assert result.status == "error", f"{command_id} should fail"
        assert result.error is not None, f"{command_id} should report an error"
        assert result.error.code == "SESSION_NOT_FOUND", (
            f"{command_id} unexpected error: {result.error.code}"
        )


def test_browser_attach_requires_valid_source_session():
    async def run():
        executor = PlaywrightExecutor()
        result = await executor.execute(
            CommandInvocation(
                command_id="browser.attach",
                command_version="1.0.0",
                run_id="run",
                step_id="attach",
                inputs={"sessionId": "missing", "pattern": "x"},
            ),
            asyncio.Event(),
        )
        await executor.close()
        return result

    result = asyncio.run(run())
    assert result.status == "error"
    assert result.error is not None
    assert result.error.code == "SESSION_NOT_FOUND"


def test_browser_navigate_action_requires_session():
    async def run():
        executor = PlaywrightExecutor()
        result = await executor.execute(
            CommandInvocation(
                command_id="browser.navigate",
                command_version="2.1.0",
                run_id="run",
                step_id="nav",
                inputs={"action": "reload", "sessionId": "missing"},
            ),
            asyncio.Event(),
        )
        await executor.close()
        return result

    result = asyncio.run(run())
    assert result.status == "error"
    assert result.error is not None
    assert result.error.code == "INVALID_INPUT"


def test_browser_navigate_goto_requires_url():
    async def run():
        executor = PlaywrightExecutor()
        result = await executor.execute(
            CommandInvocation(
                command_id="browser.navigate",
                command_version="2.1.0",
                run_id="run",
                step_id="nav",
                inputs={"action": "goto"},
            ),
            asyncio.Event(),
        )
        await executor.close()
        return result

    result = asyncio.run(run())
    assert result.status == "error"
    assert result.error is not None
    assert result.error.code == "INVALID_INPUT"
