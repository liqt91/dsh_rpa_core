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
"""

from __future__ import annotations

import os

import pytest

MATRIX_ENABLED = os.environ.get("RPA_COMMAND_MATRIX") == "1"
MATRIX_ENV_HINT = "RPA_COMMAND_MATRIX=1"


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
