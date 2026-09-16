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

import copy
import json
import sys
from pathlib import Path

# Qt 绑定在模块顶层导入：本模块本身已被 CLI 延迟导入，未装 extra 时不会触达。
from PySide6.QtCore import QMimeData, Qt
from PySide6.QtGui import QAction, QDrag, QStandardItem
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QFileDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from rpa_core.catalog import CommandCatalog, load_catalog

from .command_palette import (
    ROLE_COMMAND_ID,  # noqa: F401  # re-export：既有测试从 app 导入
    CommandCardDelegate,
    load_command_display_names,
)
from .flow_model import _MIME_COMMAND


class _CommandTree(QTreeWidget):
    """指令树：支持拖到画布，重写 startDrag 发自定义 MIME。"""

    def startDrag(self, supportedActions) -> None:  # noqa: N802 (Qt naming)
        item = self.currentItem()
        if item is None or item.parent() is None:
            # 只有叶子（有 parent）才可拖；根是分组不可拖
            return
        mime = QMimeData()
        # 读叶子上的 ROLE_COMMAND_ID：catalog 命令是完整 id（browser.navigate），
        # 控制流节点是 node_type（sequence/if/forEach/try/return/@else）。
        # 控制流节点走 node_type:xxx 格式以便画布识别。
        raw = item.data(0, ROLE_COMMAND_ID) or ""
        if raw in ("sequence", "if", "forEach", "try", "return"):
            raw = f"node_type:{raw}"
        elif raw == ELSE_COMMAND_ID:
            return  # @else 拖到画布无上下文，不处理
        mime.setData(_MIME_COMMAND, raw.encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)

# 在树 item 上携带完整命令 id 的自定义数据角色（组节点不携带）。
# 定义已迁至 command_palette.py（delegate 与树共用），此处仅为兼容 re-export。

# 命名空间 → 左侧分组中文名；未列入的前缀回退为前缀原文，避免新增命名空间时漏配。
NAMESPACE_LABELS = {
    "browser": "浏览器",
    "data": "数据处理",
    "workflow": "流程控制",
    "desktop": "桌面自动化",
}
# 分组在指令树中的固定展示顺序（高频在前），其余命名空间按名字追加在最后。
NAMESPACE_ORDER = ["browser", "data", "workflow", "desktop"]

