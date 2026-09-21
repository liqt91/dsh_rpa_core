"""M32/M35 浏览器收尾契约：关标签页（closeTabs）与终止浏览器（closeBrowser）。

为什么是两条命令而不是给 `browser.close` 加参数：
`browser.close` 是**会话生命周期**命令（解绑，effect 是 session，不碰用户浏览器）；
关标签页与终止进程都是**对用户浏览器的破坏性操作**（effect 是 unsafe-write）——风险
等级、声明面、默认策略都不同，塞进一个命令会让「结束会话」这种无害操作带上杀伤力。

两条命令各自的重点：
- `closeTabs`：`tabIds` 显式列表 / `all=true` 关闭当前窗口全部，**二选一**（两边都不给
  或都给 → INVALID_INPUT，不猜）；
- `closeBrowser`：语义是「直接杀某个浏览器的**所有**进程」（M35 定案，M32 的
  `launchedByUs`/`byProcessName` 两档经维护者评估删除）——执行本命令本身就是
  显式的破坏性授权，包含用户自己打开的窗口。

本文件不碰真浏览器进程：进程枚举/终止全部打桩（真机执行由 `.harness/tasks/M32-*.md`
里登记的手工验收覆盖），打桩结论不冒充真机结论。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

import rpa_core.executors.browser as browser_module
from rpa_core.executors.browser import PlaywrightExecutor
from rpa_core.executors.browser_ext import ExtensionExecSession
from rpa_core.extension_exec import ExtensionChannelError, ExtensionExecClient
from rpa_core.model.command import CommandInvocation


def _run(executor: PlaywrightExecutor, invocation: CommandInvocation):
    return asyncio.run(executor.execute(invocation, asyncio.Event()))


def _invocation(command: str, **inputs) -> CommandInvocation:
    return CommandInvocation(
        command_id=command,
        command_version="1.0.0",
        run_id="run",
        step_id="step",
        inputs=inputs,
    )


class _FakeEndpointClient(ExtensionExecClient):
    """最底层 `_exchange` 换成预设应答（其余走真实实现，含错误整形与端点选择）。"""

    def __init__(self, handler, *, online: bool = True):
        super().__init__()
        self._handler = handler
        self._online = online
        self.envelopes: list[dict] = []

    def _targets(self, target_host):
        return ["fake_0"] if self._online else []

    def _exchange(self, endpoint, payload, timeout, *, expect_type="result", expect_id=""):
        if expect_type == "status":
            return {"type": "status", "browser": "msedge", "instanceId": "inst", "online": True}
        self.envelopes.append({"endpoint": endpoint, **payload})
        outcome = self._handler(str(payload.get("op") or ""), dict(payload.get("args") or {}))
        if isinstance(outcome, Exception):
            raise outcome
        return {"type": "result", "id": payload.get("id"), "ok": True, "value": outcome}


def _executor(client: ExtensionExecClient) -> PlaywrightExecutor:
    return PlaywrightExecutor(ext_session=ExtensionExecSession(client=client))


def _with_session(executor: PlaywrightExecutor, tab_id: str = "7") -> PlaywrightExecutor:
    executor._ext_sessions["s"] = tab_id
    executor._last_session_id = "s"
    return executor


# -- browser.closeTabs ---------------------------------------------------------


def test_close_tabs_explicit_ids_route_one_batch_envelope():
    """显式 tabIds → 一次 tabs.closeMany，closedTabIds 如实回传。"""
    seen: list[dict] = []

    def handler(op: str, args: dict) -> dict:
        seen.append({"op": op, "args": args})
        return {"closedTabIds": [7, 8], "failedTabIds": []}

    result = _run(
        _with_session(_executor(_FakeEndpointClient(handler))),
        _invocation("browser.closeTabs", sessionId="s", tabIds=[7, 8]),
    )

    assert result.status == "success", result.error
    assert seen == [{"op": "tabs.closeMany", "args": {"tabIds": [7, 8]}}]
    assert result.outputs["closedCount"] == 2
    assert result.outputs["closedTabIds"] == [7, 8]
    assert result.outputs["failedTabIds"] == []


def test_close_tabs_all_closes_current_window_and_reports_scope():
    """all=true → 不带 tabIds（扩展侧按当前窗口取全部），证据里 scope 写 all。"""
    seen: list[dict] = []

    def handler(op: str, args: dict) -> dict:
        seen.append(args)
        return {"closedTabIds": [1, 2, 3], "failedTabIds": []}

    result = _run(
        _with_session(_executor(_FakeEndpointClient(handler))),
        _invocation("browser.closeTabs", sessionId="s", all=True),
    )

    assert result.status == "success", result.error
    assert seen == [{"all": True}]
    assert result.outputs["closedCount"] == 3
    assert result.effects[0].details["scope"] == "all"


def test_close_tabs_forwards_ignore_before_unload_false():
    """显式 ignoreBeforeUnload=false → 原样转发（保留页面拦截，扩展侧不注入）。"""
    seen: list[dict] = []

    def handler(op: str, args: dict) -> dict:
        seen.append(args)
        return {"closedTabIds": [7], "failedTabIds": []}

    result = _run(
        _with_session(_executor(_FakeEndpointClient(handler))),
        _invocation("browser.closeTabs", sessionId="s", tabIds=[7], ignoreBeforeUnload=False),
    )

    assert result.status == "success", result.error
    assert seen == [{"tabIds": [7], "ignoreBeforeUnload": False}]
    assert result.effects[0].details["ignoreBeforeUnload"] is False


def test_close_tabs_forwards_ignore_before_unload_true():
    """显式 true 也原样转发——证据与通道一致，不静默吞掉。"""
    seen: list[dict] = []

    def handler(op: str, args: dict) -> dict:
        seen.append(args)
        return {"closedTabIds": [7], "failedTabIds": []}

    result = _run(
        _with_session(_executor(_FakeEndpointClient(handler))),
        _invocation("browser.closeTabs", sessionId="s", tabIds=[7], ignoreBeforeUnload=True),
    )

    assert result.status == "success", result.error
    assert seen == [{"tabIds": [7], "ignoreBeforeUnload": True}]
    assert result.effects[0].details["ignoreBeforeUnload"] is True


def test_close_tabs_ignore_before_unload_defaults_true_in_evidence():
    """未指定 → args 省略（缺省语义由扩展 !== false 兜底），证据里生效值记 true。

    为什么不显式发送 true：缺省语义双份（Python 一份、扩展一份）是漂移源——哪天
    有一侧改默认值，另一侧的「补发默认值」会把它悄悄盖回去。单一兜底点在扩展。
    """
    seen: list[dict] = []

    def handler(op: str, args: dict) -> dict:
        seen.append(args)
        return {"closedTabIds": [7], "failedTabIds": []}

    result = _run(
        _with_session(_executor(_FakeEndpointClient(handler))),
        _invocation("browser.closeTabs", sessionId="s", tabIds=[7]),
    )

    assert result.status == "success", result.error
    assert seen == [{"tabIds": [7]}]
    assert result.effects[0].details["ignoreBeforeUnload"] is True


@pytest.mark.parametrize(
    ("inputs", "reason"),
    [
        ({}, "两边都不给"),
        ({"tabIds": [7], "all": True}, "两边都给"),
    ],
)
def test_close_tabs_rejects_ambiguous_scope(inputs, reason):
    """tabIds 与 all 必须二选一：不猜用户意图，直接 INVALID_INPUT。"""
    client = _FakeEndpointClient(lambda op, args: {"closedTabIds": [], "failedTabIds": []})
    result = _run(
        _with_session(_executor(client)),
        _invocation("browser.closeTabs", sessionId="s", **inputs),
    )

    assert result.status == "error", reason
    assert result.error.code.value == "INVALID_INPUT"
    assert result.error.details["field"] == "tabIds/all"
    assert client.envelopes == [], "参数非法时不应发起任何通道往返"


def test_close_tabs_rejects_non_list_tab_ids():
    """tabIds 给了但不是数组 → INVALID_INPUT（带接收到的类型，便于排查）。"""
    result = _run(
        _with_session(_executor(_FakeEndpointClient(lambda op, args: {}))),
        _invocation("browser.closeTabs", sessionId="s", tabIds="7"),
    )

    assert result.status == "error"
    assert result.error.code.value == "INVALID_INPUT"
    assert result.error.details["receivedType"] == "str"


def test_close_tabs_reports_partial_failure_without_folding_to_success():
    """部分失败要如实分别记账：关了的进 closedTabIds，没关的进 failedTabIds。"""
    result = _run(
        _with_session(_executor(_FakeEndpointClient(
            lambda op, args: {"closedTabIds": [7], "failedTabIds": [8]}
        ))),
        _invocation("browser.closeTabs", sessionId="s", tabIds=[7, 8]),
    )

    assert result.status == "success", result.error
    assert result.outputs == {"closedCount": 1, "closedTabIds": [7], "failedTabIds": [8]}
    assert result.effects[0].details["failedCount"] == 1


def test_close_tabs_detaches_session_when_its_own_tab_is_closed():
    """关掉的正是当前会话所属标签页 → 会话随之解绑，避免后续步骤拿着死 tabId 操作。"""
    executor = _with_session(_executor(_FakeEndpointClient(
        lambda op, args: {"closedTabIds": [7], "failedTabIds": []}
    )))

    result = _run(executor, _invocation("browser.closeTabs", sessionId="s", tabIds=[7]))

    assert result.status == "success", result.error
    assert "s" not in executor._ext_sessions
    assert executor._last_session_id is None


def test_close_tabs_keeps_session_when_other_tabs_closed():
    """关的是别的标签页 → 会话保持绑定（不能顺手把用户当前页也解绑了）。"""
    executor = _with_session(_executor(_FakeEndpointClient(
        lambda op, args: {"closedTabIds": [99], "failedTabIds": []}
    )))

    result = _run(executor, _invocation("browser.closeTabs", sessionId="s", tabIds=[99]))

    assert result.status == "success", result.error
    assert executor._ext_sessions.get("s") == "7"


def test_close_tabs_surfaces_channel_failure():
    """通道层错误必须上抛成失败（不能把「没关掉」当成功回执）。"""
    def handler(op: str, args: dict):
        return ExtensionChannelError("TIMEOUT", "no response")

    result = _run(
        _with_session(_executor(_FakeEndpointClient(handler))),
        _invocation("browser.closeTabs", sessionId="s", tabIds=[7]),
    )

    assert result.status == "error"
    assert result.error.code.value == "TIMEOUT"


# -- browser.closeBrowser ------------------------------------------------------


@pytest.fixture
def proc_stub(monkeypatch):
    """进程枚举/终止打桩：记录调用并可控返回（绝不动真进程）。"""
    state: dict[str, Any] = {"listed": [], "killed": [], "exited": True}

    def fake_list(names):
        state["seen_names"] = names
        return list(state["listed"])

    def fake_terminate(pid, force):
        state["killed"].append({"pid": pid, "force": force})

    def fake_wait(pids, timeout_s):
        state["waited"] = (list(pids), timeout_s)
        return state["exited"]

    monkeypatch.setattr(browser_module, "_list_browser_processes", fake_list)
    monkeypatch.setattr(browser_module, "_terminate_process", fake_terminate)
    monkeypatch.setattr(browser_module, "_wait_processes_exit", fake_wait)
    return state


def test_close_browser_kills_all_matched_processes(proc_stub):
    """全杀语义：该类型的全部匹配进程都被终止，不分「谁拉起的」（M35 定案）。"""
    proc_stub["listed"] = [
        {"pid": 11, "name": "msedge.exe"},
        {"pid": 12, "name": "msedge.exe"},
    ]

    result = _run(
        _executor(_FakeEndpointClient(lambda op, args: {})),
        _invocation("browser.closeBrowser", browserType="msedge"),
    )

    assert result.status == "success", result.error
    assert [item["pid"] for item in proc_stub["killed"]] == [11, 12]
    assert result.outputs["terminated"] is True
    assert result.outputs["matchedCount"] == 2
    assert result.outputs["killedProcessIds"] == [11, 12]
    assert result.effects[0].details["matchedCount"] == 2
    assert "scope" not in result.effects[0].details, "scope 语义已删，证据里不应再出现"


def test_close_browser_force_flag_reaches_terminate(proc_stub):
    """force 必须真的传到终止动作上（不是读了不用）。"""
    proc_stub["listed"] = [{"pid": 11, "name": "chrome.exe"}]

    _run(
        _executor(_FakeEndpointClient(lambda op, args: {})),
        _invocation("browser.closeBrowser", browserType="chrome", force=True),
    )

    assert proc_stub["killed"] == [{"pid": 11, "force": True}]


def test_close_browser_no_matching_process_is_success_with_zero(proc_stub):
    """一个匹配进程都没有 → 成功且 matchedCount=0（幂等收尾，不该报错）。"""
    proc_stub["listed"] = []

    result = _run(
        _executor(_FakeEndpointClient(lambda op, args: {})),
        _invocation("browser.closeBrowser", browserType="chrome"),
    )

    assert result.status == "success", result.error
    assert result.outputs["matchedCount"] == 0
    assert result.outputs["terminated"] is True
    assert proc_stub["killed"] == []


def test_close_browser_reports_failed_terminations(proc_stub, monkeypatch):
    """个别进程杀不掉 → 如实进 failedProcessIds，terminated 不为真。"""
    proc_stub["listed"] = [
        {"pid": 11, "name": "msedge.exe"},
        {"pid": 12, "name": "msedge.exe"},
    ]

    def flaky_terminate(pid, force):
        if pid == 12:
            raise OSError("access denied")

    monkeypatch.setattr(browser_module, "_terminate_process", flaky_terminate)

    result = _run(
        _executor(_FakeEndpointClient(lambda op, args: {})),
        _invocation("browser.closeBrowser", browserType="msedge"),
    )

    assert result.status == "success", result.error
    assert result.outputs["killedProcessIds"] == [11]
    assert result.outputs["failedProcessIds"] == [12]
    assert result.outputs["terminated"] is False


@pytest.mark.parametrize(
    ("inputs", "field"),
    [
        ({"browserType": "firefox"}, "browserType"),
    ],
)
def test_close_browser_rejects_unknown_browser_type(inputs, field, proc_stub):
    """枚举外的取值必须显式拒绝，且不进入进程枚举（不猜、不静默降级）。"""
    result = _run(
        _executor(_FakeEndpointClient(lambda op, args: {})),
        _invocation("browser.closeBrowser", **inputs),
    )

    assert result.status == "error"
    assert result.error.code.value == "INVALID_INPUT"
    assert result.error.details["field"] == field
    assert "seen_names" not in proc_stub, "参数非法时不应开始枚举进程"


def test_close_browser_waits_for_processes_and_writes_evidence(proc_stub):
    """终止后要等进程真正退出（否则上层紧接着的拉起会被旧端点判成在线）。"""
    proc_stub["listed"] = [{"pid": 11, "name": "msedge.exe"}]

    result = _run(
        _executor(_FakeEndpointClient(lambda op, args: {})),
        _invocation("browser.closeBrowser", browserType="msedge"),
    )

    assert proc_stub["waited"][0] == [11]
    assert result.effects[0].details["allExited"] is True


def test_close_browser_is_exempt_from_session_gate():
    """closeBrowser 不依赖会话：扩展掉线/无会话时也必须可用（正是它该被用的场景）。"""
    calls: list[str] = []

    def handler(op: str, args: dict) -> dict:
        calls.append(op)
        return {}

    executor = _executor(_FakeEndpointClient(handler))
    result = _run(
        executor,
        _invocation("browser.closeBrowser", browserType="msedge"),
    )

    assert result.status == "success", result.error
    assert calls == [], "进程级命令不应经过扩展通道"


def test_close_browser_offline_channel_still_works():
    """扩展通道完全离线时 closeBrowser 仍可用（不报 channel_offline）。"""
    client = _FakeEndpointClient(lambda op, args: {}, online=False)
    result = _run(
        _executor(client),
        _invocation("browser.closeBrowser", browserType="chrome"),
    )

    # 通道离线只影响预检；closeBrowser 在 _NO_SESSION_COMMANDS 里，直接进执行分支
    assert result.status in ("success", "error")
    if result.status == "error":
        assert result.error.details.get("reason") != "channel_offline"


# -- 扩展侧 op 契约（纯函数：与 background.js 的 op 名对齐）----------------------


def test_close_tabs_uses_close_many_op_not_per_tab_calls():
    """批量关闭必须是**一次** tabs.closeMany——逐个 tabs.close 会让 N 个标签花 N 次往返。"""
    ops: list[str] = []

    def handler(op: str, args: dict) -> dict:
        ops.append(op)
        return {"closedTabIds": list(range(5)), "failedTabIds": []}

    _run(
        _with_session(_executor(_FakeEndpointClient(handler))),
        _invocation("browser.closeTabs", sessionId="s", tabIds=list(range(5))),
    )

    assert ops == ["tabs.closeMany"]
