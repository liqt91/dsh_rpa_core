"""L1 契约矩阵驱动（桌面 Win32 通道）：与 uia 同构，差别只在后端与用例表。

用例表是 `cases/desktop_win32.json`（19 条，比 uia 侧多 `hotkey` / `menuSelect`，
这两条由 `tests/contract/test_desktop_contract.py` 里的
`test_uia_and_win32_command_sets_diverge_only_in_win32_extras` 钉住），
装配与断言都走 `desktop_harness.py` 与 uia 侧同一份代码。

**元素级正路径的现状（2026-09-22 补靶子后重写）**：此前这里写的是「win32 后端的定位器认
`title` / `className` / `controlId`，而靶子的控件是 WinForms 动态类名
（`WindowsForms10.EDIT.app.0.xxx`）且没有稳定的 `controlId`，所以 `findElement`
只能覆盖负路径」——**这个判因实测不成立**：
`title` 比的是控件的窗口文本，对文本恒定的控件（Button / Label / 只读 Edit）完全可用，
元素级正路径早就可以跑（实测 `title='Submit'`、`title='note-ready'` 都唯一命中）。
真正不成立的是另外两条：`className` 是随编译产物变的动态名（且两个 Edit 撞同名），
`control_id` 的过滤在 pywinauto 上根本不生效（实测任何取值都返回全部子控件）。
靶子这一轮补了原生菜单栏与 ListBox / ComboBox / 可拖 Label，`menuSelect` 与 `drag`
因此第一次有了成功路径。当前仍缺的（`select` / `getSelectedText` / `input`）
是**实现缺口**不是靶子缺口，逐条记在 `.harness/tasks/M38-command-matrix.md` §1.5。

`demo_app`（真靶子）由 `tests/commands/conftest.py` 转出，本文件**不要**自己 import 它——
理由见该模块里的记录（会让 session 级 fixture 被 setup 两次）。
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
