from pathlib import Path

import pytest

from rpa_core.catalog import load_catalog
from rpa_core.compiler import WorkflowCompileError, WorkflowCompiler
from rpa_core.model.workflow import Workflow

ROOT = Path(__file__).resolve().parents[2]


def catalog():
    return load_catalog(ROOT / "commands")


def workflow(root):
    return Workflow.model_validate(
        {"id": "test", "name": "test", "inputs": {"url": "string"}, "root": root}
    )


def test_catalog_is_loaded_and_digest_is_stable():
    first = catalog()
    second = catalog()
    assert len(first) == 55
    assert first.digest == second.digest
    with pytest.raises(TypeError):
        first._commands["x"] = None


def test_compile_accepts_explicit_references_and_capabilities():
    plan = WorkflowCompiler(catalog()).compile(
        workflow(
            {
                "type": "action",
                "id": "open",
                "command": "browser.navigate",
                "with": {"url": "https://x", "headless": True},
            }
        ),
        {"browser.control", "process.start"},
    )
    assert plan.catalog_digest == catalog().digest


def test_compile_rejects_unknown_command_and_forward_step():
    compiler = WorkflowCompiler(catalog())
    with pytest.raises(WorkflowCompileError, match="Unknown command"):
        compiler.compile(
            workflow({"type": "action", "id": "bad", "command": "browser.nope", "with": {}}),
            set(),
        )
    with pytest.raises(WorkflowCompileError, match="forward step"):
        compiler.compile(
            workflow(
                {
                    "type": "sequence",
                    "id": "root",
                    "children": [
                        {
                            "type": "action",
                            "id": "later",
                            "command": "browser.getText",
                            "with": {
                                "sessionId": "${steps.future.outputs.sessionId}",
                                "selector": "h1",
                            },
                        },
                        {
                            "type": "action",
                            "id": "future",
                            "command": "browser.navigate",
                            "with": {"url": "https://x"},
                        },
                    ],
                }
            ),
            {"browser.read", "process.start", "browser.control"},
        )


def test_compile_accepts_output_name_reference_after_declaration():
    """打开网页配 output_aliases 后，后续节点可引用 ${web}（sessionId 整值）与其子字段。"""
    plan = WorkflowCompiler(catalog()).compile(
        workflow(
            {
                "type": "sequence",
                "id": "root",
                "children": [
                    {
                        "type": "action",
                        "id": "open",
                        "command": "browser.navigate",
                        "output_aliases": {"sessionId": "web"},
                        "with": {"url": "https://x", "headless": True},
                    },
                    {
                        "type": "action",
                        "id": "read",
                        "command": "browser.getText",
                        "with": {"sessionId": "${web}", "selector": "h1"},
                    },
                ],
            }
        ),
        {"browser.control", "browser.read", "process.start"},
    )
    assert plan.workflow.root.children[0].output_aliases == {"sessionId": "web"}


def test_compile_rejects_forward_variable_reference():
    """引用在 output_aliases 声明节点之前出现 → 报 forward variable reference。"""
    compiler = WorkflowCompiler(catalog())
    with pytest.raises(WorkflowCompileError, match="Forward variable reference"):
        compiler.compile(
            workflow(
                {
                    "type": "sequence",
                    "id": "root",
                    "children": [
                        {
                            "type": "action",
                            "id": "read",
                            "command": "browser.getText",
                            "with": {"sessionId": "${web}", "selector": "h1"},
                        },
                        {
                            "type": "action",
                            "id": "open",
                            "command": "browser.navigate",
                            "output_aliases": {"sessionId": "web"},
                            "with": {"url": "https://x"},
                        },
                    ],
                }
            ),
            {"browser.control", "browser.read", "process.start"},
        )


def test_compile_rejects_undeclared_variable_reference():
    """从未声明的裸变量名引用 → 报作用域外根（Unsupported reference root）。"""
    compiler = WorkflowCompiler(catalog())
    with pytest.raises(WorkflowCompileError, match="Unsupported reference root: nope"):
        compiler.compile(
            workflow(
                {
                    "type": "sequence",
                    "id": "root",
                    "children": [
                        {
                            "type": "action",
                            "id": "open",
                            "command": "browser.navigate",
                            "with": {"url": "https://x", "headless": True},
                        },
                        {
                            "type": "action",
                            "id": "read",
                            "command": "browser.getText",
                            "with": {"sessionId": "${nope}", "selector": "h1"},
                        },
                    ],
                }
            ),
            {"browser.control", "browser.read", "process.start"},
        )


