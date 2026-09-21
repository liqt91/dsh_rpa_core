"""M28 S4 扩展通道度量契约：每步的往返数与耗时必须如实进执行证据。

为什么要有这一层：性能回归在浏览器自动化里几乎从不表现为「崩了」，而是表现为
**每步多打了几次往返**（多一次探测、自愈候选退化成逐条重试、跨端点反复重发）。
往返数可确定性断言、不需要真浏览器，所以：

- 计数点只有一处（`ExtensionExecClient` 的传输层）。本文件用「假端点 + 真客户端」
  的桩：`submit` / `status` / 端点选择 / 错误整形 / 度量累计全是生产实现，只有最
  底层的 `_exchange` 换成预设应答——断言的数字就是生产路径上的数字。
- 每步往返数进 `CommandResult.diagnostics["extension"]`（成功与失败都有），再随
  `scopes.steps.<node>.diagnostics` 与 checkpoint 落库。

两个容易被误解的口径（本文件就是它们的规格说明）：

1. **会话内命令 = 1 次 status 探测 + 1 次命令往返**。执行器在发命令前先确认扩展还在
   轮询（否则会白等 timeoutMs），这次探测也是真实往返，故 `statusProbes` 单独计量、
   不混进 `ops`。
2. 探测带 2s TTL 缓存，**连续两步不会各探一次**——所以基线是「每步 1 次命令往返，
   探测按 TTL 摊销」。

基线表（每条命令几个来回）见 `docs/extension-channel-baseline.md`。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from rpa_core.executors.browser import PlaywrightExecutor
from rpa_core.executors.browser_ext import ExtensionExecSession
from rpa_core.extension_exec import ChannelMetrics, ExtensionChannelError, ExtensionExecClient
from rpa_core.model.command import CommandInvocation


class _FakeEndpointClient(ExtensionExecClient):
    """把最底层 `_exchange` 换成预设应答的假端点（其余全走真实实现）。"""

    def __init__(self, handler, *, status_reply: dict | None = None, endpoints: int = 1):
        super().__init__()
        self._handler = handler
        self._status_reply = status_reply
        self._endpoints = endpoints
        self.envelopes: list[dict] = []

    def _targets(self, target_host):
        return [f"fake_{index}" for index in range(self._endpoints)]

    def _exchange(self, endpoint, payload, timeout, *, expect_type="result", expect_id=""):
        self.envelopes.append({"endpoint": endpoint, **payload})
        if expect_type == "status":
            reply = self._status_reply if self._status_reply is not None else {"online": True}
            return {"type": "status", "browser": "msedge", "instanceId": "inst", **reply}
        # 多端点桩：第一个端点恒不可用，用来验「跨端点重发」的计数口径
        if self._endpoints > 1 and endpoint == "fake_0":
            raise ExtensionChannelError("CHANNEL_OFFLINE", "first endpoint is dead")
        outcome = self._handler(str(payload.get("op") or ""), payload.get("args") or {})
        if isinstance(outcome, Exception):
            raise outcome
        return {"type": "result", "id": payload.get("id"), "ok": True, "value": outcome}


def _page_handler(by_selector: dict[str, dict]):
    """page.call 按 selector 回预设 payload（未列出的 → 0 命中）。"""

    def handle(op: str, args: dict) -> dict:
        assert op == "page.call", f"unexpected op: {op}"
        return by_selector.get(str(args.get("selector")), {"matchedCount": 0, "result": None})

    return handle


def _executor(client: _FakeEndpointClient, flow_dir: Path | None = None) -> PlaywrightExecutor:
    return PlaywrightExecutor(
        ext_session=ExtensionExecSession(client=client), flow_dir=flow_dir
    )


def _invocation(command: str, **inputs) -> CommandInvocation:
    return CommandInvocation(
        command_id=command,
        command_version="1.0.0",
        run_id="run",
        step_id="step",
        inputs=inputs,
    )


def _run(executor: PlaywrightExecutor, invocation: CommandInvocation):
    """带会话执行（模拟「打开网页」之后的一步）。"""
    executor._ext_sessions["s"] = "7"
    executor._last_session_id = "s"
    return asyncio.run(executor.execute(invocation, asyncio.Event()))


def _metrics(result) -> dict:
    return result.diagnostics["extension"]


def _write_element(flow_dir: Path, name: str, css: str, candidates: list[dict]) -> None:
    elements = flow_dir / "elements"
    elements.mkdir(parents=True, exist_ok=True)
    (elements / f"{name}.json").write_text(
        json.dumps(
            {
                "name": name,
                "kind": "browser",
                "selector": {"css": css, "candidates": candidates},
                "metadata": {"matchedCount": 1},
            }
        ),
        encoding="utf-8",
    )


def test_session_command_pays_one_probe_plus_one_command_round_trip():
    """会话内命中命令 = 1 次 status 探测 + 1 次 page.call（基线的最小单元）。"""
    client = _FakeEndpointClient(_page_handler({"#ok": {"matchedCount": 1, "result": True}}))
    result = _run(_executor(client), _invocation("browser.click", selector="#ok", sessionId="s"))

    assert result.status == "success", result.error
    metrics = _metrics(result)
    assert metrics["roundTrips"] == 2
    assert metrics["ops"] == 1
    assert metrics["statusProbes"] == 1
    assert metrics["retries"] == 0
    assert metrics["byOp"] == {"page.call": 1}
    assert metrics["stepMs"] >= 0
    assert metrics["channelShare"] is not None
    assert len(client.envelopes) == 2  # 与真实发出的信封数一致
    json.dumps(result.diagnostics)  # 必须可 JSON 序列化（会落进 checkpoint/events）


def test_status_probe_is_amortized_by_ttl_cache():
    """连续两步只探测一次（2s TTL）：第二步起只剩命令往返。"""
    client = _FakeEndpointClient(_page_handler({"#ok": {"matchedCount": 1, "result": True}}))
    executor = _executor(client)

    first = _run(executor, _invocation("browser.click", selector="#ok", sessionId="s"))
    second = _run(executor, _invocation("browser.click", selector="#ok", sessionId="s"))

    assert _metrics(first)["roundTrips"] == 2  # 探测 + 命令
    assert _metrics(second)["roundTrips"] == 1  # 复用探测缓存
    assert _metrics(second)["statusProbes"] == 0
    assert _metrics(second)["ops"] == 1  # 按步清账，不累计上一步
    assert len(client.envelopes) == 3


def test_self_healing_candidate_costs_one_more_round_trip(tmp_path):
    """主选择器失效 + 1 条候选救回 → 命令往返从 1 变 2（自愈不是免费的，要看得见）。"""
    flow_dir = tmp_path / "flows" / "demo"
    _write_element(flow_dir, "ok", "#gone", [{"kind": "name", "selector": "button[name=a]"}])
    client = _FakeEndpointClient(
        _page_handler({"button[name=a]": {"matchedCount": 1, "result": True}})
    )
    result = _run(
        _executor(client, flow_dir),
        _invocation("browser.click", selector="#gone", sessionId="s"),
    )

    assert result.status == "success", result.error
    assert _metrics(result)["byOp"] == {"page.call": 2}
    assert _metrics(result)["roundTrips"] == 3  # 1 探测 + 2 命令
    assert _metrics(result)["retries"] == 0  # 同一端点内重试，不是端点重发


def test_precheck_failure_on_main_selector_is_one_command_round_trip(tmp_path):
    """主选择器命中但预检不过 → 只 1 次命令往返（不试候选），失败结果同样带度量。"""
    flow_dir = tmp_path / "flows" / "demo"
    _write_element(flow_dir, "ok", "#ok", [{"kind": "name", "selector": "button[name=ok]"}])
    client = _FakeEndpointClient(
        _page_handler({
            "#ok": {
                "matchedCount": 1,
                "result": None,
                "precheck": {"code": "ELEMENT_COVERED", "message": "被遮挡"},
            }
        })
    )
    result = _run(
        _executor(client, flow_dir),
        _invocation("browser.click", selector="#ok", sessionId="s"),
    )

    assert result.status == "error"
    assert result.error.code == "ELEMENT_COVERED"
    assert _metrics(result)["byOp"] == {"page.call": 1}
    assert [envelope["op"] for envelope in client.envelopes if "op" in envelope] == ["page.call"]


def test_channel_failure_still_records_round_trip():
    """命令整体失败（端点挂了）也记往返——失败路径的成本同样要可观测。"""

    def handler(op: str, args: dict) -> dict:
        raise ExtensionChannelError("CHANNEL_OFFLINE", "bridge gone")

    result = _run(
        _executor(_FakeEndpointClient(handler)),
        _invocation("browser.click", selector="#ok", sessionId="s"),
    )

    assert result.status == "error"
    assert _metrics(result)["ops"] == 1
    assert _metrics(result)["channelMs"] >= 0


def test_probe_only_command_counts_probe_without_ops():
    """没有会话时的探测：probes=1、ops=0、byOp 为空（探测不冒充命令）。"""
    client = _FakeEndpointClient(_page_handler({}))
    # 会话不存在 → 走「扩展在线？→ 报缺少会话」，全程只有一次探测
    result = asyncio.run(
        _executor(client).execute(_invocation("browser.click", selector="#ok"), asyncio.Event())
    )

    assert result.status == "error"
    metrics = _metrics(result)
    assert metrics["roundTrips"] == 1
    assert metrics["ops"] == 0
    assert metrics["statusProbes"] == 1
    assert metrics["byOp"] == {}


def test_cross_endpoint_retry_counted_as_retry_not_new_op():
    """多端点并存且第一个端点不可用 → 重发计入 retries，不冒充一条新命令。"""
    client = _FakeEndpointClient(
        _page_handler({"#ok": {"matchedCount": 1, "result": True}}), endpoints=2
    )
    result = _run(_executor(client), _invocation("browser.click", selector="#ok", sessionId="s"))

    assert result.status == "success", result.error
    metrics = _metrics(result)
    assert metrics["ops"] == 1  # 同一条命令，不是一个新命令
    assert metrics["retries"] == 1  # 换端点再发一次
    assert metrics["byOp"] == {"page.call": 1}
    assert len(client.envelopes) == 4  # 2 端点探测 + 2 端点命令


def test_command_without_channel_traffic_writes_no_metrics():
    """不碰通道的命令（关闭会话=解绑）不写空壳度量。"""
    client = _FakeEndpointClient(_page_handler({}))
    result = _run(_executor(client), _invocation("browser.close", sessionId="s"))

    assert result.status == "success", result.error
    assert "extension" not in result.diagnostics
    assert client.envelopes == []


def test_test_stub_without_metrics_attribute_is_tolerated():
    """老桩没有 metrics 属性 → 度量静默跳过，命令行为不变（向后兼容）。"""

    class _Stub:
        def status(self):
            return {"online": True}

        def page_call(self, tab_id, selector, method, *, args=None, timeout_seconds=1,
                      target_host=None):
            return {"matchedCount": 1, "result": True}

    result = _run(
        PlaywrightExecutor(ext_session=_Stub()),
        _invocation("browser.click", selector="#ok", sessionId="s"),
    )

    assert result.status == "success", result.error
    assert result.diagnostics == {}


def test_metrics_snapshot_shape_is_stable():
    """度量字段名是工具契约（证据消费方按名读），改名要连带改文档与基线表。"""
    metrics = ChannelMetrics()
    metrics.record_envelope("page.call", exchange_ms=12.34, first=True)
    metrics.record_probe(exchange_ms=1.0)

    payload = metrics.as_diagnostics(step_ms=100.0)

    assert set(payload) == {
        "roundTrips", "ops", "statusProbes", "retries", "channelMs", "byOp",
        "stepMs", "channelShare",
    }
    assert payload["roundTrips"] == 2
    assert payload["channelMs"] == 13.3
    assert payload["channelShare"] == 0.133


# -- 每条命令的往返数基线（表即测试）------------------------------------------
# `docs/extension-channel-baseline.md` 的「每命令信封数」一栏由本表机器校验：
# 谁让某条命令多打一次往返（或漏了一次），改文档没用——这里会红。

_OP_VALUES: dict[str, dict] = {
    "page.call": {"matchedCount": 1, "result": True},
    "page.eval": {"result": 1},
    "tabs.list": {"tabs": [{"id": 4, "url": "https://a.test/", "title": "a"}]},
    "tabs.create": {
        "tabId": "9", "url": "https://a.test/", "timedOut": False, "instanceId": "inst",
    },
    "tabs.navigate": {"url": "https://a.test/"},
    "tabs.history": {"url": "https://a.test/"},
    "tabs.waitLoad": {"url": "https://a.test/"},
    "tabs.stopLoading": {"url": "https://a.test/"},
    "screenshot": {"dataUrl": "data:image/png;base64,QUJD"},
    "cookies.get": {"value": "v"},
    "cookies.getAll": {"cookies": [{"name": "n", "value": "v"}]},
    "cookies.set": {"count": 1},
    "cookies.remove": {"count": 1},
}


def _matrix_handler(op: str, args: dict) -> dict:
    assert op in _OP_VALUES, f"未在往返数基线表里登记的 op：{op}（新增命令请一并登记）"
    return dict(_OP_VALUES[op])


# (command, inputs, 期望 byOp)；`{tmp}` 会被替换成本用例的临时目录
_BASELINE_CASES = [
    ("browser.click", {"selector": "#ok"}, {"page.call": 1}),
    ("browser.hover", {"selector": "#ok"}, {"page.call": 1}),
    ("browser.input", {"selector": "#ok", "text": "x"}, {"page.call": 1}),
    ("browser.scroll", {"position": "bottom"}, {"page.call": 1}),
    ("browser.select", {"selector": "#ok", "option": "a"}, {"page.call": 1}),
    ("browser.check", {"selector": "#ok"}, {"page.call": 1}),
    ("browser.getText", {"selector": "#ok"}, {"page.call": 1}),
    ("browser.waitFor", {"selector": "#ok", "state": "visible"}, {"page.call": 1}),
    ("browser.queryAll", {"selector": "#ok"}, {"page.call": 1}),
    ("browser.getPosition", {"selector": "#ok"}, {"page.call": 1}),
    ("browser.getScrollPosition", {"selector": "#ok"}, {"page.call": 1}),
    ("browser.getSelectOptions", {"selector": "#ok"}, {"page.call": 1}),
    ("browser.setValue", {"selector": "#ok", "value": "v"}, {"page.call": 1}),
    ("browser.setAttribute", {"selector": "#ok", "name": "n", "value": "v"}, {"page.call": 1}),
    ("browser.drag", {"selector": "#ok", "targetSelector": "#dst"}, {"page.call": 1}),
    ("browser.executeScript", {"script": "1+1"}, {"page.eval": 1}),
    ("browser.waitLoad", {}, {"tabs.waitLoad": 1}),
    ("browser.stopLoading", {}, {"tabs.stopLoading": 1}),
    ("browser.screenshot", {"savePath": "{tmp}/shot.png"}, {"screenshot": 1}),
    ("browser.navigate", {"action": "back"}, {"tabs.history": 1}),
    ("browser.cookieGetAll", {}, {"cookies.getAll": 1}),
    ("browser.cookieGet", {"name": "n"}, {"cookies.get": 1}),
    ("browser.cookieSet", {"cookies": [{"name": "n", "value": "v"}]}, {"cookies.set": 1}),
    ("browser.cookieRemove", {"name": "n"}, {"cookies.remove": 1}),
]


@pytest.mark.parametrize(
    ("command", "inputs", "expected_ops"), _BASELINE_CASES, ids=[c[0] for c in _BASELINE_CASES]
)
def test_command_round_trip_baseline(command, inputs, expected_ops, tmp_path):
    """会话内命令各打几个信封（不含 status 探测，探测单独计量）。"""
    resolved = {
        key: (value.replace("{tmp}", str(tmp_path)) if isinstance(value, str) else value)
        for key, value in inputs.items()
    }
    client = _FakeEndpointClient(_matrix_handler)
    result = _run(_executor(client), _invocation(command, sessionId="s", **resolved))

    assert result.status == "success", result.error
    assert _metrics(result)["byOp"] == expected_ops


def test_no_session_commands_and_detach_baseline():
    """无会话命令（打开网页/attach/listPages）与「不碰通道」的命令（close）。"""
    # 打开网页：status 探测（可能被 TTL 缓存省掉）+ tabs.create
    open_client = _FakeEndpointClient(_matrix_handler)
    opened = asyncio.run(
        _executor(open_client).execute(
            _invocation("browser.navigate", url="a.test"), asyncio.Event()
        )
    )
    assert opened.status == "success", opened.error
    assert _metrics(opened)["byOp"] == {"tabs.create": 1}

    # attach / listPages：一次 tabs.list
    attach_client = _FakeEndpointClient(_matrix_handler)
    attached = asyncio.run(
        _executor(attach_client).execute(
            _invocation("browser.attach", pattern="a.test"), asyncio.Event()
        )
    )
    assert attached.status == "success", attached.error
    assert _metrics(attached)["byOp"] == {"tabs.list": 1}

    pages_client = _FakeEndpointClient(_matrix_handler)
    pages = _run(_executor(pages_client), _invocation("browser.listPages", sessionId="s"))
    assert pages.status == "success", pages.error
    assert _metrics(pages)["byOp"] == {"tabs.list": 1}

    # close = 本地解绑，一个信封都不发（因此也不写度量）
    close_client = _FakeEndpointClient(_matrix_handler)
    closed = _run(_executor(close_client), _invocation("browser.close", sessionId="s"))
    assert closed.status == "success", closed.error
    assert "extension" not in closed.diagnostics
    assert close_client.envelopes == []


@pytest.mark.parametrize("command", ["browser.upload", "browser.download", "browser.handleDialog"])
def test_unimplemented_commands_spend_no_command_round_trip(command):
    """未实现命令（upload/download/handleDialog）只花一次在线探测，不假装有往返。"""
    client = _FakeEndpointClient(_matrix_handler)
    result = _run(_executor(client), _invocation(command, sessionId="s"))

    assert result.status == "error"
    assert result.error.code == "COMMAND_NOT_FOUND"
    metrics = _metrics(result)
    assert metrics["byOp"] == {}
    assert metrics["ops"] == 0


def test_polling_command_round_trips_scale_with_wait_time():
    """waitFor 是轮询式：命中前每 0.3s 一轮，每轮都是一次真实往返（往返数＞1 是设计）。"""
    polls = {"count": 0}

    def handler(op: str, args: dict) -> dict:
        if op == "page.call":
            polls["count"] += 1
            return {"matchedCount": 1 if polls["count"] >= 3 else 0, "result": None}
        return dict(_OP_VALUES[op])

    client = _FakeEndpointClient(handler)
    result = _run(
        _executor(client),
        _invocation("browser.waitFor", selector="#late", state="visible", sessionId="s"),
    )

    assert result.status == "success", result.error
    assert _metrics(result)["byOp"] == {"page.call": 3}
    assert _metrics(result)["ops"] == 3  # 轮询不是「重试」，每轮都是一条新命令信封


# -- 落库链路（真 catalog + 真编排器，只有扩展通道是假端点）---------------------


def test_metrics_reach_run_checkpoint(tmp_path):
    """端到端：往返数真的随 `scopes.steps.<node>.diagnostics` 落进 checkpoint。

    这是「每步往返数可复盘」的最后一环——不落库的话，度量只活在进程里，运行历史
    与 GUI 看不到，也就谈不上基线。
    """
    from rpa_core.catalog import load_catalog
    from rpa_core.compiler import WorkflowCompiler
    from rpa_core.executors import ExecutorRegistry
    from rpa_core.model.workflow import Workflow
    from rpa_core.runtime import Orchestrator

    commands_dir = Path(__file__).resolve().parents[2] / "commands"
    catalog = load_catalog(commands_dir)
    plan = WorkflowCompiler(catalog).compile(
        Workflow.model_validate(
            {
                "id": "metrics-run",
                "name": "metrics-run",
                "root": {
                    "type": "action",
                    "id": "list",
                    "command": "browser.listPages",
                    "with": {"sessionId": "s"},
                },
            }
        ),
        {"browser.read"},
    )
    client = _FakeEndpointClient(_matrix_handler)

    async def run():
        runs_dir = tmp_path / "runs"
        orchestrator = Orchestrator(
            catalog,
            ExecutorRegistry({"browser.playwright": _executor(client)}),
            runs_dir,
        )
        return await orchestrator.run(plan), runs_dir

    result, runs_dir = asyncio.run(run())
    assert result.status.value == "succeeded", result
    checkpoint = json.loads(
        (runs_dir / result.run_id / "checkpoint.json").read_text(encoding="utf-8")
    )
    steps = checkpoint["scopes"]["steps"]
    diagnostics = [step["diagnostics"] for step in steps.values()][0]

    assert diagnostics["extension"]["roundTrips"] == 2  # 1 次在线探测 + 1 次 tabs.list
    assert diagnostics["extension"]["byOp"] == {"tabs.list": 1}
    assert diagnostics["extension"]["statusProbes"] == 1
    assert diagnostics["extension"]["stepMs"] > 0  # perf_counter：不再被 Windows 粒度吃成 0
