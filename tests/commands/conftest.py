"""指令测试（L1 契约层）目录约定。

## 0. 缺省不进默认门禁：显式开关才收集

`docs/command-testing-strategy.md` §4 定案：这套测试是**按需启用的独立测试**——
全量参数矩阵的规模是既有门禁的数倍，压进每次提交的回环会显著拖慢开发反馈，
而它要抓的是周期性/发布前的系统性覆盖，不是每次改动的即时反馈。

因此本目录**缺省不收集**（配置见文件末尾的 `collect_ignore_glob`）。需要时：

    RPA_COMMAND_MATRIX=1 uv run pytest tests/commands
    （PowerShell: $env:RPA_COMMAND_MATRIX=1; uv run pytest tests/commands）

注意区分两件事：

- **用例表与 catalog 是否对齐**（覆盖率校验）→ `.harness/scripts/check_command_matrix.py`，
  **在默认门禁里**（2026-09-22 维护者定案）。它只读用例表 JSON 与 manifest、不执行任何
  命令，毫秒级——正是「防用例表随时间腐烂」最该每次跑的位置。
- **全量参数矩阵本身**（本目录的用例）→ 按需，不进默认门禁。

## 1. 危险面守卫：用例不得真的杀进程 / 拉起浏览器

M36 的教训是「打桩边界要沿**真实副作用面**划，不沿参数传递面划」（当时是剪贴板 +
合成键击落到维护者前台窗口）。本模块把同一条教训推广到进程面——**2026-09-22 实测
付出过代价**：新建 M38 的建表脚手架（`.harness/spike/probe_browser_commands.py`）
第一版没给 `browser.closeBrowser` 打桩，跑一次探针**杀掉了本机 21 个真实浏览器进程**
（`browser.closeBrowser` 的语义按 M35 定案就是「按名杀掉该浏览器的全部进程」）。

下面两个 autouse 守卫把这条面钉死，用例要验证这些路径必须自己显式打桩：

- `_terminate_process` / `_wait_processes_exit` / `_list_browser_processes`：
  终止与枚举浏览器进程（`browser.closeBrowser`）——枚举本身无副作用，但**返回值随本机
  环境漂移**（维护者开着浏览器就有 20 多条），故也钉成空列表；
- `launch_browser`：`browser.navigate` 在目标浏览器离线时会**真的拉起浏览器**
  （`_launch_target`）——那会在维护者桌面上弹出一个新窗口。

全局输入面（`send_keys` / 剪贴板）已由上层 `tests/conftest.py` 的 `_block_global_input`
覆盖，本目录无需重复。

## 2. 结构化实时报告：给 GUI「指令测试」页签用的数据合同

工作台（`gui/command_matrix.py`）要「启动 + 展示」这套矩阵，需要一份**机器可读、可增量读**的
结果流。三条候选里选了这个，理由写在这里免得后人重走一遍：

- ❌ **解析 pytest 的 `-v` 输出**：`pyproject.toml` 的 `addopts = "-q"` 会把 `-v` 抵消回默认
  verbosity（实测：180 个用例只产出 16 行 stdout，没有任何 `PASSED` 行）——依赖调用方的
  verbosity 算术，太脆。
- ❌ **只读 `--junitxml`**：格式标准，但它**只在会话结束时落盘**，页面上没有「跑到哪了」。
- ✅ **本模块的 JSONL 钩子**：由被测方自己按用例逐个 append + flush，既实时又是干净的结构化
  数据（`command` / `variant` 已切好，页面不必再去解析 pytest 的 nodeid 拼接规则）。

**缺省不写**：只有设置了环境变量 `RPA_COMMAND_MATRIX_REPORT=<路径>` 才启用，默认门禁与
命令行手工跑都不产出任何文件。

## 3. 共享的临时目录（落盘面的根）

`matrix_tmp_dir` 是**整个矩阵共用的**临时目录，`{tmp}` 占位符由驱动物化到它（浏览器驱动）
或它的子目录（数据驱动：每个变体一个干净目录，见 `matrix.py` docstring）。

刻意**不用** pytest 的 `tmp_path`：那会为每个用例各建一个编号目录（矩阵几百个用例），
会话结束时 pytest 在 atexit 里批量删除，在受限执行环境会被批量删除守卫拦下并以
`SystemExit(1)` 收场——**测试全绿却让整个门禁看起来失败**（2026-09-22 实测）。
共用一个目录后，pytest 连 basetemp 都不必创建。门禁看的是退出码。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

MATRIX_ENABLED = os.environ.get("RPA_COMMAND_MATRIX") == "1"
MATRIX_ENV_HINT = "RPA_COMMAND_MATRIX=1"
# 结构化实时报告的落盘路径（GUI 页签用）；未设置则完全不产出文件。
REPORT_ENV = "RPA_COMMAND_MATRIX_REPORT"
# 参数化用例的 id 前缀（`_iter_variants` 给每个变体设的 id 是 `<命令>::<变体名>`）。
_NODE_PREFIX = "test_command_variant["
# 当前会话的实时报告写入器（`pytest_configure` 里按环境变量决定是否创建）。
_LIVE: _LiveReport | None = None


def split_variant_node(node: str) -> tuple[str, str] | None:
    """从 pytest nodeid 切出 `(命令, 变体)`；形状不符返回 `None`。

    实测形状：`tests/commands/test_browser_matrix.py::test_command_variant[browser.click::default]`
    ——参数化 id 里的 `::` 不会被转义，可以直接按它切。
    """
    start = node.find(_NODE_PREFIX)
    if start < 0:
        return None
    body = node[start + len(_NODE_PREFIX) :]
    if not body.endswith("]"):
        return None
    command, sep, variant = body[:-1].partition("::")
    if not sep or not command or not variant:
        return None
    return command, variant


class _LiveReport:
    """按行 append + flush 的 JSONL 写入器（页面靠轮询增量读）。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = path.open("w", encoding="utf-8", newline="\n")
        self.counts: dict[str, int] = {}
        self.started = time.perf_counter()

    def emit(self, record: dict) -> None:
        self._stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._stream.flush()

    def case(self, node: str, outcome: str, duration_s: float) -> None:
        self.counts[outcome] = self.counts.get(outcome, 0) + 1
        record: dict = {
            "event": "case",
            "node": node,
            "outcome": outcome,
            "durationMs": round(duration_s * 1000, 3),
        }
        split = split_variant_node(node)
        if split is not None:
            record["command"], record["variant"] = split
        self.emit(record)

    def summary(self, collected: int, exit_status: int) -> None:
        self.emit(
            {
                "event": "summary",
                "collected": collected,
                "exitStatus": exit_status,
                "counts": self.counts,
                "durationMs": round((time.perf_counter() - self.started) * 1000, 3),
            }
        )
        self._stream.close()


