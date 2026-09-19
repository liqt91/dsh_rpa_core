"""运行控制契约测试（GUI 功能补齐 切 E）。

- 运行前置：需要流程库 + 命名流程；
- 真实子进程运行（RunManager）：succeeded 后状态行、事件流、节点着色；
- 取消运行；
- RunInputsDialog 覆盖值收集。

测试在 offscreen Qt 平台运行；缺 PySide6 时整组跳过。
真实子进程用例较慢（秒级），与 devserver run_control 测试同理。
"""

from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from rpa_core.gui.flow_model import ROLE_RUN_STATE  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from rpa_core.gui.app import build_application

    return build_application()


@pytest.fixture(scope="module")
def catalog(qapp):
    from rpa_core.catalog import load_catalog
    from rpa_core.cli import _commands_root

    return load_catalog(_commands_root())


_ALIVE_WINDOWS = []  # 会话期保活：offscreen 下让旧窗口整体存活，避免悬挂删除竞态


@pytest.fixture()
def window(catalog, tmp_path):
    from rpa_core.gui.app import MainWindow

    win = MainWindow(catalog, workflows_root=tmp_path / "workflows")
    _ALIVE_WINDOWS.append(win)  # 防 GC：旧窗口留待会话结束统一释放
    yield win
    # 只停计时器/子进程/网关，不做 deleteLater/processEvents（offscreen 下
    # 事件泵时机不可控，悬挂 DeferredDelete 命中已回收父窗口会段错误）
    win._shutdown_run_manager()


def _write_flow(window, name: str, command: str, with_args: dict) -> None:
    """直接往流程库写一条单指令流程并打开。"""
    document = {
        "schema_version": "1.0",
        "id": name,
        "name": name,
        "root": {
            "type": "sequence",
            "id": "root",
            "children": [
                {"type": "action", "id": "s1", "command": command,
                 "with": with_args},
            ],
        },
    }
    window._store.write(name, document)
    window._open_named_flow(name)


def _wait_run_finished(window, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        window._poll_run()
        if not window.run_action.isEnabled():  # 仍在运行
            time.sleep(0.2)
            continue
        return
    raise AssertionError("运行超时未结束")


def test_run_requires_named_flow(window):
    window._run_workflow()
    assert "流程库" in window.statusBar().currentMessage()


def test_run_requires_store(catalog):
    from rpa_core.gui.app import MainWindow

    plain = MainWindow(catalog)  # 无 workflows_root
    plain._run_workflow()
    assert "流程库" in plain.statusBar().currentMessage()


def test_run_succeeds_and_marks_node(window):
    _write_flow(window, "okr", "data.datetimeNow", {})
    run_id = window._start_run("okr")
    assert run_id is not None
    assert not window.run_action.isEnabled()
    assert window.cancel_run_action.isEnabled()

    _wait_run_finished(window)

    assert "succeeded" in window._run_status_label.text()
    events_text = window._run_events_view.toPlainText()
    assert "开始" in events_text and "运行结束" in events_text
    item = window.flow_model.find_by_id("s1")
    assert item.data(ROLE_RUN_STATE) == "succeeded"


def test_cancel_run(window):
    _write_flow(window, "slow", "workflow.sleep", {"seconds": 30})
    window._start_run("slow")
    window._cancel_run()
    _wait_run_finished(window)
    # 取消后：要么子进程落了 cancelled 结果，要么被直接终止（非零退出）
    label = window._run_status_label.text()
    assert "运行中" not in label
    assert window.run_action.isEnabled()


def test_run_inputs_dialog_values(qapp):
    from rpa_core.gui.app import RunInputsDialog

    dialog = RunInputsDialog({"count": 3, "tag": None})
    assert dialog.values() == {}  # 空 = 沿用默认
    dialog._edits["count"].setText("10")
    dialog._edits["tag"].setText('"abc"')
    assert dialog.values() == {"count": 10, "tag": "abc"}
    dialog._edits["count"].setText("{bad")
    with pytest.raises(ValueError, match="count"):
        dialog.values()


def test_clear_run_states(window):
    from rpa_core.gui.flow_model import iter_real_nodes

    item = window.flow_model.find_by_id("open")
    item.setData("succeeded", ROLE_RUN_STATE)
    window._clear_run_states()
    assert all(
        node.data(ROLE_RUN_STATE) is None for node in iter_real_nodes(window.flow_model)
    )


# ---- 运行时悬浮窗 -------------------------------------------------------------
def test_float_window_states(qapp):
    from rpa_core.gui.run_float import RunFloatWindow

    win = RunFloatWindow()
    win.show_running("正在执行：打开网页", 2)
    assert win.state == "running"
    assert win.progress_label.text() == "2 步"
    assert win.cancel_button.isEnabled()

    win.show_result("succeeded")
    assert win.state == "succeeded"
    assert not win.cancel_button.isEnabled()

    win.show_result("failed", "TIMEOUT · 节点 navigate")
    assert win.state == "failed"
    assert "TIMEOUT" in win.step_label.text()


def test_float_window_survives_app_deactivation(qapp):
    """运行浮窗必须带 WA_MacAlwaysShowToolWindow。

    macOS 上 Qt.Tool 对应 NSPanel，**应用一旦不激活系统就隐藏全部 tool
    window**（Qt 源码即 `hidesOnDeactivate = (type & Qt::Tool) && !属性`）。
    浮窗存在的全部意义就是「主窗口已最小化、用户在看别的应用时仍能看到执行
    进度」；缺这个属性它会跟主窗口一起消失（真机 2026-09-19 实测）。
    """
    from PySide6.QtCore import Qt

    from rpa_core.gui.run_float import RunFloatWindow

    win = RunFloatWindow()
    assert win.windowFlags() & Qt.WindowType.Tool
    assert win.testAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)


def test_run_shows_float_and_minimizes(window):
    _write_flow(window, "flo", "workflow.sleep", {"seconds": 3})
    window._start_run("flo")
    assert window._run_float is not None
    assert window._run_float.state == "running"
    assert window.isMinimized()
    # 运行中即可读到事件（早期 run_id 标记行生效）
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        window._poll_run()
        if window._events_seen > 0:
            break
        time.sleep(0.2)
    assert window._events_seen > 0, "运行中未读到任何事件"
    window._cancel_run()
    _wait_run_finished(window)
    # 取消后浮窗停留（不自动还原）
    assert window._run_float is not None
    assert window._run_float.state == "cancelled"
    window._restore_from_float()
    assert not window.isMinimized()


def test_run_success_auto_restores(window):
    _write_flow(window, "fast", "data.datetimeNow", {})
    window._start_run("fast")
    _wait_run_finished(window)
    assert window._run_float.state == "succeeded"
    # 成功后按 2 秒排程自动还原。这里不断言真实等待：qWait 会一次性泵出
    # 整个测试会话累积的 DeferredDelete（offscreen 无事件循环，积压含已回收
    # 窗口的悬空目标）→ 原生崩溃。改为断言排程参数 + 手动触发还原动作。
    assert window._restore_timer is not None
    assert window._restore_timer.isActive()
    assert window._restore_timer.interval() == 2000
    window._restore_from_float()
    assert not window.isMinimized()
