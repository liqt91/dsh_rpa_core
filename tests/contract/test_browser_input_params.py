"""浏览器扩展通道的输入参数契约（M28 S3 / M29 S1：修「声明了不生效」的漂移）。

背景：`browser.input` 的 `mode/keyIntervalMs/clickBeforeInput` 与 `browser.click` 的
`postDelayMs` 在 manifest 里声明，但扩展通道（执行器 → 扩展）此前**一个都没生效**——
默认值还从 manifest 的 `fill` 漂成了 `type`，而扩展只认历史值 `"set"`，导致三种模式实际
落到同一条逐字分支（桌面执行器一直正确，属浏览器侧独有缺陷）。

M29 S1 补上同一族的另外两个：`browser.click` 的 `simulateHuman` / `clickPosition` 此前
**声明了没转发**（点击事件连坐标都没有），`modifiers` 的枚举值 `Ctrl`/`Win` 也对不上扩展
里比的 `"Control"`/`"Meta"`（勾了等于没勾）。

这里锁住执行器侧的转发契约；扩展侧的纯函数与反漂移由 `scripts/check_input_helpers.mjs`
与 `scripts/check_click_helpers.mjs`（门禁内）覆盖。
"""

from __future__ import annotations

import asyncio
import time

from rpa_core.executors.browser import PlaywrightExecutor
from rpa_core.model.command import CommandInvocation


def _executor() -> PlaywrightExecutor:
    return PlaywrightExecutor(ext_session=object())  # 仅测参数整形，不触发通道


def test_input_args_forward_declared_params_and_manifest_default():
    """转发 mode/keyIntervalMs/clickBeforeInput；缺省 mode=fill（与 manifest 一致）。"""
    executor = _executor()
    args = executor._ext_method_args(
        "browser.input",
        {
            "text": "hello",
            "mode": "type",
            "append": True,
            "pressEnter": True,
            "keyIntervalMs": 120,
            "clickBeforeInput": True,
        },
    )
    assert args == {
        "text": "hello",
        "mode": "type",
        "append": True,
        "pressEnter": True,
        "keyIntervalMs": 120,
        "clickBeforeInput": True,
    }

    # 缺省：mode 必须是 manifest 的默认值 fill（此前漂成 type）
    default_args = executor._ext_method_args("browser.input", {"text": "x"})
    assert default_args["mode"] == "fill"
    assert default_args["keyIntervalMs"] is None  # 未配置则由扩展按 0 处理
    assert default_args["clickBeforeInput"] is False


def test_post_delay_sleeps_only_when_declared():
    """`postDelayMs`：>0 才等待；非数字/负数/缺省都不等待（不因脏数据卡住流程）。"""
    executor = _executor()

    started = time.monotonic()
    asyncio.run(executor._post_delay({"postDelayMs": 60}))
    assert time.monotonic() - started >= 0.05

    started = time.monotonic()
    for value in (None, 0, -10, "abc"):
        asyncio.run(executor._post_delay({"postDelayMs": value}))
    assert time.monotonic() - started < 0.05


class _RecordingExt:
    """只记录 page.call 的入参（不碰通道），用于验证参数真的走到了扩展那一层。"""

    def __init__(self):
        self.calls: list[dict] = []
        self.client = type("C", (), {"status": lambda self: {"online": True}})()

    def page_call(self, tab_id, selector, method, *, args=None, timeout_seconds=1,
                  target_host=None):
        self.calls.append(
            {"tab_id": tab_id, "selector": selector, "method": method, "args": dict(args or {})}
        )
        return {"matchedCount": 1, "result": True}


def test_click_args_forward_declared_params_and_manifest_default():
    """`simulateHuman` / `clickPosition`：默认值与 manifest 严格一致，且显式值原样透传。"""
    executor = _executor()
    args = executor._ext_method_args(
        "browser.click",
        {
            "selector": "#ok",
            "button": "right",
            "clickType": "double",
            "modifiers": ["Ctrl", "Win"],
            "simulateHuman": False,
            "clickPosition": "random",
        },
    )
    assert args == {
        "button": "right",
        "clickType": "double",
        "modifiers": ["Ctrl", "Win"],
        "simulateHuman": False,
        "clickPosition": "random",
    }

    default_args = executor._ext_method_args("browser.click", {"selector": "#ok"})
    assert default_args["simulateHuman"] is True  # manifest default: true
    assert default_args["clickPosition"] == "center"  # manifest default: center


def test_click_params_reach_the_extension_payload():
    """端到端到 `page.call`：manifest 声明的两个参数必须出现在扩展载荷里（不能只活在执行器）。"""
    ext = _RecordingExt()
    executor = PlaywrightExecutor(ext_session=ext)
    invocation = CommandInvocation(
        command_id="browser.click",
        command_version="1.0.0",
        run_id="run",
        step_id="step",
        inputs={
            "selector": "#ok",
            "simulateHuman": False,
            "clickPosition": "random",
            "modifiers": ["Ctrl"],
        },
    )
    result = asyncio.run(
        executor._ext_page_command(
            "browser.click", invocation, invocation.inputs, "sess", "7", "#ok", 5.0, "msedge",
        )
    )
    assert result.status == "success", result.error
    assert ext.calls == [
        {
            "tab_id": "7",
            "selector": "#ok",
            "method": "click",
            "args": {
                "button": "left",
                "clickType": "single",
                "modifiers": ["Ctrl"],
                "simulateHuman": False,
                "clickPosition": "random",
            },
        }
    ]


