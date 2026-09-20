"""探测 Qt 顶级窗口是否已向 OLE 注册 IDropTarget（pywin32 正规实现）。

RegisterDragDrop 对已注册 HWND 返回 DRAGDROP_E_ALREADYREGISTERED(0x80040101)，
未注册返回 S_OK（挂上空 target，脚本随即退出，不影响产品）。
"""

from __future__ import annotations

import sys
import time

import pythoncom
from PySide6.QtWidgets import QApplication, QMainWindow, QTreeWidget, QTreeWidgetItem
from win32com.server.util import wrap

from rpa_core.catalog import load_catalog
from rpa_core.cli import _commands_root
from rpa_core.gui.app import MainWindow, SAMPLE_WORKFLOW

ALREADY_REGISTERED = 0x80040101 - 0x100000000


class DummyDropTarget:
    """pywin32 IDropTarget 空实现：接受全部事件但不允许放置效果。"""

    _com_interfaces_ = [pythoncom.IID_IDropTarget]
    _public_methods_ = ["DragEnter", "DragOver", "DragLeave", "Drop"]

    def DragEnter(self, data_obj, key_state, point):
        return 0  # DROPEFFECT_NONE

    def DragOver(self, key_state, point):
        return 0

    def DragLeave(self):
        return pythoncom.S_OK

    def Drop(self, data_obj, key_state, point):
        return 0


def probe(hwnd: int, tag: str, holds: list) -> None:
    target = wrap(DummyDropTarget(), pythoncom.IID_IDropTarget)
    holds.append(target)
    try:
        pythoncom.RegisterDragDrop(hwnd, target)
        verdict = "S_OK -> 之前【未注册】"
    except pythoncom.com_error as exc:
        code = exc.hresult
        if code == ALREADY_REGISTERED:
            verdict = "ALREADY_REGISTERED -> Qt 已注册"
        else:
            verdict = f"HRESULT {code & 0xFFFFFFFF:#010x}: {exc.strerror}"
    print(f"[{tag}] hwnd={hwnd} {verdict}", flush=True)


app = QApplication(sys.argv)
holds: list = []

catalog = load_catalog(_commands_root())
win_a = MainWindow(catalog, SAMPLE_WORKFLOW)
win_a.show()
app.processEvents()
time.sleep(0.3)
app.processEvents()
probe(int(win_a.winId()), "A-product", holds)

win_b = QMainWindow()
tree_b = QTreeWidget()
tree_b.setHeaderHidden(True)
tree_b.addTopLevelItem(QTreeWidgetItem(["x"]))
tree_b.setDragDropMode(QTreeWidget.DragDropMode.InternalMove)
win_b.setCentralWidget(tree_b)
win_b.move(700, 100)
win_b.show()
app.processEvents()
time.sleep(0.3)
app.processEvents()
probe(int(win_b.winId()), "B-native", holds)

win_c = QMainWindow()
win_c.move(700, 500)
win_c.show()
app.processEvents()
time.sleep(0.2)
probe(int(win_c.winId()), "C-baseline", holds)

print("DONE", flush=True)
