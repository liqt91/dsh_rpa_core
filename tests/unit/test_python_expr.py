"""python 模式（py 开关）字段求值测试。

设计约束：
- 用户 Python 只在 worker 子进程执行（规则 5），orchestrator 进程不 eval/exec（规则 6）。
- 赋值产生的变量由 worker 回传、由 orchestrator 合并进 scopes.variables，
  handler 不直接改动作用域（规则 3），因此变量可跨节点使用。
"""

import asyncio
from pathlib import Path

import pytest

from rpa_core.catalog import load_catalog
from rpa_core.compiler import WorkflowCompiler
from rpa_core.executors import ExecutorRegistry
from rpa_core.executors.python_worker import PythonWorkerExecutor
from rpa_core.model.command import CommandInvocation
from rpa_core.model.workflow import Workflow
from rpa_core.runtime import Orchestrator
from rpa_core.workers.python_worker import _eval_expression

COMMANDS = Path(__file__).resolve().parents[2] / "commands"


def _invoke(expressions, variables=None):
    return CommandInvocation(
        command_id="python.evalExpression",
        command_version="1.0.0",
        run_id="run",
        step_id="step",
        attempt=1,
        inputs={"expressions": expressions, "variables": variables or {}},
        deadline_monotonic=0,
    )


def test_expression_value_is_returned():
    result = _eval_expression(_invoke({"text": "1 + 1"}))
    assert result.status == "success"
    assert result.outputs["values"] == {"text": 2}


def test_assigned_variable_is_returned_for_write_back():
    """赋值语句产生的变量要回传，供后续节点引用。"""
    result = _eval_expression(_invoke({"text": "total = 10 + 5\ntotal * 2"}))
    assert result.outputs["values"] == {"text": 30}
    assert result.outputs["assignedVariables"] == {"total": 15}


def test_flow_variables_are_injected_as_locals():
    result = _eval_expression(
        _invoke({"text": 'webpage1["url"].upper()'}, {"webpage1": {"url": "https://a.b"}})
    )
    assert result.outputs["values"] == {"text": "HTTPS://A.B"}
    assert result.outputs["assignedVariables"] == {}


def test_multiple_fields_share_one_namespace():
    """同一节点内多个 py 字段共享命名空间，前面的赋值后面可用。"""
    result = _eval_expression(_invoke({"a": "x = 7\nx", "b": "x + 1"}))
    assert result.outputs["values"] == {"a": 7, "b": 8}
    assert result.outputs["assignedVariables"] == {"x": 7}


def test_updating_existing_variable_is_reported():
    result = _eval_expression(_invoke({"text": "n = n + 1\nn"}, {"n": 5}))
    assert result.outputs["values"] == {"text": 6}
    assert result.outputs["assignedVariables"] == {"n": 6}


def test_internal_namespace_keys_are_never_returned():
    """exec/eval 注入的 __builtins__ 等内部键不能污染流程变量。"""
    result = _eval_expression(_invoke({"text": "v = 1\nv"}))
    assert all(not key.startswith("__") for key in result.outputs["assignedVariables"])


def test_broken_expression_fails_cleanly():
    result = _eval_expression(_invoke({"text": "1 / 0"}))
    assert result.status == "error"
    assert "ZeroDivisionError" in result.error.message


def test_non_jsonable_value_is_converted_before_returning():
    """set/tuple 等非 JSON 原生类型要收敛，否则子进程回传会失败。"""
    result = _eval_expression(_invoke({"text": "set([1, 2])"}))
    assert result.status == "success"
    assert result.outputs["values"]["text"] == [1, 2]


