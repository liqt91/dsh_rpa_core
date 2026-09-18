"""M23 G2 画布交互契约：多选批量移动/删除、右键菜单接线、Ctrl+F 画布内查找。

offscreen Qt；缺 PySide6 时整组跳过。
"""

from __future__ import annotations

import copy
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtCore import QItemSelectionModel, QMimeData, Qt  # noqa: E402
from PySide6.QtWidgets import QAbstractItemView, QLineEdit, QTreeView  # noqa: E402

from rpa_core.gui.app import SAMPLE_WORKFLOW, MainWindow  # noqa: E402
from rpa_core.gui.flow_model import (  # noqa: E402
    ROLE_NODE_ID,
    build_model_from_workflow,
    model_to_workflow,
)

_MIME = "application/x-rpa-flow-node"


@pytest.fixture(scope="module")
def qapp():
    from rpa_core.gui.app import build_application

    return build_application()


@pytest.fixture(scope="module")
def catalog(qapp):
    from rpa_core.catalog import load_catalog
    from rpa_core.cli import _commands_root

    return load_catalog(_commands_root())


def _mime(ids: list[str]) -> QMimeData:
    data = QMimeData()
    data.setData(_MIME, ";".join(ids).encode("utf-8"))
    return data


def _top_ids(model) -> list[str]:
    root = model.invisibleRootItem()
    return [root.child(row).data(ROLE_NODE_ID) for row in range(root.rowCount())]


# -- 多选批量移动（模型层） ----------------------------------------------------


def _flat_model() -> object:
    document = {
        "schema_version": "1.0",
        "id": "flat",
        "name": "flat",
        "root": {
            "type": "sequence",
            "id": "root",
            "children": [
                {"type": "action", "id": "a1", "command": "data.setVar", "with": {}},
                {"type": "action", "id": "a2", "command": "workflow.sleep", "with": {}},
                {"type": "action", "id": "a3", "command": "data.limit", "with": {}},
                {"type": "action", "id": "a4", "command": "data.fileExists", "with": {}},
            ],
        },
    }
    return build_model_from_workflow(document)


def test_multi_drag_moves_all_selected_in_order():
    model = _flat_model()
    # 把 a1、a2 一起拖到 a4 之后（顶层 row=4）
    target = model.invisibleRootItem()
    moved = model.dropMimeData(
        _mime(["a1", "a2"]), Qt.DropAction.MoveAction, 4, 0, target.index()
    )
    assert moved is True
    assert _top_ids(model) == ["a3", "a4", "a1", "a2"]


def test_multi_drag_preserves_relative_order_across_parents():
    document = {
        "schema_version": "1.0",
        "id": "nested",
        "name": "nested",
        "root": {
            "type": "sequence",
            "id": "root",
            "children": [
                {"type": "action", "id": "a1", "command": "data.setVar", "with": {}},
                {
                    "type": "forEach",
                    "id": "loop",
                    "items": "${rows}",
                    "children": [
                        {"type": "action", "id": "in1", "command": "data.limit",
                         "with": {}},
                        {"type": "action", "id": "in2", "command": "workflow.sleep",
                         "with": {}},
                    ],
                },
            ],
        },
    }
    model = build_model_from_workflow(document)
    loop = model.find_by_id("loop")
    # a1 与 in2 一起拖进 loop 末尾 → 相对顺序 a1 在前、in2 在后
    moved = model.dropMimeData(
        _mime(["a1", "in2"]), Qt.DropAction.MoveAction, -1, 0, loop.index()
    )
    assert moved is True
    child_ids = _real_child_ids(loop)
    assert child_ids == ["in1", "a1", "in2"]


def test_multi_drag_skips_descendants_of_dragged_container():
    # 造一个容器 + 子节点：把「容器」与其「子节点」同时选中拖动
    document = {
        "schema_version": "1.0", "id": "c", "name": "c",
        "root": {
            "type": "sequence", "id": "root",
            "children": [
                {"type": "forEach", "id": "loop", "items": "${rows}",
                 "children": [
                     {"type": "action", "id": "in1", "command": "data.limit",
                      "with": {}},
                 ]},
                {"type": "action", "id": "tail", "command": "workflow.sleep",
                 "with": {}},
            ],
        },
    }
    model = build_model_from_workflow(document)
    root = model.invisibleRootItem()
    # 同时拖「loop 容器」与「其子 in1」到末尾：只应移动 loop（子随父走，不重复移动）
    moved = model.dropMimeData(
        _mime(["loop", "in1"]), Qt.DropAction.MoveAction, -1, 0, root.index()
    )
    assert moved is True
    assert _top_ids(model) == ["tail", "loop"]
    loop = model.find_by_id("loop")
    assert _real_child_ids(loop) == ["in1"]


