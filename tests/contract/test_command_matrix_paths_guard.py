"""指令测试用例表的**路径安全阀**契约（默认门禁里跑）。

安全阀本身在 `tests/commands/guard.py`（驱动的执行前检查），这里验证它**两个方向都管用**：

- 正向：合法用例不许被误杀（误杀比不拦更糟——它会把整套用例表逼成「讨好阀门」的形状）；
- 负向：越界路径必须在执行任何命令**之前**判失败（这是「不碰维护者真实文件」的唯一保证）；
- 接线：驱动必须**真的调**它（`assert_confined` 是纯函数，没人调就只是一段注释）。

为什么放在 `tests/contract/` 而不是 `tests/commands/`：那边按需启用（`RPA_COMMAND_MATRIX=1`），
而这道阀门是**安全**性质的——它该在每次提交都跑；另外把它放进矩阵目录会让结构化报告多出
两条没有 `<命令>::<变体>` 形状的记录，页签的进度分母（来自用例表）就对不上了。
"""

from __future__ import annotations

import inspect
import os
from pathlib import Path

import pytest

from tests.commands.guard import assert_confined


def test_safety_valve_accepts_legitimate_variants(tmp_path):
    """正向：相对路径、`{tmp}` 物化后的绝对路径、`setup` 都在变体目录内 → 放行。"""
    base = tmp_path / "variant"
    base.mkdir(parents=True)
    variant = {
        "inputs": {
            "workspace": f"{base}/ws/deep",
            "path": "../up.txt",
            "text": "x",
        },
        "setup": {"files": [{"path": "ws/note.txt", "text": "n"}], "dirs": ["ws/empty"]},
        "expect": {"files": [{"path": "ws/note.txt", "equals": "n"}]},
    }

    assert_confined(variant, base)  # 不抛异常即通过


def test_safety_valve_rejects_paths_outside_the_variant_dir(tmp_path):
    """负向：越界路径（绝对路径指向系统目录 + `../` 越界的 setup 路径）必须判失败。

    样本不造文件：阀门只看**解析后的位置**，不关心目标是否存在。
    """
    base = tmp_path / "variant"
    base.mkdir(parents=True)
    outside = Path(os.environ.get("SystemRoot") or "C:/Windows")
    variant = {
        "inputs": {"workspace": str(outside), "path": "x.txt", "text": "x"},
        "setup": {"files": [{"path": "../escape.txt", "text": "x"}]},
    }

    with pytest.raises(AssertionError) as error:
        assert_confined(variant, base)

    message = str(error.value)
    assert "workspace" in message  # 越界的 workspace 被点名
    assert "setup.files" in message  # `../` 越界的 setup 也被点名
    assert "安全阀拦下" in message  # 文案里说清「未执行任何命令」


def test_driver_calls_the_safety_valve_before_running_commands():
    """接线：数据驱动必须在组命令之前调安全阀（纯函数没人调就只是注释）。

    用源码断言而不是运行断言：要证明的是「调用点在执行之前」，跑一次只能证明「结果对」。
    """
    from tests.commands import test_data_matrix

    source = inspect.getsource(test_data_matrix.test_command_variant)
    valve_at = source.find("assert_confined(")
    run_at = source.find("PythonWorkerExecutor(")

    assert valve_at >= 0, "驱动没有调 assert_confined——安全阀形同虚设"
    assert run_at >= 0, "驱动里找不到执行器构造点（断言锚点已过期，请更新）"
    assert valve_at < run_at, "安全阀跑在执行器构造之后：命令已经执行过了"
