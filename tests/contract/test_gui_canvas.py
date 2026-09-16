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
    # sequence root 已扁平化：invisibleRootItem 上挂着 root 的原始 id，
    # children 直接是原 sequence 的顶层指令（没有"顺序执行"卡片行）
    root = model.invisibleRootItem()
    assert root.data(ROLE_NODE_ID) == "root"  # root 的 id 被保留
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


def test_if_then_else_and_foreach_map_to_flat_structure():
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
    root = model.invisibleRootItem()

    # if：扁平结构（影刀式）——then 子节点 → 「否则」指令行 → else 子节点 → 结束 如果
    if_item = _child(root, 0)
    assert if_item.data(ROLE_NODE_TYPE) == "if"
    rows = [
        (if_item.child(r).data(ROLE_NODE_TYPE), if_item.child(r).data(ROLE_COMMAND_ID))
        for r in range(if_item.rowCount())
    ]
    assert rows == [
        ("action", "data.setVar"),
        ("else-branch", None),
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


def test_if_without_else_has_no_else_row():
    """「否则」按需添加：AST 里没有 else 段的 if 不显示否则行（默认不加）。"""
    workflow = {
        "schema_version": "1.0", "id": "s", "name": "s",
        "root": {"type": "sequence", "id": "root", "children": [
            {"type": "if", "id": "c", "condition": {"op": "truthy", "left": "${x}"},
             "then": [{"type": "action", "id": "a1", "command": "data.setVar",
                       "with": {}}]},
        ]},
    }
    model = build_model_from_workflow(workflow)
    if_item = _child(model.invisibleRootItem(), 0)
    types = [if_item.child(r).data(ROLE_NODE_TYPE) for r in range(if_item.rowCount())]
    assert types == ["action", "end-bracket"]  # 没有 else-branch
    # else 段为空时不落盘 else 键（默认形态不制造空 else）
    doc = model_to_workflow(model, {"schema_version": "1.0", "id": "s", "name": "s"})
    assert "else" not in doc["root"]["children"][0]


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
    root = model.invisibleRootItem()
    before = [_child(root, r).data(ROLE_NODE_ID) for r in range(root.rowCount())]
    assert _drop(model, before[0], root, row=root.rowCount())  # 首项移到末尾
    after = [_child(root, r).data(ROLE_NODE_ID) for r in range(root.rowCount())]
    assert after == before[1:] + [before[0]]


def test_cross_container_move_into_if_then_branch():
    """跨容器拖拽：落到 if 内 then 段末尾（默认无否则行时即结束行之前）。"""
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
    root = model.invisibleRootItem()
    if_item = _child(root, 1)
    then_tail = 1  # then 子节点 after 之后、结束行之前
    assert _drop(model, "free", if_item, row=then_tail)
    moved = model.find_by_id("free")
    assert moved.parent() is if_item
    assert root.rowCount() == 1  # 已移出根
    assert moved.row() == then_tail  # 落在 then 段末尾
    assert _child(if_item, then_tail + 1).data(ROLE_NODE_TYPE) == "end-bracket"


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
    root = model.invisibleRootItem()
    if_item = _child(root, 0)
    # 把 if 自身拖进它自己 → 成环，必须拒绝
    assert not _drop(model, "c", if_item)
    # 拖到 if 的结束标记行上 → 路由到 if 本身，同样成环，拒绝
    bracket = _child(if_item, if_item.rowCount() - 1)
    assert bracket.data(ROLE_NODE_TYPE) == "end-bracket"
    assert not _drop(model, "c", bracket)
    # 拖到 action 叶子上 → 叶子非容器，拒绝
    assert not _drop(model, "c", _child(if_item, 0))


def test_add_else_branch_creates_optional_row():
    """「否则」按需添加：默认没有，add_else_branch 之后才出现，且只加一次。"""
    workflow = {
        "schema_version": "1.0", "id": "s", "name": "s",
        "root": {"type": "sequence", "id": "root", "children": [
            {"type": "if", "id": "c", "condition": {"op": "truthy", "left": "${x}"},
             "then": [{"type": "action", "id": "t1", "command": "data.limit",
                       "with": {}}]},
        ]},
    }
    model = build_model_from_workflow(workflow)
    if_item = model.find_by_id("c")
    assert [if_item.child(r).data(ROLE_NODE_TYPE) for r in range(if_item.rowCount())] == [
        "action", "end-bracket"
    ]
    marker = model.add_else_branch(if_item)
    assert marker is not None
    assert marker.data(ROLE_NODE_TYPE) == "else-branch"
    assert [if_item.child(r).data(ROLE_NODE_TYPE) for r in range(if_item.rowCount())] == [
        "action", "else-branch", "end-bracket"  # 插在 then 之后、结束行之前
    ]
    assert model.add_else_branch(if_item) is None  # 已有则不重复添加
    # 非 if 容器不允许添加否则
    assert model.add_else_branch(model.invisibleRootItem()) is None


def test_else_branch_can_be_dragged_inside_if_but_not_out():
    """「否则」是可拖指令，但只能在 if 内——拖到别的容器必须拒绝。"""
    workflow = {
        "schema_version": "1.0", "id": "s", "name": "s",
        "root": {"type": "sequence", "id": "root", "children": [
            {"type": "if", "id": "c", "condition": {"op": "truthy", "left": "${x}"},
             "then": [{"type": "action", "id": "t1", "command": "data.limit",
                       "with": {}}]},
            {"type": "if", "id": "c2", "condition": {"op": "truthy", "left": "${y}"},
             "then": [{"type": "action", "id": "t2", "command": "data.limit",
                       "with": {}}]},
        ]},
    }
    model = build_model_from_workflow(workflow)
    if_item = model.find_by_id("c")
    marker = model.add_else_branch(if_item)
    marker_index = model.indexFromItem(marker)
    # 它是"可拖放的指令"：可选中、可拖
    assert model.flags(marker_index) & Qt.ItemFlag.ItemIsSelectable
    assert model.flags(marker_index) & Qt.ItemFlag.ItemIsDragEnabled
    mime = model.mimeData([marker_index])
    assert model.canDropMimeData(
        mime, Qt.DropAction.MoveAction, -1, 0, model.indexFromItem(if_item)
    )
    # 拖进另一个 if：会静默改写两个 if 的分支归属，拒绝
    assert not model.canDropMimeData(
        mime, Qt.DropAction.MoveAction, -1, 0, model.indexFromItem(model.find_by_id("c2"))
    )
    # 拖到根 sequence：非 if 容器，拒绝
    assert not model.canDropMimeData(
        mime, Qt.DropAction.MoveAction, -1, 0, model.indexFromItem(model.invisibleRootItem())
    )
    # 在自己 if 内移动则合法：拖到最前面 → then 段清空，原 then 子节点改归 else
    assert model.dropMimeData(
        mime, Qt.DropAction.MoveAction, 0, 0, model.indexFromItem(if_item)
    )
    doc = model_to_workflow(model, {"schema_version": "1.0", "id": "s", "name": "s"})
    check = doc["root"]["children"][0]
    assert check["then"] == []
    assert [c["id"] for c in check["else"]] == ["t1"]


def test_gutter_numbering_is_logical_and_stable_on_collapse(qapp):
    """编号栏行号是逻辑行号（全树前序位置）：序号跟随指令本身，
    折叠容器只是隐藏子行，下方指令的行号不因此改变（影刀同款）。"""
    from rpa_core.gui.canvas import CardDelegate

    model = build_model_from_workflow({
        "schema_version": "1.0", "id": "s", "name": "s",
        "root": {
            "type": "sequence", "id": "root",
            "children": [
                {"type": "action", "id": "a1", "command": "data.setVar", "with": {}},
                {"type": "if", "id": "c",
                 "condition": {"op": "truthy", "left": "${x}"},
                 "then": [{"type": "action", "id": "t1", "command": "data.setVar",
                           "with": {}}]},
                {"type": "action", "id": "a2", "command": "workflow.sleep",
                 "with": {}},
            ],
        },
    })
    view = build_canvas(model)
    view.resize(640, 480)
    view.show()
    view.expandAll()
    qapp.processEvents()
    delegate = view.itemDelegate()
    assert isinstance(delegate, CardDelegate)

    # 展开时全树前序：a1=1，if=2，then 内 t1=3，结束行=4，a2=5
    a2 = model.index(2, 0)
    assert delegate._row_number(a2) == 5
    # 折叠 if：t1/结束行被隐藏，但 a2 的逻辑行号仍是 5（不随可见性改变）
    view.setExpanded(model.index(1, 0), False)
    qapp.processEvents()
    assert delegate._row_number(a2) == 5
    view.deleteLater()


def test_gutter_error_badge_on_missing_required_args(qapp):
    """错误徽标判定：action 必填参数缺失为错误，补齐后消除；非 action 行不判。"""
    from rpa_core.catalog import load_catalog
    from rpa_core.cli import _commands_root

    catalog = load_catalog(_commands_root())
    model = build_model_from_workflow({
        "schema_version": "1.0", "id": "s", "name": "s",
        "root": {
            "type": "sequence", "id": "root",
            "children": [
                {"type": "action", "id": "nav", "command": "browser.navigate",
                 "with": {"url": "https://example.com"}},  # 缺必填 browserType
                {"type": "action", "id": "ok", "command": "workflow.sleep",
                 "with": {"seconds": 1}},
            ],
        },
    })
    view = build_canvas(model, catalog=catalog)
    delegate = view.itemDelegate()

    assert delegate._has_config_error(model.index(0, 0)) is True   # 缺 browserType
    assert delegate._has_config_error(model.index(1, 0)) is False  # 必填已填
    view.deleteLater()


def test_gutter_collapse_button_toggles_expansion(qapp):
    """编号栏收起/展开按钮：合成点击热区 → 容器折叠，再点 → 展开。"""
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent, QPointingDevice
    from PySide6.QtWidgets import QApplication

    from rpa_core.gui.canvas import collapse_button_rect

    model = build_model_from_workflow({
        "schema_version": "1.0", "id": "s", "name": "s",
        "root": {
            "type": "sequence", "id": "root",
            "children": [
                {"type": "forEach", "id": "f", "items": "${rows}",
                 "children": [{"type": "action", "id": "a", "command": "data.limit",
                               "with": {}}]},
            ],
        },
    })
    view = build_canvas(model)
    view.resize(640, 480)
    view.show()
    view.expandAll()
    qapp.processEvents()

    container = model.index(0, 0)
    assert view.isExpanded(container)

    def _click(pos):
        event = QMouseEvent(
            QEvent.Type.MouseButtonRelease, QPointF(pos),
            QPointF(view.viewport().mapToGlobal(pos)),
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            QPointingDevice.primaryPointingDevice(),
        )
        QApplication.sendEvent(view.viewport(), event)

    btn = collapse_button_rect(view.visualRect(container))
    _click(btn.center())
    assert not view.isExpanded(container)   # 折叠
    _click(btn.center())
    assert view.isExpanded(container)       # 再点展开
    view.deleteLater()


def test_delete_button_rect_inside_card_right_tail():
    """删除按钮矩形：卡片右尾内部（右边距 6px）、垂直居中、固定尺寸——
    paint 与 mouseReleaseEvent 热区共用同一函数，保证所见即所点。"""
    from PySide6.QtCore import QRect

    from rpa_core.gui.canvas import delete_button_rect

    card = QRect(10, 10, 400, 40)
    btn = delete_button_rect(card)
    assert card.contains(btn)
    assert card.right() - btn.right() == 6
    assert btn.height() == 18 and btn.width() == 36
    assert btn.center().y() == card.center().y()


def test_else_branch_delete_button_click_removes_marker(qapp):
    """回归：点击「否则」指令行的删除按钮可删除该行（取消 else 分支）。

    旧实现在 mouseReleaseEvent 里用 ROLE_IS_VIRTUAL 一刀切拦截，
    而 else-branch 行 virtual=True（模型层 remove_item 对其例外放行），
    导致点删除按钮无效；修复后可删性判定委托模型单源。
    """
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent, QPointingDevice
    from PySide6.QtWidgets import QApplication

    from rpa_core.gui.canvas import delete_button_rect

    model = build_model_from_workflow({
        "schema_version": "1.0", "id": "s", "name": "s",
        "root": {
            "type": "sequence", "id": "root",
            "children": [
                {"type": "if", "id": "c",
                 "condition": {"op": "truthy", "left": "${x}"},
                 "then": [{"type": "action", "id": "t1", "command": "data.setVar",
                           "with": {}}],
                 "else": [{"type": "action", "id": "e1", "command": "workflow.sleep",
                           "with": {}}]},
            ],
        },
    })
    view = build_canvas(model)
    view.resize(640, 480)
    view.show()
    view.expandAll()
    qapp.processEvents()

    else_index = None
    if_index = model.index(0, 0)
    for row in range(model.rowCount(if_index)):
        candidate = model.index(row, 0, if_index)
        if candidate.data(ROLE_NODE_TYPE) == "else-branch":
            else_index = candidate
            break
    assert else_index is not None, "夹具应含 else-branch 行"

    # 合成点击：删除按钮热区中心（热区与绘制矩形同源）
    btn = delete_button_rect(view.visualRect(else_index))
    pos = QPointF(btn.center())
    event = QMouseEvent(
        QEvent.Type.MouseButtonRelease, pos,
        QPointF(view.viewport().mapToGlobal(btn.center())),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        QPointingDevice.primaryPointingDevice(),
    )
    QApplication.sendEvent(view.viewport(), event)

    for row in range(model.rowCount(if_index)):
        assert model.index(row, 0, if_index).data(ROLE_NODE_TYPE) != "else-branch"
    # AST 回写：else 段取消，e1 并入 then 末尾
    doc = model_to_workflow(model, {"schema_version": "1.0", "id": "s", "name": "s"})
    check = doc["root"]["children"][0]
    assert [c["id"] for c in check["then"]] == ["t1", "e1"]
    assert "else" not in check
    view.deleteLater()


def test_flags_enforce_drag_drop_rules():
    # 自带夹具（顶层 action + forEach），不依赖 workflows/test 手测草稿的内容——
    # 该文件随时被手动编辑，历史上因其缺少顶层 forEach 导致本测试 StopIteration。
    model = build_model_from_workflow({
        "schema_version": "1.0", "id": "s", "name": "s",
        "root": {
            "type": "sequence", "id": "root",
            "children": [
                {"type": "action", "id": "a1", "command": "data.setVar", "with": {}},
                {"type": "forEach", "id": "f", "items": "${rows}",
                 "children": [{"type": "action", "id": "a2", "command": "data.limit",
                               "with": {}}]},
            ],
        },
    })
    # sequence root 已扁平化，顶层直接是真实节点（action/forEach/if/return）
    # 顶层节点都应该可拖（它们都是原 sequence 的 children，不是 root 容器本身）
    top0 = model.index(0, 0)  # 顶层第一个真实节点
    flags0 = model.flags(top0)
    assert flags0 & Qt.ItemFlag.ItemIsDragEnabled  # 顶层 action 可拖
    # 顶层 action 叶子不可放置（drop 到它上面等于 drop 到其父容器）
    assert not (flags0 & Qt.ItemFlag.ItemIsDropEnabled)

    # 顶层 forEach 容器：可拖可放
    for_each_row = next(
        row for row in range(model.invisibleRootItem().rowCount())
        if model.invisibleRootItem().child(row).data(ROLE_NODE_TYPE) == "forEach"
    )
    container_index = model.index(for_each_row, 0)
    container_flags = model.flags(container_index)
    assert container_flags & Qt.ItemFlag.ItemIsDragEnabled
    assert container_flags & Qt.ItemFlag.ItemIsDropEnabled


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
    """invalid parent（invisibleRootItem）在扁平化后是合法 drop 目标——
    item 应该被移动到顶层指定位置。
    """
    model = build_model_from_workflow(real_workflow)
    source = model.invisibleRootItem().child(0)
    mime = model.mimeData([model.indexFromItem(source)])
    # row=-1 表示 append 到末尾；row=0 表示插到最前
    initial_count = model.invisibleRootItem().rowCount()
    ok = model.dropMimeData(mime, Qt.DropAction.MoveAction, -1, 0, QModelIndex())
    assert ok is True
    assert model.invisibleRootItem().rowCount() == initial_count  # 同一批元素总数不变


def test_drag_enter_probe_with_invalid_parent_accepted(real_workflow):
    """QTreeView.dragEnterEvent 用无效 parent + row=-1 探测模型接受能力。

    若此处拒绝，拖拽从进入控件起就被整体忽略（dragMove/drop 回调不再发生），
    真实窗口将表现为「完全无法拖拽」；格式可接受时必须放行。
    """
    model = build_model_from_workflow(real_workflow)
    source = model.invisibleRootItem().child(0)
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
    loop = _child(model.invisibleRootItem(), 1)
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


def test_else_branch_hit_test_splits_then_and_else(qapp):
    """「否则」行上半 → then 分支末尾；下半 → else 分支开头。

    影刀式扁平结构下，if 的两个分支由这条指令行分割，落点必须严格分侧：
    画面上"放到 否则 上面"就该属于 then，"放到 否则 下面"才属于 else。
    """
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QDragMoveEvent

    model = build_model_from_workflow(_IF_WORKFLOW)
    if_item = model.find_by_id("c")
    marker = next(
        if_item.child(r) for r in range(if_item.rowCount())
        if if_item.child(r).data(ROLE_NODE_TYPE) == "else-branch"
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
