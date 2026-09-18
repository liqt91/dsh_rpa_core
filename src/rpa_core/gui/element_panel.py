"""元素库面板（GUI 功能补齐 切 G）。

与 Web 编辑器元素库同契约：元素是命名流程的附属资产
（``<flow>/elements/<name>.json``，ElementDescriptor 文档）。
面板只做展示与动作入口，存储/校验/捕获逻辑由 app 接线。

捕获后的确认对话框（``ElementDialog``）对齐 Web ``openElementDialog``：
改名 / selector 编辑 / metadata 只读 / 捕获时命中数展示；同名覆盖保护由
调用方（app）确认。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class ElementPanel(QWidget):
    """元素库面板：列表 + 刷新/捕获/校验/删除/插入按钮。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 8)

        toolbar = QHBoxLayout()
        self.refresh_button = QPushButton("刷新")
        self.capture_button = QPushButton("捕获元素")
        self.capture_button.setToolTip(
            "混合捕获：移动鼠标框选（网页走浏览器插件、桌面走 UIA），"
            "Ctrl+Click 捕获（桌面也可用 F9），Esc 取消"
        )
        self.verify_button = QPushButton("校验")
        self.insert_button = QPushButton("插入参数")
        self.insert_button.setToolTip(
            "把选中元素填入画布当前指令的 selector/locator 参数"
        )
        self.delete_button = QPushButton("删除")
        for button in (
            self.refresh_button,
            self.capture_button,
            self.verify_button,
            self.insert_button,
            self.delete_button,
        ):
            toolbar.addWidget(button)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        self.hint_label = QLabel("")
        self.hint_label.setStyleSheet("color: #64707d;")
        layout.addWidget(self.hint_label)

        self.list = QListWidget()
        layout.addWidget(self.list, 1)

    def set_elements(self, entries: list[dict[str, Any]]) -> None:
        """刷新列表；entries 为 {name, summary} dict。"""
        self.list.clear()
        for entry in entries:
            self.list.addItem(f"{entry['name']}    {entry.get('summary', '')}")

    def current_name(self) -> str | None:
        item = self.list.currentItem()
        if item is None:
            return None
        return item.text().split(" ", 1)[0].strip()


def summarize_element(document: dict) -> str:
    """元素摘要：kind · selector 简述（对齐 Web elementSummary）。"""
    kind = document.get("kind", "?")
    selector = document.get("selector") or {}
    if kind == "browser":
        return f"browser · {selector.get('css', '')}"
    locator = selector.get("locator") or {}
    name = locator.get("name") or locator.get("controlType") or ""
    return f"desktop · {name}"


