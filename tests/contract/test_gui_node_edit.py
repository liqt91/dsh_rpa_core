"""节点增删契约测试（GUI 切片 5）。

覆盖：
- 唯一节点 id 分配（n1、n2…跳过已占用）；
- 新 action item 的命令/空参数/holder raw 正确；
- 插入落点：根末尾、if 的 then 分组、虚拟组、叶子同级；
- 删除：叶子可删、根与虚拟分组受保护、容器连带子树；
- 增删后回写文档可通过 Workflow 校验，并驱动主窗口脏标记。

offscreen Qt 平台；缺 PySide6 时整组跳过。
"""

from __future__ import annotations

import copy
import os

# 必须在导入 Qt / 创建 QApplication 之前指定离屏平台
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QLineEdit  # noqa: E402

from rpa_core.gui.app import SAMPLE_WORKFLOW, MainWindow  # noqa: E402
from rpa_core.gui.flow_model import (  # noqa: E402
    ROLE_ARGS_RAW,
    ROLE_COMMAND_ID,
    ROLE_IS_VIRTUAL,
    ROLE_NODE_ID,
    ROLE_NODE_TYPE,
    build_item,
    build_model_from_workflow,
    model_to_workflow,
)
from rpa_core.model.workflow import Workflow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from rpa_core.gui.app import build_application

    return build_application()


@pytest.fixture(scope="module")
def catalog(qapp):
    from rpa_core.catalog import load_catalog
    from rpa_core.cli import _commands_root

    return load_catalog(_commands_root())


@pytest.fixture()
def model():
    return build_model_from_workflow(copy.deepcopy(SAMPLE_WORKFLOW))


def _doc(model) -> dict:
    """回写为完整文档（含示例 meta），用于 Workflow 校验。"""
    meta = {key: SAMPLE_WORKFLOW[key] for key in SAMPLE_WORKFLOW if key != "root"}
    return model_to_workflow(model, meta)


def _else_marker(if_item):
    """取 if 下的「否则」分割行 item。"""
    return next(
        if_item.child(r) for r in range(if_item.rowCount())
        if if_item.child(r).data(ROLE_NODE_TYPE) == "else-marker"
    )


# ---- 模型层：id 与新建 ----------------------------------------------------
def test_allocate_node_id_unique(model):
    existing = model.existing_ids()
    # id 在「插入」时分配；连续插入两次得到不重复且不撞既有 id 的新 id
    first = model.insert_command("workflow.sleep").data(ROLE_NODE_ID)
    second = model.insert_command("data.setVar").data(ROLE_NODE_ID)
    assert first == "n1" and second == "n2"
    assert first not in existing and second not in existing


def test_allocate_skips_occupied_id(model):
    # 手工挂入 id="n1" 的真实节点后，分配结果应跳到 n2
    occupied = build_item(
        {"type": "action", "id": "n1", "command": "workflow.sleep", "with": {}}
    )
    model.item(0).appendRow(occupied)
    assert model.allocate_node_id() == "n2"


def test_create_action_item_shape(model):
    item = model.create_action_item("data.setVar")
    assert item.data(ROLE_COMMAND_ID) == "data.setVar"
    assert item.data(ROLE_NODE_TYPE) == "action"
    assert item.data(ROLE_NODE_ID).startswith("n")
    holder = item.data(ROLE_ARGS_RAW)
    assert holder.args == {}
    assert holder.raw == {
        "type": "action", "id": item.data(ROLE_NODE_ID),
        "command": "data.setVar", "with": {},
    }


# ---- 插入落点 -------------------------------------------------------------
def test_insert_without_target_appends_to_root(model):
    new_item = model.insert_command("workflow.sleep")
    root = model.item(0)
    assert root.child(root.rowCount() - 1) is new_item
    doc = _doc(model)
    assert doc["root"]["children"][-1]["command"] == "workflow.sleep"
    Workflow.model_validate(doc)  # 回写文档必须合法


def test_insert_targeting_if_goes_into_then(model):
    """选中 if 卡片新增 → 追加进 then 分支（「否则」标记行之前）。"""
    if_item = model.find_by_id("check")
    marker_row = _else_marker(if_item).row()
    new_item = model.insert_command("workflow.sleep", if_item)
    assert new_item.parent() is if_item  # 不再是嵌套的 branch-then 虚拟组
    assert new_item.row() == marker_row  # 顶在「否则」行原位置 = then 分支末位
    assert _else_marker(if_item).row() == marker_row + 1
    doc = _doc(model)
    check = next(c for c in doc["root"]["children"] if c["id"] == "check")
    assert check["then"][-1]["command"] == "workflow.sleep"
    assert [c["id"] for c in check["else"]] == ["wait"]  # else 段未被串扰
    Workflow.model_validate(doc)


