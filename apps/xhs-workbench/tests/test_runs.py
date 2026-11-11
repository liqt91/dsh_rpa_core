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
from src.runs import find_run_dirs, list_runs, run_dir_of, summarize_run


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


def _write_editor_run(
    data_root: Path, run_no: str, uuid: str, *, mtime: float | None = None
) -> Path:
    """PPX 编辑器的 run 布局：_sandbox/runs/run-N/artifacts/<uuid>/result.json。"""
    run_dir = data_root / "_sandbox" / "runs" / run_no / "artifacts" / uuid
    run_dir.mkdir(parents=True)
    (run_dir / "result.json").write_text(
        json.dumps({"run_id": uuid, "status": "succeeded", "outputs": {}}), encoding="utf-8"
    )
    if mtime is not None:
        os.utime(run_dir / "result.json", (mtime, mtime))
    return run_dir


def test_list_runs_unions_console_and_editor_roots(tmp_path: Path) -> None:
    """两个真实产出路径都要看见：控制台采集写 run_artifacts/，编辑器写 _sandbox/runs/...

    只扫一个根的话，「在编辑器里跑完，流程页看不到这次运行」——同一件事两条路径、
    只接了一条（本项目踩过多次）。
    """
    console_root = tmp_path / "run_artifacts"
    console_root.mkdir()
    _write_run(console_root, "console-1")
    _write_editor_run(tmp_path, "run-1", "editor-1", mtime=1_900_000_000)

    ids = [r["run_id"] for r in list_runs([console_root, tmp_path / "_sandbox" / "runs"])]
    assert ids == ["editor-1", "console-1"]  # 编辑器那次更新，排前面


def test_find_run_dirs_does_not_hardcode_depth(tmp_path: Path) -> None:
    """不写死嵌套深度：换一层目录布局也不该漏（只认「目录里有 result.json」）。"""
    deep = tmp_path / "a" / "b" / "c" / "run-deep"
    deep.mkdir(parents=True)
    (deep / "result.json").write_text('{"status": "succeeded"}', encoding="utf-8")
    shallow = tmp_path / "run-shallow"
    shallow.mkdir()
    (shallow / "result.json").write_text('{"status": "succeeded"}', encoding="utf-8")
    found = {p.name for p in find_run_dirs(tmp_path)}
    assert found == {"run-deep", "run-shallow"}


def test_run_dir_of_searches_all_roots(tmp_path: Path) -> None:
    console_root = tmp_path / "run_artifacts"
    console_root.mkdir()
    _write_run(console_root, "console-1")
    editor = _write_editor_run(tmp_path, "run-1", "editor-1", mtime=1_900_000_000)
    roots = [console_root, tmp_path / "_sandbox" / "runs"]
    assert run_dir_of(roots, "editor-1") == editor.resolve()
    assert run_dir_of(roots, "console-1") == (console_root / "console-1").resolve()
    # 不存在时给规范路径（调用方按 404 处理），不抛越界
    assert run_dir_of(roots, "nope") == (console_root / "nope").resolve()
