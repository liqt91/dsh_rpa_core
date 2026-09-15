"""中部流程卡片画布（GUI 切片 2）。

用 ``QTreeView`` + 自绘 :class:`CardDelegate` 复刻 Web 编辑器 ``#canvas`` 的
节点卡片观感（ADR 0014 §8.2 已验证可行）：

- 圆角白卡片浮在浅灰画布上，选中浅蓝、hover 轻阴影；
- 左侧 4px 深度彩色线（按树深度换色，对应 Web ``ol.children`` 的 border-left）；
- 行内：拖柄 + 同级序号 + 命令名（粗体）+ 等宽参数摘要 + 类型徽标；
- 虚拟分组（则执行/否则执行/异常处理）渲染为轻量分组条而非卡片；
- 拖拽重排走 Qt 原生 InternalMove，合法性由 FlowTreeModel.flags/canDropMimeData 约束。

首版仅浅色（qlight）调色板；深色随全局皮肤切换在后续切片接入。
"""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QTreeView, QWidget

from rpa_core.gui.flow_model import (
    _TYPE_BADGE,
    ROLE_ARGS_SUMMARY,
    ROLE_COMMAND_ID,
    ROLE_IS_VIRTUAL,
    ROLE_NODE_TYPE,
    FlowTreeModel,
)

# 深度色线（Web 端 depth-0..5 同谱系的饱和色）
_DEPTH_COLORS = ["#0969da", "#1a7f37", "#9a6700", "#8250df", "#bc4c00", "#0598bc"]

# action 命令命名空间 → 徽标底色
_BADGE_COLORS = {
    "browser": ("#ddf4ff", "#0969da"),
    "data": ("#dafbe1", "#1a7f37"),
    "workflow": ("#fff8c5", "#9a6700"),
    "desktop": ("#fbefff", "#8250df"),
}
_CONTAINER_BADGE = ("#ffebe9", "#cf222e")

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


def _index_depth(index: QModelIndex) -> int:
    """QModelIndex 没有 depth()：沿父链计数（根节点深度 0）。"""
    depth = 0
    parent = index.parent()
    while parent.isValid():
        depth += 1
        parent = parent.parent()
    return depth


class CardDelegate(QStyledItemDelegate):
    """自绘流程节点卡片。"""

    def sizeHint(self, option, index) -> QSize:
        return QSize(option.rect.width(), _ROW_HEIGHT)

    def paint(self, painter: QPainter, option, index: QModelIndex) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        if index.data(ROLE_IS_VIRTUAL):
            self._paint_group(painter, option, index)
        else:
            self._paint_card(painter, option, index)
        painter.restore()

    # ---- 虚拟分组条：浅底 + 左侧小色条 + 灰色标签 ------------------------
    def _paint_group(self, painter: QPainter, option, index: QModelIndex) -> None:
        rect = option.rect.adjusted(8, 3, -8, -3)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#eef1f4"))
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

    # ---- 节点卡片 --------------------------------------------------------
    def _paint_card(self, painter: QPainter, option, index: QModelIndex) -> None:
        rect = option.rect.adjusted(8, 3, -8, -3)
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

        # 拖柄（根节点 depth==0 时不画，根不可拖）
        if depth > 0:
            painter.setPen(QPen(QColor(_GRIP)))
            grip_font = QFont(option.font)
            painter.setFont(grip_font)
            painter.drawText(QRect(x, content.top(), 16, content.height()),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, "≡")
        x += 18

        # 同级序号
        painter.setPen(QPen(QColor(_ARGS_TEXT)))
        painter.drawText(QRect(x, content.top(), 24, content.height()),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         f"{index.row() + 1}.")
        x += 26

        command_id = index.data(ROLE_COMMAND_ID)
        node_type = index.data(ROLE_NODE_TYPE)
        badge_text, badge_bg, badge_fg = self._badge(node_type, command_id)

        # 右侧徽标（先测量，预留右侧空间）
        badge_font = QFont(option.font)
        badge_font.setPointSizeF(max(7.0, option.font.pointSizeF() - 1.5))
        painter.setFont(badge_font)
        badge_w = painter.fontMetrics().horizontalAdvance(badge_text) + 14
        badge_rect = QRect(content.right() - badge_w,
                           content.top() + (content.height() - 20) // 2,
                           badge_w, 20)

        # 命令名（粗体）
        title_font = QFont(option.font)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(QPen(QColor(_CMD_TEXT)))
        title_rect = QRect(x, content.top(),
                           content.right() - badge_w - 8 - x, content.height())
        painter.drawText(title_rect,
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         option.fontMetrics.elidedText(
                             index.data(Qt.ItemDataRole.DisplayRole),
                             Qt.TextElideMode.ElideRight, title_rect.width()))
        name_w = painter.fontMetrics().horizontalAdvance(
            index.data(Qt.ItemDataRole.DisplayRole))
        name_w = min(name_w + 4, title_rect.width())

        # 参数摘要（等宽、灰色，跟在命令名后，剩余空间省略）
        summary = index.data(ROLE_ARGS_SUMMARY) or ""
        if summary:
            mono = QFont("Consolas")
            mono.setPointSizeF(max(7.5, option.font.pointSizeF() - 1.5))
            painter.setFont(mono)
            painter.setPen(QPen(QColor(_ARGS_TEXT)))
            summary_x = x + min(name_w, title_rect.width() - 60)
            summary_rect = QRect(summary_x, content.top(),
                                 content.right() - badge_w - 8 - summary_x, content.height())
            painter.drawText(summary_rect,
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                             painter.fontMetrics().elidedText(
                                 summary, Qt.TextElideMode.ElideRight, summary_rect.width()))

        # 徽标
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(badge_bg))
        painter.drawRoundedRect(badge_rect, 4, 4)
        painter.setPen(QPen(QColor(badge_fg)))
        painter.setFont(badge_font)
        painter.drawText(badge_rect,
                         Qt.AlignmentFlag.AlignCenter, badge_text)

    def _badge(self, node_type: str | None, command_id: str | None) -> tuple[str, str, str]:
        """返回（徽标文本, 背景色, 文字色）。"""
        if node_type == "action" and command_id:
            namespace = command_id.split(".", 1)[0]
            bg, fg = _BADGE_COLORS.get(namespace, ("#eaeef2", "#57606a"))
            return namespace, bg, fg
        if node_type in _TYPE_BADGE:
            return _TYPE_BADGE[node_type], _CONTAINER_BADGE[0], _CONTAINER_BADGE[1]
        return "node", "#eaeef2", "#57606a"


def build_canvas(model: FlowTreeModel, parent: QWidget | None = None) -> QTreeView:
    """组装卡片画布：QTreeView + CardDelegate + 内部拖拽重排。"""
    tree = QTreeView(parent)
    tree.setModel(model)
    tree.setItemDelegate(CardDelegate(tree))
    tree.setHeaderHidden(True)
    tree.setRootIsDecorated(False)  # 展开箭头自绘/点击行处理，保持卡片整洁
    tree.setIndentation(22)
    tree.setExpandsOnDoubleClick(False)
    tree.setAnimated(False)  # widgets 无内建过渡，避免半开动画卡顿
    tree.setDragDropMode(QTreeView.DragDropMode.InternalMove)
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
        if node_type in ("sequence", "if", "forEach", "try",
                         "branch-then", "branch-else", "branch-catch"):
            tree.setExpanded(index, not tree.isExpanded(index))

    tree.clicked.connect(_toggle_on_click)
    return tree
