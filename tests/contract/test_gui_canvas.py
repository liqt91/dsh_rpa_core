"""GUI 切片 2：流程卡片画布的 headless 测试（AST→模型、拖拽合法性、delegate）。"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

pytest.importorskip("PySide6", reason="原生 GUI 为可选能力，需 uv sync --extra gui")

from PySide6.QtCore import QModelIndex, Qt  # noqa: E402

from rpa_core.gui.canvas import CardDelegate, build_canvas  # noqa: E402
from rpa_core.gui.flow_model import (  # noqa: E402
    ROLE_ARGS_SUMMARY,
    ROLE_COMMAND_ID,
    ROLE_IS_VIRTUAL,
    ROLE_NODE_ID,
    ROLE_NODE_TYPE,
    build_model_from_workflow,
    model_to_workflow,
    summarize_args,
)
from rpa_core.model.workflow import Workflow  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]


def _child(item, row):
    return item.child(row)


@pytest.fixture(scope="module")
def real_workflow():
    path = REPO_ROOT / "workflows" / "test" / "workflow.json"
    return Workflow.model_validate_json(path.read_text(encoding="utf-8"))


def test_real_workflow_maps_to_sequence_with_actions(real_workflow):
    model = build_model_from_workflow(real_workflow)
    root = model.item(0)
    assert root.data(ROLE_NODE_TYPE) == "sequence"
    assert root.data(ROLE_NODE_ID) == "root"
    commands = [
        _child(root, row).data(ROLE_COMMAND_ID) for row in range(root.rowCount())
    ]
    assert "data.setVar" in commands and "browser.navigate" in commands
    # navigate 带 4 个参数（action/onTimeout/url/browserType），摘要取前 2 个 + 计数
    navigate = next(
        _child(root, row) for row in range(root.rowCount())
        if _child(root, row).data(ROLE_COMMAND_ID) == "browser.navigate"
    )
    summary = navigate.data(ROLE_ARGS_SUMMARY) or ""
    assert "action=goto" in summary and summary.endswith("+2")


@pytest.fixture(scope="module")
def qapp():
    from rpa_core.gui.app import build_application

    return build_application()


def test_if_then_else_and_foreach_become_virtual_groups():
    workflow = {
        "schema_version": "1.0", "id": "s", "name": "s",
        "root": {
            "type": "sequence", "id": "root",
            "children": [
                {"type": "if", "id": "c", "condition": {"op": "truthy", "left": "${x}"},
                 "then": [{"type": "action", "id": "a1", "command": "data.setVar",
                           "with": {}}],
                 "else": [{"type": "action", "id": "a2", "command": "workflow.sleep",
                           "with": {}}]},
                {"type": "forEach", "id": "f", "items": "${rows}",
                 "children": [{"type": "action", "id": "a3", "command": "data.limit",
                               "with": {}}]},
                {"type": "try", "id": "t",
                 "children": [{"type": "action", "id": "a4", "command": "data.format",
                               "with": {}}],
                 "catch": [{"type": "action", "id": "a5", "command": "data.setVar",
                            "with": {}}]},
            ],
        },
    }
    model = build_model_from_workflow(workflow)
    root = model.item(0)

    # if：扁平结构（影刀式）——then 子节点 → 「否则」行 → else 子节点 → 结束 如果
    if_item = _child(root, 0)
    assert if_item.data(ROLE_NODE_TYPE) == "if"
    rows = [
        (if_item.child(r).data(ROLE_NODE_TYPE), if_item.child(r).data(ROLE_COMMAND_ID))
        for r in range(if_item.rowCount())
    ]
    assert rows == [
        ("action", "data.setVar"),
        ("else-marker", None),
        ("action", "workflow.sleep"),
        ("end-bracket", None),
    ]
    then_row, marker, else_row, end_row = (if_item.child(r) for r in range(4))
    assert not then_row.data(ROLE_IS_VIRTUAL)  # then 子节点是真实节点，不再是虚拟组
    assert marker.data(ROLE_IS_VIRTUAL) and else_row.data(ROLE_COMMAND_ID) == "workflow.sleep"
    assert end_row.data(Qt.ItemDataRole.DisplayRole) == "结束 如果"

    foreach = _child(root, 1)
    assert _child(foreach, 0).data(ROLE_COMMAND_ID) == "data.limit"

    try_item = _child(root, 2)
    assert _child(try_item, 0).data(ROLE_COMMAND_ID) == "data.format"
    catch_group = _child(try_item, 1)
    assert catch_group.data(ROLE_NODE_TYPE) == "branch-catch"
    assert _child(catch_group, 0).data(ROLE_COMMAND_ID) == "data.setVar"


def test_else_marker_is_always_present_even_for_if_without_else():
    """「否则」行常驻：没有 else 的 if 也有明确落点，可直接拖进去建分支。"""
    workflow = {
        "schema_version": "1.0", "id": "s", "name": "s",
        "root": {"type": "sequence", "id": "root", "children": [
            {"type": "if", "id": "c", "condition": {"op": "truthy", "left": "${x}"},
             "then": [{"type": "action", "id": "a1", "command": "data.setVar",
                       "with": {}}]},
        ]},
    }
    model = build_model_from_workflow(workflow)
    if_item = _child(model.item(0), 0)
    types = [if_item.child(r).data(ROLE_NODE_TYPE) for r in range(if_item.rowCount())]
    assert types == ["action", "else-marker", "end-bracket"]


def test_summarize_args_limits_pairs_and_truncates():
    assert summarize_args({}) == ""
    assert summarize_args({"path": "a.txt"}) == "path=a.txt"
    summary = summarize_args({"a": "1", "b": "2", "c": "3"})
    assert summary.endswith("+1")


def _drop(model, source_id: str, target_item, row: int = -1) -> bool:
    """模拟 QTreeView InternalMove：取源行 mime 后投递到目标容器。"""
    source = model.find_by_id(source_id)
    index = model.indexFromItem(source)
    mime = model.mimeData([index])
    parent_index = model.indexFromItem(target_item)
    return model.dropMimeData(mime, Qt.DropAction.MoveAction, row, 0, parent_index)


def test_same_level_reorder_via_drop(real_workflow):
    model = build_model_from_workflow(real_workflow)
    root = model.item(0)
    before = [_child(root, r).data(ROLE_NODE_ID) for r in range(root.rowCount())]
    assert _drop(model, before[0], root, row=root.rowCount())  # 首项移到末尾
    after = [_child(root, r).data(ROLE_NODE_ID) for r in range(root.rowCount())]
    assert after == before[1:] + [before[0]]


def test_cross_container_move_into_if_then_branch():
    """跨容器拖拽：落到 if 的「否则」行之前 = 进 then 分支。"""
    workflow = {
        "schema_version": "1.0", "id": "s", "name": "s",
        "root": {"type": "sequence", "id": "root", "children": [
            {"type": "action", "id": "free", "command": "data.setVar", "with": {}},
            {"type": "if", "id": "c", "condition": {"op": "truthy", "left": "${x}"},
             "then": [{"type": "action", "id": "inside", "command": "data.limit",
                       "with": {}}]},
        ]},
    }
    model = build_model_from_workflow(workflow)
    root = model.item(0)
    if_item = _child(root, 1)
    marker_row = 1  # then 子节点 after 之后、结束行之前
    assert _drop(model, "free", if_item, row=marker_row)
    moved = model.find_by_id("free")
    assert moved.parent() is if_item
    assert root.rowCount() == 1  # 已移出根
    assert [moved.row(), _child(if_item, moved.row() + 1).data(ROLE_NODE_TYPE)] == [
        marker_row, "else-marker"
    ]  # 落在 then 分支末尾，仍在「否则」行之前


def test_drop_into_own_descendant_is_rejected():
    workflow = {
        "schema_version": "1.0", "id": "s", "name": "s",
        "root": {"type": "sequence", "id": "root", "children": [
            {"type": "if", "id": "c", "condition": {"op": "truthy", "left": "${x}"},
             "then": [{"type": "action", "id": "inside", "command": "data.limit",
                       "with": {}}]},
        ]},
    }
    model = build_model_from_workflow(workflow)
    root = model.item(0)
    if_item = _child(root, 0)
    # 把 if 自身拖进它自己 → 成环，必须拒绝
    assert not _drop(model, "c", if_item)
    # 拖到 if 的「否则」标记行上 → 路由到 if 本身，同样成环，拒绝
    marker = _child(if_item, 1)
    assert not _drop(model, "c", marker)
    # 拖到 action 叶子上 → 叶子非容器，拒绝
    assert not _drop(model, "c", _child(if_item, 0))


def test_flags_enforce_drag_drop_rules(real_workflow):
    model = build_model_from_workflow(real_workflow)
    root_index = model.index(0, 0)
    root_flags = model.flags(root_index)
    assert not (root_flags & Qt.ItemFlag.ItemIsDragEnabled)  # 根不可拖
    assert root_flags & Qt.ItemFlag.ItemIsDropEnabled

    leaf_index = model.index(0, 0, root_index)
    leaf_flags = model.flags(leaf_index)
    assert leaf_flags & Qt.ItemFlag.ItemIsDragEnabled
    assert not (leaf_flags & Qt.ItemFlag.ItemIsDropEnabled)  # action 叶子不可放置


def test_canvas_builds_and_delegate_gives_fixed_row_height(qapp, real_workflow):
    model = build_model_from_workflow(real_workflow)
    view = build_canvas(model)
    assert view.model() is model
    delegate = view.itemDelegate()
    assert isinstance(delegate, CardDelegate)
    index = model.index(0, 0)
    from PySide6.QtCore import QSize
    from PySide6.QtWidgets import QStyleOptionViewItem

    hint = delegate.sizeHint(QStyleOptionViewItem(), index)
    assert isinstance(hint, QSize)
    assert hint.height() == 46
    view.deleteLater()


def test_invalid_parent_index_dropped(real_workflow):
    model = build_model_from_workflow(real_workflow)
    source = model.item(0).child(0)
    mime = model.mimeData([model.indexFromItem(source)])
    assert not model.dropMimeData(
        mime, Qt.DropAction.MoveAction, 0, 0, QModelIndex()
    )


def test_drag_enter_probe_with_invalid_parent_accepted(real_workflow):
    """QTreeView.dragEnterEvent 用无效 parent + row=-1 探测模型接受能力。

    若此处拒绝，拖拽从进入控件起就被整体忽略（dragMove/drop 回调不再发生），
    真实窗口将表现为「完全无法拖拽」；格式可接受时必须放行。
    """
    model = build_model_from_workflow(real_workflow)
    source = model.item(0).child(0)
    mime = model.mimeData([model.indexFromItem(source)])
    assert model.canDropMimeData(
        mime, Qt.DropAction.MoveAction, -1, -1, QModelIndex()
    )
    # 非本模型的 MIME 仍必须拒绝
    from PySide6.QtCore import QMimeData

    assert not model.canDropMimeData(
        QMimeData(), Qt.DropAction.MoveAction, -1, -1, QModelIndex()
    )


# ---- 回归：结束标记行（end-bracket）的落点语义 -----------------------------
# 结束行是容器自己的最后一个 child，所以"追加到容器末尾"不能用 rowCount()：
# 那会落到结束行**下方**（画面上在容器外，AST 里却在容器内）。
_BRACKET_WORKFLOW = {
    "schema_version": "1.0", "id": "s", "name": "s",
    "root": {"type": "sequence", "id": "root", "children": [
        {"type": "action", "id": "free", "command": "data.setVar", "with": {}},
        {"type": "forEach", "id": "loop", "items": "${rows}", "item_var": "row",
         "children": [
             {"type": "action", "id": "inner", "command": "data.limit", "with": {}},
         ]},
        {"type": "action", "id": "tail", "command": "data.setVar", "with": {}},
    ]},
}


def test_drop_at_container_tail_lands_before_end_bracket():
    """row=-1（拖到容器内部 / 结束行上半）必须插在「结束 X」之上。"""
    model = build_model_from_workflow(_BRACKET_WORKFLOW)
    loop = _child(model.item(0), 1)
    assert loop.rowCount() == 2  # inner + 结束行
    assert _drop(model, "free", loop, row=-1)
    rows = [_child(loop, r).data(ROLE_NODE_ID) for r in range(loop.rowCount())]
    assert rows == ["inner", "free", None]  # 结束行（无 id）仍在最后
    assert _child(loop, loop.rowCount() - 1).data(ROLE_IS_VIRTUAL) is True


def test_end_bracket_hit_test_splits_into_tail_and_sibling(qapp):
    """结束行上半 → 容器末尾（结束行之前）；下半 → 容器的同级下方。

    这是 canvas.FlowTreeView.dragMoveEvent 对结束行的 50/50 命中路由：
    下半是把节点拖出容器、成为容器同级的**唯一**手法。
    """
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QDragMoveEvent

    model = build_model_from_workflow(_BRACKET_WORKFLOW)
    loop = model.find_by_id("loop")
    bracket = _child(loop, loop.rowCount() - 1)
    view = build_canvas(model)
    view.resize(640, 900)
    view.show()
    qapp.processEvents()
    rect = view.visualRect(model.indexFromItem(bracket))
    assert rect.isValid() and rect.height() > 0

    mime = model.mimeData([model.indexFromItem(model.find_by_id("free"))])

    def probe(y: int):
        event = QDragMoveEvent(
            QPoint(rect.center().x(), y), Qt.DropAction.MoveAction, mime,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        )
        view.dragMoveEvent(event)
        # _drag_target 是 dragMoveEvent 与 dropEvent/paintEvent 之间约定的落点状态
        return view._drag_target

    upper = probe(rect.top() + 2)
    assert upper is not None and upper["mode"] == "end_above"
    assert upper["row"] == -1
    assert upper["parent"] == model.indexFromItem(loop)

    lower = probe(rect.bottom() - 2)
    assert lower is not None and lower["mode"] == "end_below"
    assert lower["parent"] == model.indexFromItem(loop).parent()
    view.deleteLater()


_IF_WORKFLOW = {
    "schema_version": "1.0", "id": "s", "name": "s",
    "root": {"type": "sequence", "id": "root", "children": [
        {"type": "action", "id": "free", "command": "data.setVar", "with": {}},
        {"type": "if", "id": "c", "condition": {"op": "truthy", "left": "${x}"},
         "then": [{"type": "action", "id": "t1", "command": "data.limit", "with": {}}],
         "else": [{"type": "action", "id": "e1", "command": "workflow.sleep",
                   "with": {}}]},
    ]},
}


def test_else_marker_hit_test_splits_then_and_else(qapp):
    """「否则」行上半 → then 分支末尾；下半 → else 分支开头。

    影刀式扁平结构下，if 的两个分支由这条标记行分割，落点必须严格分侧：
    画面上"放到 否则 上面"就该属于 then，"放到 否则 下面"才属于 else。
    """
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QDragMoveEvent

    model = build_model_from_workflow(_IF_WORKFLOW)
    if_item = model.find_by_id("c")
    marker = next(
        if_item.child(r) for r in range(if_item.rowCount())
        if if_item.child(r).data(ROLE_NODE_TYPE) == "else-marker"
    )
    view = build_canvas(model)
    view.resize(640, 900)
    view.show()
    qapp.processEvents()
    rect = view.visualRect(model.indexFromItem(marker))
    assert rect.isValid() and rect.height() > 0

    mime = model.mimeData([model.indexFromItem(model.find_by_id("free"))])

    def drop_at(y: int) -> dict:
        event = QDragMoveEvent(
            QPoint(rect.center().x(), y), Qt.DropAction.MoveAction, mime,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        )
        view.dragMoveEvent(event)
        assert view._drag_target is not None
        return view._drag_target

    upper = drop_at(rect.top() + 2)
    assert upper["mode"] == "else_above"
    assert (upper["row"], upper["parent"]) == (marker.row(), model.indexFromItem(if_item))

    lower = drop_at(rect.bottom() - 2)
    assert lower["mode"] == "else_below"
    assert (lower["row"], lower["parent"]) == (
        marker.row() + 1, model.indexFromItem(if_item)
    )

    # 真投一次上半：节点必须落进 then，else 段原样
    target = drop_at(rect.top() + 2)
    assert model.dropMimeData(
        mime, Qt.DropAction.MoveAction, target["row"], 0, target["parent"]
    )
    meta = {"schema_version": "1.0", "id": "s", "name": "s"}
    check = next(
        c for c in model_to_workflow(model, meta)["root"]["children"] if c["id"] == "c"
    )
    assert [c["id"] for c in check["then"]] == ["t1", "free"]
    assert [c["id"] for c in check["else"]] == ["e1"]
    view.deleteLater()
