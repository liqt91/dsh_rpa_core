"""workflow AST → Qt 树模型映射（GUI 切片 2）。

把 :class:`rpa_core.model.workflow.Workflow` 的不可变 AST 转成可在
``QTreeView`` 里展示/同级重排的 :class:`FlowTreeModel`。

设计约定：
- 每个 AST 节点对应一个树 item；``if`` 额外建「则执行/否则执行」两个虚拟
  分组 item，``try`` 的 ``catch`` 建「异常处理」虚拟组；虚拟组不对应 AST 节点。
- 数据经 Qt ``UserRole`` 携带（节点 id / 节点类型 / 命令 id / 参数摘要），
  delegate 与后续的「模型 → AST」回写都从角色读取，不解析显示文本。
- 拖拽：叶子 action/return 只可拖不可作为放置目标；容器与虚拟组可放置。
  首版只做内存重排（``moveRow``），保存回 workflow.json 在后续切片接入。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QMimeData, Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel

from rpa_core.model.workflow import Workflow

# ---- item 数据角色 -------------------------------------------------------
ROLE_NODE_ID = Qt.ItemDataRole.UserRole + 10
ROLE_NODE_TYPE = Qt.ItemDataRole.UserRole + 11
ROLE_COMMAND_ID = Qt.ItemDataRole.UserRole + 12
ROLE_ARGS_SUMMARY = Qt.ItemDataRole.UserRole + 13
ROLE_IS_VIRTUAL = Qt.ItemDataRole.UserRole + 14  # 虚拟分组（then/else/catch/循环体）

# 容器节点类型（可放置子节点）；action/return 为叶子
_CONTAINER_TYPES = {"sequence", "if", "forEach", "try"}
_VIRTUAL_GROUP_TYPES = {"branch-then", "branch-else", "branch-catch"}

# 控制节点中文徽标（action 的徽标直接用命令命名空间，如 browser/data）
_TYPE_BADGE = {
    "sequence": "顺序",
    "if": "如果",
    "forEach": "循环",
    "try": "异常捕获",
    "return": "返回",
    "branch-then": "则执行",
    "branch-else": "否则执行",
    "branch-catch": "异常处理",
}

_MIME_TYPE = "application/x-rpa-flow-node"


def summarize_args(with_args: dict[str, Any], limit: int = 2) -> str:
    """把 action 的 with 参数压成一行等宽摘要（key=value，最多 limit 对）。"""
    pairs: list[str] = []
    for key, value in list(with_args.items())[:limit]:
        text = value if isinstance(value, str) else repr_json(value)
        if len(text) > 18:
            text = text[:17] + "…"
        pairs.append(f"{key}={text}")
    extra = len(with_args) - limit
    if extra > 0:
        pairs.append(f"+{extra}")
    return "  ".join(pairs)


def repr_json(value: Any) -> str:
    """短 JSON 表示（中文不转义），供参数摘要使用。"""
    import json

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class FlowTreeModel(QStandardItemModel):
    """流程 AST 树模型：支持容器内同级/跨容器拖拽重排。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setColumnCount(1)

    # ---- 拖拽 MIME：只携带节点 id（移动语义，禁止跨模型复制） ------------
    def mimeTypes(self) -> list[str]:
        return [_MIME_TYPE]

    def mimeData(self, indexes) -> QMimeData:
        data = QMimeData()
        ids = [idx.data(ROLE_NODE_ID) for idx in indexes if idx.isValid()]
        ids = [node_id for node_id in ids if node_id]  # 虚拟组不可拖
        if ids:
            data.setData(_MIME_TYPE, ";".join(ids).encode("utf-8"))
        return data

    def canDropMimeData(self, data, action, row, column, parent) -> bool:
        if not data.hasFormat(_MIME_TYPE) or not parent.isValid():
            return False
        target = self.itemFromIndex(parent)
        return bool(target and target.data(ROLE_NODE_TYPE) in (
            _CONTAINER_TYPES | _VIRTUAL_GROUP_TYPES
        ))

    def dropMimeData(self, data, action, row, column, parent) -> bool:
        """执行移动：按节点 id 定位源 item，takeRow 后插入目标容器。

        QStandardItemModel 默认 dropMimeData 只认内部 mime；本模型用自定义
        只携 id 的格式，因此自行实现移动。单选（QTreeView 默认单选）。
        """
        if not self.canDropMimeData(data, action, row, column, parent):
            return False
        raw = bytes(data.data(_MIME_TYPE)).decode("utf-8")
        node_id = raw.split(";", 1)[0]
        source = self.find_by_id(node_id)
        if source is None:
            return False
        target = self.itemFromIndex(parent)
        if source is target or self._is_descendant(source, target):
            return False  # 不能移入自身或自己的后代（成环）

        source_parent = source.parent() or self.invisibleRootItem()
        target_parent = target
        source_row = source.row()
        taken = source_parent.takeRow(source_row)

        dest_row = row if row >= 0 else target_parent.rowCount()
        # 同一父级内向下移动时，源行已先被移除，Qt 给的落点索引需左移 1
        if source_parent is target_parent and source_row < dest_row:
            dest_row -= 1
        dest_row = max(0, min(dest_row, target_parent.rowCount()))
        target_parent.insertRow(dest_row, taken)
        return True

    @staticmethod
    def _is_descendant(maybe_ancestor: QStandardItem, node: QStandardItem) -> bool:
        """node 是否为 maybe_ancestor 的后代（含自身由调用方先排除）。"""
        current = node
        while current is not None:
            if current is maybe_ancestor:
                return True
            current = current.parent()
        return False

    def find_by_id(self, node_id: str) -> QStandardItem | None:
        """按 AST 节点 id 查找树 item（单流程模型遍历整棵树）。"""
        def walk(item: QStandardItem) -> QStandardItem | None:
            if item.data(ROLE_NODE_ID) == node_id:
                return item
            for row_index in range(item.rowCount()):
                found = walk(item.child(row_index))
                if found is not None:
                    return found
            return None

        root = self.item(0)
        return walk(root) if root is not None else None

    def flags(self, index):
        base = super().flags(index)
        if not index.isValid():
            return base
        # 顶层根节点是整棵树的容器：可放置不可拖走
        if not index.parent().isValid():
            return (base | Qt.ItemFlag.ItemIsDropEnabled) & ~Qt.ItemFlag.ItemIsDragEnabled
        node_type = index.data(ROLE_NODE_TYPE)
        if node_type in _VIRTUAL_GROUP_TYPES:
            # 虚拟分组：可放置/可选，但自身不可拖
            return (
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsDropEnabled
            )
        flags = base | Qt.ItemFlag.ItemIsDragEnabled
        if node_type in _CONTAINER_TYPES:
            flags |= Qt.ItemFlag.ItemIsDropEnabled
        else:
            # 叶子（action/return）不可作为放置目标
            flags &= ~Qt.ItemFlag.ItemIsDropEnabled
        return flags


