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
真正不成立的是另外两条：`className` 的哈希段**不能硬编码**（注意与早期记录的差异：
实测它不是「随编译产物变」，而是**机器 + 运行时级常量**——同机所有 .NET Framework 4.x
的 WinForms 程序共享同一段，跨重编译/跨源码/跨输出路径都不变；**换机器会变**，所以测试
侧改成按运行期读回的完整类名注入 `{listBoxClass}` / `{comboBoxClass}` 占位符），
且两个 Edit 撞同名；`control_id` 的过滤在 pywinauto 上根本不生效（实测任何取值都返回
全部子控件，S2.3 已由 `_find` 手工补滤修活）。
靶子这一轮补了原生菜单栏与 ListBox / ComboBox / 可拖 Label，`menuSelect` 与 `drag`
因此第一次有了成功路径。**`select` / `getSelectedText` 的原生消息实现已落地
（2026-09-23 BACKLOG 尾巴 #1）**，两条命令第一次有了正路径；仅剩 `input` 仍是
实现缺口（它需要一个「可写 Edit」的稳定锚点，而可写 Edit 的窗口文本就是它的内容、
天然当不了锚点，逐条记在 `.harness/tasks/M38-command-matrix.md` §1.5）。

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