def test_multi_drag_rejects_cycle():
    document = {
        "schema_version": "1.0", "id": "c", "name": "c",
        "root": {
            "type": "sequence", "id": "root",
            "children": [
                {"type": "forEach", "id": "outer", "items": "${rows}",
                 "children": [
                     {"type": "forEach", "id": "inner", "items": "${rows}",
                      "children": []},
                 ]},
            ],
        },
    }
    model = build_model_from_workflow(document)
    inner = model.find_by_id("inner")
    # 把 outer 拖进自己的后代 inner → 拒绝
    assert model.dropMimeData(
        _mime(["outer"]), Qt.DropAction.MoveAction, -1, 0, inner.index()
    ) is False


# -- 多选批量删除（窗口层） ----------------------------------------------------


@pytest.fixture()
def window(catalog):
    win = MainWindow(catalog, copy.deepcopy(SAMPLE_WORKFLOW))
    try:
        yield win
    finally:
        # offscreen 下 close 会走未保存确认框（阻塞）：先清脏标记再关
        win._dirty = False
        win.close()


def _select(window, ids: list[str]) -> None:
    """选中多个节点（保持多选）。注意 setCurrentIndex 默认 ClearAndSelect 会清空选中集，
    必须用 NoUpdate 只挪当前项。"""
    model = window.flow_model
    selection = window.canvas_view.selectionModel()
    selection.clearSelection()
    for node_id in ids:
        item = model.find_by_id(node_id)
        assert item is not None, node_id
        selection.select(
            item.index(),
            QItemSelectionModel.SelectionFlag.Select
            | QItemSelectionModel.SelectionFlag.Rows,
        )
    selection.setCurrentIndex(
        model.find_by_id(ids[-1]).index(),
        QItemSelectionModel.SelectionFlag.NoUpdate,
    )


def _real_child_ids(item) -> list[str]:
    """容器/虚拟组的真实子节点 id（过滤末尾 end-bracket 等无 id 的结构行）。"""
    return [
        item.child(row).data(ROLE_NODE_ID)
        for row in range(item.rowCount())
        if item.child(row).data(ROLE_NODE_ID)
    ]


def test_batch_delete_removes_all_selected(window):
    before = model_to_workflow(window.flow_model, {"id": "x", "name": "x"})
    _select(window, ["read", "append"])
    window._delete_selected_node()
    after = model_to_workflow(window.flow_model, {"id": "x", "name": "x"})
    assert window.flow_model.find_by_id("read") is None
    assert window.flow_model.find_by_id("append") is None
    assert "已删除 2 个节点" in window.statusBar().currentMessage()
    # 其余节点仍在
    assert window.flow_model.find_by_id("open") is not None
    assert after != before


def test_batch_delete_container_with_selected_child_deletes_once(window):
    # 同时选中容器 loop 与其子 append：只删容器（子随父走），不应报错或残留
    _select(window, ["loop", "append"])
    window._delete_selected_node()
    assert window.flow_model.find_by_id("loop") is None
    assert window.flow_model.find_by_id("append") is None


def test_batch_delete_protects_virtual_rows(window):
    # 结束行/虚拟分组不是可删节点：选中集里只有它们时删除为 no-op
    model = window.flow_model
    virtual = [
        item
        for item in _iter_items(model)
        if item.data(ROLE_NODE_ID) is None
    ]
    if not virtual:  # pragma: no cover - 示例流程必有虚拟行
        pytest.skip("no virtual rows")
    selection = window.canvas_view.selectionModel()
    selection.clearSelection()
    for item in virtual:
        selection.select(item.index(), selection.SelectionFlag.Select)
    window._delete_selected_node()
    assert window.flow_model.find_by_id("open") is not None


def _iter_items(model) -> list:
    result: list = []

    def walk(item) -> None:
        result.append(item)
        for row in range(item.rowCount()):
            walk(item.child(row))

    walk(model.invisibleRootItem())
    return result


# -- 右键菜单接线 --------------------------------------------------------------


def test_canvas_enables_multi_select_and_context_menu(window):
    view = window.canvas_view
    assert view.selectionMode() == QAbstractItemView.SelectionMode.ExtendedSelection
    assert view.contextMenuPolicy() == Qt.ContextMenuPolicy.CustomContextMenu


