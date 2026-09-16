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

from PySide6.QtCore import QModelIndex, QRect, QSize, Qt
from PySide6.QtGui import QColor, QDrag, QFont, QPainter, QPen
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QTreeView, QWidget

from rpa_core.gui.flow_model import (
    _ELSE_BRANCH_TYPE,
    _END_BRACKET_TYPE,
    _MIME_COMMAND,
    _MIME_TYPE,
    ROLE_ARGS_SUMMARY,
    ROLE_IS_VIRTUAL,
    ROLE_NODE_TYPE,
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

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # 当前拖拽落点状态，由 dragMoveEvent 写入、paintEvent/dropEvent 读取
        # None 表示没有正在进行的拖拽（dragEnter 未到达或已 leave）
        self._drag_target: dict | None = None

    # ---- Qt 拖拽三件套 ----------------------------------------------------
    def startDrag(self, supportedActions: Qt.DropAction) -> None:
        indices = self.selectionModel().selectedIndexes()
        if not indices:
            return
        mime_data = self.model().mimeData(indices)
        if mime_data is None:
            return
        drag = QDrag(self)
        drag.setMimeData(mime_data)
        # 仅 setMimeData + exec，不调基类 startDrag（它会在 exec 返回
        # MoveAction 后调 clearOrRemove 二次删源行）。
        drag.exec(supportedActions)

    def dragMoveEvent(self, event) -> None:
        """自判落点区域并驱动视觉指示条刷新。"""
        mime = event.mimeData()
        if not mime.hasFormat("application/x-rpa-flow-node"):
            event.ignore()
            return

        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        index = self.indexAt(pos)
        if not index.isValid():
            self._drag_target = None
            self.viewport().update()
            event.ignore()
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
        """
        mime = event.mimeData()
        model = self.model()
        if mime.hasFormat(_MIME_COMMAND):
            # 指令树拖入：直接用 row=-1 表示追加到目标容器末尾
            parent = self._drag_target["parent"] if self._drag_target else QModelIndex()
            self._drag_target = None
            if model.dropMimeData(mime, event.proposedAction(), -1, 0, parent):
                event.acceptProposedAction()
            else:
                event.ignore()
            self.viewport().update()
            return
        # 画布内部移动（需 dragMoveEvent 先算好 row）
        if not mime.hasFormat(_MIME_TYPE) or self._drag_target is None:
            event.ignore()
            return
        target = self._drag_target
        self._drag_target = None
        action = Qt.DropAction.MoveAction
        if model.dropMimeData(mime, action, target["row"], 0, target["parent"]):
            event.acceptProposedAction()
        else:
            event.ignore()
        self.viewport().update()

    def mouseReleaseEvent(self, event) -> None:
        """点击卡片右尾 × → 删除该节点。"""
        if event.button() == Qt.MouseButton.LeftButton:
            index = self.indexAt(event.position().toPoint())
            if index.isValid():
                rect = self.visualRect(index)
                # delete 热区：按钮在卡片右尾 22-6px 区域（16×16 按钮）
                x = event.position().x()
                if x >= rect.right() - 22 and x <= rect.right() - 6:
                    if not index.data(ROLE_IS_VIRTUAL):
                        model = self.model()
                        model.remove_row(index)
                        event.accept()
                        self.viewport().update()
                        return
        super().mouseReleaseEvent(event)

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
    """自绘流程节点卡片。"""

    def sizeHint(self, option, index) -> QSize:
        return QSize(option.rect.width(), _ROW_HEIGHT)

    def paint(self, painter: QPainter, option, index: QModelIndex) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        node_type = index.data(ROLE_NODE_TYPE)
        if node_type == _END_BRACKET_TYPE:
            self._paint_end_bracket(painter, option, index)
        elif node_type == _ELSE_BRANCH_TYPE:
            # else 分支 marker 视觉上要与其父 if 容器对齐（缩进减 1），
            # 用 _marker_rect 补偿后按白色卡片渲染（可拖可删）。
            aligned_rect, _ = self._marker_rect(option, index)
            self._paint_card(painter, option, index, rect_override=aligned_rect,
                             is_virtual_group=False, suppress_summary=True)
        elif index.data(ROLE_IS_VIRTUAL):
            # 虚拟分组（则执行/否则执行/异常处理）也渲染为白色卡片，
            # 与普通指令卡片完全一致，只是不加拖柄和序号（分组是结构行不可拖）。
            self._paint_card(painter, option, index, is_virtual_group=True)
        else:
            self._paint_card(painter, option, index)
        painter.restore()

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

        # 同级序号（虚拟分组不画）
        if not is_virtual_group:
            painter.setPen(QPen(QColor(_ARGS_TEXT)))
            painter.drawText(QRect(x, content.top(), 24, content.height()),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                             f"{index.row() + 1}.")
            x += 26

        # 内容右边界预留 32px 给删除按钮热区（22px + 10px 间距）
        content_right = content.right() - 32

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

        # 卡片尾删除按钮：hover/selected 时显示垃圾桶图标；16×16 固定尺寸，
        # 垂直居中、水平右对齐卡片尾部；只有真实节点可删
        if (hovered or selected) and not is_virtual_group:
            btn = QRect(rect.right() - 22, rect.center().y() - 8, 16, 16)
            self._paint_trash(painter, btn)

    @staticmethod
    def _paint_trash(painter: QPainter, btn: QRect) -> None:
        """在 16×16 按钮区域内绘制简洁垃圾桶图标。

        线条风格：深灰色 RoundCap 1.3px，视觉上与删除按钮的浅灰底圆角协调。
        构图：桶盖横线 + 梯形桶身（上宽下窄）+ 桶内两条短竖线。
        """
        # 按钮背景（浅灰圆角矩形）
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#eaeef2"))
        painter.drawRoundedRect(btn, 4, 4)

        # 垃圾桶图标线条
        pen = QPen(QColor("#8c959f"), 1.3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        cx = btn.center().x()
        # 桶盖横线（顶部偏上）
        painter.drawLine(cx - 4, btn.top() + 4, cx + 4, btn.top() + 4)
        # 桶身：梯形轮廓
        top_y = btn.top() + 5       # 桶口 y
        bot_y = btn.bottom() - 2    # 桶底 y
        painter.drawLine(cx - 5, top_y, cx + 5, top_y)         # 桶口
        painter.drawLine(cx - 5, top_y, cx - 3.5, bot_y)       # 左斜边
        painter.drawLine(cx + 5, top_y, cx + 3.5, bot_y)       # 右斜边
        painter.drawLine(cx - 3.5, bot_y, cx + 3.5, bot_y)     # 桶底
        # 桶内两条竖线（视觉"空桶"感）
        painter.drawLine(cx - 1.5, top_y + 1, cx - 1.5, bot_y - 1)
        painter.drawLine(cx + 1.5, top_y + 1, cx + 1.5, bot_y - 1)


def build_canvas(model: FlowTreeModel, parent: QWidget | None = None) -> FlowTreeView:
    """组装卡片画布：FlowTreeView（跳过 Qt 二次 clearOrRemove）+ CardDelegate。"""
    tree = FlowTreeView(parent)
    tree.setModel(model)
    tree.setItemDelegate(CardDelegate(tree))
    tree.setHeaderHidden(True)
    tree.setRootIsDecorated(False)  # 展开箭头自绘/点击行处理，保持卡片整洁
    tree.setIndentation(_INDENT)
    tree.setExpandsOnDoubleClick(False)
    tree.setAnimated(False)  # widgets 无内建过渡，避免半开动画卡顿
    tree.setDragDropMode(QTreeView.DragDropMode.DragDrop)  # 同时接受外部拖入 + 内部移动
    tree.setDefaultDropAction(Qt.DropAction.MoveAction)
    tree.setSelectionBehavior(QTreeView.SelectionBehavior.SelectRows)
    tree.setMouseTracking(True)
    tree.setStyleSheet(
        "QTreeView { background:#f6f8fa; border:none; }"
        "QTreeView::item { background:transparent; }"
        "QTreeView::branch { background:transparent; }"
    )
    tree.expandAll()

    def _toggle_on_click(index: QModelIndex) -> None:
        # 隐藏了默认展开箭头后，单击容器/虚拟组行即切换展开（叶子点击不折叠）
        node_type = index.data(ROLE_NODE_TYPE)
        if node_type in ("sequence", "if", "forEach", "try", "branch-catch"):
            tree.setExpanded(index, not tree.isExpanded(index))

    tree.clicked.connect(_toggle_on_click)
    return tree
