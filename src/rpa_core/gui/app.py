"""原生桌面 GUI 应用入口（第一个垂直切片）。

本切片范围（从 .harness/demo 的假数据迈向真实产品）：

- 用 QDarkStyle 的 **LightPalette** 作为默认浅色皮肤（与可行性 demo 同档观感）；
- 左侧指令树直接加载**真实 catalog**（``load_catalog`` 的不可变快照），
  按命令 id 的命名空间第一段分组（browser/data/workflow/desktop），
  带实时搜索过滤；
- 中部为占位区，后续切片再接入流程画布与参数表单；
- 结构与事件循环解耦：:func:`build_main_window` 只构建控件不进入事件循环，
  便于在 ``QT_QPA_PLATFORM=offscreen`` 下做无显示环境的冒烟测试。

PySide6 仅在 ``gui`` extra 中提供，所以本模块只能被延迟导入（CLI 在处理
``gui`` 子命令时才 import），不要在包顶层或其它热路径引用。
"""

from __future__ import annotations

import sys
from pathlib import Path

# Qt 绑定在模块顶层导入：本模块本身已被 CLI 延迟导入，未装 extra 时不会触达。
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QLineEdit,
    QMainWindow,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from rpa_core.catalog import CommandCatalog, load_catalog

# 在树 item 上携带完整命令 id 的自定义数据角色（组节点不携带）。
ROLE_COMMAND_ID = Qt.ItemDataRole.UserRole + 1

# 命名空间 → 左侧分组中文名；未列入的前缀回退为前缀原文，避免新增命名空间时漏配。
NAMESPACE_LABELS = {
    "browser": "浏览器",
    "data": "数据处理",
    "workflow": "流程控制",
    "desktop": "桌面自动化",
}
# 分组在指令树中的固定展示顺序（高频在前），其余命名空间按名字追加在最后。
NAMESPACE_ORDER = ["browser", "data", "workflow", "desktop"]


def apply_theme(app: QApplication) -> None:
    """套用 QDarkStyle 浅色主题（LightPalette）并设置跨平台中文字体回退。

    QDarkStyle 缺失时退回 Qt 自带 Fusion 风格，保证在最小依赖下仍可启动；
    字体按平台候选选第一个实际存在的族，避免中文在某些环境渲染成方框。
    """
    from PySide6.QtGui import QFont, QFontDatabase

    for family in ("Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC",
                   "Source Han Sans SC", "SimHei"):
        if QFontDatabase.hasFamily(family):
            font = QFont(family, 9)
            app.setFont(font)
            break
    try:
        from qdarkstyle import LightPalette, load_stylesheet
    except ImportError:  # pragma: no cover - 仅在缺 extra 的异常安装态触发
        app.setStyle("Fusion")
        return
    app.setStyleSheet(load_stylesheet(qt_api="pyside6", palette=LightPalette))


def _ordered_namespaces(catalog: CommandCatalog) -> list[str]:
    """收集 catalog 实际出现的命名空间，按固定顺序 + 字典序兜底排列。"""
    present = {command_id.split(".", 1)[0] for command_id in catalog}
    known = [name for name in NAMESPACE_ORDER if name in present]
    extra = sorted(present - set(NAMESPACE_ORDER))
    return known + extra


def populate_command_tree(
    tree: QTreeWidget, catalog: CommandCatalog
) -> dict[str, QTreeWidgetItem]:
    """把真实 catalog 填充进指令树，返回「命名空间 → 分组节点」映射。

    树为两级：分组节点（不可执行）→ 命令叶子（``ROLE_COMMAND_ID`` 存完整 id）。
    """
    tree.clear()
    groups: dict[str, QTreeWidgetItem] = {}
    for namespace in _ordered_namespaces(catalog):
        label = NAMESPACE_LABELS.get(namespace, namespace)
        command_ids = sorted(
            cid for cid in catalog if cid.split(".", 1)[0] == namespace
        )
        group_item = QTreeWidgetItem(tree, [f"{label}（{len(command_ids)}）"])
        group_item.setData(0, ROLE_COMMAND_ID, None)
        for command_id in command_ids:
            manifest = catalog[command_id]
            leaf = QTreeWidgetItem(group_item, [command_id])
            leaf.setData(0, ROLE_COMMAND_ID, command_id)
            # tooltip 给出执行器与命令种类，便于无中文名时辨识。
            leaf.setToolTip(
                0,
                f"{command_id}\n执行器：{manifest.executor}\n"
                f"种类：{manifest.kind.value}",
            )
        groups[namespace] = group_item
    tree.expandAll()
    return groups


def _apply_filter(tree: QTreeWidget, keyword: str) -> None:
    """按关键字过滤命令叶子；空关键字恢复全部。组随命中数显隐。"""
    keyword = keyword.strip().lower()
    for group_index in range(tree.topLevelItemCount()):
        group = tree.topLevelItem(group_index)
        if not keyword:
            group.setHidden(False)
            for child_index in range(group.childCount()):
                group.child(child_index).setHidden(False)
            group.setExpanded(False)
            continue
        visible = 0
        for child_index in range(group.childCount()):
            child = group.child(child_index)
            matched = keyword in child.data(0, ROLE_COMMAND_ID).lower()
            child.setHidden(not matched)
            visible += matched
        group.setHidden(visible == 0)
        group.setExpanded(visible > 0)