class _RecordingCookieExt:
    """记录 cookie 客户端方法的入参（M29 S2：过滤器与 tabId 的传导契约）。"""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.client = type("C", (), {"status": lambda self: {"online": True}})()

    def cookies_get_all(self, **kwargs):
        self.calls.append(("cookies.getAll", kwargs))
        return [{"name": "n", "value": "v"}]

    def cookies_get(self, name, **kwargs):
        self.calls.append(("cookies.get", {"name": name, **kwargs}))
        return "v"

    def cookies_set(self, cookies, **kwargs):
        self.calls.append(("cookies.set", {"cookies": cookies, **kwargs}))
        return len(cookies)

    def cookies_remove(self, name, **kwargs):
        self.calls.append(("cookies.remove", {"name": name, **kwargs}))
        return 1


def _cookie_invocation(command: str, **inputs) -> CommandInvocation:
    return CommandInvocation(
        command_id=command,
        command_version="1.0.0",
        run_id="run",
        step_id="step",
        inputs=inputs,
    )


def _run_phase_d(command: str, ext, **inputs):
    executor = PlaywrightExecutor(ext_session=ext)
    invocation = _cookie_invocation(command, **inputs)
    return asyncio.run(
        executor._ext_phase_d(
            command, invocation, invocation.inputs, "sess", "7", "", 5.0, "msedge",
        )
    )


def test_cookie_get_all_forwards_filters_and_tab_id():
    """`cookieGetAll` 的 name/domain/path 与 tabId 必须进载荷（此前一个都没转发）。"""
    ext = _RecordingCookieExt()
    result = _run_phase_d(
        "browser.cookieGetAll", ext, sessionId="sess", domain="a.test", path="/app",
    )
    assert result.status == "success", result.error
    assert ext.calls == [
        (
            "cookies.getAll",
            {
                "filters": {"domain": "a.test", "path": "/app"},
                "tab_id": "7",
                "timeout_seconds": 5.0,
                "target_host": "msedge",
            },
        )
    ]
    # 证据里带上实际生效的过滤器（便于事后归因「为什么只返回了这些 cookie」）
    assert result.effects[0].details["filters"] == ["domain", "path"]


def test_cookie_get_all_without_filters_still_scopes_by_tab():
    """不给过滤器 ≠ 不给 tabId：作用域 URL 仍由会话绑定的标签页决定。"""
    ext = _RecordingCookieExt()
    result = _run_phase_d("browser.cookieGetAll", ext, sessionId="sess")
    assert result.status == "success", result.error
    assert ext.calls[0][1]["filters"] is None
    assert ext.calls[0][1]["tab_id"] == "7"


def test_cookie_single_commands_forward_tab_id():
    """cookieGet/cookieSet/cookieRemove 同样要把 tabId 传下去（否则扩展拿空 url 调 API）。"""
    for command, inputs, label in (
        ("browser.cookieGet", {"name": "n"}, "cookies.get"),
        ("browser.cookieSet", {"cookies": [{"name": "n", "value": "v"}]}, "cookies.set"),
        ("browser.cookieRemove", {"name": "n"}, "cookies.remove"),
    ):
        ext = _RecordingCookieExt()
        result = _run_phase_d(command, ext, sessionId="sess", **inputs)
        assert result.status == "success", f"{command}: {result.error}"
        op, kwargs = ext.calls[0]
        assert op == label
        assert kwargs["tab_id"] == "7", f"{command} 未把 tabId 传给扩展"


def test_clipboard_rejection_surfaces_as_failure():
    """扩展显式拒绝（clipboard 粘贴未被接受）→ 明确失败，不退化成别的模式。"""
    import asyncio as _asyncio

    class _RejectingExt:
        def __init__(self):
            self.client = type("C", (), {"status": lambda self: {"online": True}})()

        def page_call(self, tab_id, selector, method, *, args=None, timeout_seconds=1,
                      target_host=None):
            return {
                "matchedCount": 1,
                "result": None,
                "inputRejected": True,
                "message": "该元素未接受剪贴板粘贴注入",
            }

    executor = PlaywrightExecutor(ext_session=_RejectingExt())
    invocation = CommandInvocation(
        command_id="browser.input",
        command_version="1.0.0",
        run_id="run",
        step_id="step",
        inputs={"selector": "#x", "text": "hi", "mode": "clipboard"},
    )
    result = _asyncio.run(
        executor._ext_page_command(
            "browser.input", invocation, invocation.inputs, "sess", "7", "#x", 5.0, "msedge",
        )
    )
    assert result.status == "error"
    assert result.error.details["reason"] == "input_mode_not_accepted"
    assert "粘贴" in result.error.message