def pytest_configure(config) -> None:
    global _LIVE
    path = os.environ.get(REPORT_ENV)
    if path:
        _LIVE = _LiveReport(Path(path))


def pytest_runtest_logreport(report) -> None:
    """每个用例只记一次终态：setup 阶段就失败的记 setup，正常走完的记 call。

    **为什么用模块级引用而不是 `report.config`**：pytest 8 的 `TestReport` 上没有 `config`
    属性（实测 `AttributeError`，曾被上面这个契约测试抓个正着）。一个 pytest 进程只有一个
    会话，模块级引用是安全的。
    """
    if _LIVE is None:
        return
    if report.when == "call" or (
        report.when == "setup" and report.outcome != "passed"
    ):
        _LIVE.case(report.nodeid, report.outcome, report.duration)


def pytest_sessionfinish(session, exitstatus) -> None:
    if _LIVE is not None:
        _LIVE.summary(session.testscollected, int(exitstatus))


@pytest.fixture(scope="session")
def matrix_tmp_dir():
    """整个矩阵共用的临时目录，供用例表里的 `{tmp}` 占位符落盘（见模块 docstring §3）。"""
    import shutil
    import tempfile

    base = Path(tempfile.mkdtemp(prefix="rpa-command-matrix-"))
    try:
        yield base
    finally:
        shutil.rmtree(base, ignore_errors=True)


# 桌面靶子 fixture 在这里**转出**（不是直接用模块里的那份），两个驱动文件都不再自己 import：
# pytest 按「fixture 定义位置的模块」给每个模块建一份 FixtureDef，于是同一个 `demo_app`
# 被 uia / win32 两个驱动各拿到一份 → **session 级 fixture 被 setup 两次** → 第二次现场编译
# 时，第一次拉起的靶子进程正占着输出 exe，`csc /out:` 覆盖失败退出码 1，整个 win32 段
# 169 个变体全部 ERROR（2026-09-22 实测）。
# 从 conftest 转出后只剩一份 FixtureDef，会话内只编译并启动一次。
# 代价：任何模块再自己 `from ... import demo_app` 都会把这条又拆回两份——别这么干。
from tests.commands.desktop_harness import demo_app  # noqa: E402,F401


@pytest.fixture(autouse=True)
def _block_destructive_browser_ops(monkeypatch):
    """禁止用例真的终止进程或拉起浏览器（见模块 docstring §1）。

    用例内自己的 `monkeypatch.setattr` 在测试体阶段执行，晚于本 autouse，
    会**覆盖**守卫的桩（monkeypatch 撤销按 LIFO），两者互不冲突。
    """
    import rpa_core.executors.browser as browser_module

    reason = (
        "指令测试禁止真实终止浏览器进程 / 拉起浏览器（M38 防线）：browser.closeBrowser "
        "会按名杀掉本机该浏览器的全部进程。请在用例内显式打桩模型中的进程面。"
    )

    def _blocked(*_args, **_kwargs):
        raise AssertionError(reason)

    monkeypatch.setattr(browser_module, "_terminate_process", _blocked)
    monkeypatch.setattr(browser_module, "_wait_processes_exit", _blocked)
    monkeypatch.setattr(browser_module, "launch_browser", _blocked)
    monkeypatch.setattr(browser_module, "_list_browser_processes", lambda names: [])


def pytest_terminal_summary(terminalreporter) -> None:
    """缺省跳过时明确告知启用方式，避免误以为覆盖丢了（与桌面 E2E 同款提示）。"""
    if MATRIX_ENABLED:
        return
    terminalreporter.write_sep("-", "command matrix skipped")
    terminalreporter.write_line(
        "指令测试（L1 全量参数矩阵）缺省未收集——它按需启用，不随默认门禁跑。"
    )
    terminalreporter.write_line(
        f"需要时：{MATRIX_ENV_HINT} uv run pytest tests/commands"
    )


# 缺省忽略本目录的全部用例文件：新增用例文件不必回来改这里。
# 覆盖率静态校验不在本目录——见 `.harness/scripts/check_command_matrix.py`（在默认门禁里）。
if not MATRIX_ENABLED:
    collect_ignore_glob = ["test_*.py"]
