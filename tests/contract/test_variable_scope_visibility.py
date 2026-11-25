"""变量可见范围：用例表 × `compiler.visible_scope` × **编译器当裁判**（M108 恢复）。

这张用例表（`tests/contract/data/scope_visibility_cases.json`）2026-10-14 为「桌面菜单
与 Web 属性面板各写一份作用域判定」而建，两端的实现判定与编译器逐格对账。后来 Qt 与旧
Web 编辑器一起退役，消费它的 `test_variable_scope_visibility.py` 与
`check_scope_visibility.mjs` 双双消失——表还在仓库里，谁也没读它。

M108 起编辑器改成**直接问后端**（`rpa.variableScopes` 读 `visible_scope`），前端不再自
己遍历一遍，于是「两份实现漂移」这条轴消失了，但「`visible_scope` 与编译器不同口径」这
条轴还在，而且更险：菜单里给出一个编译器并不放行的变量，用户点了保存才看到一句
`Forward variable reference`。所以本文件把三边重新钉在一起：

1. **表 vs 实现**：`visible_scope` 的三组名字必须与表里的 `expected*` 完全相等（等值，
   不是包含——多给一个名字就等于给了个跑不通的入口）。
2. **实现 vs 编译器**（真正的裁判）：把候选名字逐个塞进该节点的 `with.value` 编译一次。
   表里说可见的**必须编译通过**；表里说不可见的**必须编译失败**。这条不需要人写期望，
   编译器自己就是真值源，所以它防的是「表写错了」或「`visible_scope` 悄悄改了口径」。
3. **候选集来自全表**：不是只验 expected 里那几个名字，而是所有节点声明过的名字都当
   候选试一遍——只验正例的话，一个「永远返回全集」的实现也能全绿。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from rpa_core.catalog import load_catalog
from rpa_core.compiler.compiler import (
    WorkflowCompileError,
    WorkflowCompiler,
    visible_scope,
)
from rpa_core.model.workflow import Workflow

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "tests" / "contract" / "data" / "scope_visibility_cases.json"
CAPS = ROOT / "commands"
# 一个几乎不可能撞上的名字：用它证明「不可见的名字确实编译失败」这条判据本身有效
NOPE = "___definitely_not_declared___"


@pytest.fixture(scope="module")
def payload() -> dict:
    return json.loads(CASES.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(CAPS)


@pytest.fixture(scope="module")
def granted(catalog) -> set[str]:
    out: set[str] = set()
    for manifest in catalog.values():
        out.update(getattr(manifest, "capabilities", ()) or ())
    return out


def _node_at(root: dict, path: list[dict]) -> dict:
    node = root
    for step in path:
        node = node[step["key"]][step["index"]]
    return node


def _compile(document: dict, catalog, granted: set[str]) -> None:
    workflow = Workflow.model_validate(document)
    WorkflowCompiler(catalog).compile(workflow, granted)


def _candidates(payload: dict) -> list[tuple[str, str]]:
    """表里所有被声明过的名字，摊成 (可见集里的键, 编译器认的引用路径)。

    ★ 循环变量与错误变量的**引用形态不同**，这一点很容易写错（第一版本文件就写错了）：
    `validate_refs` 只有 ``root == "loop"`` 时才在 ``loop_vars`` 里找下一段，所以循环
    变量必须写成 ``${loop.row}``；错误变量则是裸名 ``${err}``（``root in error_vars``）。
    菜单插入的文本必须按这个口径拼，否则「点了插入 → 编译就红」。
    """
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(key: str, ref: str) -> None:
        if ref not in seen:
            seen.add(ref)
            pairs.append((key, ref))

    for case in payload["cases"]:
        for name in case.get("variables") or []:
            add(name, name)
        for name in case.get("loopVars") or []:
            add(name, f"loop.{name}")
        for name in case.get("errorVars") or []:
            add(name, name)
    add(NOPE, NOPE)
    return pairs


def test_cases_are_structurally_complete(payload):
    """用例表自带哨兵：没有 cases、或某条缺 expected，本文件后面的判据就是空转。"""
    assert payload["cases"], "用例表里没有 cases"
    for case in payload["cases"]:
        assert case["nodeId"], case
        for group in ("variables", "loopVars", "errorVars"):
            assert isinstance(case.get(group), list), f"{case['nodeId']} 缺 {group}"


def test_visible_scope_matches_table(payload, catalog):
    """`visible_scope` 的三组名字与用例表**逐格相等**。"""
    workflow = Workflow.model_validate(payload["workflow"])
    mismatches: list[str] = []
    for case in payload["cases"]:
        scope = visible_scope(workflow.root, catalog, case["nodeId"])
        assert scope is not None, f"visible_scope 找不到节点 {case['nodeId']}"
        got = {
            "variables": list(scope.variables),
            "loopVars": list(scope.loop_vars),
            "errorVars": list(scope.error_vars),
        }
        want = {k: list(case[k]) for k in ("variables", "loopVars", "errorVars")}
        if got != want:
            mismatches.append(f"{case['nodeId']}: 期望 {want}，实得 {got}")
    assert not mismatches, "\n".join(mismatches)


def test_compiler_agrees_with_visible_scope(payload, catalog, granted):
    """把每个候选名字塞进该节点编译一次：可见的必须过，不可见的必须红。

    正负样本各设下限：否则一个「全判可见」或「全判不可见」的实现（或一张空表）
    也能让本判据保持绿色——那正是「假判据」的形态。
    """
    candidates = _candidates(payload)
    failures: list[str] = []
    positives = 0
    negatives = 0
    for case in payload["cases"]:
        visible = set(case["variables"]) | set(case["loopVars"]) | set(case["errorVars"])
        for key, ref in candidates:
            document = copy.deepcopy(payload["workflow"])
            _node_at(document["root"], case["path"])["with"]["value"] = "${" + ref + "}"
            try:
                _compile(document, catalog, granted)
                compiled, error = True, ""
            except WorkflowCompileError as exc:
                compiled, error = False, str(exc)
            if key in visible:
                positives += 1
                if not compiled:
                    failures.append(f"{case['nodeId']}: 菜单会说 {ref} 可见，但编译失败：{error}")
            else:
                negatives += 1
                if compiled:
                    failures.append(
                        f"{case['nodeId']}: 菜单不会列出 {ref}，却编译通过了"
                        "（可见集比编译器宽松 ⇒ 用户点了保存才看到报错）"
                    )
    assert positives >= 10, f"正例只有 {positives} 条，判据没打中"
    assert negatives >= 10, f"负例只有 {negatives} 条，判据没打中"
    assert not failures, "\n".join(failures)
