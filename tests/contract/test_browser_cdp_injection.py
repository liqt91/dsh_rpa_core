"""`browser.click` 的注入通道契约（M107 第 2 层：CDP 真事件）。

背景：浏览器侧原本只有一条点击通道——扩展在页面里 `dispatchEvent` 派发合成事件，
`isTrusted` 恒为 false，任何「只信真事件」的页面/风控都能识别。第 2 层走
`chrome.debugger` 的 `Input.dispatchMouseEvent`，事件由浏览器自己生成（isTrusted=true），
且不经过 OS 输入队列（企业 EDR 把解释器令牌降成 Low 时，OS 注入必被吞）。

这里锁三件容易悄悄坏掉的事：
1. `injection=cdp` 必须真的换成 `cdp.click` 通道，**并且一次都不走 `page.call`**
   （否则就是「声明了但还在派发合成事件」——最坏的一种假绿）；
2. `injection` 缺省/脏值仍走原通道（旧流程行为不变）；
3. cdp 通道失败**不静默降级**：报错必须冒出来，不能悄悄退回合成事件。

扩展侧的 CDP 实现（attach/dispatch 序列/坐标同源）由
`.harness/spike/probe_m107_cdp_click.py` 真机验证，脚本侧纯函数由
`scripts/check_click_helpers.mjs`（门禁内）覆盖。
"""

from __future__ import annotations

import asyncio

import pytest

from rpa_core.executors.browser import PlaywrightExecutor, _injection_mode
from rpa_core.extension_exec import ExtensionChannelError
from rpa_core.model.command import CommandInvocation


def _invocation(inputs: dict) -> CommandInvocation:
    return CommandInvocation(
        command_id="browser.click",
        command_version="1.0.0",
        run_id="run",
        step_id="step",
        inputs=dict(inputs),
    )


class _RecordingExt:
    """同时记录 page.call 与 cdp.click：用来证明「走了哪条通道」。"""

    def __init__(self, cdp_error: Exception | None = None, version: str | None = "0.9.7") -> None:
        self.page_calls: list[dict] = []
        self.cdp_calls: list[dict] = []
        self.cdp_error = cdp_error
        self.version = version
        self.client = type("C", (), {"status": lambda self: {"online": True}})()

    def ext_version(self, target_host=None):
        """执行器用它做版本对账（M107 S2：旧扩展没有 cdp.click 这个 op）。"""
        return self.version

    def page_call(
        self, tab_id, selector, method, *, args=None, timeout_seconds=1, target_host=None
    ):
        self.page_calls.append(
            {"tab_id": tab_id, "selector": selector, "method": method, "args": dict(args or {})}
        )
        return {"matchedCount": 1, "result": True}

    def cdp_click(self, tab_id, selector, *, args=None, timeout_seconds=1, target_host=None):
        self.cdp_calls.append(
            {"tab_id": tab_id, "selector": selector, "args": dict(args or {})}
        )
        if self.cdp_error is not None:
            raise self.cdp_error
        return {
            "matchedCount": 1,
            "result": {"dispatched": 3, "x": 10, "y": 20, "cdpVersion": "1.3"},
        }


def _run_click(ext: _RecordingExt, inputs: dict):
    executor = PlaywrightExecutor(ext_session=ext)
    invocation = _invocation(inputs)
    return asyncio.run(
        executor._ext_page_command(
            "browser.click", invocation, invocation.inputs, "sess", "7", "#ok", 5.0, "msedge",
        )
    )


# ---- 通道选择：缺省/脏值一律 dom，只有显式 cdp 才换通道 --------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, "dom"),
        ("", "dom"),
        ("dom", "dom"),
        ("cdp", "cdp"),
        (" CDP ", "cdp"),
        ("synthetic", "dom"),  # 未知值不猜，按 dom（不静默升级）
    ],
)
def test_injection_mode_normalization(raw, expected):
    inputs = {} if raw is None else {"injection": raw}
    assert _injection_mode(inputs) == expected


# ---- 通道分流：cdp 时不许有任何一次 page.call ------------------------------


def test_cdp_injection_routes_to_debugger_and_never_touches_page_call():
    ext = _RecordingExt()
    result = _run_click(
        ext,
        {
            "selector": "#ok",
            "injection": "cdp",
            "clickPosition": "random",
            "button": "right",
            "clickType": "double",
            "modifiers": ["Ctrl"],
        },
    )
    assert result.status == "success", result.error
    # 关键判据：一条 page.call 都不能有——有就说明还在派发合成事件
    assert ext.page_calls == []
    assert ext.cdp_calls == [
        {
            "tab_id": "7",
            "selector": "#ok",
            "args": {
                "clickPosition": "random",
                "button": "right",
                "clickType": "double",
                "modifiers": ["Ctrl"],
            },
        }
    ]
    details = result.effects[0].details
    assert details["injection"] == "cdp"
    assert details["point"] == {"x": 10, "y": 20}
    assert result.outputs["matchedCount"] == 1
    assert result.outputs["sessionId"] == "sess"


