"""workflow AST → Qt 树模型映射（GUI 切片 2）。

把 :class:`rpa_core.model.workflow.Workflow` 的不可变 AST 转成可在
``QTreeView`` 里展示/同级重排的 :class:`FlowTreeModel`。

设计约定：
- 每个 AST 节点对应一个树 item；``if`` 用扁平结构（影刀式）：then 子节点直接
  挂在 if 下，一条**按需添加**的「否则」指令行做分支分割，末尾一条「结束 如果」；
  ``try`` 的 ``catch`` 仍建「异常处理」虚拟组；「否则」行与虚拟组不对应 AST 节点。
- 数据经 Qt ``UserRole`` 携带（节点 id / 节点类型 / 命令 id / 参数摘要），
  delegate 与后续的「模型 → AST」回写都从角色读取，不解析显示文本。
- 拖拽：叶子 action/return 只可拖不可作为放置目标；容器与虚拟组可放置。
  「否则」行是可拖可删的指令行，但只能留在 if 内。
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
# if 的「否则」分支指令行（影刀式结构）：if 直接挂 then 子节点，需要分支时在
# then 之后插一条「否则」指令，末尾一条「结束 如果」。
#
# 「否则」是一条**按需添加**的独立指令（影刀同款）：默认不加，只有 AST 里本来
# 就有 else 段、或用户从指令树显式添加时才出现。它不对应 AST 节点（无真实 id），
# 但作为一条"指令"参与选中 / 删除 / 拖放；拖出所在的 if 会被拒绝（脱离 if 无意义）。
_ELSE_BRANCH_TYPE = "else-branch"
# 「否则」行在拖拽 MIME 里使用的合成 id 前缀：它没有 AST id，但要能被拖动定位。
# 带上所属 if 的 id（形如 @else:c），这样多个 if 都有否则行时也能精确定位到
# 被拖的那一条；前缀里的 @ 保证不会与 allocate_node_id 生成的真实 id（n1、n2…）冲突。
_ELSE_BRANCH_ID_PREFIX = "@else:"
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
    "else-branch": "否则",
}

_MIME_TYPE = "application/x-rpa-flow-node"   # 画布内部节点拖放（move）
_MIME_COMMAND = "application/x-rpa-flow-command"  # 指令树 → 画布（new）


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


def _else_branch_row(parent: QStandardItem) -> int | None:
    """返回 parent 下「否则」指令行的行号（只有 if 会持有；没有则 None）。"""
    for row in range(parent.rowCount()):
        if parent.child(row).data(ROLE_NODE_TYPE) == _ELSE_BRANCH_TYPE:
            return row
    return None


def _branch_insert_row(parent: QStandardItem, anchor: QStandardItem) -> int:
    """在 parent 内为 anchor 选定插入行：落在 anchor 所属**分支**的末尾。

    if 的两个分支由「否则」指令行分割——它之前是 then、之后是 else，所以
    "追加到分支末尾"分别等于"否则行之前"与"容器末尾（结束行之前）"。没有
    「否则」行的 if（默认形态）只有 then 一个分支，即容器末尾。
    其余容器（sequence/forEach/try/catch）没有否则行，同样是容器末尾。
    """
    tail = _real_child_insert_row(parent)
    marker = _else_branch_row(parent)
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
        # 虚拟分组（branch-catch / 结束行）没有 id，天然不可拖；「否则」行带
        # 合成 id（_ELSE_BRANCH_ID），因此可拖。
        ids = [node_id for node_id in ids if node_id]
        if ids:
            data.setData(_MIME_TYPE, ";".join(ids).encode("utf-8"))
        return data

    @staticmethod
    def _dragged_id(data) -> str:
        """从拖拽 MIME 里取出被拖节点的 id（目前只拖单个节点）。"""
        raw = bytes(data.data(_MIME_TYPE)).decode("utf-8")
        return raw.split(";", 1)[0]

    def canDropMimeData(self, data, action, row, column, parent) -> bool:
        """放置可行性判定（同时接受画布内部 node + 指令树 command 两种 MIME）。

        QTreeView 在拖拽进入（dragEnterEvent）时会先用「无效 parent +
        row=-1」探测模型是否接受该 MIME；此处必须放行，否则拖拽从进入
        控件起就被整体拒绝，后续带落点的 dragMove/drop 回调都不会发生。
        具体落点是否合法在带有效 parent 的调用中再按容器类型判定。
        """
        if not (data.hasFormat(_MIME_TYPE) or data.hasFormat(_MIME_COMMAND)):
            return False
        # 指令树拖来的 command 直接放行——落点合法性在 dropMimeData 里处理
        if data.hasFormat(_MIME_COMMAND):
            return True
        # 以下是画布内部 node 拖放（else-branch 守卫等）
        # 先对被拖物做 else-branch 守卫（无论 parent 是否有效都适用）：
        # 否则行只能留在它所属的 if 内，不能拖到顶层或其他容器。
        dragged = self._dragged_id(data)
        if _is_else_branch_id(dragged):
            source = self.find_by_id(dragged)
            # 拖到顶层（invalid parent）= 要把否则拉出 if → 拒绝
            if not parent.isValid():
                return False
            # 有效 parent：路由到真实容器后必须是同一个 if
            parent = self._resolve_drop_parent(parent)
            if not parent.isValid():
                return False
            target = self.itemFromIndex(parent)
            if target is None:
                return False
            return (
                target.data(ROLE_NODE_TYPE) == "if"
                and source is not None
                and source.parent() is target
            )
        if not parent.isValid():
            return True  # dragEnter 能力探测：格式可接受即可
        # 如果 parent 恰好落在 end-bracket / 「否则」行上，路由到其真实容器
        parent = self._resolve_drop_parent(parent)
        if not parent.isValid():
            return False
        target = self.itemFromIndex(parent)
        if target is None:
            return False
        target_type = target.data(ROLE_NODE_TYPE)
        if target_type not in (_CONTAINER_TYPES | _VIRTUAL_GROUP_TYPES):
            return False
        # 「否则」行只能在它的 if 内移动：跨 if 迁移会静默改写两个 if 的分支归属，
        # 几乎不可能是用户意图；拖到别的容器 / 拖出树同样拒绝。
        dragged = self._dragged_id(data)
        if _is_else_branch_id(dragged):
            source = self.find_by_id(dragged)
            return (
                target_type == "if"
                and source is not None
                and source.parent() is target
            )
        return True

    def _insert_item_at_drop(
        self, new_item: QStandardItem, row: int, parent: QModelIndex
    ) -> bool:
        """把新建 item 插到 drop 指定的位置（供指令树 → 画布拖放用）。

        row=-1 / parent invalid → 追加到 invisibleRootItem 末尾（顶层）。
        row>=0 且 parent 有效 → 插到 parent 容器的 row 位置；row 超过末尾时
        追加。end-bracket / 否则行上的 drop 自动路由到其真实容器。
        """
        if not parent.isValid():
            target = self.invisibleRootItem()
            target_row = target.rowCount() if row < 0 else min(row, target.rowCount())
            target.insertRow(target_row, new_item)
            return True
        # 路由 end-bracket / 否则行
        parent = self._resolve_drop_parent(parent)
        if not parent.isValid():
            target = self.invisibleRootItem()
        else:
            target = self.itemFromIndex(parent)
        if target is None:
            target = self.invisibleRootItem()
        # 控制流容器里的有效落点必须跳过 end-bracket / 否则行
        target_row = _real_child_insert_row(target) if row < 0 else min(row, target.rowCount())
        target.insertRow(target_row, new_item)
        return True

    def dropMimeData(self, data, action, row, column, parent) -> bool:
        """执行移动/新建：区分两种 MIME。

        - _MIME_TYPE（画布内部拖放）：takeRow + insertRow 移动已有节点
        - _MIME_COMMAND（指令树 → 画布）：insert_command / insert_node 新建

        注意：配合 canvas.FlowTreeView.startDrag（重写）跳过 Qt 的
        clearOrRemove，避免 InternalMove 下 Qt 用旧索引删错节点。
        此外 FlowTreeView.dragMoveEvent 已接管 hit-test，将 end-bracket /
        「否则」行上的 drop 翻译成对其真实容器的操作；这里再做一次 fallback
        路由，防止 Qt 自身的 drop 路径绕过。
        """
        # === 分支 1：指令树 → 画布（新建节点）===
        if data.hasFormat(_MIME_COMMAND):
            raw = bytes(data.data(_MIME_COMMAND)).decode("utf-8")
            # 格式："command_id" 或 "node_type:xxx"（控制流节点）
            if raw.startswith("node_type:"):
                node_type = raw.split(":", 1)[1]
                new_item = self.create_node_item(node_type)
            else:
                command_id = raw
                if _is_else_branch_id(command_id):
                    # @else 需要给某个 if 添加否则分支——但拖入时无上下文，
                    # 此处不处理，由双击/右键菜单添加
                    return False
                new_item = self.create_action_item(command_id)
            return self._insert_item_at_drop(new_item, row, parent)

        # === 分支 2：画布内部拖放（移动节点）===
        if not data.hasFormat(_MIME_TYPE):
            return False
        # parent.isValid() == False 时对应两种情况：
        # 1) invisibleRootItem 上 drop（根容器扁平化后，顶层容器就是 invisibleRootItem）
        # 2) Qt 探测 invalid parent（canDropMimeData 里已放行，这里兜底）
        # 统一翻译为 invisibleRootItem，让顶层 drop 正常工作
        if not parent.isValid():
            # 否则分支 marker 绝不允许拖出它所属的 if → 顶层 drop 直接拒绝
            node_id = self._dragged_id(data)
            if _is_else_branch_id(node_id):
                return False
            target = self.invisibleRootItem()
            # 直接跳到移动逻辑（不需要再 _resolve_drop_parent）
            source = self.find_by_id(node_id)
            if source is None:
                return False
            if source is target:
                return False
            source_parent = source.parent() or self.invisibleRootItem()
            source_row = source.row()
            # takeRow 前算好目标位置：如果 source 在 target_row 之前，
            # takeRow 会让所有后续行前移 1，所以要减 1 修正
            # （这里 target_row 用 target.rowCount() 作为边界，不提前减）
            if source_parent is target:
                # 同在顶层：row 参数就是用户期望的目标位置
                # 但 takeRow(source_row) 后，如果 source_row < row，
                # 原 row 位置上的元素会前移到 row-1
                target_row = row - 1 if source_row < row else row
            else:
                target_row = row
            item = source_parent.takeRow(source_row)
            target_row = max(0, min(target_row, target.rowCount()))
            target.insertRow(target_row, item)
            return True
        # 结束行 / 否则行上的 drop 路由到其真实容器
        parent = self._resolve_drop_parent(parent)
        if not parent.isValid():
            return False
        target = self.itemFromIndex(parent)
        if target is None or target.data(ROLE_NODE_TYPE) not in (
            _CONTAINER_TYPES | _VIRTUAL_GROUP_TYPES
        ):
            return False
        node_id = self._dragged_id(data)
        source = self.find_by_id(node_id)
        if source is None:
            return False
        if _is_else_branch_id(node_id):
            # 与 canDropMimeData 同口径：否则行只能留在同一个 if 内
            if target.data(ROLE_NODE_TYPE) != "if" or source.parent() is not target:
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

        root = self.invisibleRootItem()
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

    def create_node_item(self, node_type: str) -> QStandardItem:
        """创建控制流节点（sequence/if/forEach/try/return）的空模板 item。

        所有节点分配唯一 node_id，以 ``build_item`` 统一走 AST 树→QStandardItem
        转换，保证虚拟分组/结束标记自动挂载。
        """
        node_id = self.allocate_node_id()
        TEMPLATES: dict[str, dict] = {
            "sequence": {"type": "sequence", "id": node_id, "children": []},
            "forEach": {"type": "forEach", "id": node_id,
                        "items": [], "item_var": "item", "children": []},
            "if": {"type": "if", "id": node_id,
                   "condition": {"op": "truthy", "left": "", "right": None},
                   "then": []},
            "try": {"type": "try", "id": node_id,
                    "children": [], "catch": [], "error_var": "error"},
            "return": {"type": "return", "id": node_id, "value": None},
        }
        if node_type not in TEMPLATES:
            raise ValueError(f"未知节点类型：{node_type}")
        return build_item(TEMPLATES[node_type])

    def insert_command(
        self, command_id: str, target: QStandardItem | None = None
    ) -> QStandardItem:
        """把新命令 action 插入到目标位置并返回新 item。

        落点规则（target 为画布当前选中项，None 时取根）：
        - 虚拟分组（catch）或 sequence/forEach/try 容器：追加为末位子节点
          （停在末尾 end-bracket 之前，不能落到「结束 X」行下方）；
        - if：追加进 **then 分支**（有「否则」行时即该行之前；默认无该行时
          就是容器末尾）；选中「否则」行或 else 分支里的节点时，追加进 else 分支；
        - 叶子（action/return）：作为其所在**分支**的末位同级节点。
        """
        new_item = self.create_action_item(command_id)
        # 根容器已扁平化：invisibleRootItem 是模型"逻辑根"，anchor 默认落在这里
        root = self.invisibleRootItem()
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

    def insert_node(
        self, node_type: str, target: QStandardItem | None = None
    ) -> QStandardItem:
        """插入控制流节点（if/forEach/try/sequence/return）。

        落点规则与 insert_command 一致：追加到当前选中位置所属分支末尾。
        与 insert_command 的区别仅在于：control 是容器（会先建 then/else/
        catch 虚拟分组 + 结束标记），而 return 是叶子。
        """
        new_item = self.create_node_item(node_type)
        root = self.invisibleRootItem()
        anchor = target if target is not None else root
        if anchor is None:
            raise ValueError("空流程模型，无法插入节点")

        node_type_of_anchor = anchor.data(ROLE_NODE_TYPE)
        if node_type_of_anchor in _VIRTUAL_GROUP_TYPES or node_type_of_anchor in (
            "sequence", "forEach", "try", "if"
        ):
            parent = anchor
        else:
            parent = anchor.parent() or root
        parent.insertRow(_branch_insert_row(parent, anchor), [new_item])
        return new_item

    def remove_row(self, index: QModelIndex) -> bool:
        """按 QModelIndex 删除——内部转 remove_item(itemFromIndex)。"""
        item = self.itemFromIndex(index)
        if item is None:
            return False
        return self.remove_item(item)

    def remove_item(self, item: QStandardItem) -> bool:
        """删除一个真实节点或「否则」指令行（整棵子树随父行移除）。

        根节点与虚拟分组（异常处理 / 结束行）受保护不可删；返回是否实际删除。

        「否则」行可删——它就是"取消 else 分支"：删掉分割行后，原本在它下面的
        子节点顺序不变、parent 仍是同一个 if，于是自然并入 then 段末尾，不丢节点。
        """
        if item is None:
            return False
        node_type = item.data(ROLE_NODE_TYPE)
        if item.data(ROLE_IS_VIRTUAL) and node_type != _ELSE_BRANCH_TYPE:
            return False
        # invisibleRootItem 自身不可删（正常情况下不会被传入）
        if item is self.invisibleRootItem():
            return False
        # 注意 Qt 语义：顶层 item（直接挂在 invisibleRootItem 下）的 parent()
        # 返回 None 而非 invisibleRootItem——必须显式回退，否则顶层非首行
        # 节点删除时 AttributeError: 'NoneType' has no attribute 'takeRow'。
        parent = item.parent() or self.invisibleRootItem()
        parent.takeRow(item.row())
        return True

    def add_else_branch(self, if_item: QStandardItem) -> QStandardItem | None:
        """给 if 添加一条「否则」指令行；已有则返回 None。

        「否则」默认不加（影刀同款）：只有用户从指令树显式添加、或 AST 里本来
        就有 else 段才会出现。插入位置是 then 段末尾（即末尾 end-bracket 之前），
        添加后落在它下面的节点归 else 分支。
        """
        if if_item is None or if_item.data(ROLE_NODE_TYPE) != "if":
            return None
        if _else_branch_row(if_item) is not None:
            return None
        marker = _make_else_branch_item(if_item.data(ROLE_NODE_ID))
        if_item.insertRow(_real_child_insert_row(if_item), [marker])
        return marker

    def flags(self, index):
        base = super().flags(index)
        if not index.isValid():
            return base
        # 根容器已扁平化：invisibleRootItem 的直接 child 就是画布顶层节点
        # （原来的 root sequence children），它们都是正常可拖可放的节点，
        # 不再有"顶层根容器不可拖"的特殊保护。
        node_type = index.data(ROLE_NODE_TYPE)
        if node_type == _END_BRACKET_TYPE:
            # 结束标记行：不对应 AST 节点，不可拖不可选，但接受 drop
            # （dragMoveEvent 会把它翻译成容器内末尾 / 容器同级后的插入）
            return (
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsDropEnabled
            )
        if node_type == _ELSE_BRANCH_TYPE:
            # 「否则」是一条独立指令：可选中、可拖、可删，同时接受 drop
            return (
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsDragEnabled
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
        """结束行 / 否则行上的 drop 路由到其真实容器。"""
        if parent_index.isValid() and parent_index.data(ROLE_NODE_TYPE) in (
            _END_BRACKET_TYPE, _ELSE_BRANCH_TYPE
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


def _is_else_branch_id(node_id: str | None) -> bool:
    """是否为「否则」行的合成 id（形如 @else:c）。"""
    return bool(node_id) and str(node_id).startswith(_ELSE_BRANCH_ID_PREFIX)


def _make_else_branch_item(if_id: str) -> QStandardItem:
    """构造 if 的「否则」分支指令行。

    带合成 id（``@else:<if_id>``）以便参与拖拽定位，同时标 virtual=True：它不对应
    AST 节点，因此不计入 existing_ids、也不被 iter_real_nodes 收集。
    """
    return _make_item(
        title="否则",
        node_type=_ELSE_BRANCH_TYPE,
        node_id=f"{_ELSE_BRANCH_ID_PREFIX}{if_id}",
        virtual=True,
    )


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
        # 影刀式扁平结构：then 子节点直接挂在 if 下；有 else 段时在 then 之后插一条
        # 「否则」指令行，再挂 else 子节点，末尾「结束 如果」。两个分支的子节点缩进
        # 一致（都由 if 直接持有，「否则」只做分割），因此没有「则执行/否则执行」两层
        # 虚拟分组。
        #
        # 「否则」按需存在：AST 没有 else 段就不插（默认不加），用户可在指令树里
        # 显式添加——此时 then 段末尾就是插入位置（结束行之前）。
        for child in raw.get("then", []):
            item.appendRow(build_item(child, label=label))
        else_children = raw.get("else") or []
        if else_children:
            item.appendRow(_make_else_branch_item(node_id))
            for child in else_children:
                item.appendRow(build_item(child, label=label))
        _add_end(item, "如果")
    return item


def build_model_from_workflow(
    workflow: Workflow | dict,
    *,
    label: Callable[[str], str] | None = None,
) -> FlowTreeModel:
    """把完整 Workflow（pydantic 或 dict）构造成 FlowTreeModel。

    **根容器扁平化**：AST 的 root 一定是 sequence，在 GUI 中没有必要单独占
    一行"顺序执行"卡片——它的 children 直接作为模型顶层项展示。所有业务逻辑
    （insert_command / dropMimeData / remove_item）原本以 model.item(0)
    为根，现在统一改为以 model.invisibleRootItem() 为根。

    注意：sequence root 的 children 里还可能混着 end-bracket（forEach 容器
    的 build_item 自带），要过滤掉 ROLE_IS_VIRTUAL 的虚拟项。
    """
    model = FlowTreeModel()
    root_node = workflow["root"] if isinstance(workflow, dict) else workflow.root
    root_item = build_item(root_node, label=label)

    # 根容器扁平化：仅当 root 是 sequence 时生效（GUI 中不必单独占一行）。
    # 其他 root 类型（if/try/forEach/action）保留原样作为模型 item(0) 显示。
    root_type = (
        root_node.get("type")
        if isinstance(root_node, dict)
        else getattr(root_node, "type", "")
    )
    if root_type == "sequence":
        # 把 root 的原始 id 挂到 invisibleRootItem 上，model_to_workflow 回写时复用
        if isinstance(root_node, dict):
            _root_id = root_node.get("id", "root")
        else:
            _root_id = getattr(root_node, "id", "root")
        model.invisibleRootItem().setData(_root_id, ROLE_NODE_ID)
        # 把 root 的直接 children 提升为模型顶层项
        # （跳过 end-bracket 等虚拟项，它们属于各自容器的内部结构）
        # 倒序 take + 正序 append，保持原顺序不变
        taken_rows = []
        for row in range(root_item.rowCount()):
            child = root_item.child(row)
            if not child.data(ROLE_IS_VIRTUAL):
                taken_rows.append(row)
        taken_rows.reverse()  # 倒序 take，避免索引偏移
        taken_items = [root_item.takeRow(row) for row in taken_rows]
        for item in reversed(taken_items):  # 正序 append 回模型
            model.appendRow(item)
        # root_item 自身被丢弃——它的 end-bracket 也随它一起释放，根容器不需要
    else:
        # 非 sequence root：保留原样作为模型顶层唯一 item
        model.appendRow(root_item)
    return model


def iter_real_nodes(model: FlowTreeModel):
    """深度优先遍历所有对应真实 AST 节点的 item（跳过虚拟分组）。"""
    def walk(item: QStandardItem):
        if not item.data(ROLE_IS_VIRTUAL):
            yield item
        for row in range(item.rowCount()):
            yield from walk(item.child(row))

    root = model.invisibleRootItem()
    # invisibleRootItem 自身没有 ROLE_IS_VIRTUAL 属性，但我们不 yield 它
    for row in range(root.rowCount()):
        yield from walk(root.child(row))


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
    """收集 item 下的虚拟分组子 item（目前只有 try 的 branch-catch）。

    只认 _VIRTUAL_GROUP_TYPES：if 的「否则」行同为 virtual，但它是指令行、
    不是分组容器，混进来只会让"分组"语义含糊。
    """
    groups: dict[str, QStandardItem] = {}
    for row in range(item.rowCount()):
        child = item.child(row)
        if child.data(ROLE_NODE_TYPE) in _VIRTUAL_GROUP_TYPES:
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
        # 以「否则」指令行为界把 if 的子节点切成 then / else 两段；没有该行时
        # （默认形态）所有子节点都归 then。
        then: list[dict[str, Any]] = []
        otherwise: list[dict[str, Any]] = []
        in_else = False
        for row in range(item.rowCount()):
            child = item.child(row)
            child_type = child.data(ROLE_NODE_TYPE)
            if child_type == _ELSE_BRANCH_TYPE:
                in_else = True
                continue
            if child_type == _END_BRACKET_TYPE:
                continue
            (otherwise if in_else else then).append(_rebuild_node(child))
        node["then"] = then
        if otherwise:
            node["else"] = otherwise
        else:
            # 「否则」行是可选指令：没添加、或添加了但下面是空的，都不落盘 else 键，
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

    根容器在 GUI 中已扁平化（model 顶层是原 sequence root 的 children），
    回写时把所有顶层 item 包装成一个 sequence root——type=sequence、
    children=顶层 item 的 _rebuild_node 列表，保持 AST 契约不变。
    """
    top_root = model.invisibleRootItem()
    # 判断是否扁平化：invisibleRootItem 上挂了 ROLE_NODE_ID = root 的原始 id
    # （build_model_from_workflow 里 sequence root 扁平化时会 setData 上去）
    root_id_was_set = bool(top_root.data(ROLE_NODE_ID))
    if not root_id_was_set and top_root.rowCount() == 1:
        # 非 sequence root 没被扁平化——item(0) 就是 root 节点
        doc_root = _rebuild_node(top_root.child(0))
        document = dict(meta)
        document["root"] = doc_root
        return document
    # sequence root 已扁平化——顶层都是原 sequence 的 children
    root_id = top_root.data(ROLE_NODE_ID) or "root"
    children: list[dict[str, Any]] = []
    for row in range(top_root.rowCount()):
        child = top_root.child(row)
        if child.data(ROLE_NODE_TYPE) == _END_BRACKET_TYPE:
            continue
        children.append(_rebuild_node(child))
    document = dict(meta)
    document["root"] = {"type": "sequence", "id": root_id, "children": children}
    return document
