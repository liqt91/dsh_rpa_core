import asyncio

import pytest

from rpa_core.executors.browser import PlaywrightExecutor
from rpa_core.model.command import CommandInvocation
from rpa_core.model.errors import ErrorCode


def _invocation(command: str, inputs: dict, step: str = "s1") -> CommandInvocation:
    return CommandInvocation(
        command_id=command,
        command_version="1.1.0",
        run_id="bsk-run",
        step_id=step,
        inputs=inputs,
    )


def _bsk_runner(script: list[tuple[tuple, dict]]):
    calls: list[list[str]] = []

    def runner(*args: str) -> dict:
        calls.append(list(args))
        head = tuple(args[:2])
        for pattern, response in script:
            if head == pattern or (pattern and pattern[0] == args[0] and len(pattern) == 1):
                if isinstance(response, list):
                    return response.pop(0)
                return response
        return {"session_id": "abcd", "ok": True, "value": None}

    runner.calls = calls
    return runner


def _count_runner(count: int = 1):
    calls: list[list[str]] = []

    def runner(*args: str) -> dict:
        calls.append(list(args))
        if args[0] == "session" and args[1] == "start":
            return {"session_id": "abcd", "agent_window_id": 1}
        if args[0] == "evaluate":
            expr = args[-1]
            if "querySelectorAll" in expr and ".length" in expr:
                return {"ok": True, "value": count}
            if "innerText" in expr and "querySelector(" in expr and "Array.from" not in expr:
                return {"ok": True, "value": "hello"}
            if "Array.from" in expr:
                return {"ok": True, "value": ["a", "b"]}
            return {"ok": True, "value": None}
        if args[0] == "navigate":
            return {"url": args[-1], "final_url": args[-1], "reached": "load"}
        if args[0] == "click":
            return {"tab_id": 1, "used_selector": args[-1]}
        if args[0] == "fill":
            return {"tab_id": 1}
        return {"ok": True}

    runner.calls = calls
    return runner


@pytest.mark.asyncio
async def test_navigate_bsk_returns_session_and_registers():
    executor = PlaywrightExecutor(bsk_runner=_count_runner())
    result = await executor.execute(
        _invocation("browser.navigate", {"transport": "bsk", "browserInstanceId": "edge1", "url": "https://x.com"}),
        asyncio.Event(),
    )
    assert result.status == "success"
    session_id = result.outputs["sessionId"]
    assert session_id
    assert session_id in executor._bsk_sessions
    assert result.outputs["url"] == "https://x.com"
    assert result.outputs["resourceType"] == "webPage"
    assert result.effects[0].details["transport"] == "bsk"
    await executor.close()


@pytest.mark.asyncio
async def test_navigate_default_stays_playwright():
    """transport 缺省 → 不创建 bsk session（走 playwright 路径，headless 缺省）。"""
    executor = PlaywrightExecutor(bsk_runner=_count_runner())
    result = await executor.execute(
        _invocation("browser.navigate", {"url": "about:blank", "headless": True}),
        asyncio.Event(),
    )
    assert result.status == "success"
    assert not executor._bsk_sessions
    assert result.outputs["sessionId"] in executor._sessions
    assert result.outputs["resourceType"] == "webPage"
    await executor.close()


@pytest.mark.asyncio
async def test_navigate_playwright_accepts_channel_and_args():
    """对标影刀「打开网页」：浏览器类型(channel) + 命令行参数(args) 透传 playwright。"""
    executor = PlaywrightExecutor()
    result = await executor.execute(
        _invocation(
            "browser.navigate",
            {"url": "about:blank", "headless": True,
             "channel": "chromium", "args": ["--window-size=800,600"]},
        ),
        asyncio.Event(),
    )
    assert result.status == "success"
    assert result.outputs["sessionId"] in executor._sessions
    await executor.close()


