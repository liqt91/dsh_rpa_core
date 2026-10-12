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


# ---- 失败归属：节点 id 在 error["details"]["nodeId"]（不在顶层） -----------------
def test_run_failure_attributes_node_from_details(window):
    """错误归属取自 `details.nodeId`；界面不许再显示「节点 None」。

    回归（真机）：两处渲染都直读 `error.get("nodeId")`，而运行时把归属放在
    `error["details"]["nodeId"]` ⇒ 一次 `browser.input` 缺 `selector` 的失败在界面上
    显示成「节点 None」，且「跳到节点」按钮不出现——明明有明确归属。
    """
    window._run_dock()  # 摘要控件属运行面板（懒创建）
    error = {
        "code": "INVALID_INPUT",
        "message": "'selector' is a required property",
        "retryable": False,
        "details": {"nodeId": "n1", "commandId": "browser.input", "attempt": 0},
    }
    assert window._error_node_id(error) == "n1"
    window._show_error_summary(error)
    assert "n1" in window._run_error_node.text()
    assert window._run_error_node.isVisibleTo(window._run_error_widget)
    assert "browser.input" in window._run_error_detail.text()


def test_run_failure_without_node_stays_blank(window):
    """没有归属的错误不硬编一个 id（否则又会出现假的「节点 None」）。"""
    assert window._error_node_id({"code": "TIMEOUT", "message": "boom"}) is None


def test_missing_required_element_param_gets_actionable_hint():
    """缺必填的 selector / locator 时给一句可操作提示；其它键与错误码不改动文本。"""
    from rpa_core.gui.app import missing_element_param_hint

    hint = missing_element_param_hint(
        {"code": "INVALID_INPUT", "message": "'selector' is a required property"}
    )
    assert hint is not None
    assert "selector" in hint and "元素" in hint  # 指到元素库这条正路

    assert (
        missing_element_param_hint(
            {"code": "INVALID_INPUT", "message": "'url' is a required property"}
        )
        is None
    ), "url 不能由元素库提供，别乱加提示"
    assert (
        missing_element_param_hint(
            {"code": "TIMEOUT", "message": "'selector' is a required property"}
        )
        is None
    ), "只对 INVALID_INPUT 生效"


def test_element_ref_missing_gets_actionable_hint():
    """M52 S6：引用的元素不存在 ⇒ `ELEMENT_REF_MISSING` 也要给「去哪儿修」。

    去快照后元素被删/改名没有兜底（fail-closed），错误本身只有元素名；
    不说清去处的话用户只看到「元素不存在」而不知道下一步。
    """
    from rpa_core.gui.app import element_ref_missing_hint

    hint = element_ref_missing_hint(
        {"code": "ELEMENT_REF_MISSING", "message": "元素 `searchBox` 在元素库里不存在"}
    )
    assert hint is not None
    assert "元素" in hint and "插入参数" in hint  # 指到元素库这条正路

    assert (
        element_ref_missing_hint({"code": "INVALID_INPUT", "message": "x"}) is None
    ), "只对 ELEMENT_REF_MISSING 生效"


def test_run_failure_hint_is_one_authority_for_both_kinds():
    """两条提示来源收敛到 `run_failure_hint`：两种病各能拿到提示、无关错误拿不到。

    直接判「组合入口」而不是只判两个纯函数——S6 新增的 `element_ref_missing_hint`
    原先只被调用侧用 `or` 串起来，漏掉任一条不会有任何症状（只剩干巴巴的元素名）。
    """
    from rpa_core.gui.app import run_failure_hint

    missing_param = run_failure_hint(
        {"code": "INVALID_INPUT", "message": "'selector' is a required property"}
    )
    assert missing_param is not None and "selector" in missing_param

    missing_element = run_failure_hint(
        {"code": "ELEMENT_REF_MISSING", "message": "元素 `searchBox` 在元素库里不存在"}
    )
    assert missing_element is not None and "元素" in missing_element

    assert run_failure_hint({"code": "TIMEOUT", "message": "boom"}) is None

