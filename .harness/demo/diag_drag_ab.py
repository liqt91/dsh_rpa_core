"""硬件级真实鼠标拖拽 A/B 诊断。

A：产品画布 FlowTreeModel + QTreeView(InternalMove)
B：同进程极简原生 QTreeWidget + InternalMove（对照组）
两轮真实硬件注入，判定「无 DragEnter」是产品代码问题还是当前 OLE/注入环境问题。
"""

from __future__ import annotations

import ctypes
import sys
import threading
import time

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QTreeWidget,
    QTreeWidgetItem,
)

from rpa_core.catalog import load_catalog
from rpa_core.cli import _commands_root
from rpa_core.gui.app import MainWindow, SAMPLE_WORKFLOW

user32 = ctypes.windll.user32
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004

WATCH = {
    QEvent.Type.MouseButtonPress: "Press",
    QEvent.Type.MouseButtonRelease: "Release",
    QEvent.Type.DragEnter: "DragEnter",
    QEvent.Type.DragMove: "DragMove",
    QEvent.Type.Drop: "Drop",
}


class Recorder(QObject):
    def __init__(self, tag, view):
        super().__init__()
        self.tag = tag
        self.view = view
        self.chain: list[str] = []
        self.mime_called = 0

    def eventFilter(self, obj, event):
        name = WATCH.get(event.type())
        if name:
            pos = event.position().toPoint() if hasattr(event, "position") else None
            if name in ("Press", "Release") and pos is not None:
                idx = self.view.indexAt(pos) if hasattr(self.view, "indexAt") else None
                nid = idx.data(256 + 10) if idx is not None and idx.isValid() else None
                self.chain.append(f"{name}:{nid}")
            else:
                self.chain.append(name)
        return False


app = QApplication(sys.argv)
recorder_tag = ["?"]  # 当前拖拽轮次标签，供类级 startDrag 插桩使用


def hardware_drag(win, start_global, end_global, recorder, settle=0.6):
    """真实硬件：按下 → 分 10 步移动 → 松开。"""
    recorder_tag[0] = recorder.tag
    hwnd = int(win.winId())
    user32.ShowWindow(hwnd, 9)
    user32.SetForegroundWindow(hwnd)
    time.sleep(settle)
    app.processEvents()

    def inject():
        time.sleep(0.4)
        QCursor.setPos(start_global)
        time.sleep(0.2)
        user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(0.25)
        for i in range(1, 11):
            x = start_global.x() + (end_global.x() - start_global.x()) * i // 10
            y = start_global.y() + (end_global.y() - start_global.y()) * i // 10
            QCursor.setPos(x, y)
            time.sleep(0.07)
        time.sleep(0.3)
        user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        time.sleep(0.3)

    threading.Thread(target=inject, daemon=True).start()
    end_deadline = time.time() + 4
    while time.time() < end_deadline:
        app.processEvents()
        time.sleep(0.02)
    print(f"[{recorder.tag}] chain: {' -> '.join(recorder.chain) or '(empty)'}",
          flush=True)


# ---- A：产品画布 ----
catalog = load_catalog(_commands_root())
win_a = MainWindow(catalog, SAMPLE_WORKFLOW)
win_a.resize(1100, 760)
win_a.move(40, 40)
rec_a = Recorder("A-product", win_a.canvas_view)
win_a.canvas_view.viewport().installEventFilter(rec_a)

# 模型 mimeData 插桩（虚函数，C++ 会调到 Python override）
_orig_mime = win_a.flow_model.mimeData


def mime_wrap(indexes):
    rec_a.mime_called += 1
    print("[A-product] model.mimeData CALLED", flush=True)
    return _orig_mime(indexes)


win_a.flow_model.mimeData = mime_wrap

_orig_start = type(win_a.canvas_view).startDrag


def start_wrap(self, actions):
    sel = self.selectionModel().selectedIndexes()
    print(
        f"[{recorder_tag[0]}] startDrag actions=", actions.value,
        "selected=", [
            (i.data(256 + 10), i.flags().value) for i in sel
        ],
        flush=True,
    )
    t0 = time.time()
    result = _orig_start(self, actions)
    print(f"[{recorder_tag[0]}] startDrag elapsed={time.time() - t0:.2f}s",
          flush=True)
    return result


type(win_a.canvas_view).startDrag = start_wrap

win_a.show()
win_a.raise_()
win_a.activateWindow()
app.processEvents()
time.sleep(0.4)
app.processEvents()

view_a = win_a.canvas_view
done_rect = view_a.visualRect(
    win_a.flow_model.indexFromItem(win_a.flow_model.find_by_id("done"))
)
check_rect = view_a.visualRect(
    win_a.flow_model.indexFromItem(win_a.flow_model.find_by_id("check"))
)
vp = view_a.viewport()
a_start = vp.mapToGlobal(done_rect.center())
a_end = vp.mapToGlobal(check_rect.center())
hardware_drag(win_a, a_start, a_end, rec_a)
print("[A-product] mime_called:", rec_a.mime_called, flush=True)

# ---- B：原生 QTreeWidget 对照 ----
win_b = QMainWindow()
win_b.resize(500, 500)
win_b.move(60, 100)
tree_b = QTreeWidget()
tree_b.setHeaderHidden(True)
for name in ("alpha", "bravo", "charlie", "delta"):
    tree_b.addTopLevelItem(QTreeWidgetItem([name]))
tree_b.setDragDropMode(QTreeWidget.DragDropMode.InternalMove)
win_b.setCentralWidget(tree_b)
rec_b = Recorder("B-native", tree_b)
tree_b.viewport().installEventFilter(rec_b)
win_b.show()
win_b.raise_()
win_b.activateWindow()
app.processEvents()
time.sleep(0.4)
app.processEvents()

vp_b = tree_b.viewport()
b_start = vp_b.mapToGlobal(tree_b.visualRect(tree_b.model().index(3, 0)).center())
b_end = vp_b.mapToGlobal(tree_b.visualRect(tree_b.model().index(0, 0)).center())
hardware_drag(win_b, b_start, b_end, rec_b)
order_b = [tree_b.topLevelItem(i).text(0) for i in range(tree_b.topLevelItemCount())]
print("[B-native] order after:", order_b, flush=True)

QTimer.singleShot(200, app.quit)
app.exec()