def _build_workflow(workspace):
    return {
        "id": "expr-modes",
        "name": "expr-modes",
        "root": {
            "type": "sequence",
            "id": "root",
            "children": [
                {
                    "type": "action",
                    "id": "n1",
                    "command": "data.limit",
                    "with": {"items": [1, 2, 3, 4, 5], "count": 3},
                    "output_aliases": {"items": "nums"},
                },
                {
                    "type": "action",
                    "id": "n2",
                    "command": "data.writeText",
                    "with": {"workspace": str(workspace), "path": "fx.txt", "text": "结果是[nums]"},
                    "_exprModes": {"text": "fx"},
                },
                {
                    "type": "action",
                    "id": "n3",
                    "command": "data.writeText",
                    "with": {
                        "workspace": str(workspace),
                        "path": "py.txt",
                        "text": "total = sum(nums)\ntotal * 10",
                    },
                    "_exprModes": {"text": "python"},
                },
                {
                    "type": "action",
                    "id": "n4",
                    "command": "data.writeText",
                    "with": {
                        "workspace": str(workspace),
                        "path": "pyvar.txt",
                        "text": "上一步的 total=[total]",
                    },
                    "_exprModes": {"text": "fx"},
                },
            ],
        },
    }


def test_fx_and_python_modes_run_end_to_end(tmp_path):
    """fx 拼接、py 求值、以及 py 赋值的变量被后续节点引用。"""
    catalog = load_catalog(COMMANDS)
    plan = WorkflowCompiler(catalog).compile(
        Workflow.model_validate(_build_workflow(tmp_path)), {"workspace.write"}
    )

    async def run():
        runner = Orchestrator(
            catalog, ExecutorRegistry({"python.worker": PythonWorkerExecutor()}), tmp_path / "runs"
        )
        return await runner.run(plan)

    result = asyncio.run(run())
    assert result.status.value == "succeeded", result.error

    assert (tmp_path / "fx.txt").read_text(encoding="utf-8") == "结果是[1, 2, 3]"
    assert (tmp_path / "py.txt").read_text(encoding="utf-8") == "60"
    assert (tmp_path / "pyvar.txt").read_text(encoding="utf-8") == "上一步的 total=6"


def test_python_value_is_stringified_for_string_schema_field(tmp_path):
    """string 字段收到非字符串求值结果时收敛为文本，非 string 字段保留原类型。"""
    catalog = load_catalog(COMMANDS)
    workflow = {
        "id": "py-type",
        "name": "py-type",
        "root": {
            "type": "action",
            "id": "n1",
            "command": "data.writeText",
            "with": {"workspace": str(tmp_path), "path": "out.txt", "text": "2 ** 5"},
            "_exprModes": {"text": "python"},
        },
    }
    plan = WorkflowCompiler(catalog).compile(
        Workflow.model_validate(workflow), {"workspace.write"}
    )

    async def run():
        runner = Orchestrator(
            catalog, ExecutorRegistry({"python.worker": PythonWorkerExecutor()}), tmp_path / "runs"
        )
        return await runner.run(plan)

    result = asyncio.run(run())
    assert result.status.value == "succeeded", result.error
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "32"


def test_non_ascii_bracket_text_is_left_untouched():
    """变量名必须是 ASCII 标识符；中文方括号文本按普通文本处理，不当作标签。"""
    result = _eval_expression(_invoke({"text": "'值=[不存在]'"}))
    assert result.outputs["values"] == {"text": "值=[不存在]"}


def test_unknown_tag_reference_fails_the_run(tmp_path):
    """fx 标签引用未定义变量时运行失败（不静默写入字面量）。"""
    catalog = load_catalog(COMMANDS)
    workflow = {
        "id": "fx-bad",
        "name": "fx-bad",
        "root": {
            "type": "action",
            "id": "n1",
            "command": "data.writeText",
            "with": {"workspace": str(tmp_path), "path": "out.txt", "text": "值=[nope]"},
            "_exprModes": {"text": "fx"},
        },
    }
    plan = WorkflowCompiler(catalog).compile(
        Workflow.model_validate(workflow), {"workspace.write"}
    )

    async def run():
        runner = Orchestrator(
            catalog, ExecutorRegistry({"python.worker": PythonWorkerExecutor()}), tmp_path / "runs"
        )
        return await runner.run(plan)

    result = asyncio.run(run())
    assert result.status.value == "failed"
    assert result.error["code"] == "INVALID_REFERENCE"


@pytest.mark.parametrize("missing", ["__builtins__"])
def test_assigned_never_contains_internal_key(missing):
    result = _eval_expression(_invoke({"f": "z = 3\nz"}))
    assert missing not in result.outputs["assignedVariables"]
