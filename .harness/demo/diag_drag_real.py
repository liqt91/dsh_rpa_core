"""硬件级真实鼠标拖拽诊断（非 offscreen、非 QTest 合成）。

QTest 合成事件无法驱动 Windows OLE 拖拽；本脚本用 user32.SetCursorPos +
mouse_event 注入与硬件等价的输入，工作线程注入、主线程跑事件循环，
并在 viewport 上记录 Press/Move/DragEnter/DragMove/Drop 事件链与模型
canDrop/drop 回调，定位「完全拖不动」断在哪一环。
"""

from __future__ import annotations

import ctypes
import sys
import threading
import time

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication

from rpa_core.catalog import load_catalog
from rpa_core.cli import _commands_root
from rpa_core.gui.app import MainWindow, SAMPLE_WORKFLOW
from rpa_core.gui.flow_model import ROLE_NODE_ID

user32 = ctypes.windll.user32
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004

WATCH = {
    QEvent.Type.MouseButtonPress: "Press",
    QEvent.Type.MouseMove: "Move",
    QEvent.Type.MouseButtonRelease: "Release",
    QEvent.Type.DragEnter: "DragEnter",
    QEvent.Type.DragMove: "DragMove",
    QEvent.Type.DragLeave: "DragLeave",
    QEvent.Type.Drop: "Drop",
}

events: list[str] = []
_view_ref: dict = {}
_move_logged = {"value": False}


class EventFilter(QObject):
    def eventFilter(self, obj, event):
        name = WATCH.get(event.type())
        if name:
            if name in ("DragMove",):
                events.append(name)
            elif name == "Move":
                events.append(name)
                if not _move_logged["value"] and event.buttons() & Qt.MouseButton.LeftButton:
                    _move_logged["value"] = True
                    view = _view_ref["view"]
                    pos = event.position().toPoint()
                    idx = view.indexAt(pos)
                    print(
                        ">>> first held-move:",
                        "pos", (pos.x(), pos.y()),
                        "buttons", event.buttons().value,
                        "index", idx.data(256 + 10) if idx.isValid() else None,
                        "dragEnabled", view.dragEnabled(),
                        "state", int(view.state()),
                        "distance", QApplication.startDragDistance(),
                        "flags", idx.flags().value if idx.isValid() else None,
                        flush=True,
                    )
            else:
                pos = event.position().toPoint() if hasattr(event, "position") else None
                hit = ""
                if name == "Press" and pos is not None:
                    idx = _view_ref["view"].indexAt(pos)
                    hit = f" idx={idx.data(256 + 10) if idx.isValid() else None}"
                events.append(
                    f"{name}@{pos.x()},{pos.y()}{hit}" if pos else name
                )
        return False


app = QApplication(sys.argv)
_screen = app.primaryScreen()
print(
    "DPR:", _screen.devicePixelRatio(),
    "logicalDPI:", _screen.logicalDotsPerInch(),
    "geometry:", _screen.geometry(),
    flush=True,
)

# 插桩 startDrag：区分「QTreeView 没发起拖拽」与「QDrag.exec 发起即失败」
from PySide6.QtWidgets import QTreeView  # noqa: E402

_orig_start_drag = QTreeView.startDrag


def traced_start_drag(self, supported_actions):
    print(f">>> startDrag CALLED actions={supported_actions}", flush=True)
    try:
        result = _orig_start_drag(self, supported_actions)
        print(">>> startDrag returned normally", flush=True)
        return result
    except Exception as exc:  # noqa: BLE001
        print(f">>> startDrag RAISED {exc!r}", flush=True)
        raise


QTreeView.startDrag = traced_start_drag

# 插桩 QDrag.exec：拿 DoDragDrop 的返回动作与耗时（≈0 + IgnoreAction 说明 OLE 未运转）
from PySide6.QtGui import QDrag  # noqa: E402

_orig_drag_exec = QDrag.exec


def traced_drag_exec(self, *args, **kwargs):
    t0 = time.time()
    result = _orig_drag_exec(self, *args, **kwargs)
    print(
        f">>> QDrag.exec args={args!r} result={result} elapsed={time.time() - t0:.2f}s",
        flush=True,
    )
    return result


