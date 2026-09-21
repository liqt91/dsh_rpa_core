"""流程 inputs 声明编辑对话框（M26 S2）。

对应用户视角的「这个流程要接收哪些输入」——之前只能手写 `workflow.json` 的顶层 `inputs`。

## 形状（与 `model/inputs.py` 一致，不引入第二份声明格式）

`inputs` 是 **`{名称: 默认值}` 扁平映射**，所以表格只有**两列**：名称 + 默认值（JSON 文本）。
`type` / `required` / `description` 在这份形状里**不存在**——加它们是形状变更（要动
`${inputs.<名>}` 的文法），属 ADR 级决定，不在本里程碑夹带。

## 校验时机（刻意的设计）

**校验在「确定」之前**，不通过则**不关闭对话框、不产出任何结果**：

- 名称：`[A-Za-z_]\\w*`（不含点号——点号是 `${...}` 的路径分隔符）、不占用保留名
  （`inputs`/`steps`/`loop`）、不重复；
- 默认值：必须是合法 JSON（空 = `null`）；
- **一次列全部问题**（`validate_entries` 的行为），不是逐个报——表格里可能有多个错，
  让用户改一个、点一次确定、再被拒一次是最烦的交互。

## 与既有保存链路的接线

对话框只负责产出 `{名: 默认值}`；**落盘走主窗口既有的 `_build_document` → `save_workflow`**
（`_workflow_meta["inputs"]` 参与 `model_to_workflow`），因此脏标记与关闭确认与其它编辑一致，
不需要在这里自己写文件。
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from rpa_core.model.inputs import (
    INPUT_NAME_PATTERN,
    InputDeclarationError,
    InputEntry,
    declaration_from_entries,
    entries_from_declaration,
    validate_entries,
)

# 名称列的占位提示：直接用正则会让人困惑，用例子更清楚
_NAME_HINT = "例如 userName（字母/下划线开头，不含点号）"


class FlowInputsDialog(QDialog):
    """流程输入声明编辑器。

    用法：`dialog = FlowInputsDialog(declaration, self)`；`exec()` 返回 `Accepted` 后
    用 `dialog.declaration()` 取回新的 `{名: 默认值}`。
    """

    def __init__(
        self, declaration: dict[str, Any] | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("流程输入")
        self._result: dict[str, Any] | None = None

        layout = QVBoxLayout(self)

        hint = QLabel(
            "声明本流程运行时接收的输入。名称将作为 ${inputs.<名称>} 被引用；"
            "默认值为 JSON（留空表示 null）。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #64707d;")
        layout.addWidget(hint)

        toolbar = QHBoxLayout()
        add_button = QPushButton("新增")
        add_button.clicked.connect(self._add_row_action)
        remove_button = QPushButton("删除选中")
        remove_button.clicked.connect(self._remove_row_action)
        toolbar.addWidget(add_button)
        toolbar.addWidget(remove_button)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        self.grid = QTableWidget(0, 2)
        self.grid.setHorizontalHeaderLabels(["名称", "默认值（JSON）"])
        self.grid.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.grid.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.grid.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        layout.addWidget(self.grid, 1)

        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #cf222e;")
        self.error_label.hide()
        layout.addWidget(self.error_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.load(declaration)

    # ---- 载入 / 收集 --------------------------------------------------------

    def load(self, declaration: dict[str, Any] | None) -> None:
        """载入既有声明（按名称排序，与 `entries_from_declaration` 同序）。"""
        self.grid.setRowCount(0)
        for entry in entries_from_declaration(declaration):
            self._append_row(entry.name, entry.json_text)

    def entries(self) -> list[InputEntry]:
        """当前表格内容 → 条目列表（不做校验，允许处于「正在输入」的中间态）。"""
        out: list[InputEntry] = []
        for row in range(self.grid.rowCount()):
            name_item = self.grid.item(row, 0)
            default_item = self.grid.item(row, 1)
            out.append(
                InputEntry(
                    name=(name_item.text().strip() if name_item else ""),
                    json_text=(default_item.text() if default_item else ""),
                )
            )
        return out

    def declaration(self) -> dict[str, Any]:
        """取回新的 `{名: 默认值}`；仅在 `exec()` 返回 `Accepted` 后调用有意义。"""
        if self._result is None:
            raise InputDeclarationError("对话框尚未确认，没有可用的声明")
        return self._result

    # ---- 编辑操作（按钮与测试共用） ------------------------------------------

    def _append_row(self, name: str, json_text: str) -> None:
        row = self.grid.rowCount()
        self.grid.insertRow(row)
        name_item = QTableWidgetItem(name)
        if not name:
            name_item.setToolTip(_NAME_HINT)
        self.grid.setItem(row, 0, name_item)
        self.grid.setItem(row, 1, QTableWidgetItem(json_text))

    def add_row(self, name: str = "", json_text: str = "") -> None:
        """追加一行（测试直接调用；空名由校验在确定时拦下）。"""
        self._append_row(name, json_text)

    def _add_row_action(self) -> None:
        self.add_row()
        row = self.grid.rowCount() - 1
        self.grid.setCurrentCell(row, 0)
        self.grid.editItem(self.grid.item(row, 0))

    def remove_row(self, row: int) -> bool:
        """删除指定行；越界返回 False（测试直接调用）。"""
        if row < 0 or row >= self.grid.rowCount():
            return False
        self.grid.removeRow(row)
        return True

    def _remove_row_action(self) -> None:
        row = self.grid.currentRow()
        if row < 0:
            self._show_error("先选中要删除的行")
            return
        self.remove_row(row)

    # ---- 确定：校验通过才产出结果 -------------------------------------------

    def _show_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.show()

    def _clear_error(self) -> None:
        self.error_label.clear()
        self.error_label.hide()

    def _on_accept(self) -> None:
        """校验 → 通过则记下结果并接受；不通过则留在对话框里（**不产出半成品**）。"""
        self._clear_error()
        entries = self.entries()
        try:
            validate_entries(entries)
            self._result = declaration_from_entries(entries)
        except InputDeclarationError as exc:
            self._show_error(str(exc))
            return
        self.accept()

    def accept(self) -> None:  # noqa: N802 - Qt 覆写要求保留驼峰
        # 防御：任何路径（含测试直接调用 accept）都要先过校验，避免绕过 _on_accept
        if self._result is None:
            self._on_accept()
            return
        super().accept()

    # ---- 便捷入口 -----------------------------------------------------------

    @staticmethod
    def edit(
        declaration: dict[str, Any] | None, parent: QWidget | None = None
    ) -> dict[str, Any] | None:
        """弹对话框；确认返回新声明，取消返回 None。"""
        dialog = FlowInputsDialog(declaration, parent)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.declaration()


def warn_and_edit(
    declaration: dict[str, Any] | None, parent: QWidget | None = None
) -> dict[str, Any] | None:
    """带「非法声明」提示的入口：既有声明本身不合法时先告知，仍允许进入编辑修正。

    「读取宽松、保存严格」（`model/inputs.py`）在 UI 层的对应做法：不合法**不阻止**打开
    编辑（否则用户无法修正），但必须**明说哪里不合法**，避免他以为一切正常。
    """
    if declaration:
        try:
            validate_entries(entries_from_declaration(declaration))
        except InputDeclarationError as exc:
            QMessageBox.warning(
                parent,
                "现有输入声明不合法",
                f"当前流程的输入声明有问题：\n\n{exc}\n\n"
                "可以在这里修正；修正前保存会被拒绝。",
            )
    return FlowInputsDialog.edit(declaration, parent)


# 供「新增行」时的名称预填（与 INPUT_NAME_PATTERN 同源，避免两处规则分叉）
def suggest_new_name(existing: list[str]) -> str:
    """给出一个未被占用的合法名称（`input1`、`input2`…）。"""
    taken = set(existing)
    index = 1
    while f"input{index}" in taken:
        index += 1
    candidate = f"input{index}"
    # 断言而非假设：规则若收紧，这里要在开发期就暴露，而不是产出非法名
    assert INPUT_NAME_PATTERN.match(candidate)
    return candidate
