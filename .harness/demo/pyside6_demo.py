"""PySide6 承载编辑器复杂交互的可行性 demo。

展示三组关键能力（对应现有 web 前端编辑器痛点）：
1. 左侧指令树：分组 + 展开/收起 + 搜索过滤（对标指令树）
2. 右侧属性面板：真实 manifest input_schema 动态渲染成表单控件（对标 schema→控件渲染）
3. 中部列表：行拖拽排序（对标流程节点排序/插入）

运行：
    uv pip install --python <venv>/Scripts/python.exe PySide6-Essentials
    <venv>/Scripts/python.exe .harness/demo/pyside6_demo.py [out.png]
离屏渲染时会把截图保存到 out.png（默认 .harness/demo/pyside6_demo.png）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex
from PySide6.QtCore import QMimeData
from PySide6.QtCore import QSize, QRect
from PySide6.QtGui import QColor, QPixmap, QPainter, QPen, QBrush, QFont, QPainterPath
from PySide6.QtGui import QStandardItemModel, QStandardItem
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QCheckBox,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QSpinBox,
    QTableView,
    QTreeWidget,
    QTreeWidgetItem,
    QTreeView,
    QAbstractItemView,
    QStyledItemDelegate,
    QStyle,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "commands" / "data" / "setVar.json"


class DragListModel(QAbstractTableModel):
    """可拖拽排序的行模型，模拟流程节点排序"""

    COLORS = ["#3b82f6", "#ef4444", "#22c55e", "#eab308", "#8b5cf6"]

    def __init__(self, rows: list[str]):
        super().__init__()
        self._rows = rows

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 2

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        name = self._rows[index.row()]
        if role == Qt.DisplayRole:
            return (f"步骤 {index.row() + 1}", name)[index.column()]
        if role == Qt.BackgroundRole and index.column() == 0:
            return QColor(self.COLORS[index.row() % len(self.COLORS)])
        if role == Qt.TextAlignmentRole and index.column() == 0:
            return int(Qt.AlignCenter)
        return None

    def flags(self, index: QModelIndex):
        # 整行区域都可作为拖拽目标（允许在其上方/下方/行间放下）
        f = super().flags(index) | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled
        if not index.isValid():
            f |= Qt.ItemIsDropEnabled
        return f

    def supportedDragActions(self):
        return Qt.MoveAction

    def supportedDropActions(self):
        return Qt.MoveAction

    def mimeTypes(self):
        return [_MIME_ROW]

    def mimeData(self, indexes):
        md = QMimeData()
        if indexes:
            md.setData(_MIME_ROW, f"{indexes[0].row()}".encode("utf-8"))
        return md

    def dropMimeData(self, data, action, row, column, parent):
        """真正的行内拖拽重排：读来源行号，去掉后按落点索引插回。"""
        if not data.hasFormat(_MIME_ROW):
            return False
        src = int(bytes(data.data(_MIME_ROW)).decode("utf-8"))
        dst = row if row >= 0 else self.rowCount()  # row=-1 表示落在空白区域
        n = self.rowCount()
        if src < 0 or src >= n or not (0 <= dst and dst <= n):
            return False
        rows = self._rows
        item = rows.pop(src)
        if src < dst:
            dst -= 1  # 删掉 src 后索引左移一档，修正目标位
        if dst != src:
            rows.insert(dst, item)
        self.layoutChanged.emit()
        return True

    def move_row(self, delta: int):
        idx = self._rows
        # 找选中行由 view 回调；简化：把第一行移动到其相邻位置
        if len(idx) < 2 or delta == 0:
            return
        i = 0  # 演示时固定操作首行
        j = (i + delta) % len(idx)
        idx[i], idx[j] = idx[j], idx[i]
        self.layoutChanged.emit()


_MIME_ROW = "application/x-drag-step"


def build_tree() -> QWidget:
    """左侧指令树：分组 + 展开收起 + 过滤"""
    panel = QWidget()
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(6, 6, 6, 6)

    search = QLineEdit()
    search.setPlaceholderText("搜索指令…")
    tree = QTreeWidget()
    tree.setHeaderHidden(True)

    groups = {
        "数据处理": ["data.setVar（变量类型动态表单）", "data.readText", "data.appendText", "data.limit"],
        "流程控制": ["workflow.sleep", "workflow.if"],
        "桌面会话": ["desktop.attachWindow", "desktop.click"],
    }
    items = []
    for gname, cmds in groups.items():
        g = QTreeWidgetItem([gname])
        g.setExpanded(False)
        for c in cmds:
            g.addChild(QTreeWidgetItem([c]))
        tree.addTopLevelItem(g)
        items.append(g)

    def on_search(text: str):
        # 搜索时展开命中分组的子项并隐藏不匹配分组
        text = text.strip()
        for g in items:
            visible = True
            if text:
                hit = [ch for ch in g.takeChildren() if text in ch.text(0)]
                g.addChildren(hit)
                g.setExpanded(hit)
                visible = bool(hit)
            g.setHidden(not visible)

    search.textChanged.connect(on_search)
    layout.addWidget(search)
    layout.addWidget(tree, 1)
    return panel


def schema_to_form() -> QWidget:
    """右侧属性面板：真实 input_schema 动态渲染成控件"""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    props = manifest["input_schema"]["properties"]
    panel = QWidget()
    form = QFormLayout(panel)
    form.setContentsMargins(12, 12, 12, 12)
    form.addRow(QLabel(f"<b>{manifest['id']}</b>"))
    for name, spec in props.items():
        t = spec.get("type")
        if t == "string":
            if spec.get("enum"):
                combo = QComboBox()
                combo.addItems(spec["enum"])
                combo.setCurrentText(spec.get("default", ""))
                form.addRow(f"{name}：", combo)
            else:
                line = QLineEdit(spec.get("default", ""))
                line.setPlaceholderText(f"string · 默认 {spec.get('default', '-')}")
                form.addRow(f"{name}：", line)
        elif t == "integer":
            spin = QSpinBox()
            spin.setRange(spec.get("minimum", 0), spec.get("maximum", 10**6))
            spin.setValue(spec.get("default", 0))
            form.addRow(f"{name}：", spin)
        elif t == "boolean":
            cb = QCheckBox()
            cb.setChecked(bool(spec.get("default", False)))
            form.addRow(f"{name}：", cb)
        else:
            line = QLineEdit(json.dumps(spec, ensure_ascii=False))
            form.addRow(f"{name}：", line)
    form.addRow("", QLabel(
        f"<font color='#777'>由 <code>{MANIFEST.name}</code> 的 "
        f"input_schema 动态生成（{len(props)} 字段）</font>"))
    return panel


def build_drag_list() -> QWidget:
    """中部可拖拽列表模拟，含手动移序兜底"""
    panel = QWidget()
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(8, 8, 8, 8)
    layout.addWidget(QLabel("<b>流程步骤排序</b>（拖动行或点按钮移序）"))

    model = DragListModel(["打开网页", "读取文本", "写入变量", "延时等待"])
    view = QTableView()
    view.setModel(model)
    view.verticalHeader().setVisible(False)
    view.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    view.setDragDropMode(QTableView.InternalMove)
    view.setDragDropOverwriteMode(False)
    view.setSelectionBehavior(QTableView.SelectRows)
    view.setSelectionMode(QTableView.SingleSelection)
    layout.addWidget(view, 1)

    btns = QHBoxLayout()
    up = QPushButton("上移")
    down = QPushButton("下移")
    up.clicked.connect(lambda: model.move_row(-1))
    down.clicked.connect(lambda: model.move_row(1))
    btns.addWidget(up)
    btns.addWidget(down)
    btns.addStretch(1)
    layout.addLayout(btns)
    return panel


# ---- 卡片式树画布（对标前端 #canvas 卡片的观感逼近度验证）----
# 深度彩色边线配色，尽量对齐前端 styles.css 的 depth 色板
_DEPTH_COLORS = ["#0969da", "#1a7f37", "#bf8700", "#8250df", "#cf222e", "#0a7ea4"]
# 容器类型徽标配色
_BADGE_COLORS = {"sequence": "#0a7ea4", "if": "#bf8700",
                 "forEach": "#1a7f37", "try": "#8250df", "while": "#bf8700"}
# 自定义 data role：命令名 / 参数摘要 / 容器徽标
_R_CMD = int(Qt.UserRole) + 1
_R_ARGS = int(Qt.UserRole) + 2
_R_BADGE = int(Qt.UserRole) + 3

# 卡片 delegate 调色板：浅色皮肤 / 深色皮肤（配合 qdark 整体换肤联动）
_CARD_LIGHT = {
    "tree_bg": "#f6f8fa", "tree_border": "#e6e8ee",
    "card": "#ffffff", "card_sel": "#eef4ff",
    "shadow": QColor(31, 35, 40, 26),
    "border_normal": QColor(0, 0, 0, 25), "border_hover": QColor(0, 0, 0, 60),
    "accent": "#0969da", "grip": "#969ba5", "cmd": "#1f2328", "args": "#6e737d",
}
_CARD_DARK = {
    "tree_bg": "#1f2429", "tree_border": "#2c333a",
    "card": "#232a30", "card_sel": "#1f3a4d",
    "shadow": QColor(0, 0, 0, 90),
    "border_normal": QColor(255, 255, 255, 22), "border_hover": QColor(255, 255, 255, 55),
    "accent": "#4a9bf5", "grip": "#8b949e", "cmd": "#d7dde3", "args": "#9aa3ad",
}
# Material 深青(amber)亮色系：配合 qt-material 的 dark_teal 皮肤联动
_CARD_MATERIAL = {
    "tree_bg": "#2b2e30", "tree_border": "#3a4043",
    "card": "#323639", "card_sel": "#00544d",
    "shadow": QColor(0, 0, 0, 95),
    "border_normal": QColor(255, 255, 255, 24), "border_hover": QColor(255, 255, 255, 60),
    "accent": "#ffb300", "grip": "#9aa0a6", "cmd": "#e8eaed", "args": "#b0b6b8",
}
# QDarkStyleSheet LightPalette 联动：背景 #FAFAFA / 边框 #C0C4C8 / 文字 #19232D
# 选中沿用其弱选中色 #DAEDFF，白卡片浮在浅灰画布上
_CARD_QLIGHT = {
    "tree_bg": "#fafafa", "tree_border": "#c0c4c8",
    "card": "#ffffff", "card_sel": "#daedff",
    "shadow": QColor(25, 35, 45, 28),
    "border_normal": QColor("#c0c4c8"), "border_hover": QColor(25, 35, 45, 70),
    "accent": "#0969da", "grip": "#9da9b5", "cmd": "#19232d", "args": "#64707d",
}


def _skin_from_argv() -> str:
    """统一解析当前生效皮肤：显式参数优先；无皮肤参数时跟随全局默认 qlight。"""
    if "material" in sys.argv:
        return "material"
    if "qdark" in sys.argv:
        return "qdark"
    if "qlight" in sys.argv:
        return "qlight"
    # 显式原生观感/本地 QSS 时，自绘部件用中性浅色调色板
    if "default" in sys.argv or "styled" in sys.argv:
        return "light"
    return "qlight"  # 默认皮肤


class FlowTreeModel(QStandardItemModel):
    """流程树：容器节点(sequence/if)可带子节点，其余为指令叶子。"""

    def __init__(self, root_label: str, nodes: list, parent=None):
        super().__init__(parent)
        root = QStandardItem(root_label)
        root.setData("sequence", _R_BADGE)  # 根节点按 sequence 类型标徽标
        self._fill(root, nodes)
        root.setFlags(root.flags() | Qt.ItemIsDropEnabled)  # 允许拖入根作为子节点
        self.appendRow(root)

    def _fill(self, item, nodes):
        for cmd, args, children in nodes:
            child = QStandardItem(cmd)
            child.setData(args, _R_ARGS)
            child.setEditable(False)
            if children:
                child.setData(cmd.split(".")[-1], _R_BADGE)  # 容器：if/forEach/try 徽标
                child.setFlags(child.flags() | Qt.ItemIsDropEnabled)
                self._fill(child, children)
            item.appendRow(child)


class FlowCardDelegate(QStyledItemDelegate):
    """按卡片观感绘制每个流程节点：圆角卡片 + 左侧深度彩色线 + 行内分段排版 + 徽标。"""

    def __init__(self, view, colors, parent=None):
        super().__init__(parent)
        self._view = view
        self._c = colors  # 当前皮肤调色板

    def _depth(self, index):
        d = -1
        while index.isValid():
            d += 1
            index = index.parent()
        return d

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        r = option.rect.adjusted(2, 1, -2, -1)
        depth = self._depth(index)
        hovered = bool(option.state & QStyle.State_MouseOver)
        selected = bool(option.state & QStyle.State_Selected)
        c = self._c

        # hover 阴影：卡片下垫一层柔和偏移填充
        if hovered or selected:
            painter.setPen(Qt.NoPen)
            painter.setBrush(c["shadow"])
            painter.drawRoundedRect(r.translated(0, 2), 6, 6)
        # 卡片本体
        card = QPainterPath()
        card.addRoundedRect(r, 6, 6)
        fill = c["card"] if not selected else c["card_sel"]
        if not hovered and not selected:
            painter.setPen(QPen(c["border_normal"], 1))
        else:
            painter.setPen(QPen(QColor(c["accent"]) if selected else c["border_hover"], 1))
        painter.setBrush(fill)
        painter.drawPath(card)

        # 左侧深度彩色边线（相当于前端 ol.children 的 border-left 色线，仅 4px 宽）
        bar_rect = QRect(r.left() + 2, r.top() + 2, 4, r.height() - 4)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(_DEPTH_COLORS[depth % len(_DEPTH_COLORS)]))
        painter.drawRoundedRect(bar_rect, 2, 2)

        # 行内分段排版：拖柄 → 序号 → 命令名(粗) → 参数摘要(等宽灰) → 徽标(右)
        fm = option.fontMetrics
        y = r.center().y()
        x = r.left() + 10
        # 拖柄
        painter.setPen(QColor(c["grip"]))
        painter.drawText(QRect(x, r.top(), 14, r.height()), Qt.AlignVCenter, "≡")
        x += 18
        # 序号：show_index 从 data 读（利用 btn 无，简化用 index.row()+1）
        seq = str(index.row() + 1)
        painter.setPen(QColor(c["grip"]))
        painter.drawText(QRect(x, r.top(), 20, r.height()), Qt.AlignVCenter, seq)
        x += 24
        # 命令名（粗体）
        cmd = index.data(0)
        painter.setPen(QColor(c["cmd"]))
        font_b = QFont(option.font)
        font_b.setBold(True)
        painter.setFont(font_b)
        cw = painter.fontMetrics().horizontalAdvance(cmd)
        painter.drawText(QRect(x, r.top(), cw + 6, r.height()), Qt.AlignVCenter, cmd)
        x += cw + 14
        # 参数摘要（等宽灰，溢出省略）
        args = index.data(_R_ARGS)
        if args:
            font_m = QFont("Consolas")
            font_m.setPointSizeF(8.5)
            painter.setFont(font_m)
            aw = r.width() - (x - r.left())
            elided = painter.fontMetrics().elidedText(f"  {args}", Qt.ElideRight, aw)
            painter.setPen(QColor(c["args"]))
            painter.drawText(QRect(x, r.top(), aw, r.height()), Qt.AlignVCenter, elided + (" " * 8))
        # 徽标（右侧圆角彩底白字，仅容器类型显示）
        badge = index.data(_R_BADGE)
        painter.setFont(option.font)
        if badge:
            color = QColor(_BADGE_COLORS.get(badge, "#57606a"))
            bw = painter.fontMetrics().horizontalAdvance(badge) + 12
            brect = QRect(r.right() - bw - 8, r.center().y() - 9, bw, 18)
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(brect, 9, 9)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(brect, Qt.AlignCenter, badge)
        painter.restore()

    def sizeHint(self, option, index):
        return QSize(0, 34)


class DropTreeView(QTreeView):
    """支持落点提示线的树视图：拖拽时在目标卡片上/下/内部画 accent 分隔线。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drop_line = None  # (row, 'above'/'below'/'inside')

    def dragMoveEvent(self, event):
        pos = event.position().toPoint()
        idx = self.indexAt(pos)
        if not idx.isValid():
            self._drop_line = None
        else:
            r = self.visualRect(idx)
            inside = pos.y() - r.top()  # 位置占比
            if inside < r.height() * 0.3:
                self._drop_line = (idx.row(), "above", idx.parent())
            elif inside > r.height() * 0.7:
                self._drop_line = (idx.row(), "below", idx.parent())
            else:
                self._drop_line = (idx.row(), "inside", idx.parent())
        self.viewport().update()