def test_canvas_menu_state_per_selection(window):
    """右键菜单可用性：真实节点可复制/删除；if 可加否则；剪贴板决定粘贴。"""
    model = window.flow_model
    open_state = window._canvas_menu_state(model.find_by_id("open").index())
    assert open_state["copy"] is True
    assert open_state["delete"] is True
    assert open_state["paste"] is False  # 剪贴板为空
    assert open_state["add_else"] is False  # open 不是 if，也无 if 祖先

    if_state = window._canvas_menu_state(model.find_by_id("check").index())
    assert if_state["add_else"] is True  # check 是 if

    read_state = window._canvas_menu_state(model.find_by_id("read").index())
    assert read_state["add_else"] is True  # read 的祖先 check 是 if

    # 剪贴板非空 → 粘贴可用（_copy_selected 复制的是当前项，需先选中）
    _select(window, ["open"])
    window._copy_selected()
    assert window._clipboard is not None
    assert window._canvas_menu_state(model.find_by_id("open").index())["paste"] is True

    # 结构行（无 id 的虚拟行）不可复制
    virtual = next(
        item for item in _iter_items(model) if item.data(ROLE_NODE_ID) is None
    )
    virtual_state = window._canvas_menu_state(virtual.index())
    assert virtual_state["copy"] is False


# -- Ctrl+F 画布内查找 ---------------------------------------------------------


def test_canvas_search_finds_and_cycles(window):
    window._show_canvas_search()
    assert window.canvas_search_bar.isVisible() or not window.isVisible()
    window._on_canvas_search_changed("data.")
    assert window._canvas_search_matches, "应匹配到 data.* 指令"
    total = len(window._canvas_search_matches)
    first = window.canvas_view.currentIndex().data(ROLE_NODE_ID)
    window._find_next_in_canvas()
    second = window.canvas_view.currentIndex().data(ROLE_NODE_ID)
    if total > 1:
        assert first != second
    assert f"/{total}" in window.statusBar().currentMessage()


def test_canvas_search_matches_node_id_and_reports_miss(window):
    window._show_canvas_search()
    window._on_canvas_search_changed("read")
    assert [item.data(ROLE_NODE_ID) for item in window._canvas_search_matches] == ["read"]
    window._on_canvas_search_changed("不存在的指令xyz")
    assert window._canvas_search_matches == []
    assert "未找到" in window.statusBar().currentMessage()


def test_canvas_search_escape_hides_bar(window):
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent

    window.show()
    try:
        window._show_canvas_search()
        assert window.canvas_search_bar.isVisible()
        window._on_canvas_search_changed("read")
        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
        window.eventFilter(window.canvas_search, event)
        assert window.canvas_search.text() == ""
        assert not window.canvas_search_bar.isVisible()
    finally:
        window.hide()


def test_canvas_search_text_includes_command_and_args(window):
    item = window.flow_model.find_by_id("open")
    text = window._canvas_item_search_text(item)
    assert "open" in text
    assert "browser.navigate" in text


def test_search_widget_is_line_edit(window):
    assert isinstance(window.canvas_search, QLineEdit)
    assert isinstance(window.canvas_view, QTreeView)


# -- 回归：结构变更后不得对已失效 item 操作（曾致 GUI 卡死/崩溃） -----------------


def test_canvas_search_matches_are_recomputed_after_structural_change(window):
    """查找匹配项在删除/移动后会失效（PySide6 C++ 对象已删）。

    回归用户报障「连续操作后卡死」：_find_next_in_canvas 对死 item 调 index()/scrollTo。
    结构变更后必须重算匹配集，且再查找不得抛异常。
    """
    import shiboken6

    window._show_canvas_search()
    window._on_canvas_search_changed("data.")
    assert window._canvas_search_matches, "示例流程应含 data.* 指令"
    victim = window._canvas_search_matches[0]
    assert window.flow_model.remove_item(victim) is True
    # 变更后匹配集已重算且全部有效
    assert all(
        item is not None and shiboken6.isValid(item)
        for item in window._canvas_search_matches
    )
    window._find_next_in_canvas()  # 不得抛 Internal C++ object already deleted