@pytest.mark.asyncio
async def test_bsk_click_and_input_and_readback():
    executor = PlaywrightExecutor(bsk_runner=_count_runner())
    open_page = await executor.execute(
        _invocation("browser.navigate", {"transport": "bsk", "url": "https://x.com"}),
        asyncio.Event(),
    )
    sid = open_page.outputs["sessionId"]
    assert open_page.outputs["url"] == "https://x.com"

    click = await executor.execute(
        _invocation("browser.click", {"sessionId": sid, "selector": "#go"}),
        asyncio.Event(),
    )
    assert click.status == "success"
    assert click.outputs["matchedCount"] == 1

    fill = await executor.execute(
        _invocation("browser.input", {"sessionId": sid, "selector": "#q", "text": "hi"}),
        asyncio.Event(),
    )
    assert fill.status == "success"

    text = await executor.execute(
        _invocation("browser.getText", {"sessionId": sid, "selector": "#out"}),
        asyncio.Event(),
    )
    assert text.status == "success"
    assert text.outputs["value"] == "hello"

    items = await executor.execute(
        _invocation("browser.queryAll", {"sessionId": sid, "selector": ".r"}),
        asyncio.Event(),
    )
    assert items.outputs["items"] == ["a", "b"]
    assert items.outputs["count"] == 2

    close = await executor.execute(
        _invocation("browser.close", {"sessionId": sid}), asyncio.Event()
    )
    assert close.status == "success"
    assert sid not in executor._bsk_sessions
    await executor.close()


@pytest.mark.asyncio
async def test_bsk_click_zero_match_is_element_not_found():
    executor = PlaywrightExecutor(bsk_runner=_count_runner(count=0))
    open_page = await executor.execute(
        _invocation("browser.navigate", {"transport": "bsk", "url": "https://x.com"}),
        asyncio.Event(),
    )
    sid = open_page.outputs["sessionId"]
    result = await executor.execute(
        _invocation("browser.click", {"sessionId": sid, "selector": "#missing"}),
        asyncio.Event(),
    )
    assert result.status == "error"
    assert result.error.code == ErrorCode.ELEMENT_NOT_FOUND
    assert result.error.details["matchedCount"] == 0
    await executor.close()


@pytest.mark.asyncio
async def test_bsk_wait_for_timeout_retryable():
    executor = PlaywrightExecutor(bsk_runner=_count_runner(count=0))
    open_page = await executor.execute(
        _invocation("browser.navigate", {"transport": "bsk", "url": "https://x.com"}),
        asyncio.Event(),
    )
    sid = open_page.outputs["sessionId"]
    result = await executor.execute(
        _invocation("browser.waitFor", {"sessionId": sid, "selector": "#never", "timeoutMs": 700}),
        asyncio.Event(),
    )
    assert result.status == "error"
    assert result.error.code == ErrorCode.TIMEOUT
    assert result.error.retryable is True
    await executor.close()


@pytest.mark.asyncio
async def test_bsk_wait_for_cancelled():
    executor = PlaywrightExecutor(bsk_runner=_count_runner(count=0))
    open_page = await executor.execute(
        _invocation("browser.navigate", {"transport": "bsk", "url": "https://x.com"}),
        asyncio.Event(),
    )
    sid = open_page.outputs["sessionId"]
    cancellation = asyncio.Event()
    cancellation.set()
    result = await executor.execute(
        _invocation("browser.waitFor", {"sessionId": sid, "selector": "#never", "timeoutMs": 5000}),
        cancellation,
    )
    assert result.status == "cancelled"
    await executor.close()


@pytest.mark.asyncio
async def test_bsk_unknown_session_is_session_not_found():
    executor = PlaywrightExecutor(bsk_runner=_count_runner())
    result = await executor.execute(
        _invocation("browser.click", {"sessionId": "nope", "selector": "#go"}),
        asyncio.Event(),
    )
    assert result.status == "error"
    assert result.error.code == ErrorCode.SESSION_NOT_FOUND
    await executor.close()


