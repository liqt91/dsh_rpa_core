import asyncio

from rpa_core.executors import PlaywrightExecutor
from rpa_core.executors.browser import _ensure_scheme
from rpa_core.model.command import CommandInvocation


def _invocation(command_id, **inputs):
    return CommandInvocation(
        command_id=command_id,
        command_version="1.0.0",
        run_id="run",
        step_id=command_id.replace(".", "-"),
        inputs=inputs,
    )


def _run(command_id, inputs):
    async def go():
        executor = PlaywrightExecutor()
        try:
            return await executor.execute(_invocation(command_id, **inputs), asyncio.Event())
        finally:
            await executor.close()

    return asyncio.run(go())


def test_browser_command_without_session_fails_explicitly():
    """扩展单通道下，无有效会话 + 扩展离线 → 报 EXECUTOR_FAILED（可操作离线/缺失会话错误）。"""
    result = _run("browser.click", {"sessionId": "missing", "selector": "#missing"})
    assert result.status == "error"
    assert result.error is not None
    assert result.error.code == "EXECUTOR_FAILED"
    assert "扩展" in result.error.message


def test_browser_session_commands_fail_explicitly_without_session():
    """新增浏览器命令（cookie/导航/多标签/DOM 原语组）在会话缺失时必须显式报错，不回退。"""
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
        # 纯 DOM 原语命令（对标影刀）：会话缺失必须显式报错
        ("browser.stopLoading", {}),
        ("browser.setValue", {"selector": "#v", "value": "x"}),
        ("browser.setAttribute", {"selector": "#v", "name": "data-x", "value": "y"}),
        ("browser.getPosition", {"selector": "#v"}),
        ("browser.getSelectOptions", {"selector": "#sel"}),
        ("browser.getScrollPosition", {}),
        ("browser.queryAll", {"selector": "div"}),
        ("browser.attach", {"pattern": "x"}),
    ]
    for command_id, extra in commands:
        result = _run(command_id, {"sessionId": "missing", **extra})
        assert result.status == "error", f"{command_id} should fail"
        assert result.error is not None, f"{command_id} should report an error"
        assert result.error.code == "EXECUTOR_FAILED", (
            f"{command_id} unexpected error: {result.error.code}"
        )


def test_browser_navigate_reload_requires_session():
    """navigate 非 goto 动作（reload/back/forward）需要已有会话：缺失时显式报错。"""
    result = _run("browser.navigate", {"action": "reload", "sessionId": "missing"})
    assert result.status == "error"
    assert result.error is not None
    assert result.error.code == "EXECUTOR_FAILED"


def test_browser_navigate_goto_requires_url():
    """goto 打开新网页需要 url：缺失时在触碰扩展之前就报 INVALID_INPUT。"""
    result = _run("browser.navigate", {"action": "goto"})
    assert result.status == "error"
    assert result.error is not None
    assert result.error.code == "INVALID_INPUT"


def test_ensure_scheme_prepends_https_only_without_scheme():
    """navigate goto 前补齐 scheme：有协议原样保留，否则默认补 https（对用户友好）。"""
    assert _ensure_scheme("www.baidu.com") == "https://www.baidu.com"
    assert _ensure_scheme("  baidu.com  ") == "https://baidu.com"
    assert _ensure_scheme("https://a.test/one") == "https://a.test/one"
    assert _ensure_scheme("http://a.test/one") == "http://a.test/one"
    assert _ensure_scheme("file:///tmp/x") == "file:///tmp/x"
    assert _ensure_scheme("about:blank") == "about:blank"
    assert _ensure_scheme("") == ""