# ---- 控制指令 -------------------------------------------------------------
# catalog 之外的流程控制指令，固定挂在指令树最前面的「流程控制」组。
# 「否则」是 if 的**可选**分支指令（影刀同款）：默认不加，需要时从这里双击添加。
CONTROL_GROUP_LABEL = "流程控制"
ELSE_COMMAND_ID = "@else"
# 控制流节点用 node_type（而非 command_id）作为左树叶子的标识，
# add_command 里据此分支调 insert_node。_CONTROL_COMMANDS 里 command_id 字段
# 存两种标识：以 @ 开头表示"这是控制指令"（@else / @return），
# 普通字符串表示"这是控制流节点类型"（sequence/forEach/if/try/return）。
# （其实 control 节点和 return 可以直接调 insert_node。为减少用户心智负担
# 统一在左树展示为可双击添加的"指令"。）
# （command id / node_type, 显示名, tooltip）
_CONTROL_COMMANDS = [
    (
        "sequence", "顺序执行",
        "顺序容器：依次执行子节点。根容器天然就是 sequence，\n"
        "手动添加可用于嵌套组织子流程。",
    ),
    (
        "if", "如果",
        "条件分支：condition=true 执行则执行分支，否则跳过。\n"
        "双击后默认条件留空，选「否则执行」行添加否则分支。",
    ),
    (
        "forEach", "循环（for each）",
        "遍历容器：依次对 items 中的每个元素执行 children。\n"
        "双击后需在右侧填 items 数组和可选 item_var。",
    ),
    (
        "try", "异常捕获",
        "异常保护：children 抛出异常时转进 catch 分支。\n"
        "双击后右侧可配置 error_var（默认 error）。",
    ),
    (
        "return", "返回",
        "流程返回：立即结束当前 run 并把 value 作为输出。\n"
        "双击后右侧可配置返回值（可留空表示成功）。",
    ),
    (
        ELSE_COMMAND_ID, "否则",
        "否则分支：先选中画布的「如果」节点（或其内部指令）再双击添加。\n"
        "添加后位于它下面的指令属于否则分支；Delete 删除它即取消分支。",
    ),
]


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
    tree: QTreeWidget,
    catalog: CommandCatalog,
    names: dict[str, str] | None = None,
) -> dict[str, QTreeWidgetItem]:
    """把控制指令与真实 catalog 填充进指令树，返回「命名空间 → 分组节点」映射。

    树为两级：分组节点（不可执行）→ 命令叶子（``ROLE_COMMAND_ID`` 存完整 id，
    显示文本为中文显示名，缺失时回退命令 id）。最前面固定一组「流程控制」，
    承载 catalog 里没有的控制指令（目前是 if 的「否则」分支）——它们与普通
    命令一样双击即可添加到画布。组内排序与 Web 面板同口径：manifest 的
    ``x-palette-order``（影刀对标顺序）优先，缺省按 id 字典序。
    """
    names = names or {}
    tree.clear()
    groups: dict[str, QTreeWidgetItem] = {}

    control_group = QTreeWidgetItem(
        tree, [f"{CONTROL_GROUP_LABEL}（{len(_CONTROL_COMMANDS)}）"]
    )
    control_group.setData(0, ROLE_COMMAND_ID, None)
    for command_id, label, tip in _CONTROL_COMMANDS:
        control_leaf = QTreeWidgetItem(control_group, [label])
        control_leaf.setData(0, ROLE_COMMAND_ID, command_id)
        control_leaf.setToolTip(0, tip)
    groups["control"] = control_group

    for namespace in _ordered_namespaces(catalog):
        label = NAMESPACE_LABELS.get(namespace, namespace)
        command_ids = sorted(
            (cid for cid in catalog if cid.split(".", 1)[0] == namespace),
            key=lambda cid: (catalog[cid].x_palette_order or 1 << 30, cid),
        )
        group_item = QTreeWidgetItem(tree, [f"{label}（{len(command_ids)}）"])
        group_item.setData(0, ROLE_COMMAND_ID, None)
        for command_id in command_ids:
            manifest = catalog[command_id]
            leaf = QTreeWidgetItem(group_item, [names.get(command_id, command_id)])
            leaf.setData(0, ROLE_COMMAND_ID, command_id)
            # tooltip 给出完整命令 id 与执行器/种类，中文名下仍可辨识身份。
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
            command_id = child.data(0, ROLE_COMMAND_ID) or ""
            # 显示名与命令 id 一起匹配：控制指令的显示名是中文（否则）、
            # 命令 id 是 @else，两者都应能被搜到
            matched = keyword in f"{child.text(0)} {command_id}".lower()
            child.setHidden(not matched)
            visible += matched
        group.setHidden(visible == 0)
        group.setExpanded(visible > 0)


