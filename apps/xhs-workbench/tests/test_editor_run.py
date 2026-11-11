"""编辑器执行控制的纯逻辑单测：事件格式化、增量日志、控制文件原子写、状态守卫。

不拉起真子进程（那归真机 E2E）；`_run_one_phase` 的进程面用集成验证覆盖。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src import editor_run as er


def _ev(kind: str, node: str | None = None, ts: str = "2026-11-11T08:00:00.000Z", **payload):
    return {"type": kind, "node_id": node, "timestamp": ts, "payload": payload}


def test_format_event_covers_the_lines_users_see() -> None:
    assert "运行开始" in er._format_event(_ev("runStarted"))
    line = er._format_event(_ev("stepStarted", "grabItems", command="browser.executeScript"))
    assert "grabItems" in line and "browser.executeScript" in line
    assert "✓" in er._format_event(_ev("stepCompleted", "grabItems"))
    failed = er._format_event(
        _ev("stepFailed", "waitItems", error={"code": "TIMEOUT", "message": "boom"})
    )
    assert "✗" in failed and "boom" in failed
    assert er._format_event(_ev("checkpointWritten", "x")) is None  # 噪音事件不显示


def test_drain_log_is_incremental_and_survives_rewrite(tmp_path: Path) -> None:
    run = er.EditorRun(keyword="k", browser="chrome")
    run.run_dir = tmp_path
    events = tmp_path / "events.jsonl"
    events.write_text(json.dumps(_ev("runStarted")) + "\n", encoding="utf-8")
    assert run.drain_log() == 1
    assert run.drain_log() == 0  # 没有新增行就不重复
    with events.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(_ev("stepStarted", "openHome", command="browser.navigate")) + "\n")
    assert run.drain_log() == 1
    assert len(run.lines) == 2
    assert "openHome" in run.lines[-1]
    # resume 会重写文件（变小）：offset 复位重读，不丢失也不崩溃
    events.write_text(json.dumps(_ev("runStarted")) + "\n", encoding="utf-8")
    run.drain_log()
    assert run.lines[-1].endswith("运行开始")


def test_control_file_write_is_atomic_and_roundtrips(tmp_path: Path) -> None:
    er._write_control(tmp_path, True)
    payload = json.loads((tmp_path / "control.json").read_text(encoding="utf-8"))
    assert payload["pause"] is True
    assert not list(tmp_path.glob("*.tmp"))  # 原子写不留尾巴
    er._write_control(tmp_path, False)
    assert json.loads((tmp_path / "control.json").read_text(encoding="utf-8"))["pause"] is False


def test_pause_resume_stop_guards_without_a_live_run() -> None:
    class _Monitor:
        def __init__(self):
            self.calls = []

    class _Ctx:
        editor = None
        monitor = _Monitor()
        lock = None

    ctx = _Ctx()
    with pytest.raises(ValueError, match="没有正在运行"):
        er.pause(ctx)
    with pytest.raises(ValueError, match="没有可继续"):
        er.resume(ctx)
    with pytest.raises(ValueError, match="没有正在运行"):
        er.stop(ctx)


def test_resume_from_paused_spawns_resume_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = tmp_path / "run-1"
    run_dir.mkdir()

    class _Monitor:
        def __init__(self):
            self.resumed = []

        def resume_running(self, kw, rid):
            self.resumed.append((kw, rid))

    class _Ctx:
        monitor = _Monitor()

    ctx = _Ctx()
    run = er.EditorRun(keyword="k", browser="chrome")
    run.state = "paused"
    run.run_dir = run_dir
    ctx.editor = run

    spawned = []

    def fake_thread(**kw):
        return type("T", (), {"start": lambda self: spawned.append(kw)})()

    monkeypatch.setattr(er.threading, "Thread", fake_thread)

    er.resume(ctx)
    assert run.state == "running"
    assert ctx.monitor.resumed == [("k", "run-1")]
    assert spawned and spawned[0]["args"][2] == "resume"


def test_stop_while_paused_finishes_without_process(tmp_path: Path) -> None:
    class _Monitor:
        def __init__(self):
            self.failed = []

        def set_failed(self, kw, err):
            self.failed.append((kw, err))

    class _Ctx:
        monitor = _Monitor()
        lock = type("L", (), {"release": lambda self: None})()

    run = er.EditorRun(keyword="k", browser="chrome")
    run.state = "paused"
    run.run_dir = tmp_path / "run-1"
    ctx = _Ctx()
    ctx.editor = run

    er.stop(ctx)
    assert run.state == "stopped"
    assert ctx.monitor.failed and "手动停止" in ctx.monitor.failed[0][1]
