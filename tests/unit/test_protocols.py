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
    # ★ 条数从**事实源现算**（`commands/**/*.json`），不写死数字：写死的话每加一条命令
    #   都要改这里，而这条判据的本意是「读盘没漏文件 / 没重复计入」。M60 加 ocr.recognize
    #   时正是硬编码的 86 先红了。
    assert len(first) == len(list((ROOT / "commands").rglob("*.json")))
    assert first.digest == second.digest
    with pytest.raises(TypeError):
        first._commands["x"] = None


def test_catalog_snapshot_is_reused_while_directory_is_unchanged():
    """目录内容未变时复用同一份快照。

    这条用例是性能契约的守卫：命令目录里每条 manifest 都要读一遍文件 + 做一次
    JSON Schema 自检；一旦缓存被去掉，整套门禁会从分钟级回到十几分钟级
    （devserver 测试的 `server` fixture 每个都要加载一次）。
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
                "with": {"url": "https://x", "browserType": "msedge"},
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
                            "with": {"url": "https://x", "browserType": "msedge"},
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
                        "with": {"url": "https://x", "browserType": "msedge"},
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
                            "with": {"url": "https://x", "browserType": "msedge"},
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
                            "with": {"url": "https://x", "browserType": "msedge"},
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
                            "with": {"url": "https://x", "browserType": "msedge"},
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
                            "with": {"url": "https://x", "browserType": "msedge"},
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


# ---- 节点参数 vs manifest input_schema（编译期与运行期同口径）----------------
#
# 背景：运行期 `orchestrator` 在取执行器**之前**用 `Draft202012Validator` 全量校验
# 节点参数（`additionalProperties: false`），而编译期过去只 `check_schema`（校验
# schema 自身合法）。于是「编译通过、第一个节点就 INVALID_INPUT」的漂移能一路走到
# 运行期——`examples/baidu-news-top10`、`examples/search-and-save` 就这样坏了很久。
# 下面的用例把新增的编译期判据钉住，包括**两条容易误伤的边界**：
#   ① 含 `${...}` 的值必须放行（运行期才求值成 array/integer）；
#   ② required 要扣掉 `elementRefs` 里的键（引用模型下被引用参数有意不写进 `with`）。


def test_compile_rejects_parameter_not_in_schema():
    """`with` 里出现 manifest 没声明的键 → 编译期拦截（运行期必 INVALID_INPUT）。"""
    with pytest.raises(WorkflowCompileError, match="Unknown parameter\\(s\\) headless"):
        WorkflowCompiler(catalog()).compile(
            workflow(
                {
                    "type": "action",
                    "id": "open",
                    "command": "browser.navigate",
                    "with": {"url": "https://x", "browserType": "msedge", "headless": True},
                }
            ),
            {"browser.control", "process.start"},
        )


def test_compile_rejects_missing_required_parameter():
    """缺 manifest 的 required 键 → 编译期拦截，而不是等到运行期才红。"""
    with pytest.raises(WorkflowCompileError, match="Missing required parameter\\(s\\) browserType"):
        WorkflowCompiler(catalog()).compile(
            workflow(
                {
                    "type": "action",
                    "id": "open",
                    "command": "browser.navigate",
                    "with": {"url": "https://x"},
                }
            ),
            {"browser.control", "process.start"},
        )


def test_compile_defers_value_validation_to_runtime():
    """★ 值（类型/枚举/范围）**有意**留给运行期，编译期只判键集合。

    这条判据钉的是**界面**，不是实现细节：把值也搬进编译器看起来更"严谨"，
    但静态看到的值与运行期校验的值根本不是同一个东西——
    ① `"${steps.x.outputs.items}"` 静态是字符串、运行期才是 array；
    ② `expr_modes[key] == "python"` 的字段里存的是 **Python 源码**（如 `len(rows)`），
       拿它对 `integer` 判类型必然误伤；
    ③ `INVALID_INPUT` 的错误码/上下文契约（`tests/unit/test_runtime_correctness.py`）
       要靠「能走到运行期」才验得了。
    所以这里明确断言：字面量类型不符、python 模式源码，**都必须编译通过**。
    """
    WorkflowCompiler(catalog()).compile(
        Workflow.model_validate(
            {
                "id": "test",
                "name": "test",
                "inputs": {},
                "root": {
                    "type": "sequence",
                    "id": "root",
                    "children": [
                        {
                            "type": "action",
                            "id": "wrongType",
                            "command": "browser.navigate",
                            # timeoutMs 是 integer，这里给字符串：运行期才红
                            "with": {
                                "url": "https://x",
                                "browserType": "msedge",
                                "timeoutMs": "not-a-number",
                            },
                        },
                        {
                            "type": "action",
                            "id": "pythonMode",
                            "command": "browser.navigate",
                            # python 模式下存的是源码，不是值
                            "with": {
                                "url": "https://x",
                                "browserType": "msedge",
                                "timeoutMs": "len('abc') * 1000",
                            },
                            "_exprModes": {"timeoutMs": "python"},
                        },
                    ],
                },
            }
        ),
        {"browser.control", "process.start"},
    )


def test_compile_accepts_element_ref_covering_required_parameter():
    """★ `elementRefs` 覆盖的 required 键不许判成「缺参数」。

    引用模型（M52 S6 / ADR-002）下被引用的参数**有意不写进 `with`**，运行期由
    `resolve_element_refs` 在 schema 校验之前换入元素库的路由值。漏了这条，
    `workflows/xiaohongshu` 的 `browser.input.selector` 就会编译红。
    """
    WorkflowCompiler(catalog()).compile(
        workflow(
            {
                "type": "action",
                "id": "input",
                "command": "browser.input",
                "with": {"text": "hello"},
                "elementRefs": {"selector": "input_el_textarea"},
            }
        ),
        {"browser.read", "browser.control", "process.start"},
    )
