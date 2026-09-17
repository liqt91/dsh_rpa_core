"""元素库面板（GUI 功能补齐 切 G）。

与 Web 编辑器元素库同契约：元素是命名流程的附属资产
（``<flow>/elements/<name>.json``，ElementDescriptor 文档）。
面板只做展示与动作入口，存储/校验/捕获逻辑由 app 接线。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
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
