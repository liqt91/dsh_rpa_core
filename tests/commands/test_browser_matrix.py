"""L1 契约矩阵驱动（浏览器通道）：读用例表 → 参数化执行 → 断言真实下发的调用与结果。

用例表是**数据**（`cases/browser.json`），本文件只是解释器 + 插桩点：新增命令覆盖时改 JSON，
不动代码。通用部分（用例加载 / `expect` 语义 / 临时目录 / 落盘断言）在
`tests/commands/matrix.py`——数据 / 工作流通道的驱动 `test_data_matrix.py` 与它共用同一套
`expect` 约定，差别只在插桩点。

**插桩点**：`PlaywrightExecutor(ext_session=ExtensionExecSession(client=ScriptedExtension()))`
——假扩展只替换最底层的 `_exchange`，用例断言的是「执行器真实下发了什么」，不是「桩被怎么
调用」（见 `harness.py` docstring）。

表与 catalog 的对齐由 `.harness/scripts/check_command_matrix.py` 静态校验（在默认门禁里）；
本文件的用例按需跑（`RPA_COMMAND_MATRIX=1`，见 `conftest.py`）。
"""

from __future__ import annotations

import time

import pytest

import rpa_core.executors.browser as browser_module
from rpa_core.extension_exec import ExtensionChannelError
from tests.commands.harness import ScriptedExtension, run_command
from tests.commands.matrix import (
    check_expect,
    iter_variants,
    materialize_inputs,
    scripted_error,
)


def _install_process_stub(monkeypatch, stub: dict) -> list[bool]:
    """打桩进程面（`browser.closeBrowser`），返回每次终止收到的 force 实参。

    conftest 的 autouse 守卫已经把这条面钉成「拦下」，这里在用例内覆盖它——
    依据是「要验证破坏性路径的用例自己显式打桩」。
    """
    pids = [int(pid) for pid in stub.get("pids") or []]
    seen_force: list[bool] = []
    monkeypatch.setattr(
        browser_module,
        "_list_browser_processes",
        lambda names: [{"pid": pid, "name": "probe.exe"} for pid in pids],
    )
    monkeypatch.setattr(browser_module, "_wait_processes_exit", lambda pids_, timeout_s: True)

    def _terminate(pid: int, force: bool) -> None:
        seen_force.append(bool(force))

    monkeypatch.setattr(browser_module, "_terminate_process", _terminate)
    return seen_force


@pytest.mark.parametrize("command, spec, variant", list(iter_variants("browser")))
def test_command_variant(monkeypatch, matrix_tmp_dir, command, spec, variant):
    expect = variant["expect"]
    replies = {**spec.get("stub", {}), **variant.get("stub", {})}
    ext = ScriptedExtension(
        replies=replies, errors=scripted_error(variant.get("errors"), ExtensionChannelError)
    )

    seen_force = (
        _install_process_stub(monkeypatch, variant["processStub"])
        if "processStub" in variant
        else []
    )

    sessions = variant["sessions"] if "sessions" in variant else None
    inputs = materialize_inputs(variant.get("inputs", {}), matrix_tmp_dir)
    started = time.perf_counter()
    result = run_command(ext, command, inputs, sessions=sessions)
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    problems = check_expect(
        expect,
        result=result,
        tmp_dir=matrix_tmp_dir,
        calls=ext.calls,
        elapsed_ms=elapsed_ms,
    )

    # -- 进程面（force 实参） ------------------------------------------------
    if variant.get("processStub", {}).get("expectForce") and not all(seen_force):
        problems.append(f"期望以 force=True 终止，实得 {seen_force}")

    assert not problems, "\n".join(["用例断言失败：", *problems])
