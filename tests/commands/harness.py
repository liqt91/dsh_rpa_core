"""指令测试 L1 契约层的公共设施：脚本化假扩展、执行器组装、命令运行。

为什么要有这一层（依据 `docs/command-testing-strategy.md` §2）：
`tests/contract/test_browser_contract.py` 原有的 6 项只覆盖「会话缺失 / 扩展离线」两类
环境性负路径，**参数级行为与输出契约零覆盖**。而真出过的漂移都藏在参数分支里——
M28 S3 的 `keyIntervalMs` 被静默忽略、M29 的 `modifiers` 勾了等于没勾、`clickPosition`
连坐标都没发出去。只有「每个参数 × 每个枚举值都有断言」才抓得住这类病。

**插桩点**：`PlaywrightExecutor(ext_session=ExtensionExecSession(client=...))`——
执行器与通道客户端都是构造注入，不需要 monkeypatch。假扩展只替换最底层的
`_exchange`（单条收口），所以 `submit` 的端点选择、失败重发、错误整形、往返度量
**仍然走真实实现**：用例断言的是「执行器真实下发了什么」，不是「桩被怎么调用」。

`targetHost` 不进 payload（只用于 `_targets` 选端点），因此多浏览器路由断言的唯一
位置是 `targets_seen`——这是刻意的：把「路由」与「参数」两件事分开断言。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rpa_core.executors.browser import PlaywrightExecutor
from rpa_core.executors.browser_ext import ExtensionExecSession
from rpa_core.extension_exec import ExtensionChannelError, ExtensionExecClient
from rpa_core.model.command import CommandInvocation, CommandResult

# 1×1 透明 PNG：screenshot 用例需要「扩展真的回了图像数据」——执行器要 base64 解码
# 后落盘，回空串会走「扩展未返回有效截图数据」的失败分支，断言就落不到 outputs 上。
TINY_PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8AAAwAB/AF+2gAAAABJRU5ErkJggg=="
)

# page.call 各原语的默认应答结果（按 method 给**类型正确**的最小值）。
# 不用例声明 stub 时，命令也要能走完「解析应答 → 整形 outputs」这段：给错类型的
# 默认值会让执行器在整形阶段就炸，用例失败的原因就与它要验证的参数无关了。
_DEFAULT_PAGE_RESULTS: dict[str, Any] = {
    "count": 1,
    "getText": "text",
    "queryAll": [],
    "getPosition": {"x": 0, "y": 0, "width": 0, "height": 0},
    "getScrollPosition": {"scrollX": 0, "scrollY": 0},
    "getSelectOptions": {"options": [], "count": 0},
    "scroll": 0,
    "check": True,
}

# 各 op 的默认应答体（`submit` 返回信封里的 `value`）。形状取自 `browser_ext.py`
# 里每个 op 封装函数的消费点——例如 `tabs.list` 读 `payload["tabs"]`。
_DEFAULT_REPLIES: dict[str, Any] = {
    "ping": {},
    "tabs.list": {"tabs": []},
    "tabs.listWindows": {"windows": []},
    "tabs.create": {"tabId": "1", "url": "https://example.test/", "instanceId": "inst"},
    "tabs.navigate": {"url": "https://example.test/"},
    "tabs.history": {"url": "https://example.test/"},
    "tabs.close": {},
    "tabs.closeMany": {"closedTabIds": [], "failedTabIds": []},
    "page.eval": {"result": None},
    "cookies.getAll": {"cookies": []},
    "cookies.get": {"value": None},
    "cookies.set": {"count": 0},
    "cookies.remove": {"count": 0},
    "screenshot": {"dataUrl": TINY_PNG_DATA_URL},
    "tabs.stopLoading": {"url": "https://example.test/"},
    "tabs.waitLoad": {"url": "https://example.test/"},
}

# 默认会话：`s` → tabId 7。用例省略 sessionId 时解析到它（缺省会话解析走真实实现）。
DEFAULT_SESSION_ID = "s"
DEFAULT_TAB_ID = "7"


@dataclass
class ExtensionCall:
    """一次真实下发给扩展的命令（op + args + 通道超时）。

    `timeout_seconds` 是信封里的 `timeoutSeconds`——`timeoutMs` 这类「只改变等待时长、
    不进 args」的参数，只有它能证明真的被消费了（否则用例只能断言「没报错」）。
    """

    op: str
    args: dict[str, Any]
    timeout_seconds: float = 0.0


class ScriptedExtension(ExtensionExecClient):
    """假扩展：按脚本应答，并逐条记录真实下发的 (op, args)。

    - ``replies``：op → 应答体（信封 `value` 的内容）；未声明的 op 走默认表。
    - ``errors``：op → ``ExtensionChannelError``，用于扩展侧错误映射的用例。
    - ``online``：False 时 `_targets` 返回空 → 通道离线（负路径）。
    - ``calls``：按调用顺序记录的 (op, args)。
    - ``targets_seen``：每次选端点时收到的 targetHost（多浏览器路由断言的唯一位置）。
    """

    def __init__(
        self,
        *,
        replies: dict[str, Any] | None = None,
        errors: dict[str, Exception] | None = None,
        online: bool = True,
    ):
        super().__init__()
        self._replies = dict(replies or {})
        self._errors = dict(errors or {})
        self._online = online
        self.calls: list[ExtensionCall] = []
        self.targets_seen: list[str | None] = []

    # -- ExtensionExecClient 的两个钩子 --------------------------------------

    def _targets(self, target_host: str | None) -> list[str]:
        self.targets_seen.append(target_host)
        return ["fake_ep"] if self._online else []

    def _exchange(
        self,
        endpoint: str,
        payload: dict[str, Any],
        timeout: float,
        *,
        expect_type: str = "result",
        expect_id: str = "",
    ) -> dict[str, Any]:
        if expect_type == "status":
            return {
                "type": "status",
                "browser": "msedge",
                "instanceId": "inst",
                "online": True,
                "extension": {"version": "test"},
            }
        op = str(payload.get("op") or "")
        args = dict(payload.get("args") or {})
        self.calls.append(
            ExtensionCall(
                op=op,
                args=args,
                timeout_seconds=float(payload.get("timeoutSeconds") or 0.0),
            )
        )
        if op in self._errors:
            raise self._errors[op]
        return {
            "type": "result",
            "id": payload.get("id"),
            "ok": True,
            "value": self._reply(op, args),
        }

    def _reply(self, op: str, args: dict[str, Any]) -> Any:
        if op in self._replies:
            return self._replies[op]
        if op == "page.call":
            method = str(args.get("method") or "")
            return {"matchedCount": 1, "result": _DEFAULT_PAGE_RESULTS.get(method)}
        return _DEFAULT_REPLIES.get(op, {})

    # -- 便捷读取（断言里少写样板） ------------------------------------------

    @property
    def ops(self) -> list[str]:
        return [call.op for call in self.calls]

    def only_call(self) -> ExtensionCall:
        """恰好一次调用时取出它——多于一次直接报错，避免「断言打在错误那条上」。"""
        assert len(self.calls) == 1, f"expect exactly 1 call, got {self.ops}"
        return self.calls[0]

    def last_call(self) -> ExtensionCall:
        assert self.calls, "no extension call was made"
        return self.calls[-1]


def make_executor(
    ext: ScriptedExtension,
    *,
    sessions: dict[str, str] | None = None,
    flow_dir: Path | None = None,
) -> PlaywrightExecutor:
    """组装执行器并预置会话（默认 `s` → tabId 7）。

    `sessions=None` 时用默认会话并把它设为「最近激活」，让省略 sessionId 的命令能解析到它
    （缺省会话解析是真实实现，我们只是把会话表填好——真实会话由 navigate/attach 建立）。
    传 `sessions={}` 可得到「无任何会话」的状态，用于会话缺失的负路径。
    """
    executor = PlaywrightExecutor(ext_session=ExtensionExecSession(client=ext), flow_dir=flow_dir)
    table = {DEFAULT_SESSION_ID: DEFAULT_TAB_ID} if sessions is None else dict(sessions)
    for session_id, tab_id in table.items():
        executor._ext_sessions[session_id] = tab_id
        # host 留空 = 任意路由；需要断言路由的用例改用 make_executor_with_host
        executor._ext_session_hosts[session_id] = ""
    if sessions is None:
        executor._last_session_id = DEFAULT_SESSION_ID
    return executor


def run_command(
    ext: ScriptedExtension,
    command: str,
    inputs: dict[str, Any] | None = None,
    *,
    sessions: dict[str, str] | None = None,
    flow_dir: Path | None = None,
) -> CommandResult:
    """以给定输入执行一条命令，返回 CommandResult（同步入口，内部自建事件循环）。"""
    executor = make_executor(ext, sessions=sessions, flow_dir=flow_dir)
    invocation = CommandInvocation(
        command_id=command,
        command_version="1.0.0",
        run_id="run",
        step_id="step",
        inputs=dict(inputs or {}),
    )
    return asyncio.run(executor.execute(invocation, asyncio.Event()))


__all__ = [
    "DEFAULT_SESSION_ID",
    "DEFAULT_TAB_ID",
    "ExtensionCall",
    "ScriptedExtension",
    "TINY_PNG_DATA_URL",
    "make_executor",
    "run_command",
]

# 供用例构造扩展侧错误（与 `_errors` 配合）
ExtensionError = ExtensionChannelError