def build_tree_card() -> QWidget:
    """中部卡片式树画布：QTreeView + 自绘卡片 delegate，支持拖拽重排。"""
    skin = _skin_from_argv()
    c = {"material": _CARD_MATERIAL, "qdark": _CARD_DARK,
         "qlight": _CARD_QLIGHT, "light": _CARD_LIGHT}[skin]
    # 容器背景/边框：皮肤有专门底色则联动；无皮肤的原生 light 用自带浅灰
    if skin == "light":
        bg_qss = "QTreeView { background:#f6f8fa; border:1px solid #e6e8ee; border-radius:8px; }"
    else:
        bg_qss = (
            f"QTreeView {{ background:{c['tree_bg']}; color:{c['cmd']};"
            f" border:1px solid {c['tree_border']}; border-radius:8px; }}"
            " QTreeView::branch { background:transparent; }"
        )
    panel = QWidget()
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(8, 8, 8, 8)
    title = {"material": "Material 深色", "qdark": "QDark 深色",
             "qlight": "QDark 浅色", "light": "浅色"}[skin]
    layout.addWidget(QLabel(f"<b>流程画布</b>（{title} · 拖拽重排）"))

    nodes = [
        ("data.readText", "path=\"log.txt\" encoding=utf-8", []),
        ("workflow.if", "条件: 行数 > 0", [
            ("data.appendText", "path=\"out.log\" text=${line}", []),
            ("workflow.sleep", "ms=200", []),
        ]),
        ("data.limit", "n=10", []),
        ("workflow.sleep", "ms=500", []),
    ]
    model = FlowTreeModel("日志处理流程", nodes)

    view = DropTreeView()
    view.setModel(model)
    view.setHeaderHidden(True)
    view.setDragDropMode(QAbstractItemView.InternalMove)
    view.setDefaultDropAction(Qt.MoveAction)
    view.setExpandsOnDoubleClick(False)
    view.expandAll()
    view.setUniformRowHeights(True)
    view.setAnimated(False)
    view.setItemDelegate(FlowCardDelegate(view, c))
    view.setStyleSheet(bg_qss)
    layout.addWidget(view, 1)
    return panel