@pytest.mark.asyncio
async def test_bsk_session_gone_mid_command_maps_session_not_found():
    def runner(*args: str) -> dict:
        if args[0] == "session" and args[1] == "start":
            return {"session_id": "abcd"}
        if args[0] == "evaluate":
            return {"code": "not_found", "message": "session stopped"}
        return {"ok": True}

    executor = PlaywrightExecutor(bsk_runner=runner)
    open_page = await executor.execute(
        _invocation("browser.navigate", {"transport": "bsk", "url": "https://x.com"}),
        asyncio.Event(),
    )
    sid = open_page.outputs["sessionId"]
    result = await executor.execute(
        _invocation("browser.click", {"sessionId": sid, "selector": "#go"}),
        asyncio.Event(),
    )
    assert result.status == "error"
    assert result.error.code == ErrorCode.SESSION_NOT_FOUND
    assert sid not in executor._bsk_sessions
    await executor.close()


@pytest.mark.asyncio
async def test_bsk_open_failure_maps_executor_failed():
    def runner(*args: str) -> dict:
        return {"code": "not_found", "message": "no browsers online"}

    executor = PlaywrightExecutor(bsk_runner=runner)
    result = await executor.execute(
        _invocation("browser.navigate", {"transport": "bsk", "url": "https://x.com"}),
        asyncio.Event(),
    )
    assert result.status == "error"
    assert result.error.code == ErrorCode.EXECUTOR_FAILED
    assert "no browsers online" in result.error.message
    await executor.close()


@pytest.mark.asyncio
async def test_executor_close_stops_all_bsk_sessions():
    runner = _count_runner()
    executor = PlaywrightExecutor(bsk_runner=runner)
    for _ in range(2):
        await executor.execute(
            _invocation("browser.navigate", {"transport": "bsk", "url": "https://x.com"}),
        asyncio.Event(),
        )
    assert len(executor._bsk_sessions) == 2
    await executor.close()
    assert not executor._bsk_sessions
    stop_calls = [c for c in runner.calls if c[:2] == ["session", "stop"]]
    assert len(stop_calls) == 2


@pytest.mark.asyncio
async def test_executor_close_keeps_keepopen_bsk_session():
    """keepOpen=True 的 bsk 会话在 executor.close() 时不 session.stop（Agent Window 保留）。"""
    runner = _count_runner()
    executor = PlaywrightExecutor(bsk_runner=runner)
    for _ in range(2):
        await executor.execute(
            _invocation("browser.navigate", {"transport": "bsk", "keepOpen": True, "url": "https://x.com"}),
            asyncio.Event(),
        )
    await executor.execute(
        _invocation("browser.navigate", {"transport": "bsk", "url": "https://x.com"}),
        asyncio.Event(),
    )
    await executor.close()
    assert not executor._bsk_sessions  # keepOpen 会话仅从登记移除，不 stop
    stop_calls = [c for c in runner.calls if c[:2] == ["session", "stop"]]
    assert len(stop_calls) == 1  # 只有非 keepOpen 的被停


@pytest.mark.asyncio
async def test_bsk_explicit_close_still_stops_keepopen_session():
    """keepOpen 不豁免显式 browser.close 命令（流程内显式关仍停）。"""
    runner = _count_runner()
    executor = PlaywrightExecutor(bsk_runner=runner)
    open_page = await executor.execute(
        _invocation("browser.navigate", {"transport": "bsk", "keepOpen": True, "url": "https://x.com"}),
        asyncio.Event(),
    )
    sid = open_page.outputs["sessionId"]
    result = await executor.execute(
        _invocation("browser.close", {"sessionId": sid}), asyncio.Event()
    )
    assert result.status == "success"
    assert sid not in executor._bsk_sessions
    stop_calls = [c for c in runner.calls if c[:2] == ["session", "stop"]]
    assert len(stop_calls) == 1
    await executor.close()
