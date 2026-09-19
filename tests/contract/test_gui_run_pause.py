"""M21 GUI 运行控制契约：暂停 / 继续 / 恢复人工确认。

- 真子进程链路：暂停请求 → 收口成 paused → 继续 → 跑完；
- 句柄分派：运行中「继续」= 撤销请求，已收口 = 起新进程 resume；
- 人工确认门：`indeterminate` 必须显式确认才带 `--allow-indeterminate`，
  默认（用户点「不恢复」）不产生任何动作。

测试在 offscreen Qt 平台运行；缺 PySide6 时整组跳过。
"""

from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")


@pytest.fixture(scope="module")
def qapp():
    from rpa_core.gui.app import build_application

    return build_application()


@pytest.fixture(scope="module")
def catalog(qapp):
    from rpa_core.catalog import load_catalog
    from rpa_core.cli import _commands_root

    return load_catalog(_commands_root())


_ALIVE_WINDOWS = []


@pytest.fixture()
def window(catalog, tmp_path):
    from rpa_core.gui.app import MainWindow

    win = MainWindow(catalog, workflows_root=tmp_path / "workflows")
    _ALIVE_WINDOWS.append(win)
    yield win
    win._shutdown_run_manager()


def _write_flow(window, name: str, command: str, with_args: dict) -> None:
    document = {
        "schema_version": "1.0",
        "id": name,
        "name": name,
        "root": {
            "type": "sequence",
            "id": "root",
            "children": [
                {"type": "action", "id": "s1", "command": command, "with": with_args},
            ],
        },
    }
    window._store.write(name, document)
    window._open_named_flow(name)


def _write_two_step_flow(window, name: str, first: int, second: int) -> None:
    """两段 sleep 的流程：第一个跑完后必然还有一个节点被挡在门外。

    单节点流程不适合验暂停——请求还没被轮询看到时节点就跑完了，按 ADR 0005
    「剩余动作全部已完成则 run 正常 succeeded，暂停不落位」的边界会直接成功。
    """
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
        if not window.run_action.isEnabled():  # 仍在运行
            time.sleep(0.2)
            continue
        return
    raise AssertionError("运行超时未结束")


class _FakeRunManager:
    """记录 GUI 到底把「继续」分派成了哪条路径。"""

    def __init__(self, status: dict):
        self._status = status
        self.calls: list[tuple] = []

    def status(self, run_id):
        return self._status

    def pause(self, run_id):
        self.calls.append(("pause", run_id))
        return {"runId": run_id}

    def continue_run(self, run_id):
        self.calls.append(("continue", run_id))
        return {"runId": "run-next"}

    def resume(self, run_id, *, allow_indeterminate=False):
        self.calls.append(("resume", run_id, allow_indeterminate))
        return {"runId": "run-next"}

    def close(self):
        pass


# --------------------------------------------------------------------------
# 真子进程链路
# --------------------------------------------------------------------------


def test_pause_then_continue_end_to_end(window):
    """完整链路：请求暂停 → 在节点边界收口成 paused → 继续 → 跑完。"""
    _write_two_step_flow(window, "pausable", first=2, second=1)
    window._start_run("pausable")
    assert window.pause_run_action.isEnabled()
    assert not window.continue_run_action.isEnabled()

    window._pause_run()
    _wait_run_finished(window)  # 收口：run_action 重新可用

    assert "已暂停" in window._run_status_label.text()
    assert window.continue_run_action.isEnabled()
    assert not window.pause_run_action.isEnabled()
    assert window._run_float.state == "paused"
    assert window._run_float.continue_button.isEnabled()

    window._continue_run()
    assert not window.run_action.isEnabled(), "继续后应重新进入运行态"
    _wait_run_finished(window)

    assert "succeeded" in window._run_status_label.text()
    assert not window.continue_run_action.isEnabled()
    # 续跑没有把历史事件重放一遍（增量读的 evidence）
    text = window._run_events_view.toPlainText()
    assert text.count("运行开始") == 1


