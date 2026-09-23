"""L2 真机冒烟驱动（浏览器通道）：与 L1 **同一份用例表**，后端换成真扩展。

策略 §2/§5.1 定案「两边共用同一份表」的落地形态：变体上加可选 **`l2` 块**——
L1 的 `expect` 一个字不动（它是按假扩展应答写的契约断言），`l2` 带真机语义：

```json
"l2": {
  "page": "basic",                 // testapps/browser/<page>.html
  "inputs": {"selector": "#kw"},   // 覆盖 variant.inputs（可选；如换靶子元素）
  "expect": {...},                 // 真机期望：outputs/effect/errorCode/files 等
  "pre": [...],                    // 可选：主命令之前的准备步（如建历史、开第二页）
  "verify": [...],                 // 可选：主命令之后的独立读侧
  "session": false                 // 可选：显式不要预置会话（缺省按命令推断）
}
```

pre/verify 步同形：`{command, inputs?, expect?, saveAs?}`，与主命令**同执行器、
同会话**；pre 步必须成功（准备失败即本变体失败），`saveAs: "名"` 把该步
outputs.tabId 存进占位符 `{名}` 供后续步骤引用（closeTabs 要真 tabId）。

## 占位符

`{tmp}` 同 L1；`{page}` → 本变体靶页 URL；`{page:NAME}` → 任意靶页 URL
（`testapps/browser/NAME.html`，含 slow 这条动态路由）；`{tab}` → 预置会话的
真实 tabId（整串占位，保 int 类型）。**顺序敏感**：`{page:NAME}` 先于 `{page}`
替换（否则子串腐蚀）。expect 侧同样替换（listPages/attach 的 URL 断言靠它）。

## 预置会话的推断

- `browser.attach` / `browser.listPages` 是无会话命令，从不预置；
- `browser.navigate` 的 inputs 没有 `sessionId` 时不预置（「无会话新建标签页」
  正是它的 L2 语义）；
- 其余命令缺省预置（navigate 到 `l2.page` 建会话，inputs 里的 `sessionId`
  一律替换成真会话）；`l2.session: false` 强制不预置（close 的空会话负路径）。

## 与 L1 的差别（就这一处）

后端：`ScriptedExtension` → **真的** `ExtensionExecClient`。执行器、会话解析、
错误整形全部走真实实现，命令经「扩展 connectNative → bridge host → 命名管道
端点」落到真实 DOM 上。`expect` 键天然分两层：`onlyCall`/`calls`/`noCalls`
是 L1 独有（L2 没有调用记录面），其余键后端无关——静态校验器强制 `l2.expect`
不得出现调用类键。

## 隔离与门禁

见 `l2_harness.py`（装配）与 `tests/commands/conftest.py`（收集门禁）。
`RPA_BROWSER_L2=1` 才跑；会拉起真实浏览器窗口（独立 profile，不碰本机真实
浏览器及其端点）。占位符：`{tmp}` 同 L1；`{page}` → 本变体靶页 URL；
inputs 里的 `sessionId` 一律替换成本变体真建的会话。
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from typing import Any

import pytest

from rpa_core.executors.browser import PlaywrightExecutor
from rpa_core.executors.browser_ext import ExtensionExecSession
from rpa_core.extension_exec import list_extension_endpoints
from rpa_core.model.command import CommandInvocation
from tests.commands.l2_harness import (
    TEST_ENDPOINT_PREFIX,
    l2_browser_session,
)
from tests.commands.matrix import (
    check_expect,
    load_cases,
    materialize_inputs,
    substitute_extra,
    variant_marks,
)

L2_ENABLED = os.environ.get("RPA_BROWSER_L2") == "1"
L2_ENV_HINT = "RPA_BROWSER_L2=1"

# 目标浏览器（msedge/chrome），默认 msedge——开发基线浏览器。
L2_BROWSER = os.environ.get("RPA_BROWSER_L2_BROWSER", "msedge").strip().lower() or "msedge"

# 无会话命令：它们自建/不需要会话，驱动从不为它们预置。
_NO_SESSION_COMMANDS = ("browser.attach", "browser.listPages")

_PAGE_TOKEN = re.compile(r"\{page:([\w-]+)\}")

pytestmark = pytest.mark.skipif(
    not L2_ENABLED,
    reason=f"L2 真机冒烟缺省跳过；设 {L2_ENV_HINT} 启用（会拉起真实浏览器窗口）",
)


def _iter_l2_variants():
    """把用例表里有 `l2` 块的变体展开成参数化用例（id 与 L1 同形）。"""
    for command, spec in sorted(load_cases("browser").items()):
        for variant in spec.get("variants", []):
            if "l2" not in variant:
                continue
            yield pytest.param(
                command,
                spec,
                variant,
                id=f"{command}::{variant['name']}",
                marks=variant_marks(variant),
            )


def _run_command(executor: PlaywrightExecutor, command: str, inputs: dict[str, Any]):
    """同步执行一条命令（执行器由调用方持有——L2 的会话是 navigate 真建的）。"""
    invocation = CommandInvocation(
        command_id=command,
        command_version="1.0.0",
        run_id="l2",
        step_id=command,
        inputs=dict(inputs),
    )
    return asyncio.run(executor.execute(invocation, asyncio.Event()))


def _substitute_page_tokens(value: Any, l2_browser) -> Any:
    """`{page:NAME}` → 靶页 URL（递归）。必须先于 `{page}` 的替换跑——

    否则 `"{page:other}".replace("{page}", url)` 会把命名页占位符腐蚀成
    `http://…:PORT:other}`。
    """
    if isinstance(value, str):
        return _PAGE_TOKEN.sub(lambda m: l2_browser.page_url(m.group(1)), value)
    if isinstance(value, dict):
        return {key: _substitute_page_tokens(item, l2_browser) for key, item in value.items()}
    if isinstance(value, list):
        return [_substitute_page_tokens(item, l2_browser) for item in value]
    return value


def _materialize(
    raw: dict[str, Any],
    l2_browser,
    tmp_dir,
    extra: dict[str, Any],
    session_id: str | None,
) -> dict[str, Any]:
    """inputs 的统一物化：{page:NAME} → materialize（{page}/{tab}/{tmp}）→ 会话替换。"""
    value = _substitute_page_tokens(raw, l2_browser)
    value = materialize_inputs(value, tmp_dir, extra=extra)
    if session_id is not None and "sessionId" in value:
        # L1 的 "s" 是假会话记号；L2 换成真建的会话
        value["sessionId"] = session_id
    return value


@pytest.fixture(scope="session")
def l2_browser():
    """session 级装配：起靶页服务 → 拉起隔离浏览器 → 等扩展上线（细节在 l2_harness）。

    刻意**直接调 `extension_launch.launch_browser`**（l2_harness 内）：conftest 的
    autouse 守卫 `_block_destructive_browser_ops` 桩掉的是 `executors.browser`
    命名空间里的绑定（L1 用例不许拉起浏览器），L2 的存在意义就是拉真浏览器——
    这是显式绕过，不是守卫失效。
    """
    if not L2_ENABLED:
        pytest.skip(f"L2 未启用（{L2_ENV_HINT}）")
    with l2_browser_session(L2_BROWSER) as l2:
        yield l2


def test_l2_endpoint_isolation(l2_browser):
    """隔离断言独立成用例：客户端可见端点必须全部带测试前缀。

    若混进无前缀端点，说明前缀隔离没生效，后续命令可能下到维护者真实浏览器——
    这条必须先于任何真命令红出来。
    """
    endpoints = list_extension_endpoints()
    assert endpoints, "没有任何测试前缀端点（fixture 的上线等待应已挡住这种情况）"
    assert all(name.startswith(TEST_ENDPOINT_PREFIX) for name in endpoints), (
        f"端点 {endpoints} 不含测试前缀 {TEST_ENDPOINT_PREFIX!r}——隔离失效"
    )


@pytest.mark.parametrize("command, spec, variant", list(_iter_l2_variants()))
def test_command_variant(l2_browser, matrix_tmp_dir, command, spec, variant):
    """表驱动主体：预置会话（按需）→ pre 步 → 主命令 → expect + verify。

    每个变体用**新执行器 + 新会话**：变体间零状态泄漏（勾选/输入的副作用不串）。
    """
    l2 = variant["l2"]
    page_url = l2_browser.page_url(l2["page"])
    executor = PlaywrightExecutor(
        ext_session=ExtensionExecSession(client=l2_browser.client)
    )

    merged_inputs = {**variant.get("inputs", {}), **l2.get("inputs", {})}

    # -- 预置会话（推断规则见模块 docstring） -----------------------------------
    session_id = None
    extra: dict[str, Any] = {"page": page_url}
    want_session = (
        command not in _NO_SESSION_COMMANDS
        and bool(l2.get("session", True))
        and not (command == "browser.navigate" and "sessionId" not in merged_inputs)
    )
    if want_session:
        nav = _run_command(
            executor,
            "browser.navigate",
            {
                "browserType": l2_browser.browser,
                "action": "goto",
                "url": page_url,
            },
        )
        assert nav.status == "success", (
            f"L2 预置会话失败（{page_url}）：{nav.model_dump_json()}"
        )
        session_id = nav.outputs["sessionId"]
        if "tabId" in nav.outputs:
            # tabId 在 navigate outputs 里是字符串，而 listPages/closedTabIds 等处
            # 是 int——{tab} 统一按 int 注入，免得 expect 侧字符串比 int 假红
            extra["tab"] = int(nav.outputs["tabId"])

    # -- pre 步：建历史/开第二页等准备（必须成功，否则本变体失败） ----------------
    for index, step in enumerate(l2.get("pre") or []):
        step_inputs = _materialize(
            step.get("inputs", {}), l2_browser, matrix_tmp_dir, extra, session_id
        )
        step_result = _run_command(executor, step["command"], step_inputs)
        assert step_result.status == "success", (
            f"L2 pre[{index}] {step['command']} 失败：{step_result.model_dump_json()}"
        )
        if step.get("saveAs"):
            extra[str(step["saveAs"])] = step_result.outputs.get("tabId")

    # -- 主命令 ------------------------------------------------------------------
    inputs = _materialize(merged_inputs, l2_browser, matrix_tmp_dir, extra, session_id)

    started = time.perf_counter()
    result = _run_command(executor, command, inputs)
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    expect = substitute_extra(_substitute_page_tokens(l2["expect"], l2_browser), extra)
    problems = check_expect(
        expect,
        result=result,
        tmp_dir=matrix_tmp_dir,
        calls=None,  # L2 没有调用记录面；l2.expect 出现调用类键会被这里判违规
        elapsed_ms=elapsed_ms,
    )

    # -- verify 步：主命令之后的独立读侧（同执行器、同会话） -------------------
    for index, step in enumerate(l2.get("verify") or []):
        step_inputs = _materialize(
            step.get("inputs", {}), l2_browser, matrix_tmp_dir, extra, session_id
        )
        if session_id is not None and "sessionId" not in step_inputs:
            step_inputs["sessionId"] = session_id
        step_result = _run_command(executor, step["command"], step_inputs)
        step_expect = substitute_extra(
            _substitute_page_tokens(step["expect"], l2_browser), extra
        )
        for problem in check_expect(
            step_expect, result=step_result, tmp_dir=matrix_tmp_dir, calls=None
        ):
            problems.append(f"[verify[{index}] {step['command']}] {problem}")

    assert not problems, "\n".join(["L2 用例断言失败：", *problems])