def test_cdp_injection_defaults_match_manifest():
    """cdp 通道的缺省值必须与 manifest 一致（center / left / single / 无修饰键）。"""
    ext = _RecordingExt()
    result = _run_click(ext, {"selector": "#ok", "injection": "cdp"})
    assert result.status == "success", result.error
    assert ext.cdp_calls[0]["args"] == {
        "clickPosition": "center",
        "button": "left",
        "clickType": "single",
        "modifiers": [],
    }


def test_default_injection_still_uses_page_call():
    """不传 injection：行为完全不变，仍走扩展内合成事件（旧流程不受影响）。"""
    ext = _RecordingExt()
    result = _run_click(ext, {"selector": "#ok", "simulateHuman": False})
    assert result.status == "success", result.error
    assert ext.cdp_calls == []
    assert [call["method"] for call in ext.page_calls] == ["click"]


# ---- 不静默降级：cdp 失败必须冒出来 ----------------------------------------


def test_cdp_channel_failure_is_not_silently_downgraded():
    """`CDP_ATTACH_FAILED` 必须原样抛出，绝不能悄悄退回合成点击。

    静默降级的后果比失败更严重：用户以为已经绕过风控，实际还在 `dispatchEvent`。
    """
    ext = _RecordingExt(cdp_error=ExtensionChannelError("CDP_ATTACH_FAILED", "调试器挂不上"))
    with pytest.raises(ExtensionChannelError) as caught:
        _run_click(ext, {"selector": "#ok", "injection": "cdp"})
    assert caught.value.code == "CDP_ATTACH_FAILED"
    assert ext.page_calls == []  # 一条合成事件都没发


def test_cdp_precheck_failure_does_not_try_candidates_for_main_selector():
    """主选择器预检失败（元素已找到但不可点）不换候选：换候选会点到别的元素。

    与 dom 通道同语义——`_cdp_click_with_fallback` 只对「未命中」重试。
    """
    ext = _RecordingExt(cdp_error=ExtensionChannelError("ELEMENT_COVERED", "被遮挡"))
    with pytest.raises(ExtensionChannelError) as caught:
        _run_click(ext, {"selector": "#ok", "injection": "cdp"})
    assert caught.value.code == "ELEMENT_COVERED"
    # 主选择器就抛了，没有进入候选循环
    assert len(ext.cdp_calls) == 1


# ---- 版本对账：旧扩展没有 cdp.click，必须点名而不是「unsupported op」 ---------
#
# 实测过的真实事故（2026-11-19）：同一台机器上 Chrome 装 0.9.2、Edge 装 0.9.7，
# 流程写 `browserType=chrome` ⇒ `cdp.click` 被旧扩展回 `unsupported op`，
# 看起来像 CDP 通道坏了。以下三条把「报清楚」钉住。


@pytest.mark.parametrize("version", ["0.9.2", "0.8.9", "0.9.6"])
def test_outdated_extension_fails_with_actionable_code(version):
    ext = _RecordingExt(version=version)
    result = _run_click(ext, {"selector": "#ok", "injection": "cdp"})
    assert result.status == "error"
    error = result.error
    assert error.code == "EXTENSION_OUTDATED"
    # 必须点名三件事：哪个浏览器、装的是哪版、要哪版——否则用户还得回来问
    assert error.details["browser"] == "msedge"
    assert error.details["extVersion"] == version
    assert error.details["requiredVersion"] == "0.9.7"
    assert "重新加载" in error.message
    # 关键：一个事件都没派发（不是「点了但不 trusted」）
    assert ext.cdp_calls == []
    assert ext.page_calls == []


@pytest.mark.parametrize("version", ["0.9.7", "0.10.0", "1.0.0", None])
def test_current_or_unknown_version_proceeds(version):
    """版本够、或根本探测不到版本时都要放行——探测抖动不能拦下能跑的流程。"""
    ext = _RecordingExt(version=version)
    result = _run_click(ext, {"selector": "#ok", "injection": "cdp"})
    assert result.status == "success", result.error
    assert len(ext.cdp_calls) == 1


def test_outdated_extension_does_not_affect_dom_channel():
    """只有 cdp 通道受版本影响：`injection=dom` 在 0.9.2 上照旧能用。"""
    ext = _RecordingExt(version="0.9.2")
    result = _run_click(ext, {"selector": "#ok"})
    assert result.status == "success", result.error
    assert [call["method"] for call in ext.page_calls] == ["click"]
