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

    if_item = _child(root, 0)
    assert if_item.data(ROLE_NODE_TYPE) == "if"
    then_group, else_group = _child(if_item, 0), _child(if_item, 1)
    assert then_group.data(ROLE_IS_VIRTUAL) and else_group.data(ROLE_IS_VIRTUAL)
    assert then_group.data(ROLE_NODE_TYPE) == "branch-then"
    assert _child(then_group, 0).data(ROLE_COMMAND_ID) == "data.setVar"
    assert _child(else_group, 0).data(ROLE_COMMAND_ID) == "workflow.sleep"

    foreach = _child(root, 1)
    assert _child(foreach, 0).data(ROLE_COMMAND_ID) == "data.limit"

    try_item = _child(root, 2)
    assert _child(try_item, 0).data(ROLE_COMMAND_ID) == "data.format"
    catch_group = _child(try_item, 1)
    assert catch_group.data(ROLE_NODE_TYPE) == "branch-catch"
    assert _child(catch_group, 0).data(ROLE_COMMAND_ID) == "data.setVar"


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


def test_cross_container_move_into_then_group():
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
    then_group = _child(_child(root, 1), 0)
    assert _drop(model, "free", then_group)
    assert model.find_by_id("free").parent() is then_group
    assert root.rowCount() == 1  # 已移出根


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
    then_group = _child(if_item, 0)
    # 把 if 自身拖进它的 then 组 → 成环，必须拒绝
    assert not _drop(model, "c", then_group)
    # 拖到 action 叶子上 → 叶子非容器，拒绝
    leaf = _child(then_group, 0)
    assert not _drop(model, "c", leaf)


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
