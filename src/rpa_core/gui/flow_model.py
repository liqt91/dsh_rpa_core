"""workflow AST → Qt 树模型映射（GUI 切片 2）。

把 :class:`rpa_core.model.workflow.Workflow` 的不可变 AST 转成可在
``QTreeView`` 里展示/同级重排的 :class:`FlowTreeModel`。

设计约定：
- 每个 AST 节点对应一个树 item；``if`` 用扁平结构（影刀式）：then 子节点直接
  挂在 if 下，一条常驻的「否则」标记行做分支分割，末尾一条「结束 如果」；
  ``try`` 的 ``catch`` 仍建「异常处理」虚拟组；标记行/虚拟组不对应 AST 节点。
- 数据经 Qt ``UserRole`` 携带（节点 id / 节点类型 / 命令 id / 参数摘要），
  delegate 与后续的「模型 → AST」回写都从角色读取，不解析显示文本。
- 拖拽：叶子 action/return 只可拖不可作为放置目标；容器与虚拟组可放置。
  首版只做内存重排（``moveRow``）；切片 4 起 ``model_to_workflow`` 可把
  当前树（含重排与参数编辑）回写为 workflow dict 供保存。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QMimeData, QModelIndex, Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel

from rpa_core.model.workflow import Workflow

# ---- item 数据角色 -------------------------------------------------------
ROLE_NODE_ID = Qt.ItemDataRole.UserRole + 10
ROLE_NODE_TYPE = Qt.ItemDataRole.UserRole + 11
ROLE_COMMAND_ID = Qt.ItemDataRole.UserRole + 12
ROLE_ARGS_SUMMARY = Qt.ItemDataRole.UserRole + 13
ROLE_IS_VIRTUAL = Qt.ItemDataRole.UserRole + 14  # 虚拟分组（then/else/catch/循环体）
ROLE_ARGS_RAW = Qt.ItemDataRole.UserRole + 15  # action 的原始 with 参数 dict（供表单编辑）

# 容器节点类型（可放置子节点）；action/return 为叶子
_CONTAINER_TYPES = {"sequence", "if", "forEach", "try"}
_VIRTUAL_GROUP_TYPES = {"branch-catch"}
# if 的分支分割行（影刀式结构）：if 直接挂 then 子节点，中间一条常驻的
# 「否则」指令行，末尾一条「结束 如果」。与 end-bracket 同属"标记行"：
# 不对应 AST 节点、不可拖、可接受 drop。
_ELSE_MARKER_TYPE = "else-marker"
# 每个容器末尾自动追加的虚拟结束行，用于：
#   1. 视觉边界：明确标出容器 children 的结束位置；
#   2. 拖拽安全网：命中结束行的下半可精确落到容器同级下方（命中区比容器卡片本身更大）；
#   3. 折叠联动：结束行的 parent 是容器，所以容器折叠时自动收起。
_END_BRACKET_TYPE = "end-bracket"

# 控制节点中文徽标（action 的徽标直接用命令命名空间，如 browser/data）
_TYPE_BADGE = {
    "sequence": "顺序",
    "if": "如果",
    "forEach": "循环",
    "try": "异常捕获",
    "return": "返回",
    "branch-catch": "异常处理",
    "else-marker": "否则",
}

_MIME_TYPE = "application/x-rpa-flow-node"


class ArgsHolder:
    """在 item 角色中携带 action 的 with 参数与所属节点的原始 AST dict。

    直接存 Python 对象引用：避免 setData(dict) 经 QVariantMap 转换时
    重排键序（QMap 按键排序）或丢失复杂值类型。

    - ``args``：当前 with 参数（表单应用时整体替换，并同步回 ``raw["with"]``）；
    - ``raw``：该节点的原始 AST dict（切片 4 回写时作为模板，保留 GUI
      不编辑的字段——condition/items/item_var/error_var/output_aliases 等）。
    """

    def __init__(
        self,
        args: dict[str, Any] | None = None,
        *,
        raw: dict[str, Any] | None = None,
    ) -> None:
        self.args: dict[str, Any] = dict(args or {})
        self.raw: dict[str, Any] | None = raw


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


def _real_child_insert_row(parent: QStandardItem) -> int:
    """容器内「追加真实子节点」的插入行：必须停在末尾 end-bracket 之前。

    end-bracket 是容器自己的最后一个 child（见 :func:`build_item` 里的
    ``_add_end``），因此 ``rowCount()`` 并不等于"末位子节点的下一行"，而是
    "结束标记行的下一行"。直接拿它追加，会把真实节点插到「结束 X」**下方**，
    而 parent 仍是该容器 —— 画面上在容器之外，AST 里却在容器之内（保存后
    重新打开会"跳"进容器）。

    所有"追加到容器末尾"的路径都必须走这里：拖拽的 row=-1、落在结束行上半、
    以及双击指令树新增。
    """
    row = parent.rowCount()
    if row > 0 and parent.child(row - 1).data(ROLE_NODE_TYPE) == _END_BRACKET_TYPE:
        row -= 1
    return row


def _else_marker_row(parent: QStandardItem) -> int | None:
    """返回 parent 下「否则」标记行的行号（只有 if 有；没有则 None）。"""
    for row in range(parent.rowCount()):
        if parent.child(row).data(ROLE_NODE_TYPE) == _ELSE_MARKER_TYPE:
            return row
    return None


def _branch_insert_row(parent: QStandardItem, anchor: QStandardItem) -> int:
    """在 parent 内为 anchor 选定插入行：落在 anchor 所属**分支**的末尾。

    if 的两个分支由「否则」标记行分割——标记行之前是 then、之后是 else，
    所以"追加到分支末尾"分别等于"标记行之前"与"容器末尾（结束行之前）"。
    其余容器（sequence/forEach/try/catch）没有标记行，即容器末尾。
    """
    tail = _real_child_insert_row(parent)
    marker = _else_marker_row(parent)
    if marker is None:
        return tail
    if anchor is parent or anchor.row() < marker:
        return marker  # then 分支末尾 = 「否则」行之前
    return tail  # else 分支末尾 = 结束行之前


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
        """放置可行性判定。

        QTreeView 在拖拽进入（dragEnterEvent）时会先用「无效 parent +
        row=-1」探测模型是否接受该 MIME；此处必须放行，否则拖拽从进入
        控件起就被整体拒绝，后续带落点的 dragMove/drop 回调都不会发生。
        具体落点是否合法在带有效 parent 的调用中再按容器类型判定。
        """
        if not data.hasFormat(_MIME_TYPE):
            return False
        if not parent.isValid():
            return True  # dragEnter 能力探测：格式可接受即可
        # 如果 parent 恰好落在 end-bracket 虚拟行上，路由到其真实容器
        parent = self._resolve_drop_parent(parent)
        if not parent.isValid():
            return False
        target = self.itemFromIndex(parent)
        return bool(target and target.data(ROLE_NODE_TYPE) in (
            _CONTAINER_TYPES | _VIRTUAL_GROUP_TYPES
        ))

    def dropMimeData(self, data, action, row, column, parent) -> bool:
        """执行移动：按节点 id 定位源 item，takeRow 后插入目标容器。

        QStandardItemModel 默认 dropMimeData 只认内部 mime；本模型用自定义
        只携 id 的格式，因此自行实现移动。单选（QTreeView 默认单选）。

        注意：配合 canvas.FlowTreeView.startDrag（重写）跳过 Qt 的
        clearOrRemove，避免 InternalMove 下 Qt 用旧索引删错节点。
        此外 FlowTreeView.dragMoveEvent 已接管 hit-test，将 end-bracket
        虚拟行上的 drop 翻译成对其真实容器的操作；这里再做一次 fallback
        路由，防止 Qt 自身的 drop 路径绕过。
        """
        if not data.hasFormat(_MIME_TYPE) or not parent.isValid():
            return False
        # end-bracket 虚拟行上的 drop 路由到其真实容器
        parent = self._resolve_drop_parent(parent)
        if not parent.isValid():
            return False
        target = self.itemFromIndex(parent)
        if target is None or target.data(ROLE_NODE_TYPE) not in (
            _CONTAINER_TYPES | _VIRTUAL_GROUP_TYPES
        ):
            return False
        raw = bytes(data.data(_MIME_TYPE)).decode("utf-8")
        node_id = raw.split(";", 1)[0]
        source = self.find_by_id(node_id)
        if source is None:
            return False
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
        # 落点不得越过末尾的 end-bracket：它是容器的 child，但不属于 children
        dest_row = max(0, min(dest_row, _real_child_insert_row(target_parent)))
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

    # ---- 节点增删（切片 5） ----------------------------------------------
    def existing_ids(self) -> set[str]:
        """收集所有真实节点 id（虚拟组无 id）。"""
        return {
            item.data(ROLE_NODE_ID)
            for item in iter_real_nodes(self)
            if item.data(ROLE_NODE_ID)
        }

    def allocate_node_id(self, prefix: str = "n") -> str:
        """生成全模型唯一的新节点 id：n1、n2…（跳过已占用）。"""
        used = self.existing_ids()
        counter = 1
        while f"{prefix}{counter}" in used:
            counter += 1
        return f"{prefix}{counter}"

    def create_action_item(self, command_id: str) -> QStandardItem:
        """按命令 id 创建一个空参数的新 action 树 item（尚未挂到树上）。"""
        node_id = self.allocate_node_id()
        raw = {
            "type": "action",
            "id": node_id,
            "command": command_id,
            "with": {},
        }
        return build_item(raw)

    def insert_command(
        self, command_id: str, target: QStandardItem | None = None
    ) -> QStandardItem:
        """把新命令 action 插入到目标位置并返回新 item。

        落点规则（target 为画布当前选中项，None 时取根）：
        - 虚拟分组（catch）或 sequence/forEach/try 容器：追加为末位子节点
          （停在末尾 end-bracket 之前，不能落到「结束 X」行下方）；
        - if：追加进 **then 分支**（即「否则」标记行之前）；选中「否则」行或
          else 分支里的节点时，追加进 else 分支；
        - 叶子（action/return）：作为其所在**分支**的末位同级节点。
        """
        new_item = self.create_action_item(command_id)
        root = self.item(0)
        anchor = target if target is not None else root
        if anchor is None:
            raise ValueError("空流程模型，无法插入节点")

        node_type = anchor.data(ROLE_NODE_TYPE)
        if node_type in _VIRTUAL_GROUP_TYPES or node_type in (
            "sequence", "forEach", "try", "if"
        ):
            parent = anchor
        else:
            # 叶子：插到其父容器/分组
            parent = anchor.parent() or root
        # 落点取 anchor 所属**分支**的末尾（if 的 then / else 由「否则」行分割）；
        # 不 appendRow：末尾可能是 end-bracket 虚拟行，必须插到它之前。
        # 注意必须用 **list 形式** 的 insertRow：PySide6 的
        # ``insertRow(row, item)`` 不接收所有权，调用方一旦丢掉返回值，C++ 侧
        # item 会被销毁——该行变成 None（严重时直接段错误）；list/tuple 形式
        # 才转移所有权（appendRow(单个 item) 也会转移，故其余调用点安全）。
        parent.insertRow(_branch_insert_row(parent, anchor), [new_item])
        return new_item

    def remove_item(self, item: QStandardItem) -> bool:
        """删除一个真实节点（整棵子树随父行移除）。

        根节点与虚拟分组受保护不可删；返回是否实际删除。
        """
        if item is None or item.data(ROLE_IS_VIRTUAL):
            return False
        if not item.parent():  # 顶层根节点
            return False
        item.parent().takeRow(item.row())
        return True

    def flags(self, index):
        base = super().flags(index)
        if not index.isValid():
            return base
        # 顶层根节点是整棵树的容器：可放置不可拖走
        if not index.parent().isValid():
            return (base | Qt.ItemFlag.ItemIsDropEnabled) & ~Qt.ItemFlag.ItemIsDragEnabled
        node_type = index.data(ROLE_NODE_TYPE)
        if node_type in (_END_BRACKET_TYPE, _ELSE_MARKER_TYPE):
            # 标记行（结束行 / 否则行）：不对应 AST 节点，不可拖不可选，但接受
            # drop——dragMoveEvent 会把它翻译成容器内对应分支的插入操作
            return (
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsDropEnabled
            )
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

    @staticmethod
    def _resolve_drop_parent(parent_index: QModelIndex) -> QModelIndex:
        """标记行（end-bracket / else-marker）上的 drop 路由到其真实容器。"""
        if (
            parent_index.isValid()
            and parent_index.data(ROLE_NODE_TYPE)
            in (_END_BRACKET_TYPE, _ELSE_MARKER_TYPE)
        ):
            return parent_index.parent()
        return parent_index


def _make_item(
    *,
    title: str,
    node_type: str,
    node_id: str | None,
    command_id: str | None = None,
    args_summary: str = "",
    args_holder: ArgsHolder | None = None,
    virtual: bool = False,
) -> QStandardItem:
    item = QStandardItem(title)
    item.setData(node_id, ROLE_NODE_ID)
    item.setData(node_type, ROLE_NODE_TYPE)
    item.setData(command_id, ROLE_COMMAND_ID)
    item.setData(args_summary, ROLE_ARGS_SUMMARY)
    item.setData(args_holder, ROLE_ARGS_RAW)
    item.setData(virtual, ROLE_IS_VIRTUAL)
    item.setEditable(False)
    return item


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
        with_args = raw.get("with", {})
        command_id = node["command"] if isinstance(node, dict) else node.command
        summary = summarize_args(with_args)
        title = (label(command_id) if label else command_id) or command_id
        return _make_item(
            title=title, node_type="action", node_id=node_id,
            command_id=command_id, args_summary=summary,
            args_holder=ArgsHolder(with_args, raw=raw),
        )

    if node_type == "return":
        value = raw.get("value")
        summary = "" if value is None else repr_json(value)
        return _make_item(
            title="返回", node_type="return", node_id=node_id,
            args_summary=summary, args_holder=ArgsHolder(raw=raw),
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
    item = _make_item(
        title=title, node_type=node_type, node_id=node_id,
        args_holder=ArgsHolder(raw=raw),
    )

    # 为容器追加虚拟结束行（parent=容器，折叠时自动收起）。
    # 规则：
    #   - sequence / forEach（只有一个 children 分支）：在 children 末尾
    #     加 end-bracket，标记整个容器结束；
    #   - try（children + catch 虚拟组）：只给 catch 虚拟组加 end-bracket，
    #     children 分支不加（否则 catch 会"跑到边界外面"）；
    #   - if（then + else 虚拟组）：只给每个虚拟组加 end-bracket，容器本身
    #     不加（虚拟组之间的视觉边界已经足够清晰）；
    #   - 根容器（node_id=="root"）：一律不加结束行，避免画布底部无意义占位。
    def _add_end(parent: QStandardItem, container_title: str) -> None:
        if parent.data(ROLE_NODE_ID) == "root":
            return
        parent.appendRow(_make_item(
            title=f"结束 {container_title}",
            node_type=_END_BRACKET_TYPE,
            node_id=None, virtual=True,
        ))

    if node_type in ("sequence", "forEach"):
        for child in raw.get("children", []):
            item.appendRow(build_item(child, label=label))
        _add_end(item, item.data(Qt.ItemDataRole.DisplayRole))
    elif node_type == "try":
        for child in raw.get("children", []):
            item.appendRow(build_item(child, label=label))
        catch = raw.get("catch", [])
        if catch:
            catch_group = _make_item(
                title="异常处理", node_type="branch-catch", node_id=None, virtual=True
            )
            item.appendRow(catch_group)
            for child in catch:
                catch_group.appendRow(build_item(child, label=label))
            _add_end(catch_group, "异常处理")
    elif node_type == "if":
        # 影刀式扁平结构：then 子节点直接挂在 if 下 → 「否则」指令行 → else 子节点
        # → 「结束 如果」。两个分支的子节点缩进一致（都由 if 直接持有，「否则」
        # 只做分割），因此不再有「则执行/否则执行」两层虚拟分组。
        for child in raw.get("then", []):
            item.appendRow(build_item(child, label=label))
        item.appendRow(_make_item(
            title="否则", node_type=_ELSE_MARKER_TYPE, node_id=None, virtual=True,
        ))
        for child in raw.get("else", []):
            item.appendRow(build_item(child, label=label))
        _add_end(item, "如果")
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


# ---- 模型 → Workflow dict 回写（切片 4：保存闭环） ------------------------
def _rebuild_group(group: QStandardItem) -> list[dict[str, Any]]:
    """虚拟分组（then/else/catch）→ 节点 dict 列表。"""
    result: list[dict[str, Any]] = []
    for row in range(group.rowCount()):
        child = group.child(row)
        # 跳过 end-bracket 虚拟结束行（它也是 group 的 child，但不对应 AST 节点）
        if child.data(ROLE_NODE_TYPE) == _END_BRACKET_TYPE:
            continue
        result.append(_rebuild_node(child))
    return result


def _virtual_groups(item: QStandardItem) -> dict[str, QStandardItem]:
    """收集 item 下的虚拟分组子 item（目前只有 try 的 branch-catch）。"""
    groups: dict[str, QStandardItem] = {}
    for row in range(item.rowCount()):
        child = item.child(row)
        if child.data(ROLE_IS_VIRTUAL):
            groups[child.data(ROLE_NODE_TYPE)] = child
    return groups


def _rebuild_node(item: QStandardItem) -> dict[str, Any]:
    """按树的当前结构把单个 item 还原为 AST 节点 dict。

    以 item 携带的原始 raw dict 为模板浅拷贝，仅替换结构相关的键
    （with/children/then/else/catch），GUI 不编辑的字段
    （condition/items/item_var/error_var/output_aliases/_exprModes 等）原样保留。
    """
    # end-bracket 虚拟行不对应任何 AST 节点：调用方应先过滤，这里做安全网
    if item.data(ROLE_NODE_TYPE) == _END_BRACKET_TYPE:
        return {}
    holder: ArgsHolder = item.data(ROLE_ARGS_RAW)
    node = dict(holder.raw)
    node_type = item.data(ROLE_NODE_TYPE)

    if node_type == "action":
        node["with"] = dict(holder.args)
        return node

    if node_type in ("sequence", "forEach", "try"):
        direct: list[dict[str, Any]] = []
        for row in range(item.rowCount()):
            child = item.child(row)
            if not child.data(ROLE_IS_VIRTUAL):
                direct.append(_rebuild_node(child))
        node["children"] = direct
        if node_type == "try":
            catch_group = _virtual_groups(item).get("branch-catch")
            if catch_group is not None:
                node["catch"] = _rebuild_group(catch_group)
            # 无 catch 虚拟组说明原本为空：保留 raw（缺省 []）
        return node

    if node_type == "if":
        # 以「否则」标记行为界把 if 的子节点切成 then / else 两段
        then: list[dict[str, Any]] = []
        otherwise: list[dict[str, Any]] = []
        in_else = False
        for row in range(item.rowCount()):
            child = item.child(row)
            child_type = child.data(ROLE_NODE_TYPE)
            if child_type == _ELSE_MARKER_TYPE:
                in_else = True
                continue
            if child_type == _END_BRACKET_TYPE:
                continue
            (otherwise if in_else else then).append(_rebuild_node(child))
        node["then"] = then
        if otherwise:
            node["else"] = otherwise
        else:
            # 「否则」行是常驻的展示元素：else 段为空就不落盘 else 键，
            # 免得每个 if 都多出一个空 else。
            node.pop("else", None)
        return node

    # return 等叶子：raw 原样
    return node


def model_to_workflow(
    model: FlowTreeModel, meta: dict[str, Any]
) -> dict[str, Any]:
    """把当前树模型回写为 workflow dict（JSON 形状，键名用 with/else 等别名）。

    meta 提供工作流级字段（schema_version/id/name/inputs/timeout_seconds）；
    返回结果应再经 ``Workflow.model_validate`` 校验后落盘。
    """
    root = model.item(0)
    if root is None:
        raise ValueError("空流程模型，无法回写")
    document = dict(meta)
    document["root"] = _rebuild_node(root)
    return document
