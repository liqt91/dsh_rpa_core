"""真实平台交互复测（非 offscreen）：Delete 焦点链路 + 拖拽入口契约。

1. 双击左树添加后焦点在左树：删除动作须仍可用；焦点进参数输入框后须暂停；
2. canDropMimeData 对 QTreeView dragEnter 的无效 parent 探测须放行
   （真实窗口曾因此表现为「完全无法拖拽」），view 拖拽配置开启；
3. 真实鼠标的放置手感以人工实测为准——Windows OLE 拖拽无法用合成事件自动化。
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtWidgets import QApplication, QLineEdit

from rpa_core.catalog import load_catalog
from rpa_core.cli import _commands_root
from rpa_core.gui.app import MainWindow, SAMPLE_WORKFLOW
from rpa_core.gui.flow_model import ROLE_NODE_ID


def log(message: str) -> None:
    print(message, flush=True)


app = QApplication(sys.argv)
catalog = load_catalog(_commands_root())
w = MainWindow(catalog, SAMPLE_WORKFLOW)
w.show()
app.processEvents()
model = w.flow_model

# ---- 1) Delete：左树聚焦仍可删；参数输入框聚焦时暂停 ----
w.add_command("workflow.sleep")
app.processEvents()
w.command_tree.setFocus()
app.processEvents()
log(f"TREE_FOCUS action_enabled: {w.delete_action.isEnabled()}")
assert w.delete_action.isEnabled()
w.delete_action.trigger()
app.processEvents()
new_gone = all(
    model.item(0).child(r).data(ROLE_NODE_ID) != "n1"
    for r in range(model.item(0).rowCount())
)
log(f"DELETE_FROM_TREE_FOCUS removed n1: {new_gone}")

# 选中 open（browser.navigate 带 url 文本框）→ 焦点进入输入框
idx = model.indexFromItem(model.find_by_id("open"))
w.canvas_view.setCurrentIndex(idx)
app.processEvents()
editor = w.param_holder.findChild(QLineEdit)
editor.setFocus()
app.processEvents()
log(f"LINEEDIT_FOCUS action_enabled: {w.delete_action.isEnabled()}")
assert not w.delete_action.isEnabled()

# ---- 2) dragEnter 探测契约 ----
mime = model.mimeData([model.indexFromItem(model.find_by_id("done"))])
probe = model.canDropMimeData(mime, Qt.DropAction.MoveAction, -1, -1, QModelIndex())
log(f"DRAG_ENTER_PROBE accepted: {probe}")
assert probe

# ---- 3) 画布拖拽配置（真实放置手感以人工实测为准：OLE 拖拽无法用合成事件自动化）----
view = w.canvas_view
log(
    "VIEW drag config: "
    f"dragEnabled={view.dragEnabled()} acceptDrops={view.acceptDrops()} "
    f"mode={view.dragDropMode().name} defaultAction={view.defaultDropAction().name}"
)
assert view.dragEnabled() and view.acceptDrops()
log("OK")
app.quit()
