"""L1 契约矩阵驱动（桌面 Win32 通道）：与 uia 同构，差别只在后端与用例表。

用例表是 `cases/desktop_win32.json`（19 条，比 uia 侧多 `hotkey` / `menuSelect`，
这两条由 `tests/contract/test_desktop_contract.py` 里的
`test_uia_and_win32_command_sets_diverge_only_in_win32_extras` 钉住），
装配与断言都走 `desktop_harness.py` 与 uia 侧同一份代码。

**本通道的元素级正路径还没有靶子**：win32 后端的定位器认 `title` / `className` /
`controlId`（`desktop_win32.py::_find` 直接转给 `window.descendants(...)`），而靶子的控件是
WinForms 动态类名（`WindowsForms10.EDIT.app.0.xxx`）且没有稳定的 `controlId`，所以
`findElement` 目前只能覆盖「找不到」这条负路径；需要覆盖正路径得先给靶子加一套 Win32 可
定位的控件（登记在 `.harness/tasks/M38-command-matrix.md` §3）。会话级命令
（`attachWindow` / `activateWindow` / `setWindowState` / …）不受这个缺口影响。
"""

from __future__ import annotations

import pytest

from tests.commands.desktop_harness import (
    requires_desktop,
    run_variant,
    variant_tmp_dir,
    win32_backend,
)
from tests.commands.matrix import iter_variants

pytestmark = requires_desktop


@pytest.mark.parametrize("command, spec, variant", list(iter_variants("desktop_win32")))
def test_command_variant(demo_app, matrix_tmp_dir, request, command, spec, variant):
    problems = run_variant(
        win32_backend(),
        demo_app,
        variant_tmp_dir(matrix_tmp_dir, request.node.name),
        command,
        variant,
    )
    assert not problems, "\n".join(["用例断言失败：", *problems])
