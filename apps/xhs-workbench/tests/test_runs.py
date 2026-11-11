"""运行记录视图单测：步骤时间线、失败归位、产物清点、run_id 路径守卫。

判据取向：这里的输出就是控制台「流程」页看到的东西，所以断言的是**用户可见的事实**
（哪一步、多久、什么输出、有没有截图），而不是内部数据结构。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from src.runs import list_runs, run_dir_of, summarize_run


def _event(kind: str, node: str | None, ts: str, **payload: Any) -> dict[str, Any]:
    return {"type": kind, "node_id": node, "timestamp": ts, "payload": payload}


def _write_run(
    root: Path,
    run_id: str,
    *,
    status: str = "succeeded",
    outputs: dict[str, Any] | None = None,
    events: list[dict[str, Any]] | None = None,
    shot: bytes | None = None,
) -> Path:
    run_dir = root / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "result.json").write_text(
        json.dumps(
            {"run_id": run_id, "status": status, "error": None, "outputs": outputs or {}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    if events is not None:
        (run_dir / "events.jsonl").write_text(
            "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n",
            encoding="utf-8",
        )
    if shot is not None:
        (run_dir / "results.png").write_bytes(shot)
    return run_dir


def _timeline_events() -> list[dict[str, Any]]:
    return [
        _event("runStarted", None, "2026-11-11T07:00:00.000Z"),
        _event("stepStarted", "openHome", "2026-11-11T07:00:00.000Z", command="browser.navigate"),
        _event("stepAttemptStarted", "openHome", "2026-11-11T07:00:00.050Z", attempt=1),
        _event(
            "stepCompleted",
            "openHome",
            "2026-11-11T07:00:02.500Z",
            attempt=1,
            outputs={"url": "x"},
        ),
        _event("stepStarted", "waitItems", "2026-11-11T07:00:02.500Z", command="browser.waitFor"),
        _event("stepAttemptStarted", "waitItems", "2026-11-11T07:00:02.500Z", attempt=1),
        _event("stepAttemptStarted", "waitItems", "2026-11-11T07:00:12.000Z", attempt=3),
        _event(
            "stepFailed",
            "waitItems",
            "2026-11-11T07:00:20.000Z",
            attempt=3,
            error={"code": "TIMEOUT", "message": "waitFor(attached) timed out"},
        ),
        _event("runFinished", None, "2026-11-11T07:00:20.500Z"),
    ]


def test_summarize_reports_steps_durations_and_failure(tmp_path: Path) -> None:
    run_dir = _write_run(tmp_path, "run-1", status="failed", events=_timeline_events())
    summary = summarize_run(run_dir)
    assert summary["duration_s"] == pytest.approx(20.5)
    assert [s["id"] for s in summary["steps"]] == ["openHome", "waitItems"]
    open_home, wait_items = summary["steps"]
    assert open_home["status"] == "ok"
    assert open_home["duration_s"] == pytest.approx(2.5)
    assert open_home["outputs"] == {"url": "x"}
    assert wait_items["status"] == "failed"
    assert wait_items["attempts"] == 3
    assert wait_items["error"]["code"] == "TIMEOUT"
    assert summary["step_ok"] == 1 and summary["step_failed"] == 1


def test_summarize_counts_items_from_grab_output(tmp_path: Path) -> None:
    items = [{"href": "h1", "title": "t", "text": "x"}] * 3
    outputs = {"grabItems": {"outputs": {"result": json.dumps(items, ensure_ascii=False)}}}
    summary = summarize_run(_write_run(tmp_path, "run-2", outputs=outputs))
    assert summary["items"] == 3
    assert summary["status"] == "succeeded"


def test_summarize_falls_back_to_result_outputs_when_events_missing(tmp_path: Path) -> None:
    """events.jsonl 缺失/被截断时，仍要能用 result.json 列出步骤与输出。"""
    outputs = {"grabItems": {"outputs": {"result": "[]"}}, "saveRaw": {"outputs": {"path": "p"}}}
    summary = summarize_run(_write_run(tmp_path, "run-3", outputs=outputs))
    assert [s["id"] for s in summary["steps"]] == ["grabItems", "saveRaw"]
    assert summary["items"] == 0


def test_artifacts_list_screenshots_but_not_runtime_files(tmp_path: Path) -> None:
    run_dir = _write_run(tmp_path, "run-4", shot=b"\x89PNG\r\n")
    summary = summarize_run(run_dir)
    assert summary["artifacts"]["screenshots"] == ["results.png"]
    names = {f["name"] for f in summary["artifacts"]["files"]}
    assert "results.png" in names
    assert not names & {"result.json", "events.jsonl", "checkpoint.json"}


def test_list_runs_is_newest_first_and_limited(tmp_path: Path) -> None:
    old = _write_run(tmp_path, "run-old")
    new = _write_run(tmp_path, "run-new")
    os.utime(old / "result.json", (1_700_000_000, 1_700_000_000))
    os.utime(new / "result.json", (1_800_000_000, 1_800_000_000))
    ids = [r["run_id"] for r in list_runs(tmp_path)]
    assert ids == ["run-new", "run-old"]
    assert len(list_runs(tmp_path, limit=1)) == 1


def test_run_dir_guard_rejects_traversal(tmp_path: Path) -> None:
    for bad in ("../etc", "a/b", "..", "", "x\\y"):
        with pytest.raises(ValueError):
            run_dir_of(tmp_path, bad)
    assert run_dir_of(tmp_path, "run-ok") == (tmp_path / "run-ok").resolve()