QDrag.exec = traced_drag_exec

catalog = load_catalog(_commands_root())
w = MainWindow(catalog, SAMPLE_WORKFLOW)
w.resize(1200, 800)
w.move(60, 60)
w.show()
w.raise_()
w.activateWindow()
app.processEvents()
_hwnd = int(w.winId())
user32.ShowWindow(_hwnd, 9)  # SW_RESTORE
user32.SetForegroundWindow(_hwnd)
time.sleep(0.3)
app.processEvents()
model = w.flow_model
view = w.canvas_view
_view_ref["view"] = view
print(
    "ACCEPT_DROPS view:", view.acceptDrops(),
    "viewport:", view.viewport().acceptDrops(),
    "dragEnabled:", view.dragEnabled(),
    flush=True,
)
view.viewport().installEventFilter(EventFilter(view))

# 模型回调插桩
calls = {"can": 0, "drop": 0, "can_args": []}
_orig_can = model.canDropMimeData
_orig_drop = model.dropMimeData


def can_wrap(data, action, row, column, parent):
    calls["can"] += 1
    ptype = parent.data(256 + 11) if parent.isValid() else None
    result = _orig_can(data, action, row, column, parent)
    calls["can_args"].append((row, str(ptype), result))
    return result


def drop_wrap(data, action, row, column, parent):
    calls["drop"] += 1
    return _orig_drop(data, action, row, column, parent)


model.canDropMimeData = can_wrap
model.dropMimeData = drop_wrap


def ids():
    return [
        model.item(0).child(r).data(ROLE_NODE_ID)
        for r in range(model.item(0).rowCount())
    ]


before = ids()
print("ORDER_BEFORE:", before, flush=True)

# 源：done（根级最后一张卡片）；目标：check 卡片上沿（应显示上方插入指示）
done_rect = view.visualRect(model.indexFromItem(model.find_by_id("done")))
check_rect = view.visualRect(model.indexFromItem(model.find_by_id("check")))
vp = view.viewport()
start = vp.mapToGlobal(done_rect.center())
end = vp.mapToGlobal(QPoint(check_rect.center().x(), check_rect.top() + 3))
print("START_GLOBAL:", start.x(), start.y(), "END_GLOBAL:", end.x(), end.y(), flush=True)


def inject():
    time.sleep(0.8)
    # 再次确保前台（SetForegroundWindow 有前台窗口锁，启动初期接管最可靠）
    user32.SetForegroundWindow(_hwnd)
    time.sleep(0.2)
    # QCursor.setPos 接受逻辑坐标，内部处理 DPI 缩放；mouse_event 只切换按键
    QCursor.setPos(start)
    time.sleep(0.2)
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.3)
    steps = 10
    for i in range(1, steps + 1):
        x = start.x() + (end.x() - start.x()) * i // steps
        y = start.y() + (end.y() - start.y()) * i // steps
        QCursor.setPos(QPoint(x, y))
        time.sleep(0.08)
    time.sleep(0.4)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    time.sleep(0.4)
    print("INJECT_DONE", flush=True)


threading.Thread(target=inject, daemon=True).start()


def report():
    # 折叠高频 Move 序列，只看关键事件
    compact: list[str] = []
    for name in events:
        if name == "Move":
            if not compact or compact[-1] != "Move...":
                compact.append("Move...")
        elif name == "DragMove":
            if not compact or compact[-1] != "DragMove...":
                compact.append("DragMove...")
        else:
            compact.append(name)
    print("EVENT_CHAIN:", " -> ".join(compact), flush=True)
    print("MODEL_CALLS:", calls["can"], calls["drop"], flush=True)
    for entry in calls["can_args"][:10]:
        print("  canDrop row=%s parent=%s -> %s" % entry, flush=True)
    print("ORDER_AFTER:", ids(), flush=True)
    print("DIRTY:", w._dirty, flush=True)
    app.quit()


QTimer.singleShot(4000, report)
sys.exit(app.exec())
