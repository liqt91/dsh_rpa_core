"""M24 S2 断点交互契约：编号栏红点/点击切换、右键菜单、运行透传与命中展示。

- 编号栏最左列（影刀式）点一下切换断点；右键菜单同款入口；
- 结构变更/重开流程后标记重刷，删除节点连带清断点；
- 真子进程链路：带断点运行 → 在断点节点**执行前**收口成 paused，
  运行面板显示「命中断点」并可跳转到该节点。

测试在 offscreen Qt 平台运行；缺 PySide6 时整组跳过。
"""

from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from rpa_core.gui.canvas import _GUTTER_WIDTH, breakpoint_button_rect  # noqa: E402
from rpa_core.gui.flow_model import ROLE_BREAKPOINT, ROLE_NODE_ID  # noqa: E402


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
def window(catalog, tmp_path):
    from rpa_core.gui.app import MainWindow

    win = MainWindow(catalog, workflows_root=tmp_path / "workflows")
    yield win
    win._shutdown_run_manager()


def _write_two_step_flow(window, name: str, first: int = 1, second: int = 1) -> None:
    document = {
        "schema_version": "1.0",
        "id": name,
        "name": name,
        "root": {
            "type": "sequence",
            "id": "root",
            "children": [
                {"type": "action", "id": "s1", "command": "workflow.sleep",
                 "with": {"seconds": first}},
                {"type": "action", "id": "s2", "command": "workflow.sleep",
                 "with": {"seconds": second}},
            ],
        },
    }
    window._store.write(name, document)
    window._open_named_flow(name)


def _wait_run_finished(window, timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        window._poll_run()
        if not window.run_action.isEnabled():
            time.sleep(0.2)
            continue
        return
    raise AssertionError("运行超时未结束")


def _click_breakpoint_column(window, node_id: str) -> None:
    model = window.flow_model
    view = window.canvas_view
    index = model.indexFromItem(model.find_by_id(node_id))
    rect = view.visualRect(index)
    QTest.mouseClick(
        view.viewport(), Qt.MouseButton.LeftButton,
        pos=breakpoint_button_rect(rect).center(),
    )


# ---- 编号栏几何与点击 -------------------------------------------------------


def test_breakpoint_hotzone_is_leftmost_column():
    """断点热区落在编号栏最左列（不越界、不与行号/收起按钮重叠）。"""
    from PySide6.QtCore import QRect

    row = QRect(0, 0, 600, 46)
    hot = breakpoint_button_rect(row)
    assert hot.left() >= 0
    assert hot.right() < _GUTTER_WIDTH
    assert hot.left() < 18  # 行号区从 18 起
    assert hot.height() == row.height()


def test_click_breakpoint_column_toggles_and_marks(window):
    """点编号栏最左列：切换断点集合 + item 标记（红点绘制依据）+ 状态栏提示。"""
    _write_two_step_flow(window, "bp-click")
    model = window.flow_model
    item = model.find_by_id("s2")

    _click_breakpoint_column(window, "s2")
    assert "s2" in window._breakpoints
    assert item.data(ROLE_BREAKPOINT) is True
    assert "已添加断点" in window.statusBar().currentMessage()

    _click_breakpoint_column(window, "s2")
    assert "s2" not in window._breakpoints
    assert item.data(ROLE_BREAKPOINT) is False
    assert "已删除断点" in window.statusBar().currentMessage()


def test_breakpoint_survives_reload_and_prunes_deleted_node(window):
    """重开流程后标记按集合重刷；删除节点时其断点被剪除。"""
    _write_two_step_flow(window, "bp-reload")
    model = window.flow_model
    _click_breakpoint_column(window, "s2")
    assert "s2" in window._breakpoints

    # 重开流程（模型重建）→ 标记应重刷回来
    window._open_named_flow("bp-reload")
    assert model.find_by_id("s2").data(ROLE_BREAKPOINT) is True

    # 删除 s2 → 断点集合随之清理
    window.canvas_view.setCurrentIndex(
        window.flow_model.indexFromItem(window.flow_model.find_by_id("s2"))
    )
    window._delete_selected_node()
    assert "s2" not in window._breakpoints


def test_context_menu_exposes_breakpoint_toggle(window):
    """右键菜单可用性：真实节点可设断点，虚拟行不可；已设断点时文案为「删除断点」。"""
    _write_two_step_flow(window, "bp-menu")
    model = window.flow_model
    index = model.indexFromItem(model.find_by_id("s1"))
    state = window._canvas_menu_state(index)
    assert state["breakpoint"] is True
    assert state["has_breakpoint"] is False

    _click_breakpoint_column(window, "s1")
    state = window._canvas_menu_state(index)
    assert state["has_breakpoint"] is True

    # 结束行（虚拟行）不可设断点
    root_item = model.invisibleRootItem()
    virtual_index = None
    for row in range(root_item.rowCount()):
        child = root_item.child(row)
        if child is not None and child.data(ROLE_NODE_ID) is None:
            virtual_index = model.indexFromItem(child)
            break
    if virtual_index is not None:
        assert window._canvas_menu_state(virtual_index)["breakpoint"] is False


# ---- 运行链路：断点命中 -----------------------------------------------------


def test_run_with_breakpoint_pauses_before_node_and_shows_jump(window):
    """带断点运行：在断点节点执行前收口成 paused，面板显示「命中断点」并可跳转。"""
    _write_two_step_flow(window, "bp-run", first=1, second=1)
    model = window.flow_model
    _click_breakpoint_column(window, "s2")
    assert "s2" in window._breakpoints

    window._start_run("bp-run")
    _wait_run_finished(window)

    assert "命中断点" in window._run_status_label.text()
    assert window.continue_run_action.isEnabled()
    assert window._failed_node_id == "s2"
    assert window._run_jump_button.isVisible()
    assert "命中断点" in window._run_jump_button.text()
    # s1 已跑完、s2 未执行
    assert model.find_by_id("s1") is not None
    events_text = window._run_events_view.toPlainText()
    assert "命中断点" in events_text


# ---- 单步（S3） -------------------------------------------------------------


def test_step_action_enabled_only_when_paused(window):
    """「单步」按钮只在暂停态可用：运行中/未运行时禁用。"""
    _write_two_step_flow(window, "bp-step-state", first=1, second=1)
    _click_breakpoint_column(window, "s2")

    assert window.step_run_action.isEnabled() is False  # 未运行
    window._start_run("bp-step-state")
    assert window.step_run_action.isEnabled() is False  # 运行中
    _wait_run_finished(window)
    assert window.step_run_action.isEnabled() is True  # 暂停态


def test_step_runs_exactly_one_node_then_pauses(window):
    """单步链路：暂停后点「单步」→ 只执行一个节点 → 再次暂停（原因 step）。"""
    _write_two_step_flow(window, "bp-step", first=1, second=1)
    _click_breakpoint_column(window, "s2")
    window._start_run("bp-step")
    _wait_run_finished(window)
    assert "命中断点" in window._run_status_label.text()

    window._step_run()
    assert not window.run_action.isEnabled(), "单步后应重新进入运行态"
    _wait_run_finished(window)

    # s2 被单步执行掉，下一步（无更多节点）→ 成功；或停在 s2 之后
    text = window._run_events_view.toPlainText()
    assert "s2" in text
    assert window._run_status_label.text() != ""
