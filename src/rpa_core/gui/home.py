"""工作台（首页）：流程列表 + 运行历史 + 管理入口 + 运行入口（ADR 0017 / M27）。

两段式宿主的第一段：这里只做「管理」——列出流程（最近运行状态/时间、元素数、修改时间）、
新建/打开/（后续切片：复制/重命名/删除/导入导出）、以及发起运行并看状态。

**运行控制（暂停/继续/单步）不在这里**：ADR 0017 决策 3 明确控制权留在编辑器，工作台只
「运行 + 看状态」，避免两处都能控制同一个 run。

后端零新增：流程来自 `WorkflowDirStore`，运行状态来自 `rpa_core.run_history`（M25）。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from rpa_core.devserver.store import WorkflowDirStore
from rpa_core.run_history import list_runs, purge_runs

_STATUS_LABELS = {
    "succeeded": "成功",
    "failed": "失败",
    "cancelled": "已取消",
    "paused": "已暂停",
    "recovery_required": "待确认",
    "indeterminate": "结果不确定",
    "running": "运行中",
}

# 打开编辑器的注入点：name 为目标流程，run_id 非空时编辑器还要载入该次历史运行的时间线
OpenEditor = Callable[..., None]


class HomeWindow(QMainWindow):
    """工作台窗口：流程库 + 运行历史两个页签。

    `open_editor` 是打开编辑器的注入点（默认由 `app.open_editor_window` 提供）：
    测试里可替换成记录调用的桩，避免真的再开一个窗口。
    """

    def __init__(
        self,
        store: WorkflowDirStore,
        catalog: Any,
        *,
        open_editor: OpenEditor | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._catalog = catalog
        self._open_editor_hook = open_editor
        self._flows: list[dict[str, Any]] = []
        self._runs: list[dict[str, Any]] = []

        self.setWindowTitle("RPA Core 工作台")
        self.resize(1000, 620)

        tabs = QTabWidget()
        tabs.addTab(self._build_flows_tab(), "流程库")
        tabs.addTab(self._build_history_tab(), "运行历史")
        self.tabs = tabs
        self.setCentralWidget(tabs)

        self.refresh_flows()

    # ---- 流程库页签 -------------------------------------------------------
    def _build_flows_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        header = QLabel("流程库")
        header.setStyleSheet("font-weight: bold; font-size: 14px;")
        layout.addWidget(header)

        buttons = QHBoxLayout()
        self.new_button = QPushButton("新建流程")
        self.new_button.clicked.connect(self._create_flow)
        self.open_button = QPushButton("打开")
        self.open_button.clicked.connect(self._open_selected)
        self.copy_button = QPushButton("复制")
        self.copy_button.clicked.connect(self._copy_flow)
        self.rename_button = QPushButton("重命名")
        self.rename_button.clicked.connect(self._rename_flow)
        self.delete_button = QPushButton("删除")
        self.delete_button.clicked.connect(self._delete_flow)
        self.import_button = QPushButton("导入")
        self.import_button.setToolTip("从 workflow.json 或流程目录导入")
        self.import_button.clicked.connect(self._import_flow)
        self.export_button = QPushButton("导出")
        self.export_button.setToolTip("把流程（含元素/数据表格）导出到指定目录")
        self.export_button.clicked.connect(self._export_flow)
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.clicked.connect(self.refresh_flows)
        for widget in (
            self.new_button, self.open_button, self.copy_button, self.rename_button,
            self.delete_button, self.import_button, self.export_button,
            self.refresh_button,
        ):
            buttons.addWidget(widget)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["流程", "最近运行", "最近运行时间", "元素数", "修改时间"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.itemDoubleClicked.connect(lambda *_: self._open_selected())
        layout.addWidget(self.table, 1)

        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: #57606a;")
        layout.addWidget(self.hint)
        return page

    # ---- 运行历史页签（M27 S2：全局视图 + 按流程筛选） ---------------------
    def _build_history_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        header = QLabel("运行历史（全部流程）")
        header.setStyleSheet("font-weight: bold; font-size: 14px;")
        layout.addWidget(header)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("流程："))
        self.history_filter = QComboBox()
        self.history_filter.setMinimumWidth(200)
        self.history_filter.currentIndexChanged.connect(lambda *_: self._render_runs())
        controls.addWidget(self.history_filter)
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.refresh_history)
        controls.addWidget(refresh)
        open_button = QPushButton("打开时间线")
        open_button.clicked.connect(self._open_selected_run)
        controls.addWidget(open_button)
        controls.addStretch(1)
        layout.addLayout(controls)

        self.history_table = QTableWidget(0, 5)
        self.history_table.setHorizontalHeaderLabels(
            ["时间", "流程", "状态", "耗时", "错误"]
        )
        self.history_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.history_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.history_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.history_table.verticalHeader().setVisible(False)
        self.history_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.history_table.itemDoubleClicked.connect(lambda *_: self._open_selected_run())
        layout.addWidget(self.history_table, 1)

        self.history_hint = QLabel("")
        self.history_hint.setWordWrap(True)
        self.history_hint.setStyleSheet("color: #57606a;")
        layout.addWidget(self.history_hint)
        return page

    # ---- 数据 -------------------------------------------------------------
    def _artifacts_root(self) -> Path:
        return self._store.root.parent / "run_artifacts"

    def refresh_flows(self) -> None:
        """重扫流程库 + 运行历史，填表（最近运行时间倒序，未运行过的排后面）。"""
        latest: dict[str, dict[str, Any]] = {}
        for run in list_runs(self._artifacts_root(), limit=0):
            workflow_id = run.get("workflowId")
            if workflow_id and workflow_id not in latest:
                latest[workflow_id] = run  # list_runs 已按时间倒序，首次即最新

        self._flows = []
        for name in self._store.list():
            try:
                document = self._store.read(name)
            except Exception:  # noqa: BLE001 - 单个流程损坏不影响列表
                continue
            flow_id = document.get("id") or name
            run = latest.get(flow_id) or {}
            self._flows.append(
                {
                    "name": name,
                    "workflowId": flow_id,
                    "status": run.get("status"),
                    "lastRunAt": run.get("endedAt") or run.get("startedAt"),
                    "elements": self._element_count(name),
                    "mtime": self._mtime(name),
                }
            )
        self._flows.sort(
            key=lambda item: (item["lastRunAt"] or "", item["mtime"] or 0), reverse=True
        )
        self._render()
        self.refresh_history()

    def _element_count(self, name: str) -> int:
        elements_dir = self._store.root / name / "elements"
        try:
            return sum(1 for path in elements_dir.iterdir() if path.is_file())
        except OSError:
            return 0

    def _mtime(self, name: str) -> float:
        try:
            return (self._store.root / name / "workflow.json").stat().st_mtime
        except OSError:
            return 0.0

    def _render(self) -> None:
        self.table.setRowCount(len(self._flows))
        for row, flow in enumerate(self._flows):
            status = flow.get("status")
            values = [
                flow["name"],
                _STATUS_LABELS.get(status, "未运行" if not status else str(status)),
                self._format_time(flow.get("lastRunAt")),
                str(flow.get("elements") or 0),
                self._format_time_from_epoch(flow.get("mtime")),
            ]
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
        if self._flows:
            self.hint.setText(f"共 {len(self._flows)} 个流程；双击打开编辑器。")
        else:
            self.hint.setText(
                f"流程库还是空的（{self._store.root}）；点「新建流程」创建第一个流程。"
            )

    def refresh_history(self) -> None:
        """重扫运行历史（全局）并刷新流程筛选下拉（保留当前选择）。"""
        self._runs = list_runs(self._artifacts_root(), limit=0)
        current = self.history_filter.currentData() if self.history_filter.count() else None
        self.history_filter.blockSignals(True)
        self.history_filter.clear()
        self.history_filter.addItem("全部流程", None)
        for flow in self._flows:
            self.history_filter.addItem(flow["name"], flow["workflowId"])
        index = self.history_filter.findData(current)
        self.history_filter.setCurrentIndex(index if index >= 0 else 0)
        self.history_filter.blockSignals(False)
        self._render_runs()

    def _run_flow_name(self, run: dict[str, Any]) -> str:
        workflow_id = run.get("workflowId")
        for flow in self._flows:
            if flow["workflowId"] == workflow_id:
                return flow["name"]
        return str(workflow_id or "—")

    def _visible_runs(self) -> list[dict[str, Any]]:
        selected = self.history_filter.currentData() if self.history_filter.count() else None
        if not selected:
            return self._runs
        return [run for run in self._runs if run.get("workflowId") == selected]

    def _render_runs(self) -> None:
        runs = self._visible_runs()
        self.history_table.setRowCount(len(runs))
        for row, run in enumerate(runs):
            duration = run.get("durationMs")
            values = [
                self._format_time(run.get("endedAt") or run.get("startedAt")),
                self._run_flow_name(run),
                _STATUS_LABELS.get(run.get("status"), str(run.get("status") or "unknown")),
                f"{duration / 1000:.1f}s" if isinstance(duration, int) else "—",
                str(run.get("errorCode") or ""),
            ]
            for column, value in enumerate(values):
                self.history_table.setItem(row, column, QTableWidgetItem(value))
        if runs:
            self.history_hint.setText(
                f"共 {len(runs)} 条运行记录；双击在编辑器里打开该流程并查看时间线。"
            )
        else:
            self.history_hint.setText("暂无运行记录（运行一次流程后会出现在这里）。")

    @staticmethod
    def _format_time(value) -> str:
        if not isinstance(value, str) or not value:
            return "—"
        return value.replace("T", " ")[:19]

    @staticmethod
    def _format_time_from_epoch(value) -> str:
        from datetime import datetime

        if not value:
            return "—"
        return datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M:%S")

    # ---- 动作 -------------------------------------------------------------
    def _selected_flow(self) -> dict[str, Any] | None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._flows):
            return None
        return self._flows[row]

    def _selected_run(self) -> dict[str, Any] | None:
        row = self.history_table.currentRow()
        runs = self._visible_runs()
        if row < 0 or row >= len(runs):
            return None
        return runs[row]

    def _open_selected(self) -> None:
        flow = self._selected_flow()
        if flow is None:
            self.hint.setText("先在上表选中一个流程。")
            return
        self.open_flow(flow["name"])

    def _open_selected_run(self) -> None:
        """双击/打开时间线：在编辑器里打开对应流程并载入该次运行的时间线。"""
        run = self._selected_run()
        if run is None:
            self.history_hint.setText("先在上表选中一条运行记录。")
            return
        workflow_id = run.get("workflowId")
        name = next(
            (flow["name"] for flow in self._flows if flow["workflowId"] == workflow_id),
            None,
        )
        if name is None:
            self.history_hint.setText(
                f"流程库里找不到该运行对应的流程（{workflow_id}）"
            )
            return
        self.open_flow(name, run_id=run["runId"])

    def open_flow(self, name: str, *, run_id: str | None = None) -> None:
        """打开编辑器（由注入的 hook 决定实现；缺省走 GUI 的编辑器打开函数）。"""
        if self._open_editor_hook is None:
            from rpa_core.gui.app import open_editor_window

            self._open_editor_hook = open_editor_window
        self._open_editor_hook(name, run_id=run_id)

    def _create_flow(self) -> None:
        """新建流程：命名（复用 store 的名称校验）→ 建空流程 → 打开编辑器。"""
        name, accepted = QInputDialog.getText(self, "新建流程", "流程名称：")
        if not accepted:
            return
        name = (name or "").strip()
        if not name:
            QMessageBox.warning(self, "新建流程", "流程名称不能为空。")
            return
        document = {
            "schema_version": "1.0",
            "id": name,
            "name": name,
            "inputs": {},
            "root": {"type": "sequence", "id": "root", "children": []},
        }
        try:
            self._store.write(name, document)
        except Exception as exc:  # noqa: BLE001 - 名称非法/写入失败都要看得见
            QMessageBox.warning(self, "新建流程", f"创建失败：{exc}")
            return
        self.refresh_flows()
        self.open_flow(name)

    # ---- 管理动作（M27 S3） ------------------------------------------------
    def _copy_flow(self) -> None:
        flow = self._selected_flow()
        if flow is None:
            self.hint.setText("先在上表选中一个流程。")
            return
        new_name, accepted = QInputDialog.getText(
            self, "复制流程", "新流程名称：", text=f"{flow['name']}_copy"
        )
        if not accepted:
            return
        try:
            self._store.copy_flow(flow["name"], (new_name or "").strip())
        except Exception as exc:  # noqa: BLE001 - 非法名/重名都要看得见
            QMessageBox.warning(self, "复制流程", f"复制失败：{exc}")
            return
        self.refresh_flows()
        self.hint.setText(f"已复制为 {(new_name or '').strip()}。")

    def _rename_flow(self) -> None:
        flow = self._selected_flow()
        if flow is None:
            self.hint.setText("先在上表选中一个流程。")
            return
        if self._is_editing(flow["name"]):
            QMessageBox.warning(
                self, "重命名流程",
                f"流程 {flow['name']} 正在编辑器里打开；请先在编辑器中保存并关闭/切换后再重命名。",
            )
            return
        new_name, accepted = QInputDialog.getText(
            self, "重命名流程", "新名称：", text=flow["name"]
        )
        if not accepted:
            return
        try:
            self._store.rename_flow(flow["name"], (new_name or "").strip())
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "重命名流程", f"重命名失败：{exc}")
            return
        self.refresh_flows()
        self.hint.setText(f"已重命名为 {(new_name or '').strip()}。")

    def _delete_flow(self) -> None:
        """删除流程：先列出连带范围（元素/数据表格/运行历史），确认后才整目录删除。"""
        flow = self._selected_flow()
        if flow is None:
            self.hint.setText("先在上表选中一个流程。")
            return
        if self._is_editing(flow["name"]):
            QMessageBox.warning(
                self, "删除流程",
                f"流程 {flow['name']} 正在编辑器里打开；请先保存并关闭/切换后再删除。",
            )
            return
        runs = [run for run in self._runs if run.get("workflowId") == flow["workflowId"]]
        has_table = (self._store.root / flow["name"] / "data").is_dir()
        lines = [
            f"流程：{flow['name']}",
            f"元素资产：{flow['elements']} 个",
            f"数据表格：{'有' if has_table else '无'}",
            f"运行历史：{len(runs)} 条",
            "",
            "以上内容将被一并删除，且不可撤销。",
        ]
        answer = QMessageBox.question(
            self,
            "删除流程",
            "\n".join(lines),
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,  # 默认取消（不可逆操作）
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.hint.setText("已取消删除。")
            return
        try:
            self._store.delete_flow(flow["name"], purge=True)
            removed = purge_runs(self._artifacts_root(), flow["workflowId"])
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "删除流程", f"删除失败：{exc}")
            return
        self.refresh_flows()
        self.hint.setText(f"已删除 {flow['name']}（连带运行历史 {removed} 条）。")

    def _import_flow(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        chosen, _ = QFileDialog.getOpenFileName(
            self, "导入流程（选择 workflow.json）", "", "工作流文件 (workflow.json)"
        )
        if not chosen:
            return
        source = Path(chosen)
        name = source.parent.name or "imported"
        try:
            self._store.import_flow(name, source)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "导入流程", f"导入失败：{exc}")
            return
        self.refresh_flows()
        self.hint.setText(f"已导入流程 {name}。")

    def _export_flow(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        flow = self._selected_flow()
        if flow is None:
            self.hint.setText("先在上表选中一个流程。")
            return
        target = QFileDialog.getExistingDirectory(self, "导出流程到目录")
        if not target:
            return
        try:
            self._store.export_flow(flow["name"], Path(target))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "导出流程", f"导出失败：{exc}")
            return
        self.hint.setText(f"已导出 {flow['name']} 到 {target}。")

    def _is_editing(self, name: str) -> bool:
        """该流程是否正被编辑器打开（编辑器单例，按 flow_path 判断）。"""
        from rpa_core.gui import app as app_module

        window = getattr(app_module, "_EDITOR_WINDOW", None)
        if window is None or window.flow_path is None:
            return False
        try:
            return Path(window.flow_path).parent.name == name
        except Exception:  # noqa: BLE001 - 判断失败按「未在编辑」处理
            return False