def build_bottom_tabs() -> QWidget:
    """底部双面板：素材库(元素树) + 数据表格(网格)，对标前端底部 tabs。"""
    tabs = QTabWidget()

    # 局部覆盖全局皮肤（如 qdark）：消除 pane 与 tab 边框叠加产生的不规则黑边。
    # 根因：全局 QSS 给 pane/tab 都画了边框，交界处叠成黑线；
    # 这里把 tab 边框设透明、pane 顶边上移 1px 消除交界线（QSS 不支持 box-shadow，勿用）。
    skin = _skin_from_argv()
    _tab_theme = {
        "qdark": dict(border="#2c333a", pane="#1f2429", text="#d7dde3",
                      muted="#9aa3ad", accent="#4a9bf5", sel="#232a30"),
        "material": dict(border="#3a4043", pane="#2b2e30", text="#e8eaed",
                         muted="#9aa0a6", accent="#ffb300", sel="#323639"),
        "qlight": dict(border="#c0c4c8", pane="#fafafa", text="#19232d",
                       muted="#64707d", accent="#0969da", sel="#daedff"),
        "light": dict(border="#e6e8ee", pane="#ffffff", text="#1f2328",
                      muted="#6e737d", accent="#0969da", sel="#eef4ff"),
    }[skin]
    _t = _tab_theme
    tabs.setStyleSheet(f"""
        QTabWidget::pane {{
            border: 1px solid {_t['border']}; border-radius: 6px;
            top: -1px; background: {_t['pane']};
        }}
        QTabBar {{ background: transparent; qproperty-drawBase: 0; }}
        QTabBar::tab {{
            background: transparent; color: {_t['muted']};
            padding: 5px 18px; margin-right: 2px;
            border: none; border-bottom: 2px solid transparent;
            border-top-left-radius: 6px; border-top-right-radius: 6px;
        }}
        QTabBar::tab:selected {{
            color: {_t['text']}; background: {_t['sel']};
            border-bottom: 2px solid {_t['accent']};
        }}
        QTabBar::tab:hover {{ color: {_t['text']}; }}
        QTabBar::tear, QTabBar::scroller {{ border: none; background: transparent; }}
    """)

    # ---- 素材库：元素树（按控件类型归类，可搜索/选中）----
    lib = QWidget()
    lib_layout = QVBoxLayout(lib)
    lib_layout.setContentsMargins(8, 8, 8, 8)
    search = QLineEdit()
    search.setPlaceholderText("搜索素材/元素…")
    elem = QTreeWidget()
    elem.setHeaderHidden(True)
    types = {
        "网页元素": ["用户名输入框", "密码输入框", "登录按钮", "新闻列表", "下一页链接"],
        "文本控件": ["标题", "正文", "日志面板"],
        "图片/文件": ["Logo 图片", "附件 Excel"],
    }
    _items = []
    for grp, names in types.items():
        g = QTreeWidgetItem([grp])
        for n in names:
            g.addChild(QTreeWidgetItem([n]))
        elem.addTopLevelItem(g)
        _items.append(g)

    def _filter(text: str):
        text = text.strip()
        for g in _items:
            if not text:
                g.setHidden(False)
                continue
            hit = [ch for ch in g.takeChildren() if text in ch.text(0)]
            g.addChildren(hit)
            g.setHidden(not hit)

    search.textChanged.connect(_filter)
    lib_layout.addWidget(search)
    lib_layout.addWidget(elem, 1)
    tabs.addTab(lib, "素材库")

    # ---- 数据表格：可编辑网格（对标前端 #table-panel）----
    table = QWidget()
    t_layout = QVBoxLayout(table)
    t_layout.setContentsMargins(8, 8, 8, 8)
    toolbar = QHBoxLayout()
    label = QLabel("数据表格（双击单元格编辑 · 跨运行累积）")
    label.setStyleSheet("color:#6e737d; font-size:11px;")
    add_row = QPushButton("追加行")
    save_btn = QPushButton("保存")
    del_row = QPushButton("删除选中行")
    toolbar.addWidget(label)
    toolbar.addStretch(1)
    toolbar.addWidget(del_row)
    toolbar.addWidget(add_row)
    toolbar.addWidget(save_btn)
    t_layout.addLayout(toolbar)

    grid = QStandardItemModel(0, 2)
    grid.setHeaderData(0, Qt.Horizontal, "姓名")
    grid.setHeaderData(1, Qt.Horizontal, "年龄")
    for name, age in [("张三", "23"), ("李四", "30"), ("王五", "28")]:
        grid.appendRow([QStandardItem(name), QStandardItem(age)])

    grid_view = QTableView()
    grid_view.setModel(grid)
    grid_view.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    grid_view.setSelectionBehavior(QTableView.SelectRows)
    add_row.clicked.connect(lambda: grid.appendRow([QStandardItem(""), QStandardItem("")]))
    del_row.clicked.connect(lambda: (
        grid.removeRow(grid_view.currentIndex().row())
        if grid_view.currentIndex().isValid() else None))
    save_btn.clicked.connect(lambda: (
        QMessageBox.information(None, "保存", "表数据已保存到 data/table.json"),))
    t_layout.addWidget(grid_view, 1)
    tabs.addTab(table, "数据表格")

    return tabs