class ElementDialog(QDialog):
    """捕获确认对话框（对齐 Web ``openElementDialog`` 的 confirm 模式）。

    - 名称可改（默认名由调用方给；同名覆盖保护在 app 侧确认）；
    - selector 可编辑：browser 为 css 单行，desktop 为 locator JSON；
    - metadata 只读展示（tag/id/classes/text/rect，desktop 另含
      controlType/automationId/window）；
    - 捕获时命中数：1 绿、其他红（对齐 Web dlg-verify ok/bad）。
    """

    def __init__(
        self,
        descriptor: dict[str, Any],
        *,
        default_name: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._descriptor = descriptor
        self.setWindowTitle("捕获确认")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)

        # 捕获时命中数（1=绿 ok，其他=红 bad，对齐 Web dlg-verify）
        verify = descriptor.get("verifyCount")
        self.verify_label = QLabel(
            "" if verify is None else f"捕获时命中 {verify} 个"
        )
        if verify is not None:
            color = "#1a7f37" if verify == 1 else "#cf222e"
            self.verify_label.setStyleSheet(f"color: {color}; font-weight: bold;")
        layout.addWidget(self.verify_label)

        form = QFormLayout()
        self.name_edit = QLineEdit(default_name)
        form.addRow("元素名", self.name_edit)
        selector = descriptor.get("selector") or {}
        if descriptor.get("kind") == "browser":
            selector_text = str(selector.get("css") or "")
        else:
            selector_text = json.dumps(
                selector.get("locator") or {}, ensure_ascii=False
            )
        self.selector_edit = QLineEdit(selector_text)
        form.addRow("selector", self.selector_edit)
        layout.addLayout(form)

        # metadata 只读（对齐 Web metaEl 的行集）
        self.meta_label = QLabel(self._metadata_text(descriptor))
        self.meta_label.setWordWrap(True)
        self.meta_label.setStyleSheet("color: #64707d;")
        layout.addWidget(self.meta_label)

        # 校验错误提示（对话框内联展示，不弹 QMessageBox，保持可测试性）
        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #cf222e;")
        layout.addWidget(self.error_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _metadata_text(descriptor: dict[str, Any]) -> str:
        meta = descriptor.get("metadata") or {}
        rect = meta.get("rect") or {}
        rect_text = ""
        if rect:
            rect_text = (
                f"rect: {rect.get('width')}×{rect.get('height')} "
                f"@ ({rect.get('x')},{rect.get('y')})"
            )
        is_desktop = descriptor.get("kind") == "desktop"
        classes = meta.get("classes") or []
        lines = [
            meta.get("tag") and f"tag: {meta['tag']}",
            meta.get("id") and f"id: {meta['id']}",
            classes and f"classes: {' '.join(str(c) for c in classes)}",
            meta.get("text") and f"text: {meta['text']}",
            rect_text,
            is_desktop and meta.get("controlType") and f"controlType: {meta['controlType']}",
            is_desktop and meta.get("automationId") and f"automationId: {meta['automationId']}",
            is_desktop and meta.get("windowTitle") and f"window: {meta['windowTitle']}",
        ]
        return "\n".join(line for line in lines if line) or "(无 metadata)"

    def accept(self) -> None:
        """保存前校验：名称/selector 非空；desktop locator 必须是合法 JSON 对象。"""
        if not self.name_edit.text().strip():
            self.error_label.setText("元素名不能为空")
            return
        if not self.selector_edit.text().strip():
            self.error_label.setText("selector 不能为空")
            return
        if self._descriptor.get("kind") != "browser":
            try:
                locator = json.loads(self.selector_edit.text().strip())
            except json.JSONDecodeError:
                self.error_label.setText("desktop locator 不是合法 JSON")
                return
            if not isinstance(locator, dict):
                self.error_label.setText("desktop locator 必须是 JSON 对象")
                return
        super().accept()

    def result_document(self) -> tuple[str, dict[str, Any]]:
        """编辑结果：（元素名, ElementDescriptor 形状 dict）。仅在 Accepted 后调用。"""
        name = self.name_edit.text().strip()
        if self._descriptor.get("kind") == "browser":
            selector: dict[str, Any] = {"css": self.selector_edit.text().strip()}
        else:
            selector = {"locator": json.loads(self.selector_edit.text().strip())}
        return name, {
            "kind": self._descriptor.get("kind"),
            "selector": selector,
            "verifyCount": self._descriptor.get("verifyCount") or 0,
            "metadata": self._descriptor.get("metadata") or {},
        }


def wire_element_panel(
    panel: ElementPanel,
    *,
    on_refresh: Callable[[], None],
    on_capture: Callable[[], None],
    on_verify: Callable[[str], None],
    on_insert: Callable[[str], None],
    on_delete: Callable[[str], None],
) -> None:
    """把面板按钮接到 app 侧逻辑（名称参数从当前选中元素取）。"""
    panel.refresh_button.clicked.connect(on_refresh)
    panel.capture_button.clicked.connect(on_capture)

    def with_name(handler: Callable[[str], None]):
        def run() -> None:
            name = panel.current_name()
            if name:
                handler(name)
        return run

    panel.verify_button.clicked.connect(with_name(on_verify))
    panel.insert_button.clicked.connect(with_name(on_insert))
    panel.delete_button.clicked.connect(with_name(on_delete))