def test_insert_targeting_else_marker_appends_to_else_branch(model):
    """选中「否则」行新增 → 追加进 else 分支（容器末尾、结束行之前）。"""
    if_item = model.find_by_id("check")
    marker = _else_marker(if_item)
    before = [c.get("id") for c in _doc(model)["root"]["children"]]
    new_item = model.insert_command("data.setVar", marker)
    assert new_item.parent() is if_item
    assert new_item.row() > marker.row()  # 落在 else 段
    doc = _doc(model)
    check = next(c for c in doc["root"]["children"] if c["id"] == "check")
    assert check["else"][-1]["command"] == "data.setVar"  # 追加在既有 else 之后
    assert check["then"][-1]["command"] == "browser.getText"  # then 未被串扰
    assert [c.get("id") for c in doc["root"]["children"]] == before  # 结构未变
    Workflow.model_validate(doc)


def test_insert_targeting_leaf_becomes_sibling(model):
    leaf = model.find_by_id("open")
    new_item = model.insert_command("workflow.sleep", leaf)
    # open 在根 sequence 下：新节点是根的末位子节点（open 的同级）
    assert new_item.parent() is model.item(0)
    assert model.item(0).child(model.item(0).rowCount() - 1) is new_item


# ---- 回归：新增节点不得落到「结束 X」行下方 -------------------------------
# end-bracket（结束标记行）是容器自己的最后一个 child，因此 rowCount() 不等于
# "末位子节点的下一行"。曾经用 appendRow(rowCount()) 追加 → 节点画在结束线下方、
# parent 却仍是该容器（画面在容器外、AST 在容器内，存盘重开后"跳"回容器体内）。
def test_insert_into_container_stops_before_end_bracket(model):
    loop = model.find_by_id("loop")
    new_item = model.insert_command("data.setVar", loop)
    assert new_item.parent() is loop
    assert loop.child(loop.rowCount() - 1).data(ROLE_IS_VIRTUAL) is True  # 结束行仍在末尾
    assert new_item.row() == loop.rowCount() - 2  # 紧跟真实子节点之后
    # 回写：新节点仍是 loop.children 的末位（语义未变，只是不再画在结束线外）
    loop_doc = next(c for c in _doc(model)["root"]["children"] if c["id"] == "loop")
    assert [c["id"] for c in loop_doc["children"]] == [
        "append", new_item.data(ROLE_NODE_ID)
    ]
    Workflow.model_validate(_doc(model))


def test_insert_targeting_leaf_inside_container_stops_before_end_bracket(model):
    """选中循环体内某个节点后新增：落点为其所在容器末尾，同样要停在结束行之前。"""
    loop = model.find_by_id("loop")
    new_item = model.insert_command("data.setVar", model.find_by_id("append"))
    assert new_item.parent() is loop
    assert loop.child(loop.rowCount() - 1).data(ROLE_IS_VIRTUAL) is True
    assert new_item.row() == loop.rowCount() - 2


def test_insert_into_if_stops_before_else_marker(model):
    """if 里选中 then 分支的节点后新增：落点是 then 分支末尾（「否则」行之前）。

    if 改用扁平结构后，then 分支的边界不再是虚拟组，而是「否则」标记行。
    """
    if_item = model.find_by_id("check")
    marker_row = _else_marker(if_item).row()
    new_item = model.insert_command("data.setVar", model.find_by_id("read"))
    assert new_item.parent() is if_item
    assert new_item.row() == marker_row  # 紧贴「否则」行原位置（顶在它之前）
    assert _else_marker(if_item).row() == marker_row + 1  # 标记行被顶下去，仍在
    doc = _doc(model)
    check = next(c for c in doc["root"]["children"] if c["id"] == "check")
    assert [c["id"] for c in check["then"]] == ["read", new_item.data(ROLE_NODE_ID)]
    Workflow.model_validate(doc)


# ---- 删除 -----------------------------------------------------------------
def test_remove_leaf(model):
    leaf = model.find_by_id("open")
    assert model.remove_item(leaf) is True
    assert model.find_by_id("open") is None
    Workflow.model_validate(_doc(model))


def test_root_and_marker_row_protected(model):
    assert model.remove_item(model.item(0)) is False
    marker = _else_marker(model.find_by_id("check"))
    assert marker.data(ROLE_IS_VIRTUAL) is True
    assert model.remove_item(marker) is False  # 「否则」标记行不可删
    end_bracket = model.find_by_id("loop").child(
        model.find_by_id("loop").rowCount() - 1
    )
    assert model.remove_item(end_bracket) is False  # 结束行不可删