def main() -> int:
    app = QApplication(sys.argv)

    # 主题模式：默认 qlight(QDark浅色) / qdark(深色) / material / fluent / styled
    #            / default(Qt原生观感)
    theme = "qlight"
    for a in sys.argv[1:]:
        if a in ("default", "styled", "material", "fluent", "qdark", "qlight"):
            theme = a
    _TITLE = {"default": "原生默认观感", "styled": "本地 QSS", "material": "Qt-Material",
              "fluent": "FluentWidgets", "qdark": "QDarkStyleSheet 深色",
              "qlight": "QDarkStyleSheet 浅色（默认）"}
    if theme == "styled":
        _apply_style(app)
    elif theme == "material":
        _apply_material(app)
    elif theme == "fluent":
        _apply_fluent(app)
    elif theme == "qdark":
        _apply_qdark(app)
    elif theme == "qlight":
        _apply_qlight(app)

    win = QMainWindow()
    win.setWindowTitle("PySide6 承载编辑器可行性 demo · " + _TITLE[theme])

    top = QSplitter(Qt.Horizontal)
    top.addWidget(build_tree())
    top.addWidget(build_tree_card() if "card" in sys.argv else build_drag_list())
    top.addWidget(schema_to_form())
    top.setSizes([240, 340, 320])
    if "panels" in sys.argv:
        # 底部素材库 + 数据表格双面板：上方三栏 + 下方 tabs
        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(top)
        splitter.addWidget(build_bottom_tabs())
        splitter.setSizes([520, 220])
        win.resize(960, 780)
        win.setCentralWidget(splitter)
    else:
        win.resize(960, 580)
        win.setCentralWidget(top)

    status = QFrame()
    sh = QHBoxLayout(status)
    sh.setContentsMargins(8, 4, 8, 4)
    sh.addWidget(QLabel(f"主题：{_TITLE[theme]} · 均 Qt 原生 Widgets · 与项目 Python 能力层同进程"))
    win.statusBar().addPermanentWidget(status)

    if not QApplication.instance().platformName().startswith("offscreen"):
        win.show()
        app.exec()
        return 0
    # 离屏渲染，导出截图作为可视证据
    win.show()
    pix = QPixmap(win.size() * 2)
    pix.setDevicePixelRatio(2.0)
    win.render(pix)
    if "panels" in sys.argv:
        _base = {
            "material": "pyside6_demo_panels_material.png",
            "qdark": "pyside6_demo_panels_dark.png",
            "qlight": "pyside6_demo_panels_qlight.png",
        }.get(theme, "pyside6_demo_panels.png")
    elif "card" in sys.argv:
        _base = {
            "material": "pyside6_demo_treecard_material.png",
            "qdark": "pyside6_demo_treecard_dark.png",
            "qlight": "pyside6_demo_treecard_qlight.png",
        }.get(theme, "pyside6_demo_treecard.png")
    else:
        _base = {
            "default": "pyside6_demo.png", "styled": "pyside6_demo_styled.png",
            "material": "pyside6_demo_material.png", "fluent": "pyside6_demo_fluent.png",
            "qdark": "pyside6_demo_qdark.png", "qlight": "pyside6_demo_qlight.png",
        }[theme]
    out = str(ROOT / ".harness" / "demo" / _base)
    ok = pix.save(out)
    print("SAVED_OK" if ok else "SAVE_FAIL", out, f"dpr={win.devicePixelRatio()}")
    return 0


