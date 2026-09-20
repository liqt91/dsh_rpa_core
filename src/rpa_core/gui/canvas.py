"""中部流程卡片画布（GUI 切片 2）。

用 ``QTreeView`` + 自绘 :class:`CardDelegate` 复刻 Web 编辑器 ``#canvas`` 的
节点卡片观感（ADR 0014 §8.2 已验证可行）：

- 圆角白卡片浮在浅灰画布上，选中浅蓝、hover 轻阴影；
- 左侧 4px 深度彩色线（按树深度换色，对应 Web ``ol.children`` 的 border-left）；
- 行内：拖柄 + 同级序号 + 命令名（粗体）+ 等宽参数摘要 + 类型徽标；
- 虚拟分组（异常处理）渲染为轻量分组条而非卡片；结束标记行渲染为与父容器对齐的
  浅灰虚线边界行；「否则」指令行渲染为带拖柄的同款浅色指令条（可按需添加/删除）；
- 拖拽重排走 Qt 原生 InternalMove，合法性由 FlowTreeModel.flags/canDropMimeData 约束。

首版仅浅色（qlight）调色板；深色随全局皮肤切换在后续切片接入。
"""

from __future__ import annotations

from PySide6.QtCore import (
    QItemSelectionModel,
    QModelIndex,
    QRect,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QDrag, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeView,
    QWidget,
)

from rpa_core.gui.flow_model import (
    _ELSE_BRANCH_TYPE,
    _END_BRACKET_TYPE,
    _MIME_COMMAND,
    _MIME_TYPE,
    ROLE_ARGS_RAW,
    ROLE_ARGS_SUMMARY,
    ROLE_BREAKPOINT,
    ROLE_COMMAND_ID,
    ROLE_IS_VIRTUAL,
    ROLE_NODE_ID,
    ROLE_NODE_TYPE,
    ROLE_RUN_STATE,
    FlowTreeModel,
)

# 深度色线（Web 端 depth-0..5 同谱系的饱和色）
_DEPTH_COLORS = ["#0969da", "#1a7f37", "#9a6700", "#8250df", "#bc4c00", "#0598bc"]

# 浅色卡片调色板（与 QDarkStyle LightPalette 协调：底 #FAFAFA / 边 #C0C4C8）
_CARD = "#ffffff"
_CARD_SELECTED = "#daedff"
_BORDER = QColor("#c0c4c8")
_BORDER_HOVER = QColor(25, 35, 45, 70)
_SHADOW = QColor(25, 35, 45, 28)
_CMD_TEXT = "#19232d"
_ARGS_TEXT = "#64707d"
_GRIP = "#9da9b5"

_ROW_HEIGHT = 46
_CARD_RADIUS = 6
_BAR_WIDTH = 4
# 树缩进步长：结束行 / 否则行按"减一级缩进"与父容器卡片对齐
_INDENT = 22
_GROUP_BG = "#eef1f4"

# 卡片尾删除按钮（文字胶囊）：固定尺寸，paint 与 mouseReleaseEvent 热区共用
_DELETE_BTN_W = 36
_DELETE_BTN_H = 18
_DELETE_BTN_MARGIN_RIGHT = 6

# 左侧编号栏（影刀式）：断点红点 + 全局行号 + 错误徽标 + 容器收起/展开按钮。
# 所有行内容统一右移该宽度，编号栏本身不随缩进移动。
# 断点列（最左，M24）与错误徽标/收起按钮不会同时出现：错误徽标只给缺必填参数的
# action 叶子，收起按钮只给有子节点的容器行。
_GUTTER_WIDTH = 58
_GUTTER_BREAKPOINT_X = 9       # 断点圆点圆心 x（最左列，影刀式点一下切换）
_GUTTER_NUMBER_X0 = 18         # 行号区左缘（右对齐到 38）
_GUTTER_NUMBER_W = 20
_GUTTER_ERROR_X = 46           # 错误徽标圆心 x
_COLLAPSE_BTN = 14             # 收起/展开按钮边长
_BREAKPOINT_RADIUS = 4


def delete_button_rect(card_rect: QRect) -> QRect:
    """卡片尾删除按钮的矩形：右对齐卡片尾（留 6px 边距）、垂直居中。

    注意 QRect 闭区间语义（right() = left+width-1）：左边距需 +1 才能
    让按钮右缘与卡片右缘精确相距 6px。
    """
    return QRect(
        card_rect.right() - _DELETE_BTN_MARGIN_RIGHT - _DELETE_BTN_W + 1,
        card_rect.top() + (card_rect.height() - _DELETE_BTN_H) // 2,
        _DELETE_BTN_W,
        _DELETE_BTN_H,
    )


def breakpoint_button_rect(row_rect: QRect) -> QRect:
    """编号栏最左列（断点列）的点击热区（影刀式：点行首切换断点）。

    热区比红点大一圈（14×整行高），便于点中；与绘制共用同一 x 基准。
    """
    return QRect(
        max(0, _GUTTER_BREAKPOINT_X - 7),
        row_rect.top(),
        14,
        row_rect.height(),
    )


def collapse_button_rect(row_rect: QRect) -> QRect:
    """编号栏内收起/展开按钮的矩形：固定在栏右侧、垂直居中。

    与 CardDelegate 绘制、FlowTreeView 点击热区共用同一矩形（所见即所点）。
    """
    size = _COLLAPSE_BTN
    return QRect(
        _GUTTER_WIDTH - size - 4,
        row_rect.top() + (row_rect.height() - size) // 2,
        size,
        size,
    )