def test_remove_container_takes_subtree(model):
    if_item = model.find_by_id("check")
    assert model.remove_item(if_item) is True
    assert model.find_by_id("check") is None
    assert model.find_by_id("read") is None  # then 内子节点随之移除
    assert model.find_by_id("wait") is None  # else 内子节点随之移除
    Workflow.model_validate(_doc(model))


# ---- 主窗口集成：双击入口、脏标记、保存 -----------------------------------
def test_add_command_via_window_marks_dirty_and_selects(catalog):
    window = MainWindow(catalog, SAMPLE_WORKFLOW)
    assert window._dirty is False
    new_item = window.add_command("workflow.sleep")
    assert window._dirty is True
    # 新节点成为画布当前选中项
    assert window.canvas_view.currentIndex().isValid()
    assert window.flow_model.itemFromIndex(
        window.canvas_view.currentIndex()
    ) is new_item


def test_double_click_command_tree_leaf_inserts(catalog):
    from PySide6.QtWidgets import QTreeWidgetItem

    from rpa_core.gui.app import ROLE_COMMAND_ID as TREE_ROLE

    window = MainWindow(catalog, SAMPLE_WORKFLOW)

    # 找到左树里 browser.navigate 叶子
    leaf: QTreeWidgetItem | None = None
    for group_index in range(window.command_tree.topLevelItemCount()):
        group = window.command_tree.topLevelItem(group_index)
        for child_index in range(group.childCount()):
            candidate = group.child(child_index)
            if candidate.data(0, TREE_ROLE) == "browser.navigate":
                leaf = candidate
                break
    assert leaf is not None

    before = window.flow_model.item(0).rowCount()
    window._on_command_double_clicked(leaf, 0)
    assert window.flow_model.item(0).rowCount() == before + 1
    # 双击分组节点（command id 为 None）不应插入
    group = window.command_tree.topLevelItem(0)
    window._on_command_double_clicked(group, 0)
    assert window.flow_model.item(0).rowCount() == before + 1


def test_delete_handler_removes_selected_and_clears_form(catalog):
    window = MainWindow(catalog, SAMPLE_WORKFLOW)
    index = window.flow_model.indexFromItem(window.flow_model.find_by_id("open"))
    window.canvas_view.setCurrentIndex(index)
    window._delete_selected_node()
    assert window.flow_model.find_by_id("open") is None
    assert window._dirty is True


def test_delete_works_while_command_tree_has_focus(catalog, qapp):
    """双击左树添加后焦点停在左树：Delete 仍须删除画布选中节点。"""
    window = MainWindow(catalog, SAMPLE_WORKFLOW)
    # show 让焦点事件真实流转；收尾用 hide 而非 close（close 会弹未保存
    # 确认框，QMessageBox.exec 在 offscreen 平台会访问冲突）。
    window.show()
    qapp.processEvents()
    try:
        index = window.flow_model.indexFromItem(
            window.flow_model.find_by_id("open")
        )
        window.canvas_view.setCurrentIndex(index)
        window.command_tree.setFocus()
        qapp.processEvents()
        # 窗口级快捷键，左树/画布聚焦都可用；仅在文本编辑控件内暂停
        assert (
            window.delete_action.shortcutContext()
            == Qt.ShortcutContext.WindowShortcut
        )
        assert window.delete_action.isEnabled()
        window.delete_action.trigger()
        assert window.flow_model.find_by_id("open") is None
    finally:
        window.hide()
        qapp.processEvents()


def test_delete_suppressed_while_param_editor_focused(catalog, qapp):
    """焦点在参数表单输入框时 Delete 交给编辑，不触发节点删除。"""
    window = MainWindow(catalog, SAMPLE_WORKFLOW)
    window.show()
    qapp.processEvents()
    try:
        index = window.flow_model.indexFromItem(
            window.flow_model.find_by_id("open")
        )
        window.canvas_view.setCurrentIndex(index)
        qapp.processEvents()
        editor = window.param_holder.findChild(QLineEdit)
        assert editor is not None  # browser.navigate 的 url 为文本输入
        editor.setFocus()
        qapp.processEvents()
        assert not window.delete_action.isEnabled()
        window.delete_action.trigger()  # 禁用态 trigger 无动作
        assert window.flow_model.find_by_id("open") is not None
    finally:
        window.hide()
        qapp.processEvents()


def test_added_node_roundtrips_through_save(catalog, tmp_path):
    window = MainWindow(catalog, SAMPLE_WORKFLOW)
    window.add_command("workflow.sleep")
    target = tmp_path / "workflow.json"
    assert window.save_workflow(target) == target
    written = Workflow.model_validate_json(target.read_text(encoding="utf-8"))
    commands = [
        node.command
        for node in written.root.children
        if node.type == "action"
    ]
    assert "workflow.sleep" in commands