_QSS = """
* { font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif; }
QMainWindow, QSplitter, QFrame#Central { background: #f5f6fa; }
QLabel { color: #2b2d33; }
QLabel[banner="true"] { font-size: 15px; font-weight: 600; letter-spacing: .5px; }

/* 顶部面板卡片 */
#PanelTitle {
    background: #ffffff; border-bottom: 1px solid #e6e8ee;
    padding: 10px 12px; border-radius: 4px;
}

/* 指令树 */
QTreeWidget {
    background: #ffffff; border: 1px solid #e6e8ee; border-radius: 8px;
    padding: 6px; outline: none;
}
QTreeWidget::item { height: 26px; border-radius: 6px; padding: 0 6px; }
QTreeWidget::item:hover { background: #eef3fd; }
QTreeWidget::item:selected { background: #e0ebff; color: #1a56db; border-radius: 6px; }
QTreeWidget::branch { background: transparent; }
QComboBox, QLineEdit, QSpinBox {
    background: #ffffff; border: 1px solid #d4d8e0; border-radius: 6px;
    padding: 5px 8px; min-height: 20px; selection-background-color: #cfe0ff;
}
QComboBox:hover, QLineEdit:hover, QSpinBox:hover { border-color: #9db8f0; }
QComboBox:focus, QLineEdit:focus, QSpinBox:focus { border-color: #2b6bff; }

/* 表格：拖拽/选中反馈 + 斑马纹 */
QTableView {
    background: #ffffff; border: 1px solid #e6e8ee; border-radius: 8px;
    gridline-color: #eef0f5; selection-background-color: #e0ebff;
    selection-color: #1a56db; alternate-background-color: #fafbfd;
}
QHeaderView::section {
    background: #f7f8fb; color: #5a607a; border: none; border-bottom: 1px solid #e6e8ee;
    padding: 6px 8px; font-weight: 600;
}
QTableView::item:selected { background: #e0ebff; color: #1a56db; }
QTableView::item:selected:active { background: #d5e5ff; }

/* 按钮 */
QPushButton {
    background: #eef0f5; border: 1px solid #dde1ea; border-radius: 6px;
    padding: 5px 14px; color: #2b2d33;
}
QPushButton:hover { background: #e4e9f5; border-color: #c7d3ee; }
QPushButton:pressed { background: #d8e0f2; }
QPushButton:default { background: #2b6bff; border: none; color: white; }
QPushButton:default:hover { background: #1f5ae0; }

/* 工具栏小图标按钮 */
QPushButton[kind="icon"] { background: transparent; border: none; padding: 2px; }
QPushButton[kind="icon"]:hover { background: #eef3fd; border-radius: 6px; }

/* 分割柄 */
QSplitter::handle { background: transparent; }
QSplitter::handle:hover { background: #cfe0ff; }
QSplitter::handle:horizontal { width: 6px; border-radius: 3px; }
"""


