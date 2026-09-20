"""最小原生拖拽探针（不含任何 rpa_core 产品代码）。

用途：判定「拖不动」是产品代码问题还是当前系统/Qt 环境问题。
操作：在窗口里用鼠标把任意一行上下拖动后松手；然后直接关闭窗口，
结果会写入 %TEMP%\\qt_native_drag_probe.txt。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMainWindow,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

OUT = Path(tempfile.gettempdir()) / "qt_native_drag_probe.txt"


class Recorder(QObject):
    def __init__(self, tree):
        super().__init__()
        self.tree = tree
        self.chain: list[str] = []

    def eventFilter(self, obj, event):
        kind = event.type()
        if kind == QEvent.Type.DragEnter:
            self.chain.append("DragEnter")
        elif kind == QEvent.Type.DragMove:
            if not self.chain or self.chain[-1] != "DragMove..":
                self.chain.append("DragMove..")
        elif kind == QEvent.Type.Drop:
            self.chain.append("Drop")
        elif kind == QEvent.Type.MouseButtonPress:
            self.chain.append("Press")
        return False


class ProbeTree(QTreeWidget):
    """hover 到可拖行显示抓手光标，按住显示闭合抓手。"""

    def __init__(self):
        super().__init__()
        self.setHeaderHidden(True)
        for i in range(6):
            self.addTopLevelItem(QTreeWidgetItem([f"可拖动行 {i + 1}"]))
        self.setDragDropMode(QTreeWidget.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.recorder = Recorder(self)
        self.viewport().installEventFilter(self.recorder)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.setCursor(Qt.CursorShape.OpenHandCursor)


def main() -> int:
    app = QApplication(sys.argv)
    win = QMainWindow()
    win.setWindowTitle("原生拖拽探针（与产品无关）")
    win.resize(380, 360)
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.addWidget(QLabel(
        "请用鼠标把下面任意一行按住拖到其他位置后松手，\n"
        "看是否出现插入横线、松手后顺序是否改变。\n"
        "然后直接关闭本窗口，结果会写到临时文件。"
    ))
    tree = ProbeTree()
    layout.addWidget(tree)
    win.setCentralWidget(box)

    def dump():
        order = [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]
        OUT.write_text(
            f"event_chain: {' -> '.join(tree.recorder.chain) or '(空)'}\n"
            f"final_order: {order}\n",
            encoding="utf-8",
        )

    win.destroyed.connect(dump)
    app.aboutToQuit.connect(dump)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
