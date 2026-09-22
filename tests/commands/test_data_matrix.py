"""L1 契约矩阵驱动（数据 / 工作流通道）：读用例表 → 真子进程执行 → 断言 outputs 与磁盘。

## 为什么这个通道的插桩点是「真子进程」而不是假桩

浏览器通道的可测面在**通道协议**（下发了什么 op/args），所以它打桩在最底层 `_exchange`。
数据 / 工作流通道不一样：命令本身就是纯 Python 的文件与字符串操作，`PythonWorkerExecutor`
真起一个 `python -m rpa_core.workers.python_worker` 子进程——**真子进程就是真机**，
一次性能测到三件事：执行器下发了什么（inputs 决定行为）、worker 回了什么（outputs 形状）、
**磁盘上留下了什么**（`expect.files`）。打桩反而会把最有价值的证据面（真实文件读写、
真实错误分支、真实进程回收）打掉。

代价是每个变体一次进程启动（实测 ~0.5s），所以本矩阵按需跑（`RPA_COMMAND_MATRIX=1`），
不进默认门禁（门禁只跑 `check_command_matrix.py` 的静态覆盖率校验）。

## 每个变体一个干净目录

数据命令会真的写文件、删文件，用例之间必须互不干扰：`{tmp}` 对每个变体物化成一个**新建的
空目录**（`matrix_tmp_dir/<命令>::<变体>`），`setup` 预置态与 `expect.files` 都相对它。

## 安全阀：用例表的路径不许越出这个目录

用例表是数据，数据会写错；这个通道的命令真的会**删**东西（`data.deletePath` 带 `recursive`
就是 `shutil.rmtree`）。所以执行前先跑一遍 `_confined_violations`：把 `inputs` 与 `setup` 里
所有路径类值按**实现自己的解析规则**（相对路径拼 `workspace` / `flowDir`）解析成绝对路径，
任何落在本变体临时目录之外的值都直接判失败——**在建表阶段就把手滑写成维护者真实目录的
用例拦下来**，而不是等它跑起来删掉东西。

注意这条阀门与用例表里的越权负路径**不冲突**：那些负路径用「在本变体目录内、但不在
workspace / flow dir 内」的绝对路径（如 `{tmp}/escape.txt`）来触发 `CAPABILITY_DENIED`——
既能证明防线生效，又永远不碰真实文件。
"""

from __future__ import annotations

import asyncio
import time

import pytest

from rpa_core.executors.python_worker import PythonWorkerExecutor
from rpa_core.model.command import CommandInvocation
from tests.commands.guard import assert_confined
from tests.commands.matrix import (
    check_expect,
    iter_variants,
    materialize_inputs,
    prepare_setup,
)

NAMESPACES = ("data", "workflow")

# 变体名里的 `::` 等字符在 Windows 上不是合法文件名字符，建目录前先净化
_INVALID_NAME_CHARS = '<>:"/\\|?*'


@pytest.fixture
def matrix_variant_dir(matrix_tmp_dir, request):
    """每个变体一个干净目录（名字取 pytest 的 id，便于失败时按图索骥）。"""
    raw = request.node.name.split("[", 1)[-1].rstrip("]")
    name = "".join("-" if char in _INVALID_NAME_CHARS else char for char in raw)
    path = matrix_tmp_dir / name
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.mark.parametrize("command, spec, variant", list(iter_variants(*NAMESPACES)))
def test_command_variant(matrix_variant_dir, command, spec, variant):
    base = matrix_variant_dir
    # 先物化 `{tmp}` 再跑安全阀：阀门要看到**真实绝对路径**，否则 `{tmp}/../x` 这类
    # 写法会被当成一个普通相对目录名而放过。
    materialized = materialize_inputs(variant, base)
    assert_confined(materialized, base)

    inputs = materialized.get("inputs", {})
    prepare_setup(materialized.get("setup") or {}, base)

    executor = PythonWorkerExecutor()
    invocation = CommandInvocation(
        command_id=command,
        command_version="1.0.0",
        run_id="matrix",
        step_id="step",
        inputs=inputs,
    )
    started = time.perf_counter()
    try:
        result = asyncio.run(executor.execute(invocation, asyncio.Event()))
    finally:
        asyncio.run(executor.close())
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    problems = check_expect(
        materialized["expect"], result=result, tmp_dir=base, elapsed_ms=elapsed_ms
    )

    # 进程面：worker 子进程必须被回收（否则几百个变体跑完会留下一堆 python）
    if executor.active_process_count:
        problems.append(
            f"worker 子进程未回收：active_process_count={executor.active_process_count}"
        )

    assert not problems, "\n".join(["用例断言失败：", *problems])