def _index_depth(index: QModelIndex) -> int:
    """QModelIndex 没有 depth()：沿父链计数（根节点深度 0）。"""
    depth = 0
    parent = index.parent()
    while parent.isValid():
        depth += 1
        parent = parent.parent()
    return depth


class FlowTreeView(QTreeView):
    """卡片画布专用 QTreeView：重写拖拽三件套，自管 hit-test + 视觉反馈。

    Qt 默认的 QTreeView 在 InternalMove 下的落点判定有两个问题：
    1. startDrag() 在 MoveAction 返回后会调 clearOrRemove() 二次删源行，
       但我们的 dropMimeData 已经原子地 takeRow+insertRow，导致卡片消失；
    2. 对自绘无边框卡片行，Qt 的 OnItem 命中区域（拖进容器内部）过宽，
       Above/Below（拖成同级）的命中区域过窄，用户想把 forEach 内的节点
       拖出到 forEach 同级时很难精准命中。

    解决方案：
    - 重写 startDrag：只创建 QDrag + exec，跳过基类的 clearOrRemove；
    - 重写 dragMoveEvent：按行高 40/20/40 的比例自判 Above/OnItem/Below，
      让「拖成同级」的命中区足够大，且完全接管落点指示条；
    - 重写 dropEvent：用 dragMoveEvent 判定的结果直接调 dropMimeData，
      不再依赖 Qt 传入的 row/parent。
    """

    # 命中比例：上 40%=AboveItem、中 20%=OnItem、下 40%=BelowItem
    # 这样 Above/Below 各有 18px 命中区（_ROW_HEIGHT=46），远大于默认的 23px/极小 OnItem。
    _ABOVE_THRESHOLD = 0.40
    _BELOW_THRESHOLD = 0.60

    # 断点切换请求（M24，影刀式点行首）：由 app 接住并维护断点集合。
    breakpoint_toggled = Signal(QModelIndex)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # 当前拖拽落点状态，由 dragMoveEvent 写入、paintEvent/dropEvent 读取
        # None 表示没有正在进行的拖拽（dragEnter 未到达或已 leave）
        self._drag_target: dict | None = None
        # 本次按下是否已引发拖拽（release 时据此决定是否补收敛选中集）
        self._press_dragged = False

    # ---- Qt 拖拽三件套 ----------------------------------------------------
    def startDrag(self, supportedActions: Qt.DropAction) -> None:
        indices = self.selectionModel().selectedIndexes()
        if not indices:
            return
        self._press_dragged = True
        mime_data = self.model().mimeData(indices)
        if mime_data is None:
            return
        drag = QDrag(self)
        drag.setMimeData(mime_data)
        from rpa_core.gui import debug_log

        if debug_log.ENABLED:
            debug_log.log(
                "startDrag",
                ids=[index.data(ROLE_NODE_ID) for index in indices],
                state=str(self.state()),
            )
        # 仅 setMimeData + exec，不调基类 startDrag（它会在 exec 返回
        # MoveAction 后调 clearOrRemove 二次删源行）。
        drag.exec(supportedActions)
        # 复位视图状态：跳过基类实现后视图可能停在 DraggingState，导致拖放后
        # 「点击不选中、鼠标移开才选中」。
        self._reset_drag_state()

    def _reset_drag_state(self) -> None:
        self._drag_target = None
        try:
            self.setState(QAbstractItemView.State.NoState)
        except (AttributeError, TypeError):  # pragma: no cover - 绑定差异
            pass
        self.viewport().update()
        from rpa_core.gui import debug_log

        if debug_log.ENABLED:
            debug_log.log("drag-reset", state=str(self.state()))

    def dragMoveEvent(self, event) -> None:
        """自判落点区域并驱动视觉指示条刷新。

        同时接受画布内部移动（x-rpa-flow-node）与指令树拖入新建
        （x-rpa-flow-command）——hit-test 与落点计算与 MIME 无关；command 的
        落点合法性由 dropMimeData / _insert_item_at_drop 兜底（此前这里只认
        node 格式，指令树拖入在 move 阶段被 ignore，表现为「拖不到画布」）。
        """
        mime = event.mimeData()
        if not (
            mime.hasFormat("application/x-rpa-flow-node")
            or mime.hasFormat(_MIME_COMMAND)
        ):
            event.ignore()
            return

        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        index = self.indexAt(pos)
        if not index.isValid():
            # 空白区（**含空流程**）：落点 = 追加到根末尾（对齐 Web 的 rootEndDrop）。
            # 空流程一行都没有，indexAt 必然无效——若不在这里接受，新建的流程就永远
            # 拖不进第一条指令（维护者报障「新建流程无法拖放指令到画布」）。
            self._drag_target = None
            if not self.model().canDropMimeData(
                mime, Qt.DropAction.MoveAction, -1, 0, QModelIndex()
            ):
                self.viewport().update()
                event.ignore()
                return
            self._drag_target = {
                "mode": "root_end",
                "row": -1,
                "parent": QModelIndex(),
                "indicator_y": pos.y(),
            }
            event.acceptProposedAction()
            self.viewport().update()
            return

        rect = self.visualRect(index)
        y_ratio = (pos.y() - rect.top()) / rect.height() if rect.height() > 0 else 0.5

        # end-bracket 虚拟行的命中路由：
        #   上半 → 插到其 parent 容器的 children 末尾（row=-1, parent=容器）
        #   下半 → 插到其 parent 容器的同级下方（row=容器.row()+1, parent=容器.parent()）
        # 用 50/50 划分，因为 end-bracket 是明确的两段语义边界。
        target_type = index.data(ROLE_NODE_TYPE)
        if target_type == _END_BRACKET_TYPE:
            container_index = index.parent()  # end-bracket 的 parent 是真实容器
            if not container_index.isValid():
                self._drag_target = None
                self.viewport().update()
                event.ignore()
                return
            if y_ratio < 0.5:
                mode = "end_above"
                row = -1
                parent = container_index
                indicator_y = rect.top()
            else:
                mode = "end_below"
                row = container_index.row() + 1
                parent = container_index.parent()
                indicator_y = rect.bottom()
        elif target_type == _ELSE_BRANCH_TYPE:
            # 「否则」指令行：同样是 50/50——
            #   上半 → then 分支末尾（否则行之前）
            #   下半 → else 分支开头（否则行之后）
            container_index = index.parent()  # 否则行的 parent 是 if 容器
            if not container_index.isValid():
                self._drag_target = None
                self.viewport().update()
                event.ignore()
                return
            parent = container_index
            if y_ratio < 0.5:
                mode = "else_above"
                row = index.row()
                indicator_y = rect.top()
            else:
                mode = "else_below"
                row = index.row() + 1
                indicator_y = rect.bottom()
        elif y_ratio < self._ABOVE_THRESHOLD:
            mode = "above"
            # 插到 index 的同级上方 → row=index.row(), parent=index.parent()
            row = index.row()
            parent = index.parent()
            indicator_y = rect.top()
        elif y_ratio > self._BELOW_THRESHOLD:
            mode = "below"
            # 插到 index 的同级下方 → row=index.row()+1, parent=index.parent()
            row = index.row() + 1
            parent = index.parent()
            indicator_y = rect.bottom()
        else:
            mode = "on"
            # 插入到 index 容器内部末尾 → row=-1, parent=index
            row = -1
            parent = index
            indicator_y = rect.bottom()  # 容器内部指示条画在底行下方

        # 指令树拖入且悬停在指令卡片中部时：指令不能落入另一个指令内部，
        # 中部落点按「其后插入」处理（容器保持 on=进入容器末尾）。
        if (
            mime.hasFormat(_MIME_COMMAND)
            and mode == "on"
            and index.data(ROLE_NODE_TYPE) == "action"
        ):
            mode = "below"
            row = index.row() + 1
            parent = index.parent()
            indicator_y = rect.bottom()

        # 传给 model.canDropMimeData 做合法性校验（成环 / 容器类型）
        action = Qt.DropAction.MoveAction
        if not self.model().canDropMimeData(mime, action, row, 0, parent):
            self._drag_target = None
            self.viewport().update()
            event.ignore()
            return

        self._drag_target = {
            "mode": mode,
            "row": row,
            "parent": parent,
            "indicator_y": indicator_y,
        }
        event.acceptProposedAction()
        self.viewport().update()  # 触发 paintEvent 重绘落点指示条

    def dragLeaveEvent(self, event) -> None:
        self._drag_target = None
        self.viewport().update()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        """用 dragMoveEvent 判定的落点直接调 dropMimeData。

        同时接受两种 MIME：
        - application/x-rpa-flow-node（画布内部移动，row 来自 dragMoveEvent 计算）
        - application/x-rpa-flow-command（指令树拖入新建，row=-1 / row=Qt 探测值）

        结构变更期间屏蔽选中模型信号：Qt 的 takeRow/insertRow 会在信号发射中途触发
        currentChanged，若此时监听方（参数面板提交/重建）回头改模型，会破坏 Qt 内部
        状态（实测出现裸空行/None 子项直至崩溃）。变更完成后再恢复并刷新一次选中态。

        注意：``blockSignals(True)`` 返回的是**之前**的阻塞状态，不能用它决定是否
        解除——否则第一次拖放后信号就被永久阻塞（之后点击收敛选中集时模型已收敛、
        视图却收不到 selectionChanged，被取消选中的行不重绘，观感是「点击无效，
        鼠标移到该行上才取消选中」）。这里无条件成对恢复。
        """
        selection = self.selectionModel()
        if selection is not None:
            selection.blockSignals(True)
        moved_ids: list[str] = []
        expanded_ids: list[str] = []
        try:
            mime = event.mimeData()
            model = self.model()
            if mime.hasFormat(_MIME_COMMAND):
                # 指令树拖入：用 dragMoveEvent 算好的落点（指示条位置）；
                # 无落点（直接 drop 的兜底路径）时 row=-1 追加到目标容器末尾
                row = self._drag_target["row"] if self._drag_target else -1
                parent = (
                    self._drag_target["parent"] if self._drag_target else QModelIndex()
                )
                self._drag_target = None
                if model.dropMimeData(mime, event.proposedAction(), row, 0, parent):
                    event.acceptProposedAction()
                else:
                    event.ignore()
                return
            # 画布内部移动（需 dragMoveEvent 先算好 row）
            if not mime.hasFormat(_MIME_TYPE) or self._drag_target is None:
                event.ignore()
                return
            target = self._drag_target
            self._drag_target = None
            action = Qt.DropAction.MoveAction
            moved_ids = [
                node_id
                for node_id in bytes(mime.data(_MIME_TYPE)).decode("utf-8").split(";")
                if node_id
            ]
            # 记录被拖容器的展开态：摘除/重新插入会丢展开状态（观感上「原来展开的
            # 变成折叠了」），移动后按 id 恢复。
            for node_id in moved_ids:
                item = model.find_by_id(node_id)
                if item is not None and self.isExpanded(item.index()):
                    expanded_ids.append(node_id)
            if model.dropMimeData(mime, action, target["row"], 0, target["parent"]):
                event.acceptProposedAction()
            else:
                event.ignore()
                moved_ids = []
                expanded_ids = []
        finally:
            if selection is not None:
                selection.blockSignals(False)
            self.viewport().update()
        # 移动成功后按新索引重新选中被移动节点并滚动到可见：拖放会摘除/插入行，
        # 旧选中索引随之失效，不重选会出现「移动过的卡片点不中/看不到」的观感。
        from rpa_core.gui import debug_log

        if debug_log.ENABLED:
            debug_log.log(
                "drop",
                accepted=event.isAccepted(),
                moved=moved_ids,
                expanded=expanded_ids,
                state=str(self.state()),
            )
        if moved_ids:
            self._reselect_nodes(moved_ids, expanded_ids)

    def _reselect_nodes(
        self, node_ids: list[str], expanded_ids: list[str] | None = None
    ) -> None:
        """按节点 id 在新树上重新选中（首个作为当前项并滚动居中），并恢复展开态。"""
        from PySide6.QtCore import QItemSelectionModel

        from rpa_core.gui import debug_log

        model = self.model()
        selection = self.selectionModel()
        if model is None or selection is None:
            return
        items = [
            item
            for item in (model.find_by_id(node_id) for node_id in node_ids)
            if item is not None
        ]
        if not items:
            return
        # 恢复被移动容器的展开态（须在有效索引上操作）
        for node_id in expanded_ids or []:
            item = model.find_by_id(node_id)
            if item is not None:
                self.setExpanded(item.index(), True)
        selection.clearSelection()
        for item in items:
            selection.select(
                item.index(),
                QItemSelectionModel.SelectionFlag.Select
                | QItemSelectionModel.SelectionFlag.Rows,
            )
        first = items[0].index()
        # 展开祖先，保证被移动节点可见
        parent = first.parent()
        while parent.isValid():
            self.setExpanded(parent, True)
            parent = parent.parent()
        selection.setCurrentIndex(first, QItemSelectionModel.SelectionFlag.NoUpdate)
        self.scrollTo(first, QAbstractItemView.ScrollHint.PositionAtCenter)
        # scrollTo 会移动布局；紧接着的点击若按旧视觉位置命中会落空（观感：
        # 「点了不选中，移开才选中」）。强制布局收敛 + 重绘，让点击命中一致。
        self.updateGeometry()
        self.viewport().update()
        if debug_log.ENABLED:
            debug_log.log(
                "reselect",
                ids=node_ids,
                selected=[i.data(ROLE_NODE_ID) for i in selection.selectedIndexes()],
                current=selection.currentIndex().data(ROLE_NODE_ID),
            )

    def mousePressEvent(self, event) -> None:
        """（诊断）记录按下时的命中/状态，再走基类选中逻辑。"""
        from rpa_core.gui import debug_log

        if event.button() == Qt.MouseButton.LeftButton:
            self._press_dragged = False
        if debug_log.ENABLED and event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            index = self.indexAt(pos)
            selection = self.selectionModel()
            debug_log.log(
                "press",
                pos=(pos.x(), pos.y()),
                node=index.data(ROLE_NODE_ID) if index.isValid() else None,
                valid=index.isValid(),
                state=str(self.state()),
                current=self.currentIndex().data(ROLE_NODE_ID),
                selected=len(selection.selectedIndexes()) if selection else -1,
            )
        super().mousePressEvent(event)
        if debug_log.ENABLED and event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            index = self.indexAt(pos)
            selection = self.selectionModel()
            debug_log.log(
                "press-after",
                node=index.data(ROLE_NODE_ID) if index.isValid() else None,
                current=self.currentIndex().data(ROLE_NODE_ID),
                selected=len(selection.selectedIndexes()) if selection else -1,
                is_selected=(
                    selection.isSelected(index)
                    if (selection and index.isValid())
                    else None
                ),
            )

    def mouseReleaseEvent(self, event) -> None:
        """编号栏与卡片尾按钮的点击分发。

        - 编号栏（左 _GUTTER_WIDTH px）：容器行的收起/展开按钮 → 切换展开态；
          栏内其余位置不冒泡（避免误触行选择/行点击折叠）；
        - 卡片尾「删除」文字按钮 → 删除该节点（热区与绘制矩形同源）。

        可删性判定委托模型（remove_row/remove_item 单源）：真实节点可删，
        虚拟分组/结束行被拒；「否则」指令行虽 virtual=True 但模型对其
        例外放行（删除=取消 else 分支），视图不再重复一份更严的策略。
        """
        from rpa_core.gui import debug_log

        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            # 视图缩进为 0（缩进由 delegate 自绘），行视觉矩形从 x=0 起满宽，
            # 编号栏内 indexAt 可直接命中所在行。
            index = self.indexAt(pos)
            if debug_log.ENABLED:
                selection = self.selectionModel()
                debug_log.log(
                    "release",
                    pos=(pos.x(), pos.y()),
                    node=index.data(ROLE_NODE_ID) if index.isValid() else None,
                    gutter=pos.x() <= _GUTTER_WIDTH,
                    current=self.currentIndex().data(ROLE_NODE_ID),
                    selected=len(selection.selectedIndexes()) if selection else -1,
                    is_selected=(
                        selection.isSelected(index)
                        if (selection and index.isValid())
                        else None
                    ),
                )
            if pos.x() <= _GUTTER_WIDTH:
                if index.isValid() and breakpoint_button_rect(
                    self.visualRect(index)
                ).contains(pos):
                    # 断点列（最左，影刀式）：点一下切换该节点断点
                    self.breakpoint_toggled.emit(index)
                    event.accept()
                    return
                if (
                    index.isValid()
                    and self.model().hasChildren(index)
                    and collapse_button_rect(self.visualRect(index)).contains(pos)
                ):
                    self.setExpanded(index, not self.isExpanded(index))
                event.accept()
                return
            if index.isValid():
                rect = self.visualRect(index)
                if delete_button_rect(rect).contains(pos):
                    if self.model().remove_row(index):
                        event.accept()
                        self.viewport().update()
                        return
        super().mouseReleaseEvent(event)
        # 兜底：Qt 在拖拽结束后偶发不应用「普通单击已选项 → 收敛选中集」（实测拖完后
        # 第一次单击停在多选，收敛滞后）。这里在「普通左键单击 + 未拖拽 + 点在已选中的
        # 可选项上 + 当前为多选」时强制收敛到该行。
        self._collapse_multi_selection_on_click(event)

    def _collapse_multi_selection_on_click(self, event) -> None:
        """普通左键单击已选项时的选中集收敛兜底（见 mouseReleaseEvent 注释）。"""
        from rpa_core.gui import debug_log

        if event.button() != Qt.MouseButton.LeftButton or event.modifiers():
            return
        if self._press_dragged:
            return
        index = self.indexAt(event.position().toPoint())
        if not index.isValid():
            return
        if not (self.model().flags(index) & Qt.ItemFlag.ItemIsSelectable):
            return
        selection = self.selectionModel()
        if selection is None or not selection.isSelected(index):
            return
        row_indexes = [i for i in selection.selectedIndexes() if i.column() == 0]
        if len(row_indexes) <= 1:
            return
        selection.clearSelection()
        selection.select(
            index,
            QItemSelectionModel.SelectionFlag.Select
            | QItemSelectionModel.SelectionFlag.Rows,
        )
        selection.setCurrentIndex(
            index,
            QItemSelectionModel.SelectionFlag.ClearAndSelect
            | QItemSelectionModel.SelectionFlag.Rows,
        )
        self.viewport().update()
        if debug_log.ENABLED:
            debug_log.log("release-collapse", node=index.data(ROLE_NODE_ID))

    # ---- 落点指示条自绘 --------------------------------------------------
    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        target = self._drag_target
        if target is None:
            return
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        rect = self.viewport().rect()
        y = target["indicator_y"]
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#0969da"))
        painter.drawRect(rect.left() + 8, y - 2, rect.width() - 16, 4)
        # 指示条两侧加小圆点，让 Above/Below/OnItem 更易辨认
        painter.setBrush(QColor("#0969da"))
        painter.drawEllipse(rect.left() + 8 - 3, y - 3, 6, 6)
        painter.drawEllipse(rect.right() - 8 - 3, y - 3, 6, 6)
        painter.end()