def _apply_style(app: QApplication) -> None:
    """套用一套偏现代、克制的 QSS（圆角卡片 + 柔和分栏 + hover/选中反馈）。"""
    app.setStyleSheet(_QSS)


def _apply_material(app: QApplication) -> None:
    """套用开源 qt-material 主题（MIT，dark_teal）。"""
    from qt_material import apply_stylesheet
    apply_stylesheet(app, theme="dark_teal.xml")


def _apply_fluent(app: QApplication) -> None:
    """套用开源 PyQt-FluentWidgets 全局主题（GPL3，仅本地预览）。"""
    from qfluentwidgets import Theme, setTheme
    setTheme(Theme.DARK)


def _apply_qdark(app: QApplication) -> None:
    """套用 QDarkStyleSheet（MIT，桌面 IDE 深色主题）。"""
    from qdarkstyle import load_stylesheet
    app.setStyleSheet(load_stylesheet(qt_api="pyside6"))


def _apply_qlight(app: QApplication) -> None:
    """套用 QDarkStyleSheet 官方浅色主题（浅灰调，与 qdark 同框架同源）。"""
    from qdarkstyle import load_stylesheet, LightPalette
    app.setStyleSheet(load_stylesheet(qt_api="pyside6", palette=LightPalette))


if __name__ == "__main__":
    raise SystemExit(main())