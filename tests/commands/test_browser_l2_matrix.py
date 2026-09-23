"""L2 真机冒烟驱动（浏览器通道）：与 L1 **同一份用例表**，后端换成真扩展。

策略 §2/§5.1 定案「两边共用同一份表」的落地形态：变体上加可选 **`l2` 块**——
L1 的 `expect` 一个字不动（它是按假扩展应答写的契约断言），`l2` 带真机语义：

```json
"l2": {
  "page": "basic",                 // testapps/browser/<page>.html
  "inputs": {"selector": "#kw"},   // 覆盖 variant.inputs（可选；如换靶子元素）
  "expect": {...},                 // 真机期望：outputs/effect/errorCode/files 等
  "verify": [                      // 可选：主命令之后的独立读侧（S4.3 起）
    {"command": "browser.getText", "inputs": {...}, "expect": {...}}
  ]
}
```

`verify` 步与主命令**同执行器、同会话**，逐条跑并用各自的 `expect` 断言——
动作类命令的证据不在执行器自报的 outputs 里（那是自证），而在「另一条命令
独立读回的页面状态」里（S2.3 桌面 select 的双读侧教训搬到浏览器侧）。
没有 `l2` 块的变体在 L2 不收集（桩形状整形、通道错误映射、纯 timeoutSeconds
下发断言天然是 L1 的事——真后端上要么不可诱导、要么不可见）。

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
    variant_marks,
)

L2_ENABLED = os.environ.get("RPA_BROWSER_L2") == "1"
L2_ENV_HINT = "RPA_BROWSER_L2=1"

# 目标浏览器（msedge/chrome），默认 msedge——开发基线浏览器。
L2_BROWSER = os.environ.get("RPA_BROWSER_L2_BROWSER", "msedge").strip().lower() or "msedge"

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
    """表驱动主体：建会话到靶页 → 跑命令 → 断言 `l2.expect`（真机口径）。

    每个变体用**新执行器 + 新会话**：变体间零状态泄漏（勾选/输入的副作用不串）。
    """
    l2 = variant["l2"]
    page_url = l2_browser.page_url(l2["page"])
    executor = PlaywrightExecutor(
        ext_session=ExtensionExecSession(client=l2_browser.client)
    )

    # 预置会话：navigate 自身除外（它的 L2 语义就是「真建会话」）
    session_id = None
    if command != "browser.navigate":
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

    raw_inputs = {**variant.get("inputs", {}), **l2.get("inputs", {})}
    inputs = materialize_inputs(raw_inputs, matrix_tmp_dir, extra={"page": page_url})
    if session_id is not None and "sessionId" in inputs:
        # L1 的 "s" 是假会话记号；L2 换成真建的会话
        inputs["sessionId"] = session_id

    started = time.perf_counter()
    result = _run_command(executor, command, inputs)
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    problems = check_expect(
        l2["expect"],
        result=result,
        tmp_dir=matrix_tmp_dir,
        calls=None,  # L2 没有调用记录面；l2.expect 出现调用类键会被这里判违规
        elapsed_ms=elapsed_ms,
    )

    # -- verify 步：主命令之后的独立读侧（同执行器、同会话） -------------------
    for index, step in enumerate(l2.get("verify") or []):
        step_inputs = materialize_inputs(
            step.get("inputs", {}), matrix_tmp_dir, extra={"page": page_url}
        )
        if session_id is not None and "sessionId" not in step_inputs:
            step_inputs["sessionId"] = session_id
        step_result = _run_command(executor, step["command"], step_inputs)
        for problem in check_expect(
            step["expect"], result=step_result, tmp_dir=matrix_tmp_dir, calls=None
        ):
            problems.append(f"[verify[{index}] {step['command']}] {problem}")

    assert not problems, "\n".join(["L2 用例断言失败：", *problems])