class MainWindow(QMainWindow):
    """编辑器主窗口：左指令树（真实 catalog）+ 中流程卡片画布 + 右参数表单。"""

    def __init__(self, catalog: CommandCatalog, workflow=None, flow_path=None) -> None:
        super().__init__()
        self.catalog = catalog
        # flow_path 为 None 时编辑的是内置示例：首次保存走「另存为」
        self.flow_path: Path | None = Path(flow_path) if flow_path else None
        self._workflow_meta: dict = {}
        self._dirty = False
        self._loading = False  # 构建模型期间抑制结构变化信号，避免误置脏标记
        self.setWindowTitle("RPA Core 编辑器")
        self.resize(1280, 800)

        self._build_toolbar()

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左栏：搜索框 + 指令树
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(8, 8, 8, 8)
        search = QLineEdit()
        search.setPlaceholderText("搜索指令…")
        tree = _CommandTree()
        tree.setHeaderHidden(True)
        tree.setDragEnabled(True)  # 允许叶子指令拖出到画布
        # 影刀式卡片渲染：叶子=圆角卡片（图标+中文名+id），分组=轻文本
        tree.setItemDelegate(CommandCardDelegate(tree))
        populate_command_tree(tree, catalog, load_command_display_names())
        search.textChanged.connect(lambda text: _apply_filter(tree, text))
        # 双击指令叶子 → 在画布当前选中位置插入新 action（组节点忽略）
        tree.itemDoubleClicked.connect(self._on_command_double_clicked)
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

        # 右栏：参数表单区（选中画布 action 卡片后渲染 manifest 表单）
        self.param_holder = QWidget()
        self.param_layout = QVBoxLayout(self.param_holder)
        self.param_layout.setContentsMargins(0, 0, 0, 0)
        self._show_param_placeholder("从画布选择指令节点以编辑参数")
        splitter.addWidget(self.param_holder)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([280, 700, 300])
        self.setCentralWidget(splitter)

        self.statusBar().showMessage(
            f"已加载 {len(catalog)} 条指令 · catalog {catalog.digest[:10]}"
        )

    def _build_toolbar(self) -> None:
        """顶部工具栏：新建 / 打开 / 保存 / 删除节点。"""
        toolbar = self.addToolBar("文件")
        toolbar.setMovable(False)

        new_action = QAction("新建", self)
        new_action.setShortcut("Ctrl+N")
        new_action.setToolTip("新建空白流程（Ctrl+N）")
        new_action.triggered.connect(self._new_action)
        toolbar.addAction(new_action)

        open_action = QAction("打开", self)
        open_action.setShortcut("Ctrl+O")
        open_action.setToolTip("打开 workflow.json（Ctrl+O）")
        open_action.triggered.connect(self._open_action)
        toolbar.addAction(open_action)

        save_action = QAction("保存", self)
        save_action.setShortcut("Ctrl+S")
        save_action.setToolTip("保存到 workflow.json（Ctrl+S）")
        save_action.triggered.connect(self._save_action)
        toolbar.addAction(save_action)

        # 删除节点用窗口级快捷键：焦点在左树（双击添加后的自然状态）或画布
        # 时都能直接删；焦点在参数表单输入控件中时禁用，让 Delete 正常编辑文本。
        self.delete_action = QAction("删除节点", self)
        self.delete_action.setShortcut("Delete")
        self.delete_action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        self.delete_action.setToolTip("删除画布选中节点（Delete）")
        self.delete_action.triggered.connect(self._delete_selected_node)
        toolbar.addAction(self.delete_action)
        app = QApplication.instance()
        app.focusChanged.connect(self._on_focus_changed)

    def _prompt_discard_changes(self) -> bool:
        """有未保存修改时弹确认。返回 True 表示用户接受丢弃（可以继续操作）。"""
        if not self._dirty:
            return True
        answer = QMessageBox.question(
            self,
            "未保存的修改",
            "当前流程有未保存的修改，是否放弃？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _new_action(self) -> None:
        """新建空白流程：清除画布、重置路径与元数据。"""
        if not self._prompt_discard_changes():
            return
        empty = {
            "schema_version": "1.0",
            "id": f"wf_{self.flow_path.stem if self.flow_path else 'new'}",
            "name": "未命名流程",
            "root": {
                "type": "sequence",
                "id": "root",
                "children": [],
            },
        }
        self.flow_path = None
        self.set_workflow(empty)

    def _open_action(self) -> None:
        """打开已保存的 workflow.json 文件。"""
        from rpa_core.model.workflow import Workflow  # 延迟导入，避免顶部 import

        if not self._prompt_discard_changes():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "打开流程", "", "工作流文件 (*.json)"
        )
        if not path:
            return
        try:
            document = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            QMessageBox.warning(self, "打开失败", f"无法读取文件：{exc}")
            return
        try:
            Workflow.model_validate(document)  # 校验合法性
        except Exception as exc:  # noqa: BLE001 - 给用户可读反馈
            QMessageBox.warning(self, "打开失败", f"文件不符合 workflow schema：{exc}")
            return
        self.flow_path = Path(path)
        self.set_workflow(document)

    def _on_focus_changed(self, old, now) -> None:
        """焦点进入文本编辑控件时暂停 Delete 删除动作，避免吞掉编辑键。"""
        editing = isinstance(
            now, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)
        ) or (isinstance(now, QComboBox) and now.isEditable())
        self.delete_action.setEnabled(not editing)

    def set_workflow(self, workflow) -> None:
        """加载 Workflow（pydantic 或 dict）并重建画布；拖拽重排发生在该模型上。"""
        # 延迟导入：flow_model/canvas 依赖 Qt，但与 catalog 同源，无额外成本
        from rpa_core.gui.canvas import build_canvas
        from rpa_core.gui.flow_model import build_model_from_workflow

        # 统一为 JSON 形状的 dict 并深拷贝：GUI 内的编辑不得回写调用方对象
        if isinstance(workflow, dict):
            document = copy.deepcopy(workflow)
        else:
            document = workflow.model_dump(by_alias=True)
        # 工作流级字段（schema_version/id/name/inputs/timeout_seconds 等）原样保留
        self._workflow_meta = {
            key: copy.deepcopy(value)
            for key, value in document.items()
            if key != "root"
        }

        if self.canvas_view is not None:
            self.canvas_layout.removeWidget(self.canvas_view)
            self.canvas_view.deleteLater()

        self._loading = True
        try:
            self.flow_model = build_model_from_workflow(document)
        finally:
            self._loading = False
        self.canvas_view = build_canvas(
            self.flow_model, self.canvas_holder, self.catalog
        )
        self.canvas_layout.addWidget(self.canvas_view)
        # 拖拽重排（takeRow/insertRow）会触发增删行信号 → 置脏
        self.flow_model.rowsInserted.connect(self._on_structure_changed)
        self.flow_model.rowsRemoved.connect(self._on_structure_changed)
        # 画布选中节点变化 → 右栏切换参数表单（每次重建 view 都需重新连接）
        self.canvas_view.selectionModel().currentChanged.connect(
            self._on_canvas_selection
        )
        self._set_dirty(False)

    # ---- 节点增删（切片 5） -----------------------------------------------
    def _on_command_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        """左树双击：叶子命令插入画布；分组节点（command id 为 None）忽略。"""
        command_id = item.data(0, ROLE_COMMAND_ID)
        if command_id:
            self.add_command(command_id)

    def add_command(self, command_id: str):
        """在画布当前选中位置插入新指令；返回新 item（添加失败时返回 None）。

        三种分支：
        - ``@else``（控制指令）：给画布选中的 if 添加否则分支指令行；
        - control node_type（sequence/if/forEach/try/return）：调 insert_node
          插入空模板容器；
        - catalog 命令（如 browser.open）：调 insert_command 插入 action。
        """
        if command_id == ELSE_COMMAND_ID:
            return self._add_else_branch()
        # 控制流节点（sequence/if/forEach/try/return）：走 insert_node
        if command_id in ("sequence", "if", "forEach", "try", "return"):
            return self._add_control_node(command_id)
        current = self.canvas_view.currentIndex()
        target = (
            self.flow_model.itemFromIndex(current) if current.isValid() else None
        )
        new_item = self.flow_model.insert_command(command_id, target)
        self._select_new_item(new_item)
        self.statusBar().showMessage(
            f"已添加 {command_id}（Delete 删除选中节点，Ctrl+S 保存）", 4000
        )
        return new_item

    def _select_new_item(self, new_item: QStandardItem) -> None:
        """展开落点父级 + 选中新节点（添加完之后统一用）。"""
        self.canvas_view.setExpanded(
            self.flow_model.indexFromItem(new_item.parent()), True
        )
        new_index = self.flow_model.indexFromItem(new_item)
        self.canvas_view.setCurrentIndex(new_index)

    def _add_control_node(self, node_type: str):
        """插入控制流节点（if/forEach/try/sequence/return）。"""
        current = self.canvas_view.currentIndex()
        target = (
            self.flow_model.itemFromIndex(current) if current.isValid() else None
        )
        new_item = self.flow_model.insert_node(node_type, target)
        self._select_new_item(new_item)
        label = {"sequence": "顺序执行", "if": "如果", "forEach": "循环",
                 "try": "异常捕获", "return": "返回"}.get(node_type, node_type)
        self.statusBar().showMessage(
            f"已添加「{label}」（可在右侧面板配置参数）", 4000
        )
        return new_item

    def _add_else_branch(self):
        """给画布选中的 if 添加「否则」指令行（默认不加，需要时才加）。

        目标取当前选中项最近的 if 祖先（选中 if 本身、if 内任意指令、或已存在的
        「否则」行都算）。没有 if 上下文或该 if 已有否则分支时，只在状态栏提示，
        不静默失败。
        """
        current = self.canvas_view.currentIndex()
        if_item = (
            self._nearest_if(self.flow_model.itemFromIndex(current))
            if current.isValid()
            else None
        )
        if if_item is None:
            self.statusBar().showMessage(
                "请先在画布选中「如果」节点（或其内部的指令），再添加否则", 5000
            )
            return None
        marker = self.flow_model.add_else_branch(if_item)
        if marker is None:
            self.statusBar().showMessage("该「如果」已经有否则分支了", 4000)
            return None
        self.canvas_view.setExpanded(self.flow_model.indexFromItem(if_item), True)
        self.canvas_view.setCurrentIndex(self.flow_model.indexFromItem(marker))
        self.statusBar().showMessage(
            "已添加否则分支：它下面的指令属于否则分支（Delete 可取消分支）", 5000
        )
        return marker

    @staticmethod
    def _nearest_if(item):
        """沿父链找到最近的 if item（item 自身是 if 时返回自身）。"""
        from rpa_core.gui.flow_model import ROLE_NODE_TYPE

        current = item
        while current is not None:
            if current.data(ROLE_NODE_TYPE) == "if":
                return current
            current = current.parent()
        return None

    def _delete_selected_node(self) -> None:
        """删除画布当前选中节点；根节点与虚拟分组受保护。

        删「否则」行等价于取消 else 分支（其下指令顺序不变，自然并入 then 段）。
        """
        from rpa_core.gui.flow_model import _ELSE_BRANCH_TYPE, ROLE_NODE_ID, ROLE_NODE_TYPE

        current = self.canvas_view.currentIndex()
        if not current.isValid():
            return
        item = self.flow_model.itemFromIndex(current)
        # 先取属性：remove_item 会把 item 从树上摘掉
        node_type = item.data(ROLE_NODE_TYPE)
        node_id = item.data(ROLE_NODE_ID)
        if not self.flow_model.remove_item(item):
            return
        what = (
            "否则分支（其下指令已并入如果分支）"
            if node_type == _ELSE_BRANCH_TYPE
            else f"节点 {node_id}"
        )
        self.statusBar().showMessage(f"已删除{what}（未保存）", 4000)
        self._show_param_placeholder("从画布选择指令节点以编辑参数")

    def _on_structure_changed(self, *args) -> None:
        """模型结构行变化（拖拽重排）时置脏；初始构建期间忽略。"""
        if not self._loading:
            self._set_dirty(True)

    def _set_dirty(self, dirty: bool) -> None:
        """更新脏标记与标题前缀。"""
        self._dirty = dirty
        name = self._workflow_meta.get("name", "未命名工作流")
        marker = "• " if dirty else ""
        self.setWindowTitle(f"{marker}RPA Core 编辑器 — {name}")

    # ---- 保存 -------------------------------------------------------------
    def _save_action(self) -> None:
        """工具栏保存：无源路径时先弹「另存为」。"""
        target = self.flow_path
        if target is None:
            suggested = "workflow.json"
            chosen, _ = QFileDialog.getSaveFileName(
                self, "另存为", suggested, "工作流文件 (*.json)"
            )
            if not chosen:
                return
            target = Path(chosen)
        saved = self.save_workflow(target)
        if saved is not None:
            self.statusBar().showMessage(f"已保存 {saved}", 4000)

    def save_workflow(self, path: Path) -> Path | None:
        """把当前模型回写为 workflow dict，校验通过后落盘；失败返回 None。

        落盘格式与 devserver WorkflowDirStore 一致：UTF-8、indent=2、末尾换行。
        """
        from rpa_core.gui.flow_model import model_to_workflow
        from rpa_core.model.workflow import Workflow

        document = model_to_workflow(self.flow_model, self._workflow_meta)
        try:
            Workflow.model_validate(document)  # 结构非法则拒绝落盘
        except Exception as exc:  # noqa: BLE001 - 校验错误统一进状态栏，不崩溃
            self.statusBar().showMessage(f"校验失败，未保存：{exc}", 6000)
            return None

        encoded = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(encoded)
        self.flow_path = path
        self._workflow_meta = {
            key: copy.deepcopy(value)
            for key, value in document.items()
            if key != "root"
        }
        self._set_dirty(False)
        return path

    # Qt 覆写要求保留驼峰方法名，故豁免 N802（行尾不能写中文括号，
    # 否则 ruff 会把说明当成 noqa code 列表的一部分而报 Invalid noqa directive）
    def closeEvent(self, event) -> None:  # noqa: N802
        """有未保存修改时询问：保存 / 不保存 / 取消。"""
        if not self._dirty:
            event.accept()
            return
        answer = QMessageBox.question(
            self,
            "未保存的修改",
            "当前流程有未保存的修改，是否保存后退出？",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Cancel:
            event.ignore()
        elif answer == QMessageBox.StandardButton.Discard:
            event.accept()
        else:
            self._save_action()
            # 另存为被取消或保存失败时不关闭，避免丢失修改
            event.accept() if not self._dirty else event.ignore()

    # ---- 右栏参数表单 -----------------------------------------------------
    def _clear_param_panel(self) -> None:
        """清空右栏内容（旧控件延迟销毁）。"""
        while self.param_layout.count():
            old = self.param_layout.takeAt(0).widget()
            if old is not None:
                old.deleteLater()

    def _show_param_placeholder(self, text: str) -> None:
        """右栏占位提示。"""
        self._clear_param_panel()
        label = QLabel(text)
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("color: #64707d; padding: 16px;")
        self.param_layout.addWidget(label)

    def _on_canvas_selection(self, current, previous) -> None:
        """画布当前节点变化：action 显参数表单，其余显对应占位。"""
        from rpa_core.gui.flow_model import (
            _ELSE_BRANCH_TYPE,
            ROLE_ARGS_RAW,
            ROLE_COMMAND_ID,
            ROLE_NODE_TYPE,
        )

        if not current.isValid():
            self._show_param_placeholder("从画布选择指令节点以编辑参数")
            return
        node_type = current.data(ROLE_NODE_TYPE)
        if node_type != "action":
            hint = {
                "return": "返回节点的编辑将在后续切片支持",
                _ELSE_BRANCH_TYPE: "否则分支指令：位于它下面的指令属于否则分支"
                "（Delete 取消分支）",
            }.get(node_type, "该节点不接受参数")
            self._show_param_placeholder(hint)
            return

        command_id = current.data(ROLE_COMMAND_ID)
        manifest = self.catalog[command_id]
        holder = current.data(ROLE_ARGS_RAW)
        args = holder.args if holder is not None else {}
        self._show_action_form(manifest, args, current, ROLE_ARGS_RAW)

    def _show_action_form(self, manifest, args, index, role_args_raw) -> None:
        """在右栏挂载「滚动表单 + 应用按钮」。"""
        from rpa_core.gui.flow_model import ROLE_ARGS_SUMMARY, summarize_args
        from rpa_core.gui.param_form import ParamForm

        self._clear_param_panel()
        form = ParamForm(manifest.input_schema, args)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(form)
        self.param_layout.addWidget(scroll, 1)

        apply_button = QPushButton("应用参数")

        def apply() -> None:
            try:
                values = form.values()
            except ValueError as exc:
                self.statusBar().showMessage(str(exc), 4000)
                return
            item = self.flow_model.itemFromIndex(index)
            # 原地更新同一 holder（保留 raw 模板），并同步 raw["with"] 供回写
            holder = item.data(role_args_raw)
            holder.args = dict(values)
            if holder.raw is not None:
                holder.raw["with"] = dict(values)
            item.setData(summarize_args(values), ROLE_ARGS_SUMMARY)
            self._set_dirty(True)
            self.statusBar().showMessage("参数已更新（未保存）", 4000)

        apply_button.clicked.connect(apply)
        self.param_layout.addWidget(apply_button)


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
             "with": {"url": "https://example.com", "browserType": "msedge",
                      "timeoutMs": 30000}},
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
                "item_var": "row",
                "children": [
                    {"type": "action", "id": "append", "command": "data.appendText",
                     "with": {"workspace": ".", "path": "out.txt", "text": "${row}"}},
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


def build_main_window(
    catalog: CommandCatalog, workflow=None, flow_path=None
) -> MainWindow:
    """构建主窗口但不进入事件循环（供 headless 冒烟测试调用）。"""
    return MainWindow(catalog, workflow=workflow, flow_path=flow_path)


def run_gui(commands_root: Path, flow_path: Path | None = None) -> int:
    """GUI 启动入口：加载真实 catalog（可选 workflow）→ 构建窗口 → 进入事件循环。"""
    from rpa_core.model.workflow import Workflow

    app = build_application()
    catalog = load_catalog(Path(commands_root))
    workflow = (
        Workflow.model_validate_json(Path(flow_path).read_text(encoding="utf-8"))
        if flow_path else None
    )
    window = build_main_window(catalog, workflow=workflow, flow_path=flow_path)
    window.show()
    return app.exec()
