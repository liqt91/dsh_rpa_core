"""变量可见范围：作用域函数必须与编译器**同口径**（维护者 2026-10-14 拍板）。

规则：当前节点不能引用**自身**或**之后**节点声明的变量。编译器早就这么判了
（``walk()`` 先 ``validate_refs``、之后才登记本节点别名），但**两个编辑器的 fx 菜单
遍历全流程**，于是菜单给出编译器不放行的选项 —— 选了保存才报
``Forward variable reference`` / ``Unknown loop reference``。

分工（不要互相替代）：

- ``compiler.compiler.visible_scope`` 是可见范围的**唯一权威函数**：GUI 菜单
  （``MainWindow._reference_groups``）直接读它，不另写一遍遍历；
- **本文件**用**编译器自己当裁判**：对每个节点 × 每个候选名字，把引用真塞进去编译
  一次，「能不能过」必须与 ``visible_scope`` 的判定**逐格相等**。所以口径漂移
  （谁改了一处遍历顺序）会立刻红 —— 这是「菜单 ⊆ 编译通过」的机器化形态；
- Web 侧（``devserver/static/app.js`` 的 ``visibleScopeAt``）是另一份实现，由 node
  门禁 ``scripts/check_scope_visibility.mjs`` 跑**同一份用例表**验。

本文件**不 import PySide6**（纯计算）：没有 gui extra 的环境也要能验口径。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from rpa_core.catalog import load_catalog
from rpa_core.cli import _commands_root
from rpa_core.compiler.compiler import (
    WorkflowCompileError,
    WorkflowCompiler,
    visible_scope,
)
from rpa_core.devserver.app import DEFAULT_CAPABILITIES
from rpa_core.model.workflow import Workflow

# 与 JS 门禁共用的用例表（两端的唯一期望来源，手写、不由实现导出）
CASES_PATH = Path(__file__).resolve().parent / "data" / "scope_visibility_cases.json"
TABLE = json.loads(CASES_PATH.read_text(encoding="utf-8"))
DOCUMENT = TABLE["workflow"]
CASES = TABLE["cases"]


def _declared_by_node(document: dict) -> dict[str, str]:
    """夹具里「节点 id → 它声明的变量名」（顺序 = 遍历序）。

    夹具只用 ``data.setVar``（``varName`` 字段），所以这里能直接读出来——**不是**
    把 ``_declared_names`` 抄一遍（那条规则仍只有 compiler 一处实现）。
    """
    found: dict[str, str] = {}

    def visit(node: dict) -> None:
        if node.get("type") == "action":
            name = (node.get("with") or {}).get("varName")
            if isinstance(name, str) and name:
                found[node["id"]] = name
        for key in ("children", "then", "else", "catch"):
            for child in node.get(key) or []:
                visit(child)

    visit(document["root"])
    return found


DECLARED_BY_NODE = _declared_by_node(DOCUMENT)
NODE_IDS = tuple(DECLARED_BY_NODE)
DECLARED = tuple(DECLARED_BY_NODE.values())
CANDIDATES = ("inputs.threshold", "loop.row", "loop.item", "err.code", "error.code") + DECLARED


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(_commands_root())


def _compile_with_reference(node_id: str, candidate: str) -> bool:
    """把 ``node_id`` 的 value 换成 ``${candidate}`` 后编译；过 = 编译器认它可见。

    单点接入：所有「可见性」判定都走真实的 ``WorkflowCompiler``，不用手写规则，
    所以不存在「测试自己也按错口径实现一遍」。
    """
    document = copy.deepcopy(DOCUMENT)
    target: dict | None = None

    def visit(node: dict) -> None:
        nonlocal target
        if node.get("id") == node_id:
            target = node
            return
        for key in ("children", "then", "else", "catch"):
            for child in node.get(key) or []:
                visit(child)

    visit(document["root"])
    assert target is not None, f"fixture 里没有节点 {node_id}"
    target["with"]["value"] = "${" + candidate + "}"
    try:
        WorkflowCompiler(load_catalog(_commands_root())).compile(
            Workflow.model_validate(document), set(DEFAULT_CAPABILITIES)
        )
    except WorkflowCompileError:
        return False
    return True


def _expected_visible(scope) -> set[str]:
    """``visible_scope`` 认为该节点可见的名字集合（含 inputs 与词法 loop/error）。"""
    names = {"inputs.threshold", *scope.variables}
    names.update(f"loop.{item}" for item in scope.loop_vars)
    names.update(f"{error}.code" for error in scope.error_vars)
    return names


def test_visible_scope_agrees_with_the_compiler_on_every_cell(catalog):
    """★ 主判据：7 个节点 × 12 个候选名字，逐格与编译器判定相等。

    这是「菜单与编译器同口径」的机器化形态：``visible_scope`` 少列（菜单漏给可用
    变量）或多列（菜单给出编译不过的选项）都会在这一条上红。
    """
    workflow = Workflow.model_validate(copy.deepcopy(DOCUMENT))
    mismatches: list[str] = []
    for node_id in NODE_IDS:
        scope = visible_scope(workflow.root, catalog, node_id)
        assert scope is not None, node_id
        expected = _expected_visible(scope)
        for candidate in CANDIDATES:
            actual = _compile_with_reference(node_id, candidate)
            if actual != (candidate in expected):
                mismatches.append(
                    f"{node_id} × {candidate}: 编译器{'放行' if actual else '拒绝'}，"
                    f"visible_scope {'列出' if candidate in expected else '未列'}"
                )
    assert not mismatches, "可见范围与编译器不一致：\n" + "\n".join(mismatches)


def test_shared_case_table_matches_visible_scope(catalog):
    """共享用例表本身要与 Python 口径一致，且覆盖夹具里**每个** action 节点。

    这张表同时驱动 JS 门禁；只在 JS 侧跑就会变成「验一份没人核对的期望」——所以
    Python 侧也要逐条对账（并挡住「夹具加了节点、用例表没跟上」）。
    """
    workflow = Workflow.model_validate(copy.deepcopy(DOCUMENT))
    assert {case["nodeId"] for case in CASES} == set(NODE_IDS), (
        "用例表必须覆盖夹具里每个 action 节点"
    )
    for case in CASES:
        scope = visible_scope(workflow.root, catalog, case["nodeId"])
        assert scope is not None, case["nodeId"]
        assert list(scope.variables) == case["variables"], case["nodeId"]
        assert list(scope.loop_vars) == case["loopVars"], case["nodeId"]
        assert list(scope.error_vars) == case["errorVars"], case["nodeId"]


def test_visible_scope_excludes_the_nodes_own_declaration(catalog):
    """自身声明不在可见集里 —— 这就是「当前节点不能引用当前节点定义的变量」。"""
    workflow = Workflow.model_validate(copy.deepcopy(DOCUMENT))
    for node_id, name in zip(NODE_IDS, DECLARED, strict=True):
        scope = visible_scope(workflow.root, catalog, node_id)
        assert scope is not None
        assert name not in scope.variables, f"{node_id} 不该看见自己声明的 {name}"


def test_visible_scope_only_sees_strictly_earlier_declarations(catalog):
    """严格之前的声明按遍历序累积：a7 看得见前面全部，a1 一个也看不见。"""
    workflow = Workflow.model_validate(copy.deepcopy(DOCUMENT))
    first = visible_scope(workflow.root, catalog, "a1")
    last = visible_scope(workflow.root, catalog, "a7")
    assert first is not None and last is not None
    assert first.variables == ()
    assert set(last.variables) == set(DECLARED) - {"finalvar"}


def test_visible_scope_reports_lexical_loop_and_error_vars(catalog):
    """循环/错误变量按**词法嵌套**给：循环体内有 loop.row，catch 里有 err.code。"""
    workflow = Workflow.model_validate(copy.deepcopy(DOCUMENT))
    inside_loop = visible_scope(workflow.root, catalog, "a4")
    in_catch = visible_scope(workflow.root, catalog, "a6")
    in_try_body = visible_scope(workflow.root, catalog, "a5")
    outside = visible_scope(workflow.root, catalog, "a7")
    assert inside_loop is not None and in_catch is not None
    assert in_try_body is not None and outside is not None

    assert inside_loop.loop_vars == ("row",)
    assert in_catch.error_vars == ("err",)
    # try 体里 error_var 还不可用；循环结束后 loop.row 也不可用
    assert in_try_body.error_vars == ()
    assert outside.loop_vars == ()


def test_cross_branch_visibility_is_inherited_from_the_compiler(catalog):
    """★ 钉住一条**刻意照抄**编译器的语义（不是本函数的 bug）。

    编译器按先序遍历累积别名 ⇒ then 里声明的变量在 else 里、try 体里声明的在
    catch 里**都被放行**。菜单与它同口径（维护者 2026-10-14：口径必须一致），
    所以这里断言「可见」；运行期是否取得到取决于分支是否真的走过 —— 那是另一件
    事（编译器侧的既有语义），改它要先改编译器，不能只改菜单。
    """
    workflow = Workflow.model_validate(copy.deepcopy(DOCUMENT))
    in_else = visible_scope(workflow.root, catalog, "a3")
    in_catch = visible_scope(workflow.root, catalog, "a6")
    assert in_else is not None and in_catch is not None
    assert "thenvar" in in_else.variables
    assert "tryvar" in in_catch.variables
    assert _compile_with_reference("a3", "thenvar")
    assert _compile_with_reference("a6", "tryvar")


def test_compiler_distinguishes_self_from_later_references():
    """自引用与「之后节点」是两件事，报错文案不再共用（否则自引用说 declared later）。"""
    with pytest.raises(WorkflowCompileError, match="Self variable reference"):
        WorkflowCompiler(load_catalog(_commands_root())).compile(
            Workflow.model_validate(
                {
                    "schema_version": "1.0",
                    "id": "self",
                    "name": "self",
                    "root": {
                        "type": "sequence",
                        "id": "root",
                        "children": [
                            {
                                "type": "action",
                                "id": "write",
                                "command": "data.setVar",
                                "with": {"varName": "snapshot", "value": "${snapshot}"},
                            }
                        ],
                    },
                }
            ),
            set(),
        )
    with pytest.raises(WorkflowCompileError, match="Forward variable reference"):
        WorkflowCompiler(load_catalog(_commands_root())).compile(
            Workflow.model_validate(
                {
                    "schema_version": "1.0",
                    "id": "forward",
                    "name": "forward",
                    "root": {
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
                    },
                }
            ),
            set(),
        )


def test_rewriting_a_variable_from_its_own_previous_value_stays_allowed():
    """``v = ${v}`` 是合法写法（读自己写入目标的旧值）：自身声明排在「已可见」之后判。

    次序写反（自身声明优先于已声明）会把这类自增/自写读旧值的流程误杀。
    """
    WorkflowCompiler(load_catalog(_commands_root())).compile(
        Workflow.model_validate(
            {
                "schema_version": "1.0",
                "id": "accumulate",
                "name": "accumulate",
                "root": {
                    "type": "sequence",
                    "id": "root",
                    "children": [
                        {
                            "type": "action",
                            "id": "init",
                            "command": "data.setVar",
                            "with": {"varName": "total", "value": "0"},
                        },
                        {
                            "type": "action",
                            "id": "bump",
                            "command": "data.setVar",
                            "with": {"varName": "total", "value": "${total}"},
                        },
                    ],
                },
            }
        ),
        set(),
    )


def test_visible_scope_returns_none_for_an_unknown_node(catalog):
    """节点不存在 ⇒ ``None``（调用方据此给空菜单，而不是退回「列全流程」）。"""
    workflow = Workflow.model_validate(copy.deepcopy(DOCUMENT))
    assert visible_scope(workflow.root, catalog, "no-such-node") is None
