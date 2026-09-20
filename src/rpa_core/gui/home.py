"""工作台（首页）：流程列表 + 管理入口 + 运行入口（ADR 0017 / M27）。

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
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from rpa_core.devserver.store import WorkflowDirStore
from rpa_core.run_history import list_runs

_STATUS_LABELS = {
    "succeeded": "成功",
    "failed": "失败",
    "cancelled": "已取消",
    "paused": "已暂停",
    "recovery_required": "待确认",
    "indeterminate": "结果不确定",
    "running": "运行中",
}


class HomeWindow(QMainWindow):
    """工作台窗口：流程列表 + 打开/新建 + 运行入口。

    `open_editor` 是打开编辑器的注入点（默认由 `app.open_editor_window` 提供）：
    测试里可替换成记录调用的桩，避免真的再开一个窗口。
    """

    def __init__(
        self,
        store: WorkflowDirStore,
        catalog: Any,
        *,
        open_editor: Callable[[str], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._catalog = catalog
        self._open_editor_hook = open_editor
        self._flows: list[dict[str, Any]] = []

        self.setWindowTitle("RPA Core 工作台")
        self.resize(1000, 620)

        central = QWidget()
        layout = QVBoxLayout(central)
        header = QLabel("流程库")
        header.setStyleSheet("font-weight: bold; font-size: 14px;")
        layout.addWidget(header)

        buttons = QHBoxLayout()
        self.new_button = QPushButton("新建流程")
        self.new_button.clicked.connect(self._create_flow)
        self.open_button = QPushButton("打开")
        self.open_button.clicked.connect(self._open_selected)
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.clicked.connect(self.refresh_flows)
        for widget in (self.new_button, self.open_button, self.refresh_button):
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

        self.setCentralWidget(central)
        self.refresh_flows()

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

    def _open_selected(self) -> None:
        flow = self._selected_flow()
        if flow is None:
            self.hint.setText("先在上表选中一个流程。")
            return
        self.open_flow(flow["name"])

    def open_flow(self, name: str) -> None:
        """打开编辑器（由注入的 hook 决定实现；缺省走 GUI 的编辑器打开函数）。"""
        if self._open_editor_hook is None:
            from rpa_core.gui.app import open_editor_window

            self._open_editor_hook = open_editor_window
        self._open_editor_hook(name)

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
