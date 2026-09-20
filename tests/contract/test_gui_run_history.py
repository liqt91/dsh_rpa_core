"""M25 S2 运行历史面板契约：列表、时间线渲染、跳到节点、再跑、继续/单步。

不依赖真实运行：直接往流程库同级造 `run_artifacts/<id>/`（与 `_artifacts_root`
的约定一致），用假 RunManager 断言动作接线。

测试在 offscreen Qt 平台运行；缺 PySide6 时整组跳过。
"""

from __future__ import annotations

import json
import os

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


@pytest.fixture()
def window(catalog, tmp_path):
    from rpa_core.gui.app import MainWindow

    win = MainWindow(catalog, workflows_root=tmp_path / "workflows")
    yield win
    win._shutdown_run_manager()


def _write_flow(window, name: str, flow_id: str = "demo-flow") -> None:
    document = {
        "schema_version": "1.0",
        "id": flow_id,
        "name": name,
        "root": {
            "type": "sequence",
            "id": "root",
            "children": [
                {"type": "action", "id": "s1", "command": "workflow.sleep",
                 "with": {"seconds": 1}},
                {"type": "action", "id": "s2", "command": "workflow.sleep",
                 "with": {"seconds": 1}},
            ],
        },
    }
    window._store.write(name, document)
    window._open_named_flow(name)


def _write_run(
    window,
    run_id: str,
    *,
    status: str = "paused",
    workflow_id: str = "demo-flow",
    inputs: dict | None = None,
    paused_at: str | None = "s2",
) -> None:
    root = window._artifacts_root()
    run_dir = root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "result.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "workflow_id": workflow_id,
                "status": status,
                "started_at": "2026-09-20T10:00:00+00:00",
                "ended_at": "2026-09-20T10:00:05+00:00",
                "error": None,
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "checkpoint.json").write_text(
        json.dumps(
            {
                "version": 1,
                "workflowId": workflow_id,
                "catalogDigest": "d",
                "completedSteps": ["root/s1"],
                "scopes": {"inputs": inputs or {"keyword": "新闻"}},
                "returnValue": None,
                "pausedAtNode": paused_at,
                "pauseReason": "breakpoint" if paused_at else None,
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "events.jsonl").write_text(
        "\n".join(
            json.dumps(event)
            for event in (
                {"type": "runStarted", "payload": {}},
                {"type": "stepCompleted", "node_id": "s1",
                 "payload": {"outputs": {"sleptMs": 1000}}},
                {"type": "runPaused", "node_id": "s2",
                 "payload": {"nodeId": "s2", "reason": "breakpoint",
                             "completedSteps": ["root/s1"]}},
            )
        )
        + "\n",
        encoding="utf-8",
    )


class _FakeRunManager:
    def __init__(self):
        self.calls: list[tuple] = []

    def resume_run(self, workflow_name, run_id, *, step=False, allow_indeterminate=False):
        self.calls.append(("resume_run", workflow_name, run_id, step))
        return {"runId": "run-next", "pid": 1}

    def start(self, workflow_name, inputs=None, breakpoints=None):
        self.calls.append(("start", workflow_name, inputs, breakpoints))
        return {"runId": "run-new", "pid": 1}

    def status(self, run_id):
        return {"running": True, "result": None}

    def events(self, run_id):
        return {"events": []}

    def close(self):
        pass


def test_history_panel_lists_runs_and_enables_actions(window):
    """面板列出历史运行；paused+有检查点才启用「继续/单步」。"""
    _write_flow(window, "demo")
    _write_run(window, "run-paused", status="paused")
    _write_run(window, "run-done", status="succeeded", paused_at=None)

    window._toggle_history_dock()
    table = window._history_table
    assert table.rowCount() == 2
    # 时间倒序（同时间时按 mtime，这里只断言两条都在）
    ids = {run["runId"] for run in window._history_runs}
    assert ids == {"run-paused", "run-done"}

    for row, run in enumerate(window._history_runs):
        table.setCurrentCell(row, 0)
        if run["runId"] == "run-paused":
            assert window._history_buttons["resume"].isEnabled() is True
            assert window._history_buttons["step"].isEnabled() is True
        else:
            assert window._history_buttons["resume"].isEnabled() is False
            assert window._history_buttons["step"].isEnabled() is False


def test_open_history_run_renders_timeline_and_jump_target(window):
    """查看时间线：事件按格式化渲染；跳转目标取暂停节点。"""
    _write_flow(window, "demo")
    _write_run(window, "run-open")
    window._toggle_history_dock()
    for row, run in enumerate(window._history_runs):
        if run["runId"] == "run-open":
            window._history_table.setCurrentCell(row, 0)

    window._open_history_run()
    text = window._run_events_view.toPlainText()
    assert "运行开始" in text
    assert "命中断点" in text  # runPaused 带 reason=breakpoint
    assert window._failed_node_id == "s2"
    assert window._history_event_nodes.count(None) >= 1  # runStarted 无节点

    # 光标停在暂停事件行 → 跳到该节点
    from rpa_core.gui.flow_model import ROLE_NODE_ID

    window._history_cursor_node = "s2"
    window._jump_to_run_node()
    assert window.canvas_view.currentIndex().data(ROLE_NODE_ID) == "s2"


def test_rerun_uses_historical_inputs(window):
    """再跑：用历史 inputs 对同一流程发起新运行（新 run_id）。"""
    _write_flow(window, "demo")
    _write_run(window, "run-rerun", inputs={"keyword": "历史输入"})
    window._toggle_history_dock()
    for row, run in enumerate(window._history_runs):
        if run["runId"] == "run-rerun":
            window._history_table.setCurrentCell(row, 0)

    fake = _FakeRunManager()
    window._run_manager = fake
    window._rerun_history_run()
    assert fake.calls, "应以历史输入发起新运行"
    kind, name, inputs, _breakpoints = fake.calls[0]
    assert (kind, name) == ("start", "demo")
    assert inputs == {"keyword": "历史输入"}


def test_resume_history_run_dispatches_step(window):
    """继续/单步：走 RunManager.resume_run（不需要本进程先托管过该 run）。"""
    _write_flow(window, "demo")
    _write_run(window, "run-resume")
    window._toggle_history_dock()
    for row, run in enumerate(window._history_runs):
        if run["runId"] == "run-resume":
            window._history_table.setCurrentCell(row, 0)

    fake = _FakeRunManager()
    window._run_manager = fake
    window._resume_history_run()
    window._resume_history_run(step=True)
    assert fake.calls[0] == ("resume_run", "demo", "run-resume", False)
    assert fake.calls[1] == ("resume_run", "demo", "run-resume", True)


def test_history_panel_empty_state_is_safe(window):
    """没有运行记录时：面板可打开、不抛异常、给出空态提示。"""
    _write_flow(window, "demo")
    window._toggle_history_dock()
    assert window._history_table.rowCount() == 0
    assert "为空" in window.statusBar().currentMessage()