class CardDelegate(QStyledItemDelegate):
    """自绘流程节点卡片 + 左侧编号栏（行号/错误徽标/收起展开按钮）。

    catalog 可选：提供后 action 行按 manifest input_schema.required 检查
    必填参数缺失，缺失即在编号栏画红色错误徽标（影刀同款）。
    """

    def __init__(self, parent: QWidget | None = None, catalog=None) -> None:
        super().__init__(parent)
        self._catalog = catalog

    def sizeHint(self, option, index) -> QSize:
        return QSize(option.rect.width(), _ROW_HEIGHT)

    def paint(self, painter: QPainter, option, index: QModelIndex) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # 视图缩进设为 0（setIndentation(0)），Qt 的 branch 引导线整个不再绘制——
        # 否则引导线落在编号栏区域与行号重叠。缩进由 delegate 自己算：
        # 内容区 = 编号栏宽 + 深度×缩进，整体右移；编号栏固定在视口左缘。
        content = QStyleOptionViewItem(option)
        depth = _index_depth(index)
        content.rect.adjust(_GUTTER_WIDTH + depth * _INDENT, 0, 0, 0)

        node_type = index.data(ROLE_NODE_TYPE)
        if node_type == _END_BRACKET_TYPE:
            self._paint_end_bracket(painter, content, index)
        elif node_type == _ELSE_BRANCH_TYPE:
            # else 分支 marker 视觉上要与其父 if 容器对齐（缩进减 1），
            # 用 _marker_rect 补偿后按白色卡片渲染（可拖可删）。
            aligned_rect, _ = self._marker_rect(content, index)
            self._paint_card(painter, content, index, rect_override=aligned_rect,
                             is_virtual_group=False, suppress_summary=True)
        elif index.data(ROLE_IS_VIRTUAL):
            # 虚拟分组（则执行/否则执行/异常处理）也渲染为白色卡片，
            # 与普通指令卡片完全一致，只是不加拖柄和序号（分组是结构行不可拖）。
            self._paint_card(painter, content, index, is_virtual_group=True)
        else:
            self._paint_card(painter, content, index)

        self._paint_gutter(painter, option, index)
        painter.restore()

    # ---- 编号栏：全局行号 + 错误徽标 + 收起/展开按钮 ----------------------
    def _paint_gutter(self, painter: QPainter, option, index: QModelIndex) -> None:
        rect = option.rect

        # 全局行号（逻辑行号：全树前序位置，折叠只是隐藏行、序号不因此改变，
        # 与影刀左侧编号栏一致；结束行/否则行同样编号）。
        # 最近一次的运行状态直接给行号着色：running 蓝 / succeeded 绿 / failed 红。
        run_state = index.data(ROLE_RUN_STATE)
        number_color = {
            "running": "#0969da",
            "succeeded": "#1a7f37",
            "failed": "#cf222e",
        }.get(run_state, "#8c959f")
        painter.setPen(QPen(QColor(number_color)))
        painter.setFont(option.font)
        number_rect = QRect(_GUTTER_NUMBER_X0, rect.top(), _GUTTER_NUMBER_W, rect.height())
        painter.drawText(
            number_rect,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            str(self._row_number(index)),
        )

        # 断点红点（M24，影刀式：编号栏最左列）
        if index.data(ROLE_BREAKPOINT):
            center_y = rect.center().y()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#cf222e"))
            painter.drawEllipse(
                _GUTTER_BREAKPOINT_X - _BREAKPOINT_RADIUS,
                center_y - _BREAKPOINT_RADIUS,
                _BREAKPOINT_RADIUS * 2,
                _BREAKPOINT_RADIUS * 2,
            )

        # 错误徽标：红底白 !（action 必填参数缺失时）
        if self._has_config_error(index):
            radius = 5
            center_y = rect.center().y()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#cf222e"))
            painter.drawEllipse(
                _GUTTER_ERROR_X - radius, center_y - radius, radius * 2, radius * 2
            )
            font = QFont(option.font)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QPen(QColor("#ffffff")))
            painter.drawText(
                QRect(_GUTTER_ERROR_X - radius, center_y - radius,
                      radius * 2, radius * 2),
                Qt.AlignmentFlag.AlignCenter,
                "!",
            )

        # 容器行：收起/展开按钮（− 已展开 / + 已折叠）
        if index.model().hasChildren(index):
            btn = collapse_button_rect(rect)
            painter.setPen(QPen(_BORDER, 1))
            painter.setBrush(QColor("#ffffff"))
            painter.drawRoundedRect(btn, 3, 3)
            painter.setPen(QPen(QColor("#57606a")))
            painter.setFont(option.font)
            view = option.widget
            expanded = bool(view and view.isExpanded(index))
            painter.drawText(
                btn, Qt.AlignmentFlag.AlignCenter, "−" if expanded else "+"
            )

    @staticmethod
    def _row_number(index: QModelIndex) -> int:
        """逻辑行号（1 起）：全树前序遍历中的位置。

        折叠只影响可见性、不影响编号——序号跟随指令本身（影刀同款：
        收起 if/循环后，下方指令的行号不变）。画布规模下 O(n) 遍历足够。
        """
        model = index.model()
        # 栈式前序遍历：(父 index, 该父内的行号) 逐层展开，命中目标即停
        count = 0
        stack: list[tuple[QModelIndex, int]] = [(QModelIndex(), 0)]
        while stack:
            parent, row = stack.pop()
            if row >= model.rowCount(parent):
                continue
            stack.append((parent, row + 1))  # 兄弟续位
            current = model.index(row, 0, parent)
            count += 1
            if current == index:
                return count
            stack.append((current, 0))  # 先序：子行先于兄弟
        return count or 1

    def _has_config_error(self, index: QModelIndex) -> bool:
        """action 行的必填参数缺失（未填或空值）即为配置错误。"""
        if self._catalog is None or index.data(ROLE_NODE_TYPE) != "action":
            return False
        command_id = index.data(ROLE_COMMAND_ID)
        if not command_id or command_id not in self._catalog:
            return False
        required = self._catalog[command_id].input_schema.get("required", [])
        if not required:
            return False
        holder = index.data(ROLE_ARGS_RAW)
        args = holder.args if holder is not None else {}
        return any(args.get(key) in (None, "", [], {}) for key in required)

    # ---- 结束行 / 否则行通用的"与父容器对齐"缩进补偿 ----------------------
    @staticmethod
    def _marker_rect(option, index: QModelIndex) -> tuple[QRect, int]:
        """返回（对齐到父容器后的 rect, 父容器深度）。

        这两类行都是容器的 child（depth 比容器深 1），视觉上要与容器卡片对齐。
        """
        parent_depth = _index_depth(index.parent()) if index.parent().isValid() else 0
        indent_offset = _index_depth(index) - parent_depth  # 应等于 1
        return option.rect.adjusted(-indent_offset * _INDENT, 0, 0, 0), parent_depth

    # ---- 结束标记行：虚线连接 + 灰色文字 + 缩进减 1 ----------------------
    def _paint_end_bracket(self, painter: QPainter, option, index: QModelIndex) -> None:
        """结束标记行渲染：与父容器对齐的灰色边界。

        end-bracket 是容器的虚拟最后一个 child，视觉上需要和父容器对齐
        （缩进减 1），只保留虚线连接 + 浅灰色斜体文字，不加左侧色条。
        """
        adjusted_rect, _ = self._marker_rect(option, index)

        # 虚线连接：从行左侧延伸到文字前
        dash_pen = QPen(QColor("#d0d7de"))
        dash_pen.setStyle(Qt.PenStyle.DashLine)
        dash_pen.setWidth(1)
        painter.setPen(dash_pen)
        painter.drawLine(
            adjusted_rect.left() + 4, adjusted_rect.center().y(),
            adjusted_rect.left() + 30, adjusted_rect.center().y(),
        )

        # 灰色斜体文字
        painter.setPen(QPen(QColor("#8c959f")))
        font = QFont(option.font)
        font.setPointSizeF(max(7.0, option.font.pointSizeF() - 0.5))
        font.setItalic(True)
        painter.setFont(font)
        text_rect = QRect(adjusted_rect.left() + 34, adjusted_rect.top(),
                          adjusted_rect.width() - 34, adjusted_rect.height())
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            f"└─ {index.data(Qt.ItemDataRole.DisplayRole)}",
        )

    # ---- 虚拟分组条：浅底 + 左侧小色条 + 灰色标签 ------------------------
    def _paint_group(self, painter: QPainter, option, index: QModelIndex) -> None:
        rect = option.rect.adjusted(8, 3, -8, -3)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(_GROUP_BG))
        painter.drawRoundedRect(rect, 4, 4)
        depth = _index_depth(index)
        painter.setBrush(QColor(_DEPTH_COLORS[depth % len(_DEPTH_COLORS)]))
        painter.drawRoundedRect(QRect(rect.left() + 2, rect.top() + 2, 3, rect.height() - 4), 1, 1)
        painter.setPen(QPen(QColor("#57606a")))
        font = QFont(option.font)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(rect.adjusted(12, 0, -8, 0),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         index.data(Qt.ItemDataRole.DisplayRole))

    # ---- 「否则」指令行：与父 if 卡片对齐的浅色指令条 ---------------------
    def _paint_else_branch(self, painter: QPainter, option, index: QModelIndex) -> None:
        """if 的分支分割指令（影刀式「否则」）。

        它是一条**按需添加**的独立指令：可选中、可拖、可删，所以视觉上给拖柄与
        选中态，而不是像结束行那样做成纯装饰虚线。对齐方式与「结束 如果」一致
        （缩进减 1、用父容器深度色），使 then/else 两段子节点缩进相同，整体读起来
        正是 if → 否则 → 结束如果。
        """
        rect, parent_depth = self._marker_rect(option, index)
        rect = rect.adjusted(0, 3, -8, -3)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)

        painter.setPen(QPen(_BORDER_HOVER) if hovered else QPen(Qt.PenStyle.NoPen))
        painter.setBrush(QColor(_CARD_SELECTED if selected else _GROUP_BG))
        painter.drawRoundedRect(rect, 4, 4)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(_DEPTH_COLORS[parent_depth % len(_DEPTH_COLORS)]))
        painter.drawRoundedRect(QRect(rect.left() + 2, rect.top() + 2, 3, rect.height() - 4), 1, 1)

        # 拖柄：与卡片同一位置，提示这条指令可以被拖
        painter.setPen(QPen(QColor(_GRIP)))
        painter.setFont(option.font)
        painter.drawText(QRect(rect.left() + 12, rect.top(), 16, rect.height()),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, "≡")

        painter.setPen(QPen(QColor("#57606a")))
        font = QFont(option.font)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(rect.adjusted(32, 0, -8, 0),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         index.data(Qt.ItemDataRole.DisplayRole))

    # ---- 节点卡片 --------------------------------------------------------
    def _paint_card(self, painter: QPainter, option, index: QModelIndex,
                    is_virtual_group: bool = False,
                    suppress_summary: bool = False,
                    rect_override: QRect | None = None) -> None:
        """绘制指令卡片。

        is_virtual_group=True 时为虚拟分组（则执行/否则执行/异常处理）渲染：
        白底圆角卡片完全一致，**省略拖柄**（不可拖）、**省略序号**（非指令行）、
        **标题加粗并改用类型徽标**。视觉上与普通卡片保持完全统一。
        suppress_summary=True 时不渲染参数摘要（else 分支 marker 只有标题）。
        rect_override 可替换 option.rect（用于 else-branch 的"与父容器对齐"补偿）。
        """
        effective_rect = rect_override if rect_override is not None else option.rect
        rect = effective_rect.adjusted(8, 3, -8, -3)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)

        # hover 阴影（先垫一层偏移的圆角矩形近似投影）
        if hovered and not selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_SHADOW)
            painter.drawRoundedRect(rect.translated(0, 1), _CARD_RADIUS, _CARD_RADIUS)

        # 卡片底
        painter.setPen(QPen(_BORDER_HOVER if hovered else _BORDER))
        painter.setBrush(QColor(_CARD_SELECTED if selected else _CARD))
        painter.drawRoundedRect(rect, _CARD_RADIUS, _CARD_RADIUS)

        # 左侧深度色线（仅 4px）
        depth = _index_depth(index)
        bar = QRect(rect.left() + 2, rect.top() + 2, _BAR_WIDTH, rect.height() - 4)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(_DEPTH_COLORS[depth % len(_DEPTH_COLORS)]))
        painter.drawRoundedRect(bar, 2, 2)

        content = rect.adjusted(14, 0, -10, 0)
        x = content.left()

        # 拖柄（虚拟分组不画；叶子 return 不画——它的语义不是"可拖拽的容器"）
        node_type = index.data(ROLE_NODE_TYPE)
        is_return = node_type == "return"
        if not is_virtual_group and not is_return:
            painter.setPen(QPen(QColor(_GRIP)))
            grip_font = QFont(option.font)
            painter.setFont(grip_font)
            painter.drawText(QRect(x, content.top(), 16, content.height()),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, "≡")
            x += 18

        # 同级序号已上移到左侧编号栏（全局行号），卡片内不再逐层编号

        # 内容右边界给删除按钮热区预留（按钮宽 + 右边距 + 与正文的间距）
        content_right = content.right() - (_DELETE_BTN_W + _DELETE_BTN_MARGIN_RIGHT + 4)

        # 命令名（粗体）
        title_font = QFont(option.font)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(QPen(QColor(_CMD_TEXT)))
        title_rect = QRect(x, content.top(), content_right - x, content.height())
        painter.drawText(title_rect,
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         option.fontMetrics.elidedText(
                             index.data(Qt.ItemDataRole.DisplayRole),
                             Qt.TextElideMode.ElideRight, title_rect.width()))
        name_w = painter.fontMetrics().horizontalAdvance(
            index.data(Qt.ItemDataRole.DisplayRole))
        name_w = min(name_w + 4, title_rect.width())

        # 参数摘要（等宽、灰色，跟在命令名后，剩余空间省略）
        if not suppress_summary:
            summary = index.data(ROLE_ARGS_SUMMARY) or ""
            if summary:
                mono = QFont("Consolas")
                mono.setPointSizeF(max(7.5, option.font.pointSizeF() - 1.5))
                painter.setFont(mono)
                painter.setPen(QPen(QColor(_ARGS_TEXT)))
                summary_x = x + min(name_w, title_rect.width() - 60)
                summary_rect = QRect(summary_x, content.top(),
                                     content_right - summary_x, content.height())
                painter.drawText(summary_rect,
                                 Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                                 painter.fontMetrics().elidedText(
                                     summary, Qt.TextElideMode.ElideRight, summary_rect.width()))

        # 卡片尾删除按钮：hover/selected 时显示文字按钮（「删除」红字浅底胶囊），
        # 只有真实节点可删；按钮矩形与 FlowTreeView.mouseReleaseEvent 热区同源
        if (hovered or selected) and not is_virtual_group:
            self._paint_delete_button(painter, delete_button_rect(rect), option)

    @staticmethod
    def _paint_delete_button(painter: QPainter, btn: QRect, option) -> None:
        """在按钮区域内绘制文字删除按钮：浅灰圆角胶囊 + 红色「删除」文字。"""
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#eaeef2"))
        painter.drawRoundedRect(btn, btn.height() // 2, btn.height() // 2)
        font = QFont(option.font)
        painter.setFont(font)
        painter.setPen(QPen(QColor("#cf222e")))
        painter.drawText(btn, Qt.AlignmentFlag.AlignCenter, "删除")


def build_canvas(
    model: FlowTreeModel, parent: QWidget | None = None, catalog=None
) -> FlowTreeView:
    """组装卡片画布：FlowTreeView（跳过 Qt 二次 clearOrRemove）+ CardDelegate。

    catalog 可选：传入后编号栏按 manifest 必填参数画错误徽标。
    """
    tree = FlowTreeView(parent)
    tree.setModel(model)
    tree.setItemDelegate(CardDelegate(tree, catalog))
    tree.setHeaderHidden(True)
    tree.setRootIsDecorated(False)  # 展开箭头自绘/编号栏按钮处理，保持卡片整洁
    # 缩进归 0：Qt 的 branch 引导线会画进左侧编号栏与行号重叠，
    # 改由 CardDelegate 自算缩进（编号栏宽 + 深度×_INDENT），branch 区整体消失。
    tree.setIndentation(0)
    tree.setExpandsOnDoubleClick(False)
    tree.setAnimated(False)  # widgets 无内建过渡，避免半开动画卡顿
    tree.setDragDropMode(QTreeView.DragDropMode.DragDrop)  # 同时接受外部拖入 + 内部移动
    tree.setDefaultDropAction(Qt.DropAction.MoveAction)
    tree.setSelectionBehavior(QTreeView.SelectionBehavior.SelectRows)
    # 多选（Ctrl/Shift 点选）：批量移动与批量删除的前提
    tree.setSelectionMode(QTreeView.SelectionMode.ExtendedSelection)
    tree.setMouseTracking(True)
    tree.setStyleSheet(
        "QTreeView { background:#f6f8fa; border:none; }"
        "QTreeView::item { background:transparent; }"
        "QTreeView::branch { background:transparent; }"
    )
    tree.expandAll()

    # 展开/收起只由编号栏的 −/+ 小方钮承担（mouseReleaseEvent 的 collapse_button_rect
    # 热区）；点击卡片本体不再切换折叠——避免「点一下卡片想选中，结果被折叠」的干扰。
    return tree
