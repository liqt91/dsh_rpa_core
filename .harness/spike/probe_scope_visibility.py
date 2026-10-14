"""只读探针：实测编译器对「变量可见范围」的真实口径（不改任何源码）。

做法：构造一棵含 if / forEach / try 的流程，把每个 action 节点的 ``value``
依次改成引用某一个候选名字，再调 ``WorkflowCompiler.compile``——**能过 = 可见**。
逐格判定，输出矩阵。用实测替代"读代码猜语义"。

用法：``./.venv/Scripts/python.exe .harness/spike/probe_scope_visibility.py``
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from rpa_core.catalog.loader import load_catalog  # noqa: E402
from rpa_core.compiler.compiler import (  # noqa: E402
    WorkflowCompileError,
    WorkflowCompiler,
)
from rpa_core.devserver.app import DEFAULT_CAPABILITIES  # noqa: E402
from rpa_core.model.workflow import Workflow  # noqa: E402


def _setvar(node_id: str, target: str, value: str = "1") -> dict:
    return {
        "type": "action",
        "id": node_id,
        "command": "data.setVar",
        "with": {"varName": target, "value": value},
    }


# 节点 → (声明了什么)；结构刻意覆盖 顺序 / 分支 / 循环 / 异常 四类
DOC = {
    "schema_version": "1.0",
    "id": "probe.scope",
    "name": "scope probe",
    "inputs": {"threshold": "x"},
    "root": {
        "type": "sequence",
        "id": "root",
        "children": [
            _setvar("a1", "alpha"),
            {
                "type": "if",
                "id": "iff",
                "condition": {"op": "eq", "left": "1", "right": "1"},
                "then": [_setvar("a2", "thenvar")],
                "else": [_setvar("a3", "elsevar")],
            },
            {
                "type": "forEach",
                "id": "loop",
                "items": "[1, 2]",
                "item_var": "row",
                "children": [_setvar("a4", "innervar")],
            },
            {
                "type": "try",
                "id": "try",
                "children": [_setvar("a5", "tryvar")],
                "catch": [_setvar("a6", "catchvar")],
                "error_var": "err",
            },
            _setvar("a7", "finalvar"),
        ],
    },
}

NODES = ["a1", "a2", "a3", "a4", "a5", "a6", "a7"]
CANDIDATES = [
    "inputs.threshold",
    "alpha",
    "thenvar",
    "elsevar",
    "innervar",
    "tryvar",
    "catchvar",
    "finalvar",
    "loop.row",  # 显式命名的循环变量
    "loop.item",  # 默认循环变量名（未显式命名）
    "err.code",  # 显式命名的错误变量
    "error.code",  # 默认错误变量名
]


def _probe(node_id: str, candidate: str) -> bool:
    """把 node_id 的 value 换成 ${candidate}，能编译过即「可见」。"""
    doc = copy.deepcopy(DOC)
    target = None

    def visit(node: dict) -> None:
        nonlocal target
        if node.get("id") == node_id:
            target = node
            return
        for key in ("children", "then", "else", "catch"):
            for child in node.get(key) or []:
                visit(child)

    visit(doc["root"])
    assert target is not None, node_id
    target["with"]["value"] = "${" + candidate + "}"
    try:
        WorkflowCompiler(load_catalog(ROOT / "commands")).compile(
            Workflow.model_validate(doc), set(DEFAULT_CAPABILITIES)
        )
    except WorkflowCompileError as exc:
        last[node_id, candidate] = str(exc).split(":")[0]
        return False
    return True


last: dict[tuple[str, str], str] = {}

if __name__ == "__main__":
    print("行 = 引用发生在哪个节点；列 = 被引用的名字；Y=可见/可编译，.=被拒")
    print("（被拒原因写在下方表里）\n")
    header = "节点".ljust(6) + "".join(c.ljust(17) for c in CANDIDATES)
    print(header)
    print("-" * len(header))
    verdicts: dict[tuple[str, str], bool] = {}
    for node_id in NODES:
        row = node_id.ljust(6)
        for candidate in CANDIDATES:
            ok = _probe(node_id, candidate)
            verdicts[node_id, candidate] = ok
            row += ("Y" if ok else ".").ljust(17)
        print(row)

    print("\n被拒的原因（去重）：")
    seen: dict[str, set[str]] = {}
    for key, reason in last.items():
        seen.setdefault(reason, set()).add(f"{key[0]}→{key[1]}")
    for reason, where in sorted(seen.items()):
        print(f"  [{reason}]  {', '.join(sorted(where))}")

    print("\n关键判定：")
    pairs = {
        "自引用（a1 引 alpha）": ("a1", "alpha"),
        "then 的变量在 else 里": ("a3", "thenvar"),
        "else 的变量在 if 之后": ("a7", "elsevar"),
        "循环内声明的变量在循环后": ("a7", "innervar"),
        "try 体内声明的变量在 catch 里": ("a6", "tryvar"),
        "catch 内声明的变量在 try 体里": ("a5", "catchvar"),
        "循环变量进出循环体": ("a4", "loop.row"),
        "默认循环变量名 loop.item": ("a4", "loop.item"),
        "错误变量 err.code 在 catch 内": ("a6", "err.code"),
        "错误变量 err.code 在 try 体内": ("a5", "err.code"),
        "默认错误变量名 error.code": ("a6", "error.code"),
    }
    for label, key in pairs.items():
        print(f"  {'可见' if verdicts.get(key) else '不可见'}  {label}")