def test_pause_request_action_state_before_boundary(window):
    """请求发出到落地之间：按钮态与文案如实反映「已请求、未停下」。"""
    _write_flow(window, "slowish", "workflow.sleep", {"seconds": 8})
    window._start_run("slowish")
    window._pause_run()

    assert window._pause_requested is True
    assert not window.pause_run_action.isEnabled()
    assert window.continue_run_action.isEnabled(), "未落地前「继续」应可用来撤销"
    assert "已请求暂停" in window._run_status_label.text()
    assert window._run_float.title_label.text() == "已请求暂停…"

    window._cancel_run()
    _wait_run_finished(window)


# --------------------------------------------------------------------------
# 句柄分派（不依赖真子进程）
# --------------------------------------------------------------------------


def test_continue_while_running_cancels_pending_pause(window):
    window._active_run_id = "run-1"
    window._run_manager = _FakeRunManager({"running": True, "result": None})
    window._pause_requested = True

    window._continue_run()

    assert window._run_manager.calls == [("continue", "run-1")]
    assert window._pause_requested is False
    assert "运行中" in window._run_status_label.text()


def test_continue_after_pause_starts_fresh_process(window):
    """已收口的 paused：GUI 走 continue_run（内部 resume，paused 无人工门槛）。"""
    window._active_run_id = "run-1"
    window._run_manager = _FakeRunManager({"running": False, "result": {"status": "paused"}})

    window._continue_run()

    assert window._run_manager.calls == [("continue", "run-1")]
    assert window._active_run_id == "run-next"


def test_indeterminate_needs_explicit_confirmation(window, monkeypatch):
    """默认拒绝：用户不确认就绝不恢复（ADR 0004 第 4 道门）。"""
    from rpa_core.gui.app import MainWindow

    window._active_run_id = "run-1"
    fake = _FakeRunManager(
        {"running": False, "result": {"status": "indeterminate"}}
    )
    window._run_manager = fake

    monkeypatch.setattr(MainWindow, "_confirm_resume", lambda self, terminal: False)
    window._continue_run()
    assert fake.calls == [], "未确认却发生了恢复动作"
    assert window._active_run_id == "run-1"

    monkeypatch.setattr(MainWindow, "_confirm_resume", lambda self, terminal: True)
    window._continue_run()
    assert fake.calls == [("resume", "run-1", True)]


def test_recovery_required_confirms_but_needs_no_indeterminate_flag(window, monkeypatch):
    from rpa_core.gui.app import MainWindow

    window._active_run_id = "run-1"
    fake = _FakeRunManager(
        {"running": False, "result": {"status": "recovery_required"}}
    )
    window._run_manager = fake

    monkeypatch.setattr(MainWindow, "_confirm_resume", lambda self, terminal: True)
    window._continue_run()
    assert fake.calls == [("continue", "run-1")]


def test_resume_confirmation_wording_differs_by_terminal():
    from rpa_core.gui.app import MainWindow

    ind_title, ind_body = MainWindow._resume_confirmation("indeterminate")
    rec_title, rec_body = MainWindow._resume_confirmation("recovery_required")

    assert ind_title != rec_title
    assert "副作用可能重复发生" in ind_body
    assert "重放" in rec_body


# --------------------------------------------------------------------------
# 浮窗
# --------------------------------------------------------------------------


def test_float_window_pause_and_continue_states(qapp):
    from rpa_core.gui.run_float import RunFloatWindow

    win = RunFloatWindow()
    win.show_running("正在执行：打开网页", 2)
    assert win.pause_button.isEnabled()
    assert not win.continue_button.isEnabled()

    win.show_pausing()
    assert win.title_label.text() == "已请求暂停…"
    assert not win.pause_button.isEnabled()
    assert win.continue_button.isEnabled()

    # 已请求暂停期间的状态刷新不得把提示冲掉
    win.show_running("正在执行：提交表单", 3)
    assert win.title_label.text() == "已请求暂停…"

    win.show_result("paused")
    assert "已暂停" in win.title_label.text()
    assert win.continue_button.isEnabled()
    assert not win.cancel_button.isEnabled()

    win.show_result("indeterminate")
    assert win.continue_button.isEnabled(), "需确认的终态也要给「继续」入口"

    win.show_result("succeeded")
    assert not win.continue_button.isEnabled()

    win.clear_pause_pending()
    win.show_running("准备中…", 0)
    assert win.title_label.text() == "运行中…"
