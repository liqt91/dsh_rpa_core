"""左侧指令树的卡片化渲染（影刀式指令卡片）。

- 叶子指令渲染为圆角卡片：左侧命名空间彩色图标块（取显示名首字符）+
  中文显示名（粗体）+ 小字命令 id；hover/选中态与画布卡片同调色板；
- 分组行保持轻量文本（粗体、行高收紧），不做卡片，避免视觉噪声；
- 中文显示名复用 Web 编辑器单一事实来源 ``devserver/static/i18n.js``
  （正则解析，与 tests/contract/test_editor_i18n.py 同款），
  缺失时回退命令 id 原文，不做第二份映射表。
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

# 树 item 上携带完整命令 id 的自定义数据角色（组节点不携带）。
# 历史定义在 app.py，为让 delegate 独立可用迁到本模块；app.py 仍 re-export。
ROLE_COMMAND_ID = Qt.ItemDataRole.UserRole + 1

_I18N_PATH = Path(__file__).resolve().parent.parent / "devserver" / "static" / "i18n.js"
_COMMANDS_BLOCK = re.compile(r"^\s*commands:\s*\{(.*?)\n  \}", re.DOTALL | re.MULTILINE)
_ENTRY = re.compile(r'"([\w.]+)"\s*:\s*"([^"]+)"')


@lru_cache(maxsize=1)
def load_command_display_names() -> dict[str, str]:
    """从 i18n.js 的 commands 块读出「命令 id → 中文显示名」；失败一律回退空表。"""
    try:
        text = _I18N_PATH.read_text(encoding="utf-8")
    except OSError:
        return {}
    match = _COMMANDS_BLOCK.search(text)
    if not match:
        return {}
    return {m.group(1): m.group(2) for m in _ENTRY.finditer(match.group(1))}


# 命名空间 → 图标底色（与画布深度线同谱系；按前缀匹配，desktop.win32 归 desktop）
_NAMESPACE_COLORS = (
    ("browser", QColor("#0969da")),
    ("data", QColor("#1a7f37")),
    ("desktop", QColor("#8250df")),
    ("workflow", QColor("#9a6700")),
)
_CONTROL_COLOR = QColor("#0a7ea4")  # 流程控制（catalog 之外的内置指令）
_FALLBACK_COLOR = QColor("#6e7781")


def _namespace_color(command_id: str) -> QColor:
    for prefix, color in _NAMESPACE_COLORS:
        if command_id.startswith(prefix):
            return color
    # catalog 命令都含 "."（browser.navigate）；无 "." 的是控制指令（if / @else）
    return _CONTROL_COLOR if "." not in command_id else _FALLBACK_COLOR


# 与画布卡片同调色板（canvas.py：_CARD/_CARD_SELECTED/_BORDER）
_CARD = QColor("#ffffff")
_CARD_HOVER = QColor("#f3f6f9")
_CARD_SELECTED = QColor("#daedff")
_BORDER = QColor("#c0c4c8")
_BORDER_HOVER = QColor("#8b959e")
_BORDER_SELECTED = QColor("#0969da")
_TEXT = QColor("#19232d")
_SUB_TEXT = QColor("#64707d")


class CommandCardDelegate(QStyledItemDelegate):
    """指令树 delegate：叶子卡片 + 分组轻文本。"""

    LEAF_HEIGHT = 46
    GROUP_HEIGHT = 26
    _RADIUS = 6
    _ICON_SIDE = 22

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:
        size = super().sizeHint(option, index)
        leaf = index.data(ROLE_COMMAND_ID) is not None
        return QSize(size.width(), self.LEAF_HEIGHT if leaf else self.GROUP_HEIGHT)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        command_id = index.data(ROLE_COMMAND_ID)
        if command_id is None:
            self._paint_group(painter, option, index)
            return
        self._paint_leaf(painter, option, index, str(command_id))

    # ---- 分组行：默认渲染（保留展开箭头）+ 粗体 --------------------------
    def _paint_group(self, painter, option, index) -> None:
        opt = QStyleOptionViewItem(option)
        opt.font.setBold(True)
        super().paint(painter, opt, index)

    # ---- 叶子卡片 ---------------------------------------------------------
    def _paint_leaf(self, painter, option, index, command_id: str) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = option.rect.adjusted(2, 2, -4, -2)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        bg = _CARD_SELECTED if selected else (_CARD_HOVER if hover else _CARD)
        border = (
            _BORDER_SELECTED if selected else (_BORDER_HOVER if hover else _BORDER)
        )
        painter.setPen(QPen(border, 1))
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, self._RADIUS, self._RADIUS)

        name = str(index.data(Qt.ItemDataRole.DisplayRole) or command_id)

        # 图标块：命名空间底色 + 显示名首字符
        side = self._ICON_SIDE
        icon_rect = QRect(
            rect.left() + 8,
            rect.top() + (rect.height() - side) // 2,
            side,
            side,
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_namespace_color(command_id))
        painter.drawRoundedRect(icon_rect, 5, 5)
        glyph_font = QFont(option.font)
        glyph_font.setBold(True)
        painter.setFont(glyph_font)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(
            icon_rect, Qt.AlignmentFlag.AlignCenter, (name.strip() or command_id)[:1]
        )

        text_left = icon_rect.right() + 8
        text_width = max(rect.right() - text_left - 6, 10)
        name_font = QFont(option.font)
        name_font.setBold(True)

        if "." in command_id:
            # catalog 命令：上行中文名（粗体）+ 下行小字命令 id
            name_rect = QRect(text_left, rect.top() + 7, text_width, 17)
            painter.setFont(name_font)
            painter.setPen(_TEXT)
            painter.drawText(
                name_rect,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                painter.fontMetrics().elidedText(
                    name, Qt.TextElideMode.ElideRight, text_width
                ),
            )
            sub_font = QFont(option.font)
            if sub_font.pointSizeF() > 0:
                sub_font.setPointSizeF(sub_font.pointSizeF() - 1.5)
            id_rect = QRect(text_left, rect.top() + 24, text_width, 14)
            painter.setFont(sub_font)
            painter.setPen(_SUB_TEXT)
            painter.drawText(
                id_rect,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                painter.fontMetrics().elidedText(
                    command_id, Qt.TextElideMode.ElideRight, text_width
                ),
            )
        else:
            # 控制指令（如果/循环/否则…）：单行垂直居中
            name_rect = QRect(text_left, rect.top(), text_width, rect.height())
            painter.setFont(name_font)
            painter.setPen(_TEXT)
            painter.drawText(
                name_rect,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                painter.fontMetrics().elidedText(
                    name, Qt.TextElideMode.ElideRight, text_width
                ),
            )
        painter.restore()