class MainWindow(QMainWindow):
    """编辑器主窗口：左指令树（真实 catalog）+ 中流程卡片画布。"""

    def __init__(self, catalog: CommandCatalog, workflow=None) -> None:
        super().__init__()
        self.catalog = catalog
        self.setWindowTitle("RPA Core 编辑器")
        self.resize(1280, 800)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左栏：搜索框 + 指令树
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(8, 8, 8, 8)
        search = QLineEdit()
        search.setPlaceholderText("搜索指令…")
        tree = QTreeWidget()
        tree.setHeaderHidden(True)
        populate_command_tree(tree, catalog)
        search.textChanged.connect(lambda text: _apply_filter(tree, text))
        left_layout.addWidget(search)
        left_layout.addWidget(tree)
        self.command_tree = tree

        # 中栏：流程卡片画布（AST → FlowTreeModel → CardDelegate）
        self.canvas_holder = QWidget()
        self.canvas_layout = QVBoxLayout(self.canvas_holder)
        self.canvas_layout.setContentsMargins(0, 0, 0, 0)
        self.canvas_view = None
        self.flow_model = None
        self.set_workflow(workflow or SAMPLE_WORKFLOW)

        splitter.addWidget(left)
        splitter.addWidget(self.canvas_holder)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([320, 960])
        self.setCentralWidget(splitter)

        self.statusBar().showMessage(
            f"已加载 {len(catalog)} 条指令 · catalog {catalog.digest[:10]}"
        )

    def set_workflow(self, workflow) -> None:
        """加载 Workflow（pydantic 或 dict）并重建画布；拖拽重排发生在该模型上。"""
        # 延迟导入：flow_model/canvas 依赖 Qt，但与 catalog 同源，无额外成本
        from rpa_core.gui.canvas import build_canvas
        from rpa_core.gui.flow_model import build_model_from_workflow

        if self.canvas_view is not None:
            self.canvas_layout.removeWidget(self.canvas_view)
            self.canvas_view.deleteLater()
        name = workflow.get("name", "未命名工作流") if isinstance(workflow, dict) else workflow.name
        self.flow_model = build_model_from_workflow(workflow)
        self.canvas_view = build_canvas(self.flow_model, self.canvas_holder)
        self.canvas_layout.addWidget(self.canvas_view)
        self.setWindowTitle(f"RPA Core 编辑器 — {name}")


# 内置示例流程：覆盖全部容器结构（sequence/if/forEach/try/action/return），
# 让无参数启动时画布直接呈现完整卡片形态；打开真实 workflow.json 后即被替换。
SAMPLE_WORKFLOW = {
    "schema_version": "1.0",
    "id": "sample",
    "name": "示例流程",
    "root": {
        "type": "sequence",
        "id": "root",
        "children": [
            {"type": "action", "id": "open", "command": "browser.navigate",
             "with": {"url": "https://example.com", "waitUntil": "load"}},
            {
                "type": "if",
                "id": "check",
                "condition": {"op": "truthy", "left": "${data}"},
                "then": [
                    {"type": "action", "id": "read", "command": "browser.getText",
                     "with": {"selector": "h1", "timeoutMs": 5000}},
                ],
                "else": [
                    {"type": "action", "id": "wait", "command": "workflow.sleep",
                             "with": {"seconds": 1}},
                ],
            },
            {
                "type": "forEach",
                "id": "loop",
                "items": "${rows}",
                "itemVar": "row",
                "children": [
                    {"type": "action", "id": "append", "command": "data.appendText",
                     "with": {"path": "out.txt", "text": "${row}"}},
                ],
            },
            {"type": "return", "id": "done", "value": "ok"},
        ],
    },
}


def build_application(argv: list[str] | None = None) -> QApplication:
    """创建 QApplication 并套肤。测试复用进程内单例，避免重复构造。"""
    app = QApplication.instance() or QApplication(argv if argv is not None else sys.argv)
    apply_theme(app)
    return app


def build_main_window(catalog: CommandCatalog, workflow=None) -> MainWindow:
    """构建主窗口但不进入事件循环（供 headless 冒烟测试调用）。"""
    return MainWindow(catalog, workflow=workflow)


def run_gui(commands_root: Path, flow_path: Path | None = None) -> int:
    """GUI 启动入口：加载真实 catalog（可选 workflow）→ 构建窗口 → 进入事件循环。"""
    from rpa_core.model.workflow import Workflow

    app = build_application()
    catalog = load_catalog(Path(commands_root))
    workflow = (
        Workflow.model_validate_json(Path(flow_path).read_text(encoding="utf-8"))
        if flow_path else None
    )
    window = build_main_window(catalog, workflow=workflow)
    window.show()
    return app.exec()
