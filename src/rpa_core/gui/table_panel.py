"""流程数据表格面板（GUI 功能补齐 切 H）。

与 Web 编辑器「数据表格」底栏同契约：每个命名流程一张表
（``<flow>/data/table.json``，结构 ``{schema_version, columns, rows, updated_at}``），
存储走 devserver 的 TableStore，运行期由 data.table.* 命令读写同一份文件。

面板只做编辑与展示：加列/加行/删行/清空/保存/导出 CSV；
列类型（text/number/boolean）在收集时决定单元格值的解析方式。
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

COLUMN_TYPES = ["text", "number", "boolean"]


class TablePanel(QWidget):
    """数据表格编辑面板；load() 载入表文档，collect() 收回为表文档。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._columns: list[dict[str, Any]] = []  # [{key, label, type}]
        self._loading = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 8)

        toolbar = QHBoxLayout()
        for text, handler in (
            ("加列", self._add_column_action),
            ("加行", self.add_row),
            ("删行", self._remove_row_action),
            ("清空", self._clear_action),
        ):
            button = QPushButton(text)
            button.clicked.connect(handler)
            toolbar.addWidget(button)
        toolbar.addStretch(1)
        self.hint_label = QLabel("")
        self.hint_label.setStyleSheet("color: #64707d;")
        toolbar.addWidget(self.hint_label)
        layout.addLayout(toolbar)

        self.grid = QTableWidget(0, 0)
        self.grid.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        layout.addWidget(self.grid, 1)

    # ---- 载入 / 收集 --------------------------------------------------------
    def load(self, document: dict) -> None:
        """载入表文档（columns + rows）。"""
        self._loading = True
        try:
            self._columns = [
                {
                    "key": str(c.get("key", c.get("label", ""))),
                    "label": str(c.get("label", c.get("key", ""))),
                    "type": c.get("type", "text"),
                }
                for c in document.get("columns", [])
            ]
            self.grid.setRowCount(0)
            self.grid.setColumnCount(len(self._columns))
            self.grid.setHorizontalHeaderLabels(
                [f"{c['label']}（{c['type']}）" for c in self._columns]
            )
            for row in document.get("rows", []):
                self._append_row(
                    [self._format_cell(row.get(c["key"]), c["type"]) for c in self._columns]
                )
            self.hint_label.setText(
                f"{len(self._columns)} 列 · {self.grid.rowCount()} 行"
            )
        finally:
            self._loading = False

    @staticmethod
    def _format_cell(value: Any, column_type: str) -> str:
        if value is None:
            return ""
        if column_type == "boolean":
            return "true" if value else "false"
        return str(value)

    @staticmethod
    def _parse_cell(text: str, column_type: str) -> Any:
        if column_type == "number":
            if not text.strip():
                return None
            try:
                return int(text)
            except ValueError:
                return float(text)
        if column_type == "boolean":
            return text.strip().lower() in ("true", "1", "yes", "是")
        return text

    def collect(self) -> dict:
        """收回当前编辑为表文档（供 TableStore.write）。"""
        rows = []
        for row_index in range(self.grid.rowCount()):
            row: dict[str, Any] = {}
            for col_index, column in enumerate(self._columns):
                item = self.grid.item(row_index, col_index)
                text = item.text() if item else ""
                row[column["key"]] = self._parse_cell(text, column["type"])
            rows.append(row)
        return {
            "schema_version": 1,
            "columns": [dict(c) for c in self._columns],
            "rows": rows,
            "updated_at": "",
        }

    # ---- 编辑操作（按钮与测试共用） ------------------------------------------
    def add_column(self, label: str, column_type: str = "text") -> None:
        key = label
        # key 冲突时追加序号
        existing = {c["key"] for c in self._columns}
        suffix = 2
        while key in existing:
            key = f"{label}_{suffix}"
            suffix += 1
        self._columns.append({"key": key, "label": label, "type": column_type})
        self.grid.setColumnCount(len(self._columns))
        self.grid.setHorizontalHeaderLabels(
            [f"{c['label']}（{c['type']}）" for c in self._columns]
        )

    def _add_column_action(self) -> None:
        label, ok = QInputDialog.getText(self, "加列", "列名：")
        if not ok or not label.strip():
            return
        column_type, ok = QInputDialog.getItem(
            self, "加列", "列类型：", COLUMN_TYPES, 0, False
        )
        if not ok:
            return
        self.add_column(label.strip(), column_type)

    def _append_row(self, values: list[str]) -> None:
        row = self.grid.rowCount()
        self.grid.insertRow(row)
        for col_index, value in enumerate(values):
            self.grid.setItem(row, col_index, QTableWidgetItem(value))

    def add_row(self) -> None:
        self._append_row([""] * len(self._columns))

    def _remove_row_action(self) -> None:
        row = self.grid.currentRow()
        if row >= 0:
            self.grid.removeRow(row)

    def clear_rows(self) -> None:
        self.grid.setRowCount(0)

    def _clear_action(self) -> None:
        self.clear_rows()
