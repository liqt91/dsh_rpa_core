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
import threading
from pathlib import Path

# Qt 绑定在模块顶层导入：本模块本身已被 CLI 延迟导入，未装 extra 时不会触达。
from PySide6.QtCore import QMimeData, QObject, Qt, Signal
from PySide6.QtGui import QAction, QDrag, QKeySequence, QStandardItem
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
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
from rpa_core.devserver.store import WorkflowDirStore

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

    同一 QApplication 只应用一次：测试里每个模块的 qapp fixture 都会调
    build_application()，重复 setStyleSheet 到已存在大量窗口的应用上会在
    offscreen 平台触发原生崩溃（且生产路径本就只需一次）。
    """
    if getattr(app, "_rpa_theme_applied", False):
        return
    from PySide6.QtGui import QFont, QFontDatabase

    for family in ("Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC",
                   "Source Han Sans SC", "SimHei"):
        if QFontDatabase.hasFamily(family):
            font = QFont(family, 9)
            app.setFont(font)
            break
    app._rpa_theme_applied = True  # type: ignore[attr-defined]
    try:
        from qdarkstyle import LightPalette, load_stylesheet
    except ImportError:  # pragma: no cover - 仅在缺 extra 的异常安装态触发
        app.setStyle("Fusion")
        return
    app.setStyleSheet(load_stylesheet(qt_api="pyside6", palette=LightPalette))


def present_window(window: QWidget, *, always_on_top: bool = False) -> None:
    """把窗口送到用户眼前。

    为什么不能只调 ``activateWindow()``：macOS 为防焦点窃取，**忽略后台应用的
    自激活请求**。捕获/运行期间主窗口被最小化、用户正在浏览器里操作，我们此刻
    就是后台应用；此时新窗口会正常创建，但停在浏览器之后，用户必须点一次 Dock
    图标才能看见（真机实测：元素捕获的确认对话框「要点一次 Dock 才显示」）。

    - ``always_on_top=True``：临时打开 ``WindowStaysOnTopHint``，让窗口浮到最前。
      **只用于模态对话框等短命窗口**；常驻窗口置顶会一直压住其它应用。
    - ``QApplication.alert``：兜底提醒（macOS 弹跳 Dock 图标 / Windows 闪烁任务栏
      / Linux 无操作），保证即使用户没注意到窗口也能被提示到。
    """
    if always_on_top:
        window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    window.show()
    window.raise_()
    window.activateWindow()
    QApplication.alert(window, 2000)


def _ordered_namespaces(catalog: CommandCatalog) -> list[str]:
    """收集 catalog 实际出现的命名空间，按固定顺序 + 字典序兜底排列。"""
    present = {command_id.split(".", 1)[0] for command_id in catalog}
    known = [name for name in NAMESPACE_ORDER if name in present]
    extra = sorted(present - set(NAMESPACE_ORDER))
    return known + extra


def collect_validation_issues(
    document: dict, catalog: CommandCatalog
) -> list[tuple[str, str | None]]:
    """编译校验当前流程文档，返回 (问题描述, 相关节点 id 或 None) 列表。

    三层检查（任一 schema 失败即短路）：
    1. Workflow schema 校验（pydantic）；
    2. 必填参数缺口：action 节点对照 manifest input_schema.required；
    3. 编译器静态检查（未知指令/引用/别名/能力集，与 devserver 同口径）。
    """
    from rpa_core.compiler.compiler import WorkflowCompileError, WorkflowCompiler
    from rpa_core.devserver.app import DEFAULT_CAPABILITIES
    from rpa_core.model.workflow import Workflow

    issues: list[tuple[str, str | None]] = []
    try:
        workflow = Workflow.model_validate(document)
    except Exception as exc:  # noqa: BLE001 - pydantic 错误直接透传给用户
        return [(f"schema 校验失败：{exc}", None)]

    def walk(node: dict) -> None:
        if node.get("type") == "action":
            command_id = node.get("command")
            if command_id not in catalog:
                issues.append((f"未知指令：{command_id}", node.get("id")))
            else:
                manifest = catalog[command_id]
                with_args = node.get("with") or {}
                missing = [
                    key
                    for key in manifest.input_schema.get("required", [])
                    if key not in with_args
                ]
                if missing:
                    issues.append(
                        (f"缺少必填参数：{'、'.join(missing)}", node.get("id"))
                    )
        for key in ("children", "then", "else", "catch"):
            for child in node.get(key) or []:
                walk(child)

    walk(document["root"])
    try:
        WorkflowCompiler(catalog).compile(workflow, set(DEFAULT_CAPABILITIES))
    except WorkflowCompileError as exc:
        issues.append((f"编译失败：{exc}", None))
    return issues


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


class _CaptureBridge(QObject):
    """桌面捕获子进程 → GUI 线程的结果桥（worker 线程 emit，Qt 排队投递）。"""

    finished = Signal(dict)


class MainWindow(QMainWindow):
    """编辑器主窗口：左指令树（真实 catalog）+ 中流程卡片画布 + 右参数表单。"""

    def __init__(self, catalog: CommandCatalog, workflow=None, flow_path=None,
                 workflows_root=None) -> None:
        super().__init__()
        self.catalog = catalog
        # flow_path 为 None 时编辑的是内置示例：首次保存走「另存为」
        self.flow_path: Path | None = Path(flow_path) if flow_path else None
        # 流程库（workflows 目录）：与 Web 编辑器同一存储（WorkflowDirStore），
        # 命名流程可保存进库、供运行控制使用；为 None 时退化为纯文件编辑。
        self._store = (
            WorkflowDirStore(Path(workflows_root)) if workflows_root else None
        )
        self._workflow_meta: dict = {}
        self._dirty = False
        self._loading = False  # 构建模型期间抑制结构变化信号，避免误置脏标记
        # 撤销/重做：快照式（整份 workflow 文档深拷贝），上限 50 步（对齐 Web）。
        # _last_doc 始终是「当前模型对应的文档」——结构变更经 model.mutated
        # 信号到达时变更已发生，入栈的正是它（变更前状态）。
        self._undo_stack: list[dict] = []
        self._redo_stack: list[dict] = []
        self._clipboard: dict | None = None
        self._last_doc: dict | None = None
        # 运行控制（切 E）：RunManager 子进程宿主，懒创建（需要流程库根目录）
        self._run_manager = None
        self._active_run_id: str | None = None
        self._run_timer = None
        self._run_dock_widget = None
        # 扩展通道状态徽标（ADR 0015）：端点存在即在线，无内嵌网关/端口
        self._ext_badge_timer = None
        self._ext_badge_result_timer = None
        self._ext_badge_result: tuple[bool, list] | None = None
        self._ext_badge_probe_running = False
        # 运行时悬浮窗（影刀式）：运行期间右下置顶，成功自动还原/失败停留
        self._run_float = None
        self._events_seen = 0
        self._cancel_requested = False
        self._restore_timer = None
        # 参数面板未应用的编辑（保存/运行/切换节点前自动提交）
        self._pending_apply = None
        # 进行中的元素捕获会话（混合捕获，窗口关闭时取消）
        self._capture_session = None
        self.setWindowTitle("RPA Core 编辑器")
        self.resize(1280, 800)

        self._build_toolbar()
        self._build_menu_bar()

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
        # 画布内查找（Ctrl+F）：匹配项与当前位置（M23 G2）
        self._canvas_search_matches: list = []
        self._canvas_search_pos = -1
        self._build_canvas_search_bar()
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

        # 扩展通道状态徽标（状态栏常驻）：在线 = 存在扩展 bridge 端点（ADR 0015，
        # 浏览器按需拉起 host，无 8765 常驻服务、无心跳窗口）。
        if self._store is not None:
            from PySide6.QtCore import QTimer

            self._ext_badge = QLabel("")
            self.statusBar().addPermanentWidget(self._ext_badge)
            self._ext_badge_timer = QTimer(self)
            self._ext_badge_timer.setInterval(5000)
            self._ext_badge_timer.timeout.connect(self._refresh_ext_badge)
            self._ext_badge_timer.start()
            self._refresh_ext_badge()

    def _refresh_ext_badge(self) -> None:
        """后台线程探测 bridge 端点在线状态；线程只写纯 Python 结果，绝不触碰 Qt 对象。

        结果由窗口自有的 QTimer（_drain_ext_badge）取回：计时器随窗口销毁而
        销毁，天然不存在「线程向已释放 QObject emit」的野指针竞态（此前用信号
        桥时，测试里窗口被回收后探测线程回传会直接段错误）。
        """
        if self._ext_badge_probe_running:
            return
        # 只在窗口可见时探测：隐藏窗口没有展示需求，同时避免测试（offscreen
        # 下窗口普遍不 show）里启动后台探测线程引发的生命周期竞态。
        if not self.isVisible():
            return
        self._ext_badge_probe_running = True

        def work() -> None:
            online, hosts = False, []
            try:
                from rpa_core.extension_exec import ExtensionExecClient

                status = ExtensionExecClient().status()
                online = bool(status.get("online"))
                hosts = status.get("hosts") or []
            except Exception:  # noqa: BLE001 - 探测失败即离线
                online, hosts = False, []
            self._ext_badge_result = (online, hosts)  # 纯数据，无 Qt 调用

        threading.Thread(target=work, daemon=True).start()
        if self._ext_badge_result_timer is None:
            from PySide6.QtCore import QTimer

            timer = QTimer(self)
            timer.setInterval(200)
            timer.timeout.connect(self._drain_ext_badge)
            self._ext_badge_result_timer = timer
        self._ext_badge_result_timer.start()

    def _drain_ext_badge(self) -> None:
        """UI 线程取回探测结果（由窗口自有计时器驱动）。"""
        result = self._ext_badge_result
        if result is None:
            return
        self._ext_badge_result = None
        self._ext_badge_probe_running = False
        if self._ext_badge_result_timer is not None:
            self._ext_badge_result_timer.stop()
        # 兜底存活校验：窗口/徽标若已释放，直接丢弃结果（绝不触碰悬空对象）
        import shiboken6

        if not shiboken6.isValid(self) or not shiboken6.isValid(self._ext_badge):
            return
        self._on_ext_badge(*result)

    def _on_ext_badge(self, online: bool, hosts: list) -> None:
        if online:
            joined = ", ".join(sorted({str(h) for h in hosts}))
            self._ext_badge.setText(f"插件通道：在线（{joined}）")
            self._ext_badge.setStyleSheet("color: #1a7f37;")
        else:
            self._ext_badge.setText(
                "插件通道：离线（浏览器指令不可用，详见「插件」）"
            )
            self._ext_badge.setStyleSheet("color: #cf222e;")

    def _build_toolbar(self) -> None:
        """顶部工具栏：新建 / 打开 / 保存 / 删除节点。"""
        toolbar = self.addToolBar("文件")
        toolbar.setMovable(False)

        new_action = QAction("新建", self)
        new_action.setShortcut("Ctrl+N")
        new_action.setToolTip("新建空白流程（Ctrl+N）")
        new_action.triggered.connect(self._new_action)
        toolbar.addAction(new_action)
        self._new_action_ref = new_action

        open_action = QAction("打开", self)
        open_action.setShortcut("Ctrl+O")
        open_action.setToolTip("打开 workflow.json（Ctrl+O）")
        open_action.triggered.connect(self._open_action)
        toolbar.addAction(open_action)
        self._open_action_ref = open_action

        # 流程库下拉：列出 workflows/ 下的命名流程，选中即打开（仅配置 store 时）
        if self._store is not None:
            self.flow_combo = QComboBox()
            self.flow_combo.setMinimumWidth(160)
            self.flow_combo.setToolTip("从流程库打开（workflows 目录下的命名流程）")
            self.flow_combo.activated.connect(self._on_flow_combo_activated)
            toolbar.addWidget(self.flow_combo)
            self._refresh_flow_combo()
        else:
            self.flow_combo = None

        save_action = QAction("保存", self)
        save_action.setShortcut("Ctrl+S")
        save_action.setToolTip("保存到 workflow.json（Ctrl+S）")
        save_action.triggered.connect(self._save_action)
        toolbar.addAction(save_action)
        self._save_action_ref = save_action

        # 撤销/重做/复制/粘贴：窗口级快捷键，焦点在文本控件时暂停（不吞编辑键）
        self.undo_action = QAction("撤销", self)
        self.undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        self.undo_action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        self.undo_action.triggered.connect(self._undo)
        toolbar.addAction(self.undo_action)

        self.redo_action = QAction("重做", self)
        self.redo_action.setShortcut(QKeySequence.StandardKey.Redo)
        self.redo_action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        self.redo_action.triggered.connect(self._redo)
        toolbar.addAction(self.redo_action)

        self.copy_action = QAction("复制", self)
        self.copy_action.setShortcut(QKeySequence.StandardKey.Copy)
        self.copy_action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        self.copy_action.setToolTip("复制画布选中节点子树（Ctrl+C）")
        self.copy_action.triggered.connect(self._copy_selected)
        toolbar.addAction(self.copy_action)

        self.paste_action = QAction("粘贴", self)
        self.paste_action.setShortcut(QKeySequence.StandardKey.Paste)
        self.paste_action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        self.paste_action.setToolTip("粘贴到当前选中位置所属分支末尾（Ctrl+V）")
        self.paste_action.triggered.connect(self._paste_clipboard)
        toolbar.addAction(self.paste_action)

        validate_action = QAction("校验", self)
        validate_action.setToolTip(
            "编译校验当前流程：schema、必填参数缺口、编译器静态检查"
        )
        validate_action.triggered.connect(self._validate_workflow)
        toolbar.addAction(validate_action)
        self._validate_action_ref = validate_action

        # 运行控制（ADR 0011 同款：子进程 run host，GUI 进程不含 runtime）
        self.run_action = QAction("运行", self)
        self.run_action.setToolTip("运行当前流程（须先保存到流程库）")
        self.run_action.triggered.connect(self._run_workflow)
        toolbar.addAction(self.run_action)

        self.cancel_run_action = QAction("取消运行", self)
        self.cancel_run_action.setEnabled(False)
        self.cancel_run_action.triggered.connect(self._cancel_run)
        toolbar.addAction(self.cancel_run_action)

        # 元素库 / 数据表格 / 指令清单 / 插件（dock 与对话框入口）
        elements_action = QAction("元素库", self)
        elements_action.setToolTip("当前流程的捕获元素资产（<流程>/elements/）")
        elements_action.triggered.connect(self._toggle_elements_dock)
        toolbar.addAction(elements_action)

        table_action = QAction("数据表格", self)
        table_action.setToolTip("当前流程的数据表格（<流程>/data/table.json）")
        table_action.triggered.connect(self._toggle_table_dock)
        toolbar.addAction(table_action)

        catalog_action = QAction("指令清单", self)
        catalog_action.triggered.connect(self._show_catalog_dialog)
        toolbar.addAction(catalog_action)

        extension_action = QAction("插件", self)
        extension_action.setToolTip("浏览器扩展状态与安装引导")
        extension_action.triggered.connect(self._show_extension_dialog)
        toolbar.addAction(extension_action)

        # 删除节点用窗口级快捷键：焦点在左树（双击添加后的自然状态）或画布
        # 时都能直接删；焦点在参数表单输入控件中时禁用，让 Delete 正常编辑文本。
        self.delete_action = QAction("删除节点", self)
        self.delete_action.setShortcut("Delete")
        self.delete_action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        self.delete_action.setToolTip("删除画布选中节点（Delete）")
        self.delete_action.triggered.connect(self._delete_selected_node)
        toolbar.addAction(self.delete_action)
        # 焦点进入文本编辑控件时需要让路的动作（删除/撤销/重做/复制/粘贴）
        self._edit_sensitive_actions = [
            self.delete_action,
            self.undo_action,
            self.redo_action,
            self.copy_action,
            self.paste_action,
        ]
        app = QApplication.instance()
        app.focusChanged.connect(self._on_focus_changed)

        # 画布内查找（M23 G2）：Ctrl+F 唤起，Enter 下一个匹配，Esc 关闭
        self.find_action = QAction("查找", self)
        self.find_action.setShortcut("Ctrl+F")
        self.find_action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        self.find_action.setToolTip("在画布中查找指令（Ctrl+F）")
        self.find_action.triggered.connect(self._show_canvas_search)
        toolbar.addAction(self.find_action)

    def _build_menu_bar(self) -> None:
        """菜单栏：文件/编辑/运行/帮助，复用工具栏 QAction。"""
        menu_bar = self.menuBar()

        # 文件
        file_menu = menu_bar.addMenu("文件")
        file_menu.addAction(self._new_action_ref)
        file_menu.addAction(self._open_action_ref)
        file_menu.addAction(self._save_action_ref)

        # 编辑
        edit_menu = menu_bar.addMenu("编辑")
        edit_menu.addAction(self.undo_action)
        edit_menu.addAction(self.redo_action)
        edit_menu.addSeparator()
        edit_menu.addAction(self.copy_action)
        edit_menu.addAction(self.paste_action)
        edit_menu.addAction(self.delete_action)
        edit_menu.addSeparator()
        edit_menu.addAction(self.find_action)
        edit_menu.addSeparator()
        self._toggle_variables_action = QAction("变量面板", self)
        self._toggle_variables_action.setCheckable(True)

        def _toggle_variables(on: bool) -> None:
            self._variables_dock().setVisible(on)
            if on:
                self._refresh_variables()

        self._toggle_variables_action.toggled.connect(_toggle_variables)
        edit_menu.addAction(self._toggle_variables_action)

        # 运行
        run_menu = menu_bar.addMenu("运行")
        run_menu.addAction(self.run_action)
        run_menu.addAction(self.cancel_run_action)
        run_menu.addSeparator()
        run_menu.addAction(self._validate_action_ref)

        # 帮助
        help_menu = menu_bar.addMenu("帮助")
        shortcuts_action = QAction("快捷键一览", self)
        shortcuts_action.triggered.connect(self._show_shortcuts_dialog)
        help_menu.addAction(shortcuts_action)

    def _show_shortcuts_dialog(self) -> None:
        """显示快捷键一览对话框。"""
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel

        dialog = QDialog(self)
        dialog.setWindowTitle("快捷键一览")
        dialog.setMinimumWidth(360)
        layout = QFormLayout(dialog)
        layout.setContentsMargins(16, 12, 16, 12)

        shortcuts = [
            ("Ctrl+N", "新建"),
            ("Ctrl+O", "打开"),
            ("Ctrl+S", "保存"),
            ("Ctrl+Z", "撤销"),
            ("Ctrl+Y", "重做"),
            ("Ctrl+C", "复制"),
            ("Ctrl+V", "粘贴"),
            ("Delete", "删除节点"),
            ("Ctrl+F", "画布查找"),
            ("F9", "捕获桌面元素"),
            ("Esc", "关闭搜索 / 取消捕获"),
        ]
        for key, desc in shortcuts:
            key_label = QLabel(key)
            key_label.setStyleSheet("font-family: Consolas, monospace; font-weight: bold;")
            layout.addRow(key_label, QLabel(desc))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.close)
        layout.addRow(buttons)
        dialog.exec()

    # ---- 画布内查找（M23 G2） ---------------------------------------------

    def _build_canvas_search_bar(self) -> None:
        """画布顶部查找条：默认隐藏，Ctrl+F 唤起；Enter 循环定位，Esc 关闭。"""
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(8, 4, 8, 0)
        self.canvas_search = QLineEdit()
        self.canvas_search.setPlaceholderText("查找指令（命令 id / 名称 / 参数 / 节点 id）…")
        self.canvas_search.setClearButtonEnabled(True)
        self.canvas_search.textChanged.connect(self._on_canvas_search_changed)
        self.canvas_search.returnPressed.connect(self._find_next_in_canvas)
        self.canvas_search.installEventFilter(self)
        layout.addWidget(self.canvas_search)
        bar.hide()
        self.canvas_search_bar = bar
        self.canvas_layout.addWidget(bar)

    def eventFilter(self, obj, event):  # noqa: N802 (Qt naming)
        """查找条 Esc：清空并隐藏，焦点还给画布。"""
        from PySide6.QtCore import QEvent

        if obj is getattr(self, "canvas_search", None) and (
            event.type() == QEvent.Type.KeyPress
        ):
            if event.key() == Qt.Key.Key_Escape:
                self.canvas_search.clear()
                self.canvas_search_bar.hide()
                if self.canvas_view is not None:
                    self.canvas_view.setFocus()
                return True
        return super().eventFilter(obj, event)

    def _show_canvas_search(self) -> None:
        """Ctrl+F：显示查找条并聚焦（已有内容则全选，便于替换）。"""
        if self.canvas_view is None:
            return
        self.canvas_search_bar.show()
        self.canvas_search.setFocus()
        self.canvas_search.selectAll()
        if self.canvas_search.text():
            self._on_canvas_search_changed(self.canvas_search.text())

    @staticmethod
    def _canvas_item_search_text(item) -> str:
        """节点可检索文本：卡片标题 + 节点 id/类型 + 命令 id + 参数摘要。"""
        from rpa_core.gui.flow_model import (
            ROLE_ARGS_SUMMARY,
            ROLE_COMMAND_ID,
            ROLE_NODE_ID,
            ROLE_NODE_TYPE,
        )

        parts = [
            str(item.text() or ""),
            str(item.data(ROLE_NODE_ID) or ""),
            str(item.data(ROLE_NODE_TYPE) or ""),
            str(item.data(ROLE_COMMAND_ID) or ""),
            str(item.data(ROLE_ARGS_SUMMARY) or ""),
        ]
        return " ".join(parts).lower()

    def _on_canvas_search_changed(self, text: str) -> None:
        """查询变化：重算匹配集并跳到第一个。"""
        query = (text or "").strip().lower()
        self._canvas_search_matches = []
        self._canvas_search_pos = -1
        if query and self.flow_model is not None:
            for item in self._iter_canvas_items():
                if query in self._canvas_item_search_text(item):
                    self._canvas_search_matches.append(item)
        if self._canvas_search_matches:
            self._find_next_in_canvas()
        elif query:
            self.statusBar().showMessage("画布中未找到匹配指令", 3000)

    def _iter_canvas_items(self) -> list:
        """全树前序遍历的真实节点（跳过结束行等纯结构行）。"""
        from rpa_core.gui.flow_model import ROLE_NODE_ID

        result: list = []
        if self.flow_model is None:
            return result

        def walk(item) -> None:
            if item.data(ROLE_NODE_ID):
                result.append(item)
            for row in range(item.rowCount()):
                walk(item.child(row))

        walk(self.flow_model.invisibleRootItem())
        return result

    def _find_next_in_canvas(self) -> None:
        """跳到下一个匹配：选中 + 展开祖先 + 滚动居中（先剔除已失效的 item）。"""
        import shiboken6

        matches = [
            item
            for item in self._canvas_search_matches
            if item is not None and shiboken6.isValid(item)
        ]
        if not matches:
            # 匹配项全被结构变更摘除：按当前查询重算，避免对死 item 操作
            self._invalidate_canvas_search()
            return
        self._canvas_search_matches = matches
        if not matches:
            return
        self._canvas_search_pos = (self._canvas_search_pos + 1) % len(matches)
        item = matches[self._canvas_search_pos]
        index = item.index()
        view = self.canvas_view
        parent = index.parent()
        while parent.isValid():
            view.expand(parent)
            parent = parent.parent()
        view.setCurrentIndex(index)
        view.scrollTo(index, view.ScrollHint.PositionAtCenter)
        total = len(matches)
        self.statusBar().showMessage(
            f"匹配 {self._canvas_search_pos + 1}/{total}（Enter 下一个，Esc 关闭）", 4000
        )

    # ---- 右键菜单（M23 G2） -----------------------------------------------

    def _wire_canvas_context_menu(self) -> None:
        """画布右键菜单：复制/粘贴/删除/添加「否则」（按上下文启用）。"""
        view = self.canvas_view
        view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        view.customContextMenuRequested.connect(self._canvas_context_menu)

    def _canvas_menu_state(self, index) -> dict:
        """右键菜单各项可用性（抽出以便测试，不弹菜单）。"""
        from rpa_core.gui.flow_model import (
            _ELSE_BRANCH_TYPE,
            ROLE_IS_VIRTUAL,
            ROLE_NODE_TYPE,
        )

        item = self.flow_model.itemFromIndex(index) if index.isValid() else None
        deletable = bool(
            item is not None
            and (
                not item.data(ROLE_IS_VIRTUAL)
                or item.data(ROLE_NODE_TYPE) == _ELSE_BRANCH_TYPE
            )
        )
        return {
            "copy": bool(item is not None and not item.data(ROLE_IS_VIRTUAL)),
            "paste": bool(self._clipboard),
            "delete": deletable,
            "add_else": self._nearest_if(item) is not None if item is not None else False,
        }

    def _canvas_context_menu(self, pos) -> None:
        from PySide6.QtWidgets import QMenu

        view = self.canvas_view
        index = view.indexAt(pos)
        if index.isValid():
            # 右键落在未选中项上 → 先选中它；落在选中集内 → 保留多选（便于批量操作）
            if index not in view.selectionModel().selectedIndexes():
                view.setCurrentIndex(index)
        state = self._canvas_menu_state(index)
        menu = QMenu(self)
        copy = menu.addAction("复制")
        copy.setEnabled(state["copy"])
        paste = menu.addAction("粘贴")
        paste.setEnabled(state["paste"])
        remove = menu.addAction("删除")
        remove.setEnabled(state["delete"])
        menu.addSeparator()
        add_else = menu.addAction("添加「否则」")
        add_else.setEnabled(state["add_else"])
        chosen = menu.exec(view.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen is copy:
            self._copy_selected()
        elif chosen is paste:
            self._paste_clipboard()
        elif chosen is remove:
            self._delete_selected_node()
        elif chosen is add_else:
            self._add_else_branch()

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

    # ---- 流程库（切 F：命名流程管理） ---------------------------------------
    def _refresh_flow_combo(self, select: str | None = None) -> None:
        """刷新流程库下拉；select 指定后选中该流程。"""
        if self.flow_combo is None:
            return
        self.flow_combo.blockSignals(True)
        self.flow_combo.clear()
        self.flow_combo.addItem("流程库…", None)
        for name in self._store.list():
            self.flow_combo.addItem(name, name)
        if select is not None:
            index = self.flow_combo.findData(select)
            if index >= 0:
                self.flow_combo.setCurrentIndex(index)
        self.flow_combo.blockSignals(False)

    def _on_flow_combo_activated(self, index: int) -> None:
        name = self.flow_combo.itemData(index)
        if name:
            self._open_named_flow(name)

    def _open_named_flow(self, name: str) -> None:
        """从流程库打开命名流程。"""
        from rpa_core.model.workflow import Workflow

        if not self._prompt_discard_changes():
            self._refresh_flow_combo(select=self._current_flow_name())
            return
        try:
            document = self._store.read(name)
            Workflow.model_validate(document)
        except Exception as exc:  # noqa: BLE001 - 给用户可读反馈
            QMessageBox.warning(self, "打开失败", f"无法打开流程 {name}：{exc}")
            self._refresh_flow_combo(select=self._current_flow_name())
            return
        self.flow_path = self._store.directory(name) / "workflow.json"
        self.set_workflow(document)
        self._refresh_flow_combo(select=name)
        self.statusBar().showMessage(f"已打开流程 {name}", 4000)

    def _current_flow_name(self) -> str | None:
        """当前流程在流程库中的名字（不在库中则 None）。"""
        if self._store is None or self.flow_path is None:
            return None
        try:
            if self.flow_path.parent.parent.resolve() != self._store.root.resolve():
                return None
            if self.flow_path.name != "workflow.json":
                return None
        except OSError:
            return None
        return self.flow_path.parent.name

    def _save_named_flow(self, name: str) -> Path | None:
        """保存为流程库中的命名流程；非法名/校验失败返回 None。"""
        from rpa_core.devserver.store import WorkflowStoreError

        document = self._build_document()
        if document is None:
            return None
        try:
            self._store.write(name, document)
        except WorkflowStoreError as exc:
            self.statusBar().showMessage(f"保存失败：{exc}", 6000)
            return None
        self.flow_path = self._store.directory(name) / "workflow.json"
        self._workflow_meta = {
            key: copy.deepcopy(value)
            for key, value in document.items()
            if key != "root"
        }
        self._set_dirty(False)
        self._refresh_flow_combo(select=name)
        return self.flow_path

    def _on_focus_changed(self, old, now) -> None:
        """焦点进入文本编辑控件时暂停 Delete 等动作，避免吞掉编辑键。"""
        editing = isinstance(
            now, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)
        ) or (isinstance(now, QComboBox) and now.isEditable())
        for action in self._edit_sensitive_actions:
            action.setEnabled(not editing)

    # ---- 撤销 / 重做 / 复制粘贴（切 B） -------------------------------------
    def _current_document(self) -> dict:
        """当前模型的完整 workflow 文档深拷贝（历史快照）。"""
        from rpa_core.gui.flow_model import model_to_workflow

        return copy.deepcopy(model_to_workflow(self.flow_model, self._workflow_meta))

    def _begin_edit(self) -> None:
        """变更前记账：把变更前文档（_last_doc）压入撤销栈。"""
        if self._last_doc is not None:
            self._undo_stack.append(self._last_doc)
            if len(self._undo_stack) > 50:
                self._undo_stack.pop(0)
            self._redo_stack.clear()

    def _end_edit(self) -> None:
        """变更后记账：刷新 _last_doc 为变更后文档。"""
        self._last_doc = self._current_document()

    def _on_model_mutated(self) -> None:
        """结构变更（插入/删除/拖拽）经 model.mutated 到达时变更已完成。"""
        if self._loading:
            return
        # 结构变更后，画布查找的匹配项可能已被摘除（Python 包装失效）——必须重算，
        # 否则 _find_next_in_canvas 会对死 item 调 index()/scrollTo，抛
        # "Internal C++ object already deleted" 或让视图卡住。
        self._invalidate_canvas_search()
        self._begin_edit()
        self._end_edit()

    def _invalidate_canvas_search(self) -> None:
        """清空并（若查找条有内容）重算画布查找匹配集。"""
        self._canvas_search_matches = []
        self._canvas_search_pos = -1
        search = getattr(self, "canvas_search", None)
        if search is not None and search.text():
            self._on_canvas_search_changed(search.text())

    def _undo(self) -> None:
        if not self._undo_stack:
            self.statusBar().showMessage("没有可撤销的操作", 3000)
            return
        self._redo_stack.append(self._current_document())
        doc = self._undo_stack.pop()
        self.set_workflow(doc, reset_history=False)
        self._set_dirty(True)
        self.statusBar().showMessage(
            f"已撤销（还可撤销 {len(self._undo_stack)} 步）", 3000
        )

    def _redo(self) -> None:
        if not self._redo_stack:
            self.statusBar().showMessage("没有可重做的操作", 3000)
            return
        self._undo_stack.append(self._current_document())
        doc = self._redo_stack.pop()
        self.set_workflow(doc, reset_history=False)
        self._set_dirty(True)
        self.statusBar().showMessage("已重做", 3000)

    def _copy_selected(self) -> None:
        """复制画布选中节点子树到内部剪贴板（虚拟行不可复制）。"""
        from rpa_core.gui.flow_model import ROLE_IS_VIRTUAL, subtree_to_ast

        current = self.canvas_view.currentIndex()
        if not current.isValid():
            return
        item = self.flow_model.itemFromIndex(current)
        if item.data(ROLE_IS_VIRTUAL):
            self.statusBar().showMessage("该行是结构标记，不能复制", 3000)
            return
        self._clipboard = subtree_to_ast(item)
        self.statusBar().showMessage(
            f"已复制节点 {self._clipboard.get('id')}（Ctrl+V 粘贴）", 3000
        )

    def _paste_clipboard(self) -> None:
        """粘贴剪贴板子树：全树 id 重映射，别名撞车自动改名。"""
        from rpa_core.gui.flow_model import clone_for_paste

        if not self._clipboard:
            self.statusBar().showMessage("剪贴板为空：先 Ctrl+C 复制一个节点", 3000)
            return
        node = clone_for_paste(self.flow_model, self._clipboard)
        current = self.canvas_view.currentIndex()
        target = (
            self.flow_model.itemFromIndex(current) if current.isValid() else None
        )
        # 插入期间屏蔽选中信号：插入会触发 currentChanged → 表单提交/重建重入改模型。
        # blockSignals 返回的是之前的阻塞状态，不能据此决定是否解除，须无条件成对恢复。
        selection = self.canvas_view.selectionModel()
        if selection is not None:
            selection.blockSignals(True)
        try:
            new_item = self.flow_model.insert_subtree(node, target)
        finally:
            if selection is not None:
                selection.blockSignals(False)
        self._select_new_item(new_item)
        self.statusBar().showMessage("已粘贴（未保存）", 3000)

    def set_workflow(self, workflow, *, reset_history: bool = True) -> None:
        """加载 Workflow（pydantic 或 dict）并重建画布；拖拽重排发生在该模型上。

        reset_history=False 供撤销/重做内部使用：重建画布但保留历史栈。
        """
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
        # 结构变更完成信号 → 撤销历史记账
        self.flow_model.mutated.connect(self._on_model_mutated)
        # 画布选中节点变化 → 右栏切换参数表单（每次重建 view 都需重新连接）
        self.canvas_view.selectionModel().currentChanged.connect(
            self._on_canvas_selection
        )
        self._wire_canvas_context_menu()
        if reset_history:
            self._undo_stack.clear()
            self._redo_stack.clear()
        self._last_doc = self._current_document()
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

    def _selected_canvas_items(self) -> list:
        """当前画布选中行对应的 item（按树序、去重）。"""
        view = self.canvas_view
        if view is None:
            return []
        items: list = []
        seen: set[int] = set()
        for index in view.selectionModel().selectedIndexes():
            if index.column() != 0:
                continue
            item = self.flow_model.itemFromIndex(index)
            if item is None or id(item) in seen:
                continue
            seen.add(id(item))
            items.append(item)
        return items

    @staticmethod
    def _is_item_ancestor(ancestor, item) -> bool:
        """ancestor 是否为 item 的祖先（不含自身）。"""
        current = item.parent()
        while current is not None:
            if current is ancestor:
                return True
            current = current.parent()
        return False

    def _delete_selected_node(self) -> None:
        """删除画布选中节点（支持多选批量）；根节点与虚拟分组受保护。

        删「否则」行等价于取消 else 分支（其下指令顺序不变，自然并入 then 段）。
        多选时：被删项的后代若也在选中集内，只删祖先（整棵子树随父行移除），
        避免对已摘下的 item 重复操作。
        """
        from rpa_core.gui.flow_model import (
            _ELSE_BRANCH_TYPE,
            ROLE_NODE_ID,
            ROLE_NODE_TYPE,
        )

        items = self._selected_canvas_items()
        if not items:
            return
        roots = [
            item
            for item in items
            if not any(
                other is not item and self._is_item_ancestor(other, item)
                for other in items
            )
        ]
        removed_ids: list[str] = []
        else_count = 0
        # 删除期间屏蔽选中信号：remove_item 的 takeRow 会在信号发射中途触发
        # currentChanged → 参数面板提交/重建 → 回头改模型，破坏 Qt 内部状态。
        # blockSignals 返回的是之前的阻塞状态，不能据此决定是否解除，须无条件成对恢复。
        selection = self.canvas_view.selectionModel()
        if selection is not None:
            selection.blockSignals(True)
        try:
            for item in roots:
                node_type = item.data(ROLE_NODE_TYPE)
                node_id = item.data(ROLE_NODE_ID)
                if not self.flow_model.remove_item(item):
                    continue
                if node_type == _ELSE_BRANCH_TYPE:
                    else_count += 1
                elif node_id:
                    removed_ids.append(str(node_id))
        finally:
            if selection is not None:
                selection.blockSignals(False)
        if not removed_ids and not else_count:
            return
        if len(roots) == 1 and else_count:
            message = "已删除否则分支（其下指令已并入如果分支）"
        elif len(roots) == 1:
            message = f"已删除节点 {removed_ids[0]}"
        else:
            message = f"已删除 {len(removed_ids)} 个节点"
            if else_count:
                message += f" 与 {else_count} 个否则分支"
        self.statusBar().showMessage(f"{message}（未保存）", 4000)
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

    # ---- 运行控制（切 E） ---------------------------------------------------
    def _get_run_manager(self):
        """懒创建 RunManager（子进程 run host；GUI 进程内不跑 runtime）。"""
        if self._run_manager is None:
            from rpa_core.devserver.runs import RunManager

            self._run_manager = RunManager(self._store.root)
        return self._run_manager

    def _run_dock(self):
        """懒创建底部运行面板（错误摘要 + 状态行 + 事件流 + 跳转按钮）。"""
        if self._run_dock_widget is None:
            from PySide6.QtWidgets import QDockWidget, QPushButton

            dock = QDockWidget("运行", self)
            dock.setObjectName("run-dock")
            body = QWidget()
            layout = QVBoxLayout(body)
            layout.setContentsMargins(8, 4, 8, 8)

            # 结构化错误摘要（对齐 Web renderRunError）
            self._run_error_widget = QWidget()
            err_layout = QVBoxLayout(self._run_error_widget)
            err_layout.setContentsMargins(8, 6, 8, 6)
            err_layout.setSpacing(2)
            self._run_error_code = QLabel()
            self._run_error_code.setStyleSheet("font-weight: bold; font-size: 13px;")
            self._run_error_node = QLabel()
            self._run_error_msg = QLabel()
            self._run_error_msg.setWordWrap(True)
            self._run_error_detail = QLabel()
            self._run_error_detail.setWordWrap(True)
            self._run_error_detail.setStyleSheet("color: #64707d; font-size: 12px;")
            err_layout.addWidget(self._run_error_code)
            err_layout.addWidget(self._run_error_node)
            err_layout.addWidget(self._run_error_msg)
            err_layout.addWidget(self._run_error_detail)
            self._run_error_widget.hide()

            self._run_status_label = QLabel("（尚未运行）")
            self._run_events_view = QPlainTextEdit()
            self._run_events_view.setReadOnly(True)
            self._run_events_view.setMaximumBlockCount(500)
            self._run_jump_button = QPushButton("跳转到失败节点")
            self._run_jump_button.setToolTip("在画布中定位并选中失败的节点")
            self._run_jump_button.clicked.connect(self._jump_to_failed_node)
            self._run_jump_button.hide()
            self._failed_node_id: str | None = None
            self._step_start_times: dict[str, float] = {}
            self._run_started_at: float | None = None
            layout.addWidget(self._run_error_widget)
            layout.addWidget(self._run_status_label)
            layout.addWidget(self._run_events_view, 1)
            layout.addWidget(self._run_jump_button)
            dock.setWidget(body)
            self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)
            dock.hide()
            self._run_dock_widget = dock
        return self._run_dock_widget

    def _run_workflow(self) -> None:
        """工具栏「运行」：要求流程已入流程库；脏改动先落盘（运行基于磁盘文件）。"""
        self._commit_pending_edits()  # 面板里未应用的编辑要随运行一起落盘
        if self._store is None:
            self.statusBar().showMessage("运行需要流程库（--workflows 参数）", 5000)
            return
        name = self._current_flow_name()
        if name is None:
            self.statusBar().showMessage("先用 Ctrl+S 把流程保存到流程库再运行", 5000)
            return
        if self._dirty and self._save_named_flow(name) is None:
            return  # 校验失败等原因未保存，不运行旧版本
        inputs: dict = {}
        declared = self._workflow_meta.get("inputs") or {}
        if declared:
            dialog = RunInputsDialog(declared, self)
            if dialog.exec() != RunInputsDialog.DialogCode.Accepted:
                return
            inputs = dialog.values()
        self._start_run(name, inputs)

    def _start_run(self, name: str, inputs: dict | None = None) -> str | None:
        """启动运行子进程并开始轮询；返回对外 run_id（启动失败返回 None）。"""
        try:
            handle = self._get_run_manager().start(name, inputs or None)
        except FileNotFoundError as exc:
            self.statusBar().showMessage(str(exc), 5000)
            return None
        self._active_run_id = handle["runId"]
        self._clear_run_states()
        self._events_seen = 0
        self._cancel_requested = False
        self.run_action.setEnabled(False)
        self.cancel_run_action.setEnabled(True)
        dock = self._run_dock()
        dock.show()
        self._run_status_label.setText(f"运行中…（{name}）")
        self._run_events_view.clear()
        self._run_jump_button.hide()
        self._failed_node_id = None
        self._run_error_widget.hide()
        self._step_start_times: dict[str, float] = {}
        self._run_started_at: float | None = None
        self._show_run_float()
        if self._run_timer is None:
            from PySide6.QtCore import QTimer

            self._run_timer = QTimer(self)
            self._run_timer.setInterval(800)
            self._run_timer.timeout.connect(self._poll_run)
        self._run_timer.start()
        return self._active_run_id

    def _schedule_restore(self) -> None:
        """成功后 2 秒自动还原主窗口。

        用挂在 self 上的 QTimer 而非静态 QTimer.singleShot：静态计时器没有
        属主，窗口销毁后仍可能触发，回调打到已释放对象上会直接段错误。
        """
        from PySide6.QtCore import QTimer

        if self._restore_timer is None:
            self._restore_timer = QTimer(self)
            self._restore_timer.setSingleShot(True)
            self._restore_timer.setInterval(2000)
            self._restore_timer.timeout.connect(self._restore_from_float)
        self._restore_timer.start()

    # ---- 运行时悬浮窗（影刀式） ----------------------------------------------
    def _show_run_float(self) -> None:
        """主窗口最小化 + 右下置顶浮窗（运行期间持续在场）。"""
        from rpa_core.gui.run_float import RunFloatWindow

        if self._run_float is None:
            self._run_float = RunFloatWindow()
            self._run_float.cancel_button.clicked.connect(self._cancel_run)
            self._run_float.restore_button.clicked.connect(self._restore_from_float)
        self._run_float.show_running("准备中…", 0)
        self._run_float.place_bottom_right()
        self._run_float.show()
        self.showMinimized()

    def _restore_from_float(self) -> None:
        """还原主窗口并隐藏浮窗（成功自动还原与手动还原共用，幂等）。"""
        if self._run_float is not None:
            self._run_float.hide()
        self.showNormal()
        # 运行期间用户在别的应用里 → 本进程是后台应用，需要显式抢前台
        # （macOS 会忽略后台应用的自激活请求，见 present_window 文档）。
        present_window(self)

    def _float_step_text(self, node_id: str) -> str:
        """节点 id → 画布卡片标题（悬浮窗「正在执行」行）。"""
        item = self.flow_model.find_by_id(node_id)
        title = item.text() if item is not None else ""
        return f"正在执行：{title or node_id}"

    def _node_title(self, node_id: str) -> str:
        """节点 id → 卡片标题（日志格式化用）。"""
        item = self.flow_model.find_by_id(node_id)
        return item.text() if item is not None else node_id

    def _format_event(self, event: dict) -> str:
        """把运行事件格式化为可读日志行（对齐 Web 事件展示 + 耗时/输出预览）。"""
        import time

        etype = event.get("type", "")
        node_id = event.get("node_id", "")
        payload = event.get("payload") or {}
        title = self._node_title(node_id) if node_id else node_id

        if etype == "stepStarted":
            self._step_start_times[node_id] = time.time()
            return f"▶ {title} 开始"

        if etype == "stepCompleted":
            start = self._step_start_times.pop(node_id, None)
            elapsed = time.time() - start if start else 0
            outputs = payload.get("outputs") or {}
            keys = ", ".join(outputs.keys()) if outputs else "—"
            return f"✓ {title} 完成 ({elapsed:.1f}s) — 输出: {keys}"

        if etype == "stepFailed":
            start = self._step_start_times.pop(node_id, None)
            elapsed = time.time() - start if start else 0
            err = payload.get("error") or {}
            code = err.get("code", "ERROR")
            msg = err.get("message", "")
            return f"✗ {title} 失败 ({elapsed:.1f}s) — {code}: {msg}"

        if etype == "stepRetried":
            attempt = payload.get("attempt", "?")
            next_attempt = payload.get("nextAttempt", "?")
            backoff = payload.get("backoffSeconds", "?")
            return f"↻ {title} 重试 #{attempt}→#{next_attempt}（{backoff}s 后）"

        if etype == "runStarted":
            self._run_started_at = time.time()
            return "▸ 运行开始"

        if etype == "runFinished":
            status_val = payload.get("status", "")
            if self._run_started_at is not None:
                total = time.time() - self._run_started_at
                return f"▸ 运行结束 ({status_val}) — 耗时 {total:.1f}s"
            return f"▸ 运行结束 ({status_val})"

        # 其他事件：保留原始 JSON
        return json.dumps(event, ensure_ascii=False)

    def _poll_run_events_live(self) -> None:
        """运行中增量读事件：喂悬浮窗 + 运行面板实时滚动 + 节点着色。"""
        events = self._run_manager.events(self._active_run_id)["events"]
        if not events:
            return
        new_events = events[self._events_seen:]
        self._events_seen = len(events)
        done = 0
        current_step: str | None = None
        for event in events:
            if event.get("type") == "stepCompleted":
                done += 1
            elif event.get("type") == "stepStarted":
                current_step = event.get("node_id")
        for event in new_events:
            self._run_events_view.appendPlainText(self._format_event(event))
        if self._run_float is not None and current_step is not None:
            self._run_float.show_running(self._float_step_text(current_step), done)
        self._apply_run_states(events)

    def _cancel_run(self) -> None:
        if not self._active_run_id or self._run_manager is None:
            return
        try:
            self._run_manager.cancel(self._active_run_id)
            self._cancel_requested = True
            self._run_status_label.setText("已请求取消…")
            if self._run_float is not None:
                self._run_float.show_running("正在取消…", 0)
        except KeyError:
            pass

    def _poll_run(self) -> None:
        """轮询运行状态（QTimer 驱动；测试可直接调用）；结束时落事件与状态着色。"""
        if not self._active_run_id or self._run_manager is None:
            return
        status = self._run_manager.status(self._active_run_id)
        if status["running"]:
            self._poll_run_events_live()
            return
        self._run_timer.stop()
        self.run_action.setEnabled(True)
        self.cancel_run_action.setEnabled(False)
        result = status.get("result")
        run_status = ""
        detail = ""
        if result is not None:
            run_status = result.get("status", "unknown")
            self._run_status_label.setText(f"完成：{run_status}")
            error = result.get("error")
            if error:
                detail = (
                    f"{error.get('code')} · 节点 {error.get('nodeId')}\n"
                    f"{error.get('message')}"
                )
                self._run_events_view.appendPlainText(f"失败：{detail}")
                self._show_error_summary(error)
                failed_id = error.get("nodeId")
                if failed_id:
                    self._failed_node_id = failed_id
                    self._run_jump_button.show()
        elif status.get("startupError"):
            startup = status["startupError"]
            run_status = "failed"
            self._run_status_label.setText(
                f"运行未能启动（退出码 {status.get('exitCode')}）"
            )
            if startup.get("message"):
                detail = startup["message"]
                self._run_events_view.appendPlainText(startup["message"])
                self._show_error_summary({
                    "code": "STARTUP_FAILED",
                    "message": startup["message"],
                })
        else:
            run_status = "unknown"
            self._run_status_label.setText("完成（无结果文件）")
        # 用户主动取消时子进程被终止（无结果文件/非零退出），终态如实显示「已取消」
        if self._cancel_requested and run_status != "succeeded":
            run_status = "cancelled"
            self._run_status_label.setText("已取消")
        events = self._run_manager.events(self._active_run_id)["events"]
        for event in events[self._events_seen:]:
            self._run_events_view.appendPlainText(self._format_event(event))
        self._events_seen = len(events)
        self._apply_run_states(events)
        # 悬浮窗终态：成功 2s 后自动还原主窗口；失败/取消停留等手动还原
        if self._run_float is not None:
            if run_status == "succeeded":
                self._run_float.show_result("succeeded")
                self._schedule_restore()
            elif run_status:
                self._run_float.show_result(run_status, detail)

    def _apply_run_states(self, events: list[dict]) -> None:
        """按事件流给画布节点着色（行号：蓝=运行中 绿=成功 红=失败）。"""
        from rpa_core.gui.flow_model import (
            ROLE_NODE_ID,
            ROLE_RUN_STATE,
            iter_real_nodes,
        )

        states: dict[str, str] = {}
        for event in events:
            node_id = event.get("node_id")
            if not node_id:
                continue
            if event.get("type") == "stepStarted":
                states[node_id] = "running"
            elif event.get("type") == "stepCompleted":
                states[node_id] = "succeeded"
            elif event.get("type") == "stepFailed":
                states[node_id] = "failed"
        for item in iter_real_nodes(self.flow_model):
            node_id = item.data(ROLE_NODE_ID)
            item.setData(states.get(node_id), ROLE_RUN_STATE)
        self.canvas_view.viewport().update()

    def _clear_run_states(self) -> None:
        from rpa_core.gui.flow_model import ROLE_RUN_STATE, iter_real_nodes

        for item in iter_real_nodes(self.flow_model):
            item.setData(None, ROLE_RUN_STATE)
        self.canvas_view.viewport().update()

    def _jump_to_failed_node(self) -> None:
        """在画布中定位并选中上次运行失败的节点。"""
        node_id = self._failed_node_id
        if not node_id:
            return
        item = self.flow_model.find_by_id(node_id)
        if item is None:
            self.statusBar().showMessage(f"找不到节点 {node_id}", 4000)
            return
        index = self.flow_model.indexFromItem(item)
        self.canvas_view.setCurrentIndex(index)
        self.canvas_view.scrollTo(index, self.canvas_view.ScrollHint.PositionAtCenter)
        self.statusBar().showMessage(f"已定位到失败节点 {node_id}", 4000)

    def _show_error_summary(self, error: dict) -> None:
        """填充结构化错误摘要（对齐 Web renderRunError）。"""
        code = error.get("code", "ERROR")
        node_id = error.get("nodeId", "")
        message = error.get("message", "")
        details = error.get("details") or {}

        self._run_error_code.setText(f"失败：{code}")
        color = "#cf222e"
        self._run_error_code.setStyleSheet(
            f"font-weight: bold; font-size: 13px; color: {color};"
        )
        if node_id:
            self._run_error_node.setText(f"节点：{node_id}")
            self._run_error_node.show()
        else:
            self._run_node_text = ""
            self._run_error_node.hide()
        self._run_error_msg.setText(message)

        # 补充详情（commandId / transport / reason）
        extra: list[str] = []
        if details.get("commandId"):
            extra.append(f"指令：{details['commandId']}")
        if details.get("transport"):
            extra.append(f"通道：{details['transport']}")
        if details.get("reason"):
            extra.append(f"原因：{details['reason']}")
        self._run_error_detail.setText("\n".join(extra) if extra else "")
        self._run_error_detail.setVisible(bool(extra))
        self._run_error_widget.show()

    # ---- 编译校验（切 D） ---------------------------------------------------
    def _validate_workflow(self, *, show_dialog: bool = True) -> bool:
        """编译校验当前流程；有问题时列出并定位到第一个问题节点。"""
        self._commit_pending_edits()  # 校验对象应包含面板里未应用的编辑
        issues = collect_validation_issues(self._current_document(), self.catalog)
        if not issues:
            self.statusBar().showMessage("✓ 校验通过", 5000)
            return True
        lines = []
        for message, node_id in issues:
            lines.append(f"节点 {node_id}：{message}" if node_id else message)
        self.statusBar().showMessage(f"校验发现 {len(issues)} 个问题", 5000)
        # 定位到第一个有节点归属的问题
        first_node = next((node_id for _msg, node_id in issues if node_id), None)
        if first_node:
            item = self.flow_model.find_by_id(first_node)
            if item is not None:
                self.canvas_view.setCurrentIndex(
                    self.flow_model.indexFromItem(item)
                )
        if show_dialog:
            QMessageBox.warning(
                self, "校验未通过", "发现以下问题：\n\n" + "\n".join(lines)
            )
        return False

    # ---- 元素库（切 G） -----------------------------------------------------
    def _toggle_elements_dock(self) -> None:
        dock = self._elements_dock()
        dock.show()  # 幂等；offscreen 下 isVisible 不可靠，不做取反切换
        self._refresh_elements()

    def _elements_dock(self):
        if getattr(self, "_elements_dock_widget", None) is None:
            from PySide6.QtWidgets import QDockWidget

            from rpa_core.gui.element_panel import ElementPanel, wire_element_panel

            panel = ElementPanel()
            wire_element_panel(
                panel,
                on_refresh=self._refresh_elements,
                on_capture=self._capture_element,
                on_verify=self._verify_element,
                on_insert=self._insert_element,
                on_delete=self._delete_element,
            )
            # 提示按真实平台能力改写：面板默认文案承诺「桌面走 UIA / 可用 F9」，
            # 而桌面捕获 agent 是 Windows-only（非 Windows 上该腿 start 即不可用）
            from rpa_core.capture import capture_click_label, desktop_capture_available

            if not desktop_capture_available():
                panel.capture_button.setToolTip(
                    f"网页元素捕获：移动鼠标框选，{capture_click_label()} 捕获，Esc 取消。"
                    "（桌面类元素捕获仅支持 Windows，本平台不可用）"
                )
            dock = QDockWidget("元素库", self)
            dock.setObjectName("elements-dock")
            dock.setWidget(panel)
            self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
            dock.hide()
            self._elements_dock_widget = dock
            self._element_panel = panel
        return self._elements_dock_widget

    def _element_store(self):
        """当前流程的元素资产存储；流程未入库时返回 None。"""
        name = self._current_flow_name()
        if self._store is None or name is None:
            return None
        from rpa_core.devserver.store import WorkflowStore

        return WorkflowStore(self._store.directory(name) / "elements", create=False)

    def _refresh_elements(self) -> None:
        self._elements_dock()  # 保证面板存在（直接调用路径可能先于 dock 创建）
        store = self._element_store()
        if store is None:
            self._element_panel.set_elements([])
            self._element_panel.hint_label.setText("先把流程保存到流程库，再管理元素")
            return
        entries = []
        for name in store.list():
            try:
                document = store.read(name)
            except Exception:  # noqa: BLE001 - 单个坏文件不拖垮列表
                document = {}
            from rpa_core.gui.element_panel import summarize_element

            entries.append({"name": name, "summary": summarize_element(document)})
        self._element_panel.set_elements(entries)
        self._element_panel.hint_label.setText(f"{len(entries)} 个元素")

    def _verify_element(self, name: str) -> None:
        from rpa_core.model.capture import (
            ElementDocumentError,
            selector_errors,
            validate_element_document,
        )

        store = self._element_store()
        if store is None:
            return
        try:
            element = validate_element_document(store.read(name))
            errors = selector_errors(element)
        except ElementDocumentError as exc:
            errors = [{"path": exc.path, "message": exc.message}]
        if errors:
            detail = "；".join(f"{e['path']}: {e['message']}" for e in errors[:3])
            self.statusBar().showMessage(f"元素 {name} 校验未通过：{detail}", 6000)
        else:
            self.statusBar().showMessage(f"元素 {name} 校验通过", 4000)

    def _delete_element(self, name: str) -> None:
        store = self._element_store()
        if store is None:
            return
        try:
            store.delete(name)
        except Exception as exc:  # noqa: BLE001
            self.statusBar().showMessage(f"删除失败：{exc}", 5000)
            return
        self._refresh_elements()
        self.statusBar().showMessage(f"已删除元素 {name}", 4000)

    def _insert_element(self, name: str) -> None:
        """把元素填入画布当前 action 节点的 selector/locator 参数。"""
        from rpa_core.gui.flow_model import (
            ROLE_ARGS_RAW,
            ROLE_ARGS_SUMMARY,
            ROLE_COMMAND_ID,
            ROLE_NODE_TYPE,
            summarize_args,
        )

        store = self._element_store()
        if store is None:
            return
        document = store.read(name)
        current = self.canvas_view.currentIndex()
        if not current.isValid() or current.data(ROLE_NODE_TYPE) != "action":
            self.statusBar().showMessage("先在画布选中一个指令节点", 4000)
            return
        manifest = self.catalog[current.data(ROLE_COMMAND_ID)]
        properties = manifest.input_schema.get("properties", {})
        kind = document.get("kind")
        selector = document.get("selector") or {}
        if kind == "browser" and "selector" in properties:
            key, value = "selector", selector.get("css", "")
        elif kind == "desktop" and "locator" in properties:
            key, value = "locator", selector.get("locator")
        else:
            self.statusBar().showMessage(
                f"该指令没有匹配 {kind} 元素的参数字段", 5000
            )
            return
        self._begin_edit()
        item = self.flow_model.itemFromIndex(current)
        holder = item.data(ROLE_ARGS_RAW)
        holder.args[key] = value
        if holder.raw is not None:
            holder.raw.setdefault("with", {})[key] = value
        item.setData(summarize_args(holder.args), ROLE_ARGS_SUMMARY)
        self._end_edit()
        self._set_dirty(True)
        # 重渲染表单让新值可见
        self._on_canvas_selection(current, current)
        self.statusBar().showMessage(f"已把元素 {name} 填入参数 {key}", 4000)

    # ---- 元素捕获（切 G1：单入口混合捕获） ---------------------------------
    def _capture_element(self) -> None:
        """混合捕获：网页正文走扩展、桌面走 UIA hover，先回传者胜。

        影刀式单入口——用户无需先判断目标是网页还是桌面。捕获期间主窗
        最小化（不遮挡目标），结束还原；插件离线时显式提示网页区域不可
        捕获（UIA 兜底已证伪，不静默捕获渲染层）。
        """
        if self._element_store() is None:
            self.statusBar().showMessage("先把流程保存到流程库，再捕获元素", 5000)
            return
        if self._capture_session is not None:
            self.statusBar().showMessage("已有捕获会话进行中（Esc 取消）", 4000)
            return
        # 延迟导入对齐 CLI（capture 包洁净无 pywinauto，但保持单一惯例）
        from rpa_core.capture import (
            DesktopCaptureSession,
            HybridCaptureSession,
            capture_click_label,
        )

        session = HybridCaptureSession(
            desktop_factory=DesktopCaptureSession,
            hover=True,
            timeout_seconds=90,
        )
        session.start()  # arm 扩展腿；桌面腿（agent 子进程）构造时已起
        desktop_offline = session.desktop_offline
        if session.extension_offline and desktop_offline:
            # 两条腿都不可用：没有「退化为仅桌面/仅网页」可言，直接收场。
            # 早期实现会弹「仍要继续仅桌面捕获吗」——在 macOS 上是个假选项
            # （桌面 agent 是 Windows-only），用户点了「是」也只会等到超时。
            session.close()
            self.statusBar().showMessage(
                "无法捕获：当前平台桌面捕获不可用（仅支持 Windows），"
                "且浏览器插件离线（工具栏「插件」按钮可查看安装引导）", 9000
            )
            return
        if session.extension_offline and not self._confirm_capture_offline():
            # 扩展腿离线时网页区域无法捕获（UIA 兜底已证伪）；状态栏提示在窗口最小化
            # 后不可见，必须显式确认——否则用户体验是「网页里怎么点都没反应」。
            session.close()
            self.statusBar().showMessage(
                "已取消捕获：浏览器插件离线（「插件」按钮可查看安装引导）", 6000
            )
            return
        self._capture_session = session
        click = capture_click_label()
        if desktop_offline:
            hint = f"捕获中：移动鼠标框选，{click} 或右键捕获，Esc 取消" \
                   "（桌面捕获仅支持 Windows，本平台只能捕获网页元素）"
        else:
            hint = f"捕获中：移动鼠标框选，{click} 或右键捕获（桌面也可用 F9），Esc 取消"
        if session.extension_offline:
            hint = "浏览器插件离线：网页区域无法捕获（桌面不受影响）。" + hint
        self.statusBar().showMessage(hint, 9000)
        self._capture_bridge = _CaptureBridge(self)
        self._capture_bridge.finished.connect(self._on_element_captured)
        self.showMinimized()  # 不遮挡捕获目标（ADR 0010 的原始动机）

        def work() -> None:
            try:
                result = session.pick(timeout_seconds=90)
            finally:
                session.close()
            # 捕获期间窗口可能已被关闭：先验桥对象存活再 emit，避免野指针
            import shiboken6

            if shiboken6.isValid(self._capture_bridge):
                self._capture_bridge.finished.emit(result)

        threading.Thread(target=work, daemon=True).start()

    def _confirm_capture_offline(self) -> bool:
        """扩展腿离线时的显式确认：继续（仅桌面捕获）还是取消去装插件。

        抽出为独立方法：offscreen 测试不能弹真 QMessageBox，由测试替换本方法。
        """
        choice = QMessageBox.warning(
            self,
            "浏览器插件离线",
            "未检测到浏览器插件连接：网页内捕获不可用（桌面捕获不受影响）。\n\n"
            "若目标是网页，请先用工具栏「插件」按钮把扩展装入目标浏览器"
            "（Chrome/Edge），或改用已装扩展的浏览器后重试。\n\n"
            "仍要继续仅桌面捕获吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return choice == QMessageBox.StandardButton.Yes

    def _on_element_captured(self, result: dict) -> None:
        """捕获结束：还原主窗口 → 命名 → 入库（ElementDescriptor 契约校验）。"""
        self._capture_session = None
        self.showNormal()
        # 捕获期间用户在浏览器里操作 → 本进程是后台应用，单纯 raise()/
        # activateWindow() 会被 macOS 忽略（见 present_window 文档）。
        present_window(self)
        if result.get("timeout"):
            self.statusBar().showMessage("捕获超时（90 秒无手势）", 5000)
            return
        if not result.get("kind") and (result.get("unavailable") or result.get("error")):
            # 无可用腿：透出真实原因。早期实现落到下面的「已取消捕获」分支，
            # 于是 macOS 上「桌面腿 49ms 返回不支持」被显示成用户主动取消，
            # 真相（本平台桌面捕获不可用）被完全掩盖。
            self.statusBar().showMessage(
                f"捕获失败：{result.get('error') or '无可用捕获通道'}", 9000
            )
            return
        if result.get("cancelled") or not result.get("kind"):
            self.statusBar().showMessage("已取消捕获", 4000)
            return
        confirmed = self._confirm_element_save(result)
        if confirmed is None:
            return
        name, document = confirmed
        saved = self.save_element_descriptor(name, document)
        if saved:
            self._refresh_elements()
            self.statusBar().showMessage(f"已保存元素 {name}", 4000)

    def _confirm_element_save(self, descriptor: dict) -> tuple[str, dict] | None:
        """捕获确认对话框（改名/selector 编辑/命中数）+ 同名覆盖保护。

        对齐 Web ``openElementDialog``（confirm 模式）+ 同名 confirm；返回
        ``(名称, ElementDescriptor 文档)``，用户取消返回 None。
        """
        from rpa_core.gui.element_panel import ElementDialog

        metadata = descriptor.get("metadata") or {}
        if descriptor.get("kind") == "browser":
            suffix = metadata.get("tag") or "web"
        else:
            suffix = metadata.get("controlType") or "x"
        dialog = ElementDialog(descriptor, default_name=f"el_{suffix}", parent=self)
        # 此刻用户在浏览器里刚完成捕获，我们是后台应用：不置顶的话对话框会
        # 停在浏览器后面，用户得先点一次 Dock 才看得见（真机实测）。
        present_window(dialog, always_on_top=True)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        name, document = dialog.result_document()
        store = self._element_store()
        if store is not None and name in store.list():
            answer = QMessageBox.question(
                self,
                "同名元素已存在",
                f"元素「{name}」已存在，覆盖？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return None
        return name, document

    def save_element_descriptor(self, name: str, descriptor: dict) -> bool:
        """元素入库（校验 ElementDescriptor 契约）；失败状态栏提示并返回 False。"""
        from rpa_core.model.capture import ElementDocumentError, validate_element_document

        try:
            document = validate_element_document(descriptor).document()
        except ElementDocumentError as exc:
            self.statusBar().showMessage(f"捕获结果不合法：{exc}", 6000)
            return False
        store = self._element_store()
        if store is None:
            return False
        try:
            store.write(name, document)
        except Exception as exc:  # noqa: BLE001
            self.statusBar().showMessage(f"保存失败：{exc}", 5000)
            return False
        return True

    # ---- 数据表格（切 H） ----------------------------------------------------
    def _toggle_table_dock(self) -> None:
        dock = self._table_dock()
        dock.show()
        self._refresh_table()

    def _table_dock(self):
        if getattr(self, "_table_dock_widget", None) is None:
            from PySide6.QtWidgets import QDockWidget

            from rpa_core.gui.table_panel import TablePanel

            panel = TablePanel()
            dock = QDockWidget("数据表格", self)
            dock.setObjectName("table-dock")
            body = QWidget()
            layout = QVBoxLayout(body)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(panel, 1)
            buttons = QHBoxLayout()
            save_button = QPushButton("保存表格")
            save_button.clicked.connect(self._save_table)
            export_button = QPushButton("导出 CSV")
            export_button.clicked.connect(self._export_table_csv)
            buttons.addWidget(save_button)
            buttons.addWidget(export_button)
            buttons.addStretch(1)
            layout.addLayout(buttons)
            dock.setWidget(body)
            self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)
            dock.hide()
            self._table_dock_widget = dock
            self._table_panel = panel
        return self._table_dock_widget

    def _variables_dock(self):
        """设计期变量面板：列出 output_aliases + 变量赋值，对齐 Web collectUserVariables。"""
        if getattr(self, "_variables_dock_widget", None) is None:
            from PySide6.QtWidgets import (
                QDockWidget,
                QHBoxLayout,
                QLabel,
                QPushButton,
                QTreeWidget,
                QVBoxLayout,
            )

            body = QWidget()
            layout = QVBoxLayout(body)
            layout.setContentsMargins(4, 4, 4, 4)

            header = QHBoxLayout()
            header.addWidget(QLabel("设计期变量"))
            header.addStretch(1)
            refresh_btn = QPushButton("刷新")
            refresh_btn.setFixedWidth(52)
            header.addWidget(refresh_btn)
            layout.addLayout(header)

            tree = QTreeWidget()
            tree.setHeaderHidden(True)
            tree.setRootIsDecorated(True)
            tree.setAlternatingRowColors(True)
            layout.addWidget(tree, 1)

            dock = QDockWidget("变量面板", self)
            dock.setObjectName("variables-dock")
            dock.setWidget(body)
            self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
            dock.hide()
            self._variables_dock_widget = dock
            self._variables_tree = tree
            refresh_btn.clicked.connect(self._refresh_variables)

        return self._variables_dock_widget

    def _refresh_variables(self) -> None:
        """收集并刷新变量面板内容。"""
        tree = getattr(self, "_variables_tree", None)
        if tree is None:
            return
        tree.clear()

        from PySide6.QtWidgets import QTreeWidgetItem

        from rpa_core.gui.flow_model import ROLE_ARGS_RAW, iter_real_nodes

        # 收集 output_aliases 和变量赋值
        aliases: dict[str, str] = {}  # name -> source_node_id
        var_writes: dict[str, str] = {}  # name -> source_node_id

        for item in iter_real_nodes(self.flow_model):
            holder = item.data(ROLE_ARGS_RAW)
            raw = holder.raw if holder is not None else None
            if not raw:
                continue
            node_id = raw.get("id", "?")
            # output_aliases: {"0": "varName", ...}
            for alias in (raw.get("output_aliases") or {}).values():
                if alias and alias not in aliases:
                    aliases[alias] = node_id
            # x-var-write: 通过 manifest 查找变量写入字段
            cmd_id = raw.get("command", "")
            try:
                manifest = self.catalog[cmd_id]
            except KeyError:
                manifest = None
            if manifest:
                var_write = manifest.x_var_write
                if var_write and var_write.get("field"):
                    target = (raw.get("with") or {}).get(var_write["field"])
                    if isinstance(target, str) and target and not target.startswith("${"):
                        if target not in var_writes:
                            var_writes[target] = node_id

        # 输出别名组
        if aliases:
            group = QTreeWidgetItem(tree, ["输出别名"])
            group.setExpanded(True)
            for name, nid in sorted(aliases.items()):
                child = QTreeWidgetItem(group, [f"{name}  ← {nid}"])
                child.setData(0, Qt.ItemDataRole.UserRole, name)
            tree.addTopLevelItem(group)

        # 变量赋值组
        if var_writes:
            group = QTreeWidgetItem(tree, ["变量赋值"])
            group.setExpanded(True)
            for name, nid in sorted(var_writes.items()):
                child = QTreeWidgetItem(group, [f"{name}  ← {nid}"])
                child.setData(0, Qt.ItemDataRole.UserRole, name)
            tree.addTopLevelItem(group)

        if not aliases and not var_writes:
            QTreeWidgetItem(tree, ["（暂无变量）"])

    def _table_store(self):
        if self._store is None:
            return None
        from rpa_core.devserver.store import TableStore

        return TableStore(self._store)

    def _refresh_table(self) -> None:
        self._table_dock()  # 保证面板存在
        store = self._table_store()
        name = self._current_flow_name()
        if store is None or name is None:
            self._table_panel.load({"columns": [], "rows": []})
            self._table_panel.hint_label.setText("先把流程保存到流程库，再编辑数据表格")
            return
        self._table_panel.load(store.read(name))

    def _save_table(self) -> None:
        store = self._table_store()
        name = self._current_flow_name()
        if store is None or name is None:
            self.statusBar().showMessage("先把流程保存到流程库，再保存数据表格", 5000)
            return
        store.write(name, self._table_panel.collect())
        self.statusBar().showMessage("数据表格已保存", 4000)

    def _export_table_csv(self) -> None:
        from rpa_core.workers.data_table import rows_to_csv

        document = self._table_panel.collect()
        chosen, _ = QFileDialog.getSaveFileName(
            self, "导出 CSV", "table.csv", "CSV 文件 (*.csv)"
        )
        if not chosen:
            return
        Path(chosen).write_bytes(
            rows_to_csv(document["columns"], document["rows"])
        )
        self.statusBar().showMessage(f"已导出 {chosen}", 4000)

    # ---- 指令清单 / 插件（切 I） ---------------------------------------------
    def _show_catalog_dialog(self) -> None:
        """只读指令清单对话框：分组树 + 选中命令的 manifest 摘要。"""
        dialog = QDialog(self)
        dialog.setWindowTitle(f"指令清单（{len(self.catalog)} 条）")
        dialog.resize(640, 520)
        layout = QVBoxLayout(dialog)
        tree = QTreeWidget()
        tree.setHeaderHidden(True)
        populate_command_tree(tree, self.catalog, load_command_display_names())
        detail = QPlainTextEdit()
        detail.setReadOnly(True)

        def show_detail(item, _column) -> None:
            command_id = item.data(0, ROLE_COMMAND_ID)
            if not command_id or command_id not in self.catalog:
                detail.clear()
                return
            manifest = self.catalog[command_id]
            detail.setPlainText(
                json.dumps(
                    {
                        "id": manifest.id,
                        "kind": manifest.kind.value,
                        "executor": manifest.executor,
                        "effect": manifest.effect.model_dump(mode="json"),
                        "input_schema": manifest.input_schema,
                        "output_schema": manifest.output_schema,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )

        tree.currentItemChanged.connect(show_detail)
        layout.addWidget(tree, 1)
        layout.addWidget(detail, 1)
        dialog.exec()

    def _extension_status_text(self) -> str:
        """各浏览器的扩展安装/启用状态文本（能力层本地探测，无需 devserver）。"""
        from rpa_core.extension_installer import (
            default_build_dir,
            extension_root,
            extension_status,
        )

        status = extension_status(
            "",
            default_build_dir(),
            extension_dir=extension_root(),
        )
        lines = []
        for browser, info in status["browsers"].items():
            binary = "已安装" if info.get("binary") else "未安装"
            if info.get("enabled"):
                plugin = "插件已启用"
            elif info.get("installed"):
                plugin = "插件已加载未启用"
            else:
                plugin = "插件未加载"
            if info.get("uninstallBlocked"):
                plugin += "（有卸载屏蔽记录）"
            lines.append(f"{browser}：浏览器{binary} · {plugin}")
        return "\n".join(lines)

    def _ext_hub_text(self) -> str:
        """bridge 通道当前状态文本（供插件对话框展示）。"""
        from rpa_core.extension_exec import ExtensionExecClient

        status = ExtensionExecClient().status()
        if status.get("online"):
            hosts = ", ".join(sorted({str(h) for h in status.get("hosts") or []}))
            return f"扩展通道：在线（{hosts}）——浏览器指令可用"
        return (
            "扩展通道：离线——请确认 bridge 已注册（下方「注册 bridge」按钮）"
            "且扩展已加载；离线时浏览器指令不可用"
        )

    def _native_host_status_text(self) -> str:
        """bridge host 注册状态（每浏览器一行，只读探测）。"""
        from rpa_core.extension_installer import native_host_status

        lines = []
        for browser in ("edge", "chrome"):
            info = native_host_status(browser)
            if info.get("registered"):
                ext_id = info.get("extensionId") or "?"
                lines.append(f"{browser}：bridge 已注册（扩展 ID {ext_id[:8]}…）")
            elif not info.get("hostExecutableExists"):
                lines.append(f"{browser}：bridge 未注册（缺 host 入口，先 uv sync）")
            else:
                lines.append(f"{browser}：bridge 未注册")
        return "\n".join(lines)

    def _register_bridge_hosts(self) -> str:
        """幂等注册双浏览器 bridge host（ensure_native_host 自愈）；返回逐浏览器结果。"""
        from rpa_core.extension_installer import (
            ExtensionInstallError,
            ensure_native_host,
        )

        lines = []
        for browser in ("edge", "chrome"):
            try:
                result = ensure_native_host(browser)
            except ExtensionInstallError as exc:
                lines.append(f"{browser}：注册失败（{exc}）")
            except Exception as exc:  # noqa: BLE001 - 对话框内尽力而为，不炸 GUI
                lines.append(f"{browser}：注册失败（{exc}）")
            else:
                lines.append(
                    f"{browser}：已注册（扩展 ID {result['extensionId'][:8]}…）"
                )
        return "\n".join(lines)

    def _show_extension_dialog(self) -> None:
        """插件状态 + Load unpacked 安装引导（对齐 Web 的 4 步引导）。"""
        from rpa_core.extension_installer import extension_root, open_browser, open_path_in_explorer

        dialog = QDialog(self)
        dialog.setWindowTitle("浏览器插件")
        layout = QVBoxLayout(dialog)
        status_label = QLabel(self._extension_status_text())
        layout.addWidget(status_label)
        host_label = QLabel(self._native_host_status_text())
        host_label.setWordWrap(True)
        layout.addWidget(host_label)
        hub_label = QLabel(self._ext_hub_text())
        hub_label.setWordWrap(True)
        hub_label.setStyleSheet("color: #9a6700;")
        layout.addWidget(hub_label)
        guide = QLabel(
            "安装步骤：\n"
            "0. 若上方显示 bridge 未注册：先点「注册 bridge」（通道注册，一次即可）\n"
            "1. 打开扩展源码目录（下面按钮）\n"
            "2. 打开浏览器，地址栏输入 chrome://extensions 或 edge://extensions\n"
            "3. 开启「开发人员模式」→「加载已解压的扩展程序」→ 选择该目录\n"
            "4. 回到本页刷新状态\n\n"
            "已启用但通道离线（状态栏徽标红色）时：到扩展管理页点「重新加载」，"
            "或整体退出浏览器（含托盘后台驻留）后重开——扩展后台 service worker "
            "卡死/休眠过久只能这样唤醒。"
        )
        guide.setWordWrap(True)
        layout.addWidget(guide)
        buttons = QHBoxLayout()
        register = QPushButton("注册 bridge")
        register.setToolTip(
            "向双浏览器注册 Native Messaging bridge host（幂等，可反复点）"
        )

        def refresh_dialog() -> None:
            status_label.setText(self._extension_status_text())
            host_label.setText(self._native_host_status_text())
            hub_label.setText(self._ext_hub_text())

        def register_and_refresh() -> None:
            result = self._register_bridge_hosts()
            self.statusBar().showMessage("bridge 注册完成", 4000)
            host_label.setText(
                f"{result}\n———\n{self._native_host_status_text()}"
            )
            hub_label.setText(self._ext_hub_text())

        register.clicked.connect(register_and_refresh)
        open_dir = QPushButton("打开扩展目录")
        open_dir.clicked.connect(
            lambda: open_path_in_explorer(extension_root())
        )
        open_edge = QPushButton("打开 Edge")
        open_edge.clicked.connect(lambda: open_browser("edge"))
        open_chrome = QPushButton("打开 Chrome")
        open_chrome.clicked.connect(lambda: open_browser("chrome"))
        refresh = QPushButton("刷新状态")
        refresh.clicked.connect(refresh_dialog)
        for button in (register, open_dir, open_edge, open_chrome, refresh):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        dialog.exec()

    # ---- 保存 -------------------------------------------------------------
    def _save_action(self) -> None:
        """工具栏保存：优先原位保存；无路径时走流程库命名保存 / 另存为。"""
        target = self.flow_path
        if target is None and self._store is not None:
            suggested = self._current_flow_name() or ""
            name, ok = QInputDialog.getText(
                self, "保存到流程库", "流程名（字母开头，可含数字 . _ -）：",
                text=suggested,
            )
            if not ok or not name.strip():
                return
            saved = self._save_named_flow(name.strip())
            if saved is not None:
                self.statusBar().showMessage(f"已保存 {saved}", 4000)
            return
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

    def _build_document(self) -> dict | None:
        """模型回写为 workflow dict 并校验；校验失败进状态栏，返回 None。"""
        from rpa_core.gui.flow_model import model_to_workflow
        from rpa_core.model.workflow import Workflow

        # 保存前自动提交参数面板未应用的编辑（否则用户改了参数点保存会静默丢失）
        self._commit_pending_edits()
        document = model_to_workflow(self.flow_model, self._workflow_meta)
        try:
            Workflow.model_validate(document)  # 结构非法则拒绝落盘
        except Exception as exc:  # noqa: BLE001 - 校验错误统一进状态栏，不崩溃
            self.statusBar().showMessage(f"校验失败，未保存：{exc}", 6000)
            return None
        return document

    def save_workflow(self, path: Path) -> Path | None:
        """把当前模型回写为 workflow dict，校验通过后落盘；失败返回 None。

        落盘格式与 devserver WorkflowDirStore 一致：UTF-8、indent=2、末尾换行。
        """
        document = self._build_document()
        if document is None:
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
            self._shutdown_run_manager()
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
            self._shutdown_run_manager()
            event.accept()
        else:
            self._save_action()
            # 另存为被取消或保存失败时不关闭，避免丢失修改
            if not self._dirty:
                self._shutdown_run_manager()
                event.accept()
            else:
                event.ignore()

    def _shutdown_run_manager(self) -> None:
        """窗口关闭时终止仍在运行的子进程（规则 11）。"""
        if self._run_timer is not None:
            self._run_timer.stop()
        if self._ext_badge_timer is not None:
            self._ext_badge_timer.stop()
        if self._ext_badge_result_timer is not None:
            self._ext_badge_result_timer.stop()
        if self._restore_timer is not None:
            self._restore_timer.stop()
        if self._run_manager is not None:
            self._run_manager.close()
            self._run_manager = None
        # 进行中的捕获会话一并取消（桌面 agent 子进程回收，规则 11）
        if self._capture_session is not None:
            try:
                self._capture_session.cancel()
            except Exception:  # noqa: BLE001 - 关闭路径尽力而为
                pass
            self._capture_session = None
        # 悬浮窗是无父顶层窗口（最小化主窗时不随隐），关闭主窗需显式带走
        if self._run_float is not None:
            self._run_float.close()
            self._run_float = None

    # ---- 右栏参数表单 -----------------------------------------------------
    def _clear_param_panel(self) -> None:
        """清空右栏内容（旧控件延迟销毁）并作废待提交的编辑登记。"""
        self._pending_apply = None
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

        # 结构变更进行中（拖拽/批量删除/插入）：选中变化是变更过程的一部分，
        # 此时提交表单或重建右栏会重入改模型，破坏 Qt 内部状态（实测致裸空行/崩溃）。
        if self.flow_model is not None and self.flow_model.mutating:
            from rpa_core.gui import debug_log

            if debug_log.ENABLED:
                debug_log.log("selection-skip(mutating)")
            return
        from rpa_core.gui import debug_log
        from rpa_core.gui.flow_model import ROLE_NODE_ID

        if debug_log.ENABLED:
            debug_log.log(
                "selection",
                current=current.data(ROLE_NODE_ID) if current.isValid() else None,
                previous=previous.data(ROLE_NODE_ID) if previous.isValid() else None,
            )
        # 选中变化后立刻重绘视口：mouseTracking 常开时，自绘卡片的选中高亮若等下一次
        # 鼠标移动才重绘，会出现「点击不选中、移开才选中」的观感。
        if self.canvas_view is not None:
            self.canvas_view.viewport().update()
        # 切换节点前先提交上一个面板未应用的编辑（Web 即改即生效，GUI 靠此对齐）。
        # 同一节点重渲染（如插入元素后刷新表单）不提交：表单持有的是变更前状态，
        # 提交会把程序性修改回灌覆盖。
        if current != previous:
            self._commit_pending_edits()

        if not current.isValid():
            self._show_param_placeholder("从画布选择指令节点以编辑参数")
            return
        node_type = current.data(ROLE_NODE_TYPE)
        if node_type in ("sequence", "if", "forEach", "try", "return"):
            holder = current.data(ROLE_ARGS_RAW)
            self._show_control_form(node_type, holder.raw if holder else {}, current)
            return
        if node_type != "action":
            hint = {
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
        item = self.flow_model.itemFromIndex(index)
        holder = item.data(role_args_raw)
        raw = holder.raw if holder is not None else None
        form = ParamForm(
            manifest.input_schema,
            args,
            expr_modes=(raw or {}).get("_exprModes"),
            variable_provider=self._collect_reference_paths,
            manifest=manifest,
            output_aliases=(raw or {}).get("output_aliases"),
            raw=raw,
        )

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
            self._begin_edit()
            item = self.flow_model.itemFromIndex(index)
            # 原地更新同一 holder（保留 raw 模板），并同步 raw["with"] 供回写
            holder = item.data(role_args_raw)
            holder.args = dict(values)
            if holder.raw is not None:
                holder.raw["with"] = dict(values)
                modes = form.expr_modes()
                if modes:
                    holder.raw["_exprModes"] = modes
                else:
                    holder.raw.pop("_exprModes", None)
                aliases = form.output_aliases()
                if aliases:
                    holder.raw["output_aliases"] = aliases
                else:
                    holder.raw.pop("output_aliases", None)
                rt = form.retry_timeout_values()
                if rt.get("timeout_seconds") is not None:
                    holder.raw["timeout_seconds"] = rt["timeout_seconds"]
                else:
                    holder.raw.pop("timeout_seconds", None)
                if rt.get("retry_count") is not None:
                    holder.raw["retry_count"] = rt["retry_count"]
                else:
                    holder.raw.pop("retry_count", None)
            item.setData(summarize_args(values), ROLE_ARGS_SUMMARY)
            self._end_edit()
            self._set_dirty(True)
            if getattr(self, "_variables_dock_widget", None) is not None:
                self._refresh_variables()
            self.statusBar().showMessage("参数已更新（未保存）", 4000)

        apply_button.clicked.connect(apply)
        self.param_layout.addWidget(apply_button)
        self._mount_pending_apply(
            apply,
            lambda: self._action_form_dirty(form, holder, raw),
            index,
        )

    @staticmethod
    def _action_form_dirty(form, holder, raw) -> bool:
        """参数表单相对模型是否有未应用的编辑（含 fx 模式、输出别名、超时/重试）。"""
        try:
            values = form.values()
        except ValueError:
            return True  # 非法输入也算待处理，交给 apply 报错
        if values != dict(holder.args):
            return True
        if form.expr_modes() != ((raw or {}).get("_exprModes") or {}):
            return True
        if form.output_aliases() != ((raw or {}).get("output_aliases") or {}):
            return True
        rt = form.retry_timeout_values()
        raw = raw or {}
        if rt.get("timeout_seconds") != raw.get("timeout_seconds"):
            return True
        if rt.get("retry_count") != raw.get("retry_count"):
            return True
        return False

    def _mount_pending_apply(self, apply_fn, dirty_fn, index) -> None:
        """登记当前参数面板的「应用」入口，供保存/运行/切换节点时自动提交。

        GUI 与 Web 的差异：Web 改字段即生效，GUI 需要点「应用参数」。不点就
        保存会静默丢掉修改（维护者实测报障）——因此在保存/运行/校验/切换
        节点前自动提交未应用的编辑（无改动则跳过，不产生撤销历史）。
        """
        self._pending_apply = (apply_fn, dirty_fn, index)

    def _commit_pending_edits(self) -> None:
        """把参数面板未应用的编辑落到模型；模型已重建/无改动/变更中则跳过。"""
        if self.flow_model is not None and self.flow_model.mutating:
            return
        pending = self._pending_apply
        if pending is None:
            return
        self._pending_apply = None
        apply_fn, dirty_fn, index = pending
        # 画布可能已被撤销/重做重建：索引不属于当前模型时丢弃陈旧面板
        try:
            if index.model() is not self.flow_model:
                return
        except RuntimeError:
            return
        if not dirty_fn():
            return
        apply_fn()

    def _collect_reference_paths(self) -> list[str]:
        """收集可引用的变量/路径（fx「＋变量」下拉内容）。

        口径对齐 Web computeReferencePaths 的常用子集：
        - 用户变量：output_aliases 值 + data.setVar 的字面量 varName；
        - inputs.<声明的入参名>；
        - steps.<节点id>.outputs.<输出字段>（按 manifest output_schema 展开）；
        - 内置作用域：loop.item / error.code / error.message。
        """
        from rpa_core.gui.flow_model import (
            ROLE_ARGS_RAW,
            ROLE_COMMAND_ID,
            iter_real_nodes,
        )

        paths: list[str] = []
        for name in (self._workflow_meta.get("inputs") or {}):
            paths.append(f"inputs.{name}")
        for item in iter_real_nodes(self.flow_model):
            holder = item.data(ROLE_ARGS_RAW)
            raw = holder.raw if holder is not None else None
            if not raw:
                continue
            for alias in (raw.get("output_aliases") or {}).values():
                paths.append(alias)
            if raw.get("command") == "data.setVar":
                var_name = (raw.get("with") or {}).get("varName")
                if isinstance(var_name, str) and var_name:
                    paths.append(var_name)
            command_id = item.data(ROLE_COMMAND_ID)
            node_id = raw.get("id")
            if command_id in self.catalog and node_id:
                outputs = (
                    self.catalog[command_id].output_schema.get("properties") or {}
                )
                for field in outputs:
                    paths.append(f"steps.{node_id}.outputs.{field}")
        paths.extend(["loop.item", "error.code", "error.message"])
        # 去重保序
        return list(dict.fromkeys(paths))

    def _show_control_form(self, node_type: str, raw: dict, index) -> None:
        """控制流节点（sequence/if/forEach/try/return）的结构参数表单。

        直接编辑 AST 结构字段（condition/items/item_var/error_var/value），
        应用后同步 raw 与画布卡片标题。sequence 无可编辑字段，只显示说明。
        """
        from rpa_core.gui.flow_model import (
            ROLE_ARGS_RAW,
            ROLE_ARGS_SUMMARY,
            control_node_title,
            repr_json,
        )
        from rpa_core.gui.param_form import ControlNodeForm

        self._clear_param_panel()
        form = ControlNodeForm(node_type, raw)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(form)
        self.param_layout.addWidget(scroll, 1)

        if node_type == "sequence":
            return  # 顺序容器无参数

        apply_button = QPushButton("应用参数")

        def apply() -> None:
            try:
                updates = form.apply_values()
            except ValueError as exc:
                self.statusBar().showMessage(str(exc), 4000)
                return
            self._begin_edit()
            item = self.flow_model.itemFromIndex(index)
            holder = item.data(ROLE_ARGS_RAW)
            if holder is None or holder.raw is None:
                self._undo_stack.pop()  # 无变更可回收刚才的历史记账
                return
            holder.raw.update(updates)
            if node_type == "return":
                value = holder.raw.get("value")
                item.setData("" if value is None else repr_json(value),
                             ROLE_ARGS_SUMMARY)
            else:
                item.setText(control_node_title(node_type, holder.raw))
            self._end_edit()
            self._set_dirty(True)
            if getattr(self, "_variables_dock_widget", None) is not None:
                self._refresh_variables()
            self.statusBar().showMessage("参数已更新（未保存）", 4000)

        apply_button.clicked.connect(apply)
        self.param_layout.addWidget(apply_button)

        def control_dirty() -> bool:
            try:
                updates = form.apply_values()
            except ValueError:
                return True  # 非法输入也算待处理，交给 apply 报错
            current_item = self.flow_model.itemFromIndex(index)
            holder = (
                current_item.data(ROLE_ARGS_RAW) if current_item is not None else None
            )
            if holder is None or holder.raw is None:
                return False
            return any(holder.raw.get(key) != value for key, value in updates.items())

        self._mount_pending_apply(apply, control_dirty, index)


class RunInputsDialog(QDialog):
    """运行参数对话框：流程声明 inputs 时逐参数覆盖（值为 JSON 文本）。

    空文本 = 保持流程里声明的默认值；非法 JSON 在 values() 抛 ValueError。
    """

    def __init__(self, inputs: dict, parent: QWidget | None = None) -> None:
        from PySide6.QtWidgets import QDialogButtonBox, QFormLayout

        super().__init__(parent)
        self.setWindowTitle("运行参数")
        self._edits: dict[str, QLineEdit] = {}
        layout = QVBoxLayout(self)
        form = QFormLayout()
        for name, default in inputs.items():
            edit = QLineEdit()
            if default is not None:
                edit.setPlaceholderText(f"默认：{json.dumps(default, ensure_ascii=False)}")
            self._edits[name] = edit
            form.addRow(QLabel(name), edit)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> dict:
        """收集覆盖值：只含用户实际填写的参数（空 = 沿用默认）。"""
        result: dict = {}
        for name, edit in self._edits.items():
            text = edit.text().strip()
            if not text:
                continue
            try:
                result[name] = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"参数 {name} 不是合法 JSON：{exc}") from exc
        return result


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
                "condition": {"op": "truthy",
                              "left": "${steps.open.outputs.sessionId}"},
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
                "items": ["第一行", "第二行"],
                "item_var": "row",
                "children": [
                    {"type": "action", "id": "append", "command": "data.appendText",
                     "with": {"workspace": ".", "path": "out.txt",
                              "text": "${loop.row}"}},
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
    catalog: CommandCatalog, workflow=None, flow_path=None, workflows_root=None
) -> MainWindow:
    """构建主窗口但不进入事件循环（供 headless 冒烟测试调用）。"""
    return MainWindow(
        catalog, workflow=workflow, flow_path=flow_path,
        workflows_root=workflows_root,
    )


def run_gui(
    commands_root: Path,
    flow_path: Path | None = None,
    workflows_root: Path | None = None,
) -> int:
    """GUI 启动入口：加载真实 catalog（可选 workflow）→ 构建窗口 → 进入事件循环。"""
    from rpa_core.model.workflow import Workflow

    app = build_application()
    catalog = load_catalog(Path(commands_root))
    workflow = (
        Workflow.model_validate_json(Path(flow_path).read_text(encoding="utf-8"))
        if flow_path else None
    )
    window = MainWindow(
        catalog, workflow=workflow, flow_path=flow_path,
        workflows_root=workflows_root,
    )
    window.show()
    return app.exec()
