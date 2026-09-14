import json
from pathlib import Path

import pytest

from rpa_core.catalog import clear_catalog_cache, load_catalog
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
    # 清掉快照缓存，逼出一次真正独立的读盘 + 校验：digest 必须仍然一致
    # （否则「同一份 manifest 集合」就不构成稳定契约）。
    clear_catalog_cache()
    second = catalog()
    assert first is not second
    assert len(first) == 83
    assert first.digest == second.digest
    with pytest.raises(TypeError):
        first._commands["x"] = None


def test_catalog_snapshot_is_reused_while_directory_is_unchanged():
    """目录内容未变时复用同一份快照。

    这条用例是性能契约的守卫：命令目录有 83 条 manifest，每次重新解析都要读
    83 个文件 + 166 次 JSON Schema 自检；一旦缓存被去掉，整套门禁会从分钟级
    回到十几分钟级（devserver 测试的 `server` fixture 每个都要加载一次）。
    """
    assert catalog() is catalog()


def _probe_manifest(version: str, command_id: str = "probe.echo") -> dict:
    return {
        "id": command_id,
        "version": version,
        "executor": "probe",
        "kind": "action",
        "risk": "read",
        "capabilities": [],
        "resources": [],
        "stability": "stable",
        "effect": {"kind": "pure", "replay": "safe", "idempotency": "none"},
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "errors": ["EXECUTOR_FAILED"],
        "implementation": {"handler": "probe:execute"},
    }


def test_catalog_snapshot_follows_manifest_changes(tmp_path):
    """改写 / 新增 / 删除 manifest 都必须让缓存失效，不能读到旧快照。"""
    root = tmp_path / "commands"
    root.mkdir()
    target = root / "echo.json"
    target.write_text(json.dumps(_probe_manifest("1.0.0")), encoding="utf-8")
    original = load_catalog(root)

    target.write_text(json.dumps(_probe_manifest("2.0.0")), encoding="utf-8")
    rewritten = load_catalog(root)
    assert rewritten["probe.echo"].version == "2.0.0"
    assert rewritten.digest != original.digest

    (root / "other.json").write_text(
        json.dumps(_probe_manifest("1.0.0", "probe.other")), encoding="utf-8"
    )
    assert len(load_catalog(root)) == 2

    (root / "other.json").unlink()
    assert len(load_catalog(root)) == 1


def test_catalog_snapshot_failure_is_not_cached(tmp_path):
    """空目录报错后，补齐 manifest 应当能正常加载（异常不进缓存）。"""
    root = tmp_path / "commands"
    root.mkdir()
    with pytest.raises(ValueError, match="No command manifests found"):
        load_catalog(root)
    (root / "echo.json").write_text(json.dumps(_probe_manifest("1.0.0")), encoding="utf-8")
    assert len(load_catalog(root)) == 1


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