def _make_item(
    *,
    title: str,
    node_type: str,
    node_id: str | None,
    command_id: str | None = None,
    args_summary: str = "",
    virtual: bool = False,
) -> QStandardItem:
    item = QStandardItem(title)
    item.setData(node_id, ROLE_NODE_ID)
    item.setData(node_type, ROLE_NODE_TYPE)
    item.setData(command_id, ROLE_COMMAND_ID)
    item.setData(args_summary, ROLE_ARGS_SUMMARY)
    item.setData(virtual, ROLE_IS_VIRTUAL)
    item.setEditable(False)
    return item


def _action_title(node: Any) -> tuple[str, str]:
    """返回 action 节点的（命令 id, 参数摘要）。"""
    with_args = node.get("with", {}) if isinstance(node, dict) else node.with_
    command_id = node["command"] if isinstance(node, dict) else node.command
    return command_id, summarize_args(with_args)


def _node_dict(node: Any) -> dict:
    return node if isinstance(node, dict) else node.model_dump(by_alias=True)


def build_item(node: Any, *, label: Callable[[str], str] | None = None) -> QStandardItem:
    """递归把单个 AST 节点构造成树 item。

    label: 可选的命令 id → 中文名映射（GUI 暂无 i18n 时直接用命令 id）。
    """
    raw = _node_dict(node)
    node_type = raw["type"]
    node_id = raw["id"]

    if node_type == "action":
        command_id, summary = _action_title(raw)
        title = (label(command_id) if label else command_id) or command_id
        return _make_item(
            title=title, node_type="action", node_id=node_id,
            command_id=command_id, args_summary=summary,
        )

    if node_type == "return":
        value = raw.get("value")
        summary = "" if value is None else repr_json(value)
        return _make_item(
            title="返回", node_type="return", node_id=node_id, args_summary=summary
        )

    # 容器节点
    if node_type == "forEach":
        items_ref = raw.get("items")
        title = f"循环（{repr_json(items_ref)[:24]}）"
    elif node_type == "if":
        cond = raw.get("condition", {})
        title = f"如果（{cond.get('left', '')} {cond.get('op', '')} {cond.get('right', '')}）"
    elif node_type == "try":
        title = "异常捕获"
    else:
        title = "顺序执行"
    item = _make_item(title=title, node_type=node_type, node_id=node_id)

    def attach_children(children: list[Any], parent: QStandardItem) -> None:
        for child in children:
            parent.appendRow(build_item(child, label=label))

    if node_type in ("sequence", "forEach"):
        attach_children(raw.get("children", []), item)
    elif node_type == "try":
        attach_children(raw.get("children", []), item)
        catch = raw.get("catch", [])
        if catch:
            catch_group = _make_item(
                title="异常处理", node_type="branch-catch", node_id=None, virtual=True
            )
            item.appendRow(catch_group)
            attach_children(catch, catch_group)
    elif node_type == "if":
        then_group = _make_item(
            title="则执行", node_type="branch-then", node_id=None, virtual=True
        )
        item.appendRow(then_group)
        attach_children(raw.get("then", []), then_group)
        otherwise = raw.get("else", [])
        if otherwise:
            else_group = _make_item(
                title="否则执行", node_type="branch-else", node_id=None, virtual=True
            )
            item.appendRow(else_group)
            attach_children(otherwise, else_group)
    return item


def build_model_from_workflow(
    workflow: Workflow | dict,
    *,
    label: Callable[[str], str] | None = None,
) -> FlowTreeModel:
    """把完整 Workflow（pydantic 或 dict）构造成 FlowTreeModel。"""
    model = FlowTreeModel()
    root_node = workflow["root"] if isinstance(workflow, dict) else workflow.root
    root_item = build_item(root_node, label=label)
    model.appendRow(root_item)
    return model


def iter_real_nodes(model: FlowTreeModel):
    """深度优先遍历所有对应真实 AST 节点的 item（跳过虚拟分组）。"""
    def walk(item: QStandardItem):
        if not item.data(ROLE_IS_VIRTUAL):
            yield item
        for row in range(item.rowCount()):
            yield from walk(item.child(row))

    root = model.item(0)
    if root is not None:
        yield from walk(root)