def test_compile_rejects_duplicate_alias():
    """两个节点声明同名别名 → 编译期拦截（否则运行时静默覆盖）。"""
    compiler = WorkflowCompiler(catalog())
    with pytest.raises(WorkflowCompileError, match="Duplicate alias: 'web'"):
        compiler.compile(
            workflow(
                {
                    "type": "sequence",
                    "id": "root",
                    "children": [
                        {
                            "type": "action",
                            "id": "open1",
                            "command": "browser.navigate",
                            "output_aliases": {"sessionId": "web"},
                            "with": {"url": "https://x"},
                        },
                        {
                            "type": "action",
                            "id": "open2",
                            "command": "browser.navigate",
                            "output_aliases": {"sessionId": "web"},
                            "with": {"url": "https://y"},
                        },
                    ],
                }
            ),
            {"browser.control", "process.start"},
        )


@pytest.mark.parametrize("reserved", ["inputs", "steps", "loop"])
def test_compile_rejects_reserved_alias_root(reserved):
    """别名占用内置作用域根名 → 编译期拦截（否则静默遮蔽整个作用域）。"""
    compiler = WorkflowCompiler(catalog())
    with pytest.raises(WorkflowCompileError, match="Reserved alias name"):
        compiler.compile(
            workflow(
                {
                    "type": "sequence",
                    "id": "root",
                    "children": [
                        {
                            "type": "action",
                            "id": "open",
                            "command": "browser.navigate",
                            "output_aliases": {"sessionId": reserved},
                            "with": {"url": "https://x"},
                        },
                    ],
                }
            ),
            {"browser.control", "process.start"},
        )


def test_compile_allows_var_write_reassignment():
    """data.setVar 允许同名重赋值（定义 → 修改 → 再读），不参与重复拦截。"""
    plan = WorkflowCompiler(catalog()).compile(
        workflow(
            {
                "type": "sequence",
                "id": "root",
                "children": [
                    {
                        "type": "action",
                        "id": "define",
                        "command": "data.setVar",
                        "with": {"varName": "webpage1", "value": "first"},
                    },
                    {
                        "type": "action",
                        "id": "reassign",
                        "command": "data.setVar",
                        "with": {"varName": "webpage1", "value": "second"},
                    },
                    {
                        "type": "action",
                        "id": "read",
                        "command": "data.setVar",
                        "with": {"varName": "snapshot", "value": "${webpage1}"},
                    },
                ],
            }
        ),
        set(),
    )
    assert plan.workflow.root.children[1].with_["varName"] == "webpage1"


def test_compile_rejects_var_write_to_reserved_root():
    """data.setVar 的 varName 指向保留根名同样拦截。"""
    with pytest.raises(WorkflowCompileError, match="Reserved alias name: 'inputs'"):
        WorkflowCompiler(catalog()).compile(
            workflow(
                {
                    "type": "sequence",
                    "id": "root",
                    "children": [
                        {
                            "type": "action",
                            "id": "write",
                            "command": "data.setVar",
                            "with": {"varName": "inputs", "value": "x"},
                        },
                    ],
                }
            ),
            set(),
        )


def test_compile_rejects_forward_reference_to_var_write():
    """引用 data.setVar 尚未声明的变量 → 前向引用拦截。"""
    with pytest.raises(WorkflowCompileError, match="Forward variable reference"):
        WorkflowCompiler(catalog()).compile(
            workflow(
                {
                    "type": "sequence",
                    "id": "root",
                    "children": [
                        {
                            "type": "action",
                            "id": "read",
                            "command": "data.setVar",
                            "with": {"varName": "snapshot", "value": "${later}"},
                        },
                        {
                            "type": "action",
                            "id": "define",
                            "command": "data.setVar",
                            "with": {"varName": "later", "value": "x"},
                        },
                    ],
                }
            ),
            set(),
        )
