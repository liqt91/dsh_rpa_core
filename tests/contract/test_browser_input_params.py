"""浏览器扩展通道的输入参数契约（M28 S3：修「声明了不生效」的漂移）。

背景：`browser.input` 的 `mode/keyIntervalMs/clickBeforeInput` 与 `browser.click` 的
`postDelayMs` 在 manifest 里声明，但扩展通道（执行器 → 扩展）此前**一个都没生效**——
默认值还从 manifest 的 `fill` 漂成了 `type`，而扩展只认历史值 `"set"`，导致三种模式实际
落到同一条逐字分支（桌面执行器一直正确，属浏览器侧独有缺陷）。

这里锁住执行器侧的转发契约；扩展侧的纯函数与反漂移由 `scripts/check_input_helpers.mjs`
（门禁内）覆盖。
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
