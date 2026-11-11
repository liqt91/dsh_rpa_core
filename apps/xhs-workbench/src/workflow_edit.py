"""工作流编辑：对 workflow JSON 做改参数/插节点/删节点/上下移，全程保持结构合法。

只管「改文档」，不管编译校验——校验走 ``rpa-core validate`` 子进程（CLI 契约），
编译不过时把原始报错原样回给前端，不在这里自己实现一份会漂移的校验器。

护栏（都不是静默降级，都是显式拒绝）：
- 删被后续节点引用的步骤 ⇒ 拒绝（否则保存即留下悬空 ``${steps.x.outputs...}``）。
- 参数值为空串 ⇒ 删除该键（required 缺键交给 validate 报出来，不偷偷留空串）。
- 形如 ``[inputs.x]`` 的文本 ⇒ 自动把 ``_exprModes[key]`` 置为 ``"fx"``；
  整串 ``${...}`` 引用不需要 fx（编译器整串认引用），普通文本也不挂 fx。
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

_STEP_REF_RE = re.compile(r"\$\{steps\.([A-Za-z0-9_]+)\.outputs\.")
_FX_TAG_RE = re.compile(r"^\[.*\]$", re.S)


def load(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"流程文件顶层必须是对象：{path}")
    return data


def save(path: str | Path, document: dict[str, Any]) -> None:
    """原子写（tmp + os.replace）：run/validate 可能在读，绝不能让它们读到半截文件。"""
    fp = Path(path)
    fd, tmp_name = tempfile.mkstemp(dir=fp.parent, prefix=fp.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(document, ensure_ascii=False, indent=2) + "\n")
        os.replace(tmp_name, fp)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def _action_lists(node: Any):
    """产出 (children_list, ) 元组供定位；只下钻有 children 的容器。"""
    if isinstance(node, dict):
        children = node.get("children")
        if isinstance(children, list):
            yield children
            for child in children:
                yield from _action_lists(child)


def _root_children(document: dict[str, Any]) -> list[dict[str, Any]]:
    root = document.get("root")
    if not isinstance(root, dict) or not isinstance(root.get("children"), list):
        raise ValueError("流程 root 必须是带 children 的容器节点")
    return root["children"]


def find_node(document: dict[str, Any], node_id: str) -> tuple[list, int, dict] | None:
    """按 id 定位节点，返回 (所在 children 列表, 下标, 节点)。找不到返回 None。"""
    for children in _action_lists(document.get("root")):
        for i, node in enumerate(children):
            if isinstance(node, dict) and str(node.get("id") or "") == node_id:
                return children, i, node
    return None


def list_node_ids(document: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for children in _action_lists(document.get("root")):
        for node in children:
            if isinstance(node, dict) and node.get("command"):
                out.append(str(node.get("id") or ""))
    return out


def _referenced_steps(node: dict[str, Any]) -> set[str]:
    refs: set[str] = set()
    for value in (node.get("with") or {}).values():
        if isinstance(value, str):
            refs.update(_STEP_REF_RE.findall(value))
    return refs


def update_params(document: dict[str, Any], node_id: str, params: dict[str, Any]) -> None:
    """整体替换 ``with``：前端发回的就是「保存后应该长这样」的完整参数集。

    空串键被丢弃；``_exprModes`` 按内容形状自动维护（fx 标签 / 整串引用 / 纯文本）。
    """
    found = find_node(document, node_id)
    if found is None:
        raise KeyError(f"节点不存在：{node_id}")
    _, _, node = found
    if not node.get("command"):
        raise ValueError(f"节点 {node_id} 不是 action，不能改参数")

    cleaned: dict[str, Any] = {}
    expr_modes: dict[str, str] = dict(node.get("_exprModes") or {})
    for key, value in params.items():
        if isinstance(value, str):
            text = value.strip()
            if not text:
                expr_modes.pop(key, None)
                continue
            # fx 标签语法（[inputs.x] 可与文本混排）才挂 fx；整串 ${...} 引用是编译器
            # 默认认的形状，不需要 fx 标记；普通文本同样不挂。
            if _FX_TAG_RE.match(text):
                expr_modes[key] = "fx"
            else:
                expr_modes.pop(key, None)
        else:
            expr_modes.pop(key, None)
        cleaned[key] = value
    node["with"] = cleaned
    if expr_modes:
        node["_exprModes"] = expr_modes
    else:
        node.pop("_exprModes", None)


def _allocate_id(document: dict[str, Any], command: str) -> str:
    base = command.rsplit(".", 1)[-1]
    used = set(list_node_ids(document))
    if base not in used:
        return base
    n = 2
    while f"{base}{n}" in used:
        n += 1
    return f"{base}{n}"


def insert_node(
    document: dict[str, Any],
    command: str,
    after_id: str | None,
    defaults: dict[str, Any],
) -> dict[str, Any]:
    """在 ``after_id`` 之后插入新节点（after_id=None ⇒ 追加到末尾），返回新节点。"""
    children = _root_children(document)
    node = {
        "type": "action",
        "id": _allocate_id(document, command),
        "command": command,
        "with": dict(defaults),
    }
    if after_id:
        found = find_node(document, after_id)
        if found is not None and found[0] is children:
            children.insert(found[1] + 1, node)
            return node
        # anchor 不在顶层（嵌套节点）：落到顶层末尾，别插错层
    children.append(node)
    return node


def delete_node(document: dict[str, Any], node_id: str) -> None:
    found = find_node(document, node_id)
    if found is None:
        raise KeyError(f"节点不存在：{node_id}")
    children, index, node = found
    # 护栏：被后续节点引用的步骤不许删（删了就是悬空引用，静默生成坏流程）
    referrers = [
        other.get("id")
        for other_id in list_node_ids(document)
        for other in [find_node(document, other_id)[2]]
        if other_id != node_id and node_id in _referenced_steps(other)
    ]
    if referrers:
        names = sorted(set(map(str, referrers)))
        raise ValueError(f"节点 {node_id} 被 {names} 引用，先改掉那些步骤的参数")
    del children[index]


def move_node(document: dict[str, Any], node_id: str, direction: int) -> None:
    """同层上移/下移一位。到顶/到底或跨容器一律显式拒绝。"""
    found = find_node(document, node_id)
    if found is None:
        raise KeyError(f"节点不存在：{node_id}")
    children, index, node = found
    target = index + (-1 if direction < 0 else 1)
    if target < 0 or target >= len(children):
        raise ValueError("已经在边界上，移不动了")
    children[index], children[target] = children[target], children[index]
