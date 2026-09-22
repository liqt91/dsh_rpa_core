"""L1 契约矩阵驱动（桌面 UIA 通道）：读用例表 → 真靶子上执行 → 断言输出契约与错误码。

用例表是**数据**（`cases/desktop.json`），本文件只是解释器 + 装配入口：新增命令覆盖时
改 JSON，不动代码。装配、占位符物化、变体级清理都在 `desktop_harness.py`——win32 后端的
驱动 `test_desktop_win32_matrix.py` 与它共用同一套 `expect` 约定（`tests/commands/matrix.py`），
差别只在后端。

**没有桩**：这里跑的是真桌面靶子（`testapps/desktop/Program.cs` 现场编译出的 WinForms
程序）。桌面侧的证据面（真实错误码、会话表、元素解析）本来就在真窗口上，把它换成
「桩被怎么调用」等于换掉了要证的东西——完整理由见 `desktop_harness.py` 的模块 docstring。

与浏览器 / 数据通道的两点差别：

1. 没有通道调用记录，所以用例表里不能出现 `onlyCall` / `calls` / `noCalls`
   （`check_expect` 收到 `calls=None` 会把这类声明判成违规，不静默跳过）；
2. 变体的 `inputs` 可以用 `{session}` / `{element:名字}` / `{appTitle}` / `{pid}` 占位符，
   由装配按 `setup` 现算（这些值每次都不同，写不进表里）。

表与 catalog 的对齐由 `.harness/scripts/check_command_matrix.py` 静态校验（在默认门禁里）；
本文件的用例按需跑：`RPA_COMMAND_MATRIX=1 RPA_DESKTOP_E2E=1 pytest tests/commands`。

`demo_app`（真靶子）由 `tests/commands/conftest.py` 转出，本文件**不要**自己 import 它——
那会让 pytest 为两个驱动各建一份 FixtureDef，session 级 fixture 被 setup 两次（第二次现场
编译撞上第一次拉起的进程占着的 exe，整段 ERROR，见 conftest 里的记录）。
"""

from __future__ import annotations

import pytest

from tests.commands.desktop_harness import (
    requires_desktop,
    run_variant,
    uia_backend,
    variant_tmp_dir,
)
from tests.commands.matrix import iter_variants

pytestmark = requires_desktop


@pytest.mark.parametrize("command, spec, variant", list(iter_variants("desktop")))
def test_command_variant(demo_app, matrix_tmp_dir, request, command, spec, variant):
    problems = run_variant(
        uia_backend(),
        demo_app,
        variant_tmp_dir(matrix_tmp_dir, request.node.name),
        command,
        variant,
    )
    assert not problems, "\n".join(["用例断言失败：", *problems])