def test_find_next_tolerates_stale_matches(window):
    """即使匹配集里混入已失效 item，_find_next_in_canvas 也必须安全跳过。"""
    import shiboken6

    window._on_canvas_search_changed("read")
    stale = window.flow_model.find_by_id("read")
    window.flow_model.remove_item(stale)
    # 人为把死 item 塞回匹配集（模拟结构变更未触发重算的极端情形）
    window._canvas_search_matches = [stale]
    window._find_next_in_canvas()  # 不抛异常即可
    assert all(
        item is None or shiboken6.isValid(item) for item in window._canvas_search_matches
    )


def test_move_invisible_root_is_rejected():
    """invisibleRootItem（扁平化根的 id 挂在它上面）不可移动：不得插入空行。"""
    model = _flat_model()
    root = model.invisibleRootItem()
    assert model.dropMimeData(
        _mime(["root"]), Qt.DropAction.MoveAction, 0, 0, root.index()
    ) is False
    assert _top_ids(model) == ["a1", "a2", "a3", "a4"]


# -- 拖放后的视图状态（回归：移动过的卡片点不中/看不到） ------------------------


class _MockDrop:
    """最小 drop 事件替身（只暴露 FlowTreeView.dropEvent 用到的接口）。"""

    def __init__(self, mime_data):
        self._mime = mime_data
        self._accepted = False

    def mimeData(self):
        return self._mime

    def proposedAction(self):
        return Qt.DropAction.MoveAction

    def acceptProposedAction(self):
        self._accepted = True

    def accept(self):
        self._accepted = True

    def ignore(self):
        self._accepted = False

    def isAccepted(self):
        return self._accepted


def test_drop_reselects_moved_nodes(window):
    """拖放成功后按新索引重选被移动节点（否则旧选中索引失效 → 卡片点不中）。"""
    window.show()
    try:
        model = window.flow_model
        view = window.canvas_view
        selection = view.selectionModel()
        _select(window, ["open", "check"])
        view._drag_target = {"row": -1, "parent": model.find_by_id("loop").index()}
        event = _MockDrop(_mime(["open", "check"]))
        view.dropEvent(event)
        assert event.isAccepted() is True
        moved = [i.data(ROLE_NODE_ID) for i in selection.selectedIndexes()]
        assert set(moved) == {"open", "check"}
        assert selection.currentIndex().data(ROLE_NODE_ID) == "open"
    finally:
        window._dirty = False
        window.hide()


def test_drop_preserves_expansion_of_moved_container(window):
    """拖放后保持被移动容器的展开/收起原样（摘除重插会丢展开态）。"""
    window.show()
    try:
        model = window.flow_model
        view = window.canvas_view
        done_index = model.find_by_id("done").index()
        target = {"row": done_index.row() + 1, "parent": done_index.parent()}

        # 展开的容器移动后仍展开
        view.setExpanded(model.find_by_id("loop").index(), True)
        _select(window, ["loop"])
        view._drag_target = dict(target)
        event = _MockDrop(_mime(["loop"]))
        view.dropEvent(event)
        assert event.isAccepted() is True
        assert view.isExpanded(model.find_by_id("loop").index()) is True

        # 收起的容器移动后仍收起
        view.setExpanded(model.find_by_id("check").index(), False)
        _select(window, ["check"])
        view._drag_target = dict(target)
        event = _MockDrop(_mime(["check"]))
        view.dropEvent(event)
        assert event.isAccepted() is True
        assert view.isExpanded(model.find_by_id("check").index()) is False
    finally:
        window._dirty = False
        window.hide()


def test_click_card_does_not_toggle_expansion(window):
    """展开/收起只由编号栏 −/+ 承担：点击卡片本体不再折叠。"""
    from PySide6.QtTest import QTest

    window.show()
    try:
        model = window.flow_model
        view = window.canvas_view
        check_index = model.find_by_id("check").index()
        view.setExpanded(check_index, True)
        rect = view.visualRect(check_index)
        QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
        assert view.isExpanded(check_index) is True
    finally:
        window._dirty = False
        window.hide()


def test_plain_click_on_selected_collapses_multi_selection(window):
    """普通单击多选中的某项 → 选中集收敛到它（拖放后 Qt 偶发不收敛，靠兜底兜住）。"""
    from PySide6.QtTest import QTest

    window.show()
    try:
        model = window.flow_model
        view = window.canvas_view
        _select(window, ["open", "check"])
        index = model.find_by_id("open").index()
        rect = view.visualRect(index)
        QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
        selection = view.selectionModel()
        ids = [
            i.data(ROLE_NODE_ID) for i in selection.selectedIndexes() if i.column() == 0
        ]
        assert ids == ["open"]
    finally:
        window._dirty = False
        window.hide()
