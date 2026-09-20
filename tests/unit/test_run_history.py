"""M25 S1 运行历史读取器契约：只读、容错、排序与 CLI 输出。

不依赖真实运行：直接造 `run_artifacts/<id>/` 目录结构（result/events/checkpoint）。
"""

import json
from pathlib import Path

import pytest

from rpa_core import cli
from rpa_core.run_history import RunNotFoundError, list_runs, read_run


def _write_run(
    root: Path,
    run_id: str,
    *,
    status: str = "succeeded",
    started: str = "2026-09-20T10:00:00+00:00",
    ended: str = "2026-09-20T10:00:02+00:00",
    workflow_id: str = "demo",
    error: dict | None = None,
    checkpoint: dict | None = None,
    events: list[dict] | None = None,
    result_raw: str | None = None,
) -> Path:
    run_dir = root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    if result_raw is not None:
        (run_dir / "result.json").write_text(result_raw, encoding="utf-8")
    else:
        (run_dir / "result.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "workflow_id": workflow_id,
                    "status": status,
                    "started_at": started,
                    "ended_at": ended,
                    "error": error,
                }
            ),
            encoding="utf-8",
        )
    if checkpoint is not None:
        (run_dir / "checkpoint.json").write_text(
            json.dumps(checkpoint), encoding="utf-8"
        )
    if events is not None:
        (run_dir / "events.jsonl").write_text(
            "\n".join(json.dumps(event) for event in events) + "\n",
            encoding="utf-8",
        )
    return run_dir


def test_list_runs_summarizes_and_sorts_by_time(tmp_path):
    """摘要字段完整；按结束时间倒序（新在前）。"""
    _write_run(tmp_path, "old", ended="2026-09-20T09:00:00+00:00")
    _write_run(tmp_path, "new", ended="2026-09-20T12:00:00+00:00", status="failed",
               error={"code": "TIMEOUT", "message": "超时"})
    _write_run(tmp_path, "mid", ended="2026-09-20T10:30:00+00:00")

    runs = list_runs(tmp_path)
    assert [item["runId"] for item in runs] == ["new", "mid", "old"]
    newest = runs[0]
    assert newest["status"] == "failed"
    assert newest["errorCode"] == "TIMEOUT"
    assert newest["durationMs"] == 7_200_000  # 10:00 → 12:00
    assert newest["resumable"] is False  # 没写检查点


def test_list_runs_limit_and_empty_root(tmp_path):
    for index in range(5):
        _write_run(tmp_path, f"r{index}", ended=f"2026-09-20T10:0{index}:00+00:00")
    assert len(list_runs(tmp_path, limit=3)) == 3
    assert len(list_runs(tmp_path, limit=0)) == 5  # 0 = 不限制
    assert list_runs(tmp_path / "missing") == []


def test_list_runs_tolerates_corrupt_and_ignores_non_runs(tmp_path):
    """损坏的 result.json 不炸整体（status=unknown）；非运行目录被忽略。"""
    _write_run(tmp_path, "good")
    _write_run(tmp_path, "broken", result_raw="{not json")
    (tmp_path / "not-a-run").mkdir()
    (tmp_path / "not-a-run" / "readme.txt").write_text("x", encoding="utf-8")

    runs = {item["runId"]: item for item in list_runs(tmp_path)}
    assert set(runs) == {"good", "broken"}
    assert runs["broken"]["status"] == "unknown"


def test_read_run_returns_inputs_checkpoint_and_events(tmp_path):
    checkpoint = {
        "workflowId": "demo",
        "completedSteps": ["root/s1"],
        "scopes": {"inputs": {"keyword": "新闻"}},
        "breakpoints": ["s2"],
        "consumedBreakpoints": ["s2"],
        "pauseReason": "breakpoint",
        "pausedAtNode": "s2",
    }
    _write_run(
        tmp_path, "run-a", status="paused", checkpoint=checkpoint,
        events=[
            {"type": "runStarted", "payload": {}},
            {"type": "stepCompleted", "node_id": "s1", "payload": {"outputs": {"x": 1}}},
        ],
    )
    detail = read_run(tmp_path, "run-a")
    assert detail["status"] == "paused"
    assert detail["inputs"] == {"keyword": "新闻"}
    assert detail["breakpoints"] == ["s2"]
    assert detail["consumedBreakpoints"] == ["s2"]
    assert detail["pauseReason"] == "breakpoint"
    assert detail["pausedAtNode"] == "s2"
    assert detail["resumable"] is True
    assert detail["eventCount"] == 2
    assert detail["events"][1]["node_id"] == "s1"


def test_read_run_skips_corrupt_event_lines(tmp_path):
    run_dir = _write_run(tmp_path, "run-b", events=[{"type": "runStarted"}])
    with (run_dir / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("{half-written\n")
    detail = read_run(tmp_path, "run-b")
    assert detail["eventCount"] == 1


def test_read_run_unknown_raises(tmp_path):
    with pytest.raises(RunNotFoundError):
        read_run(tmp_path, "nope")
    (tmp_path / "empty-dir").mkdir()
    with pytest.raises(RunNotFoundError):
        read_run(tmp_path, "empty-dir")


def _run_cli(argv, capsys):
    import sys

    old = sys.argv
    sys.argv = ["rpa-core", *argv]
    try:
        code = cli.main()
    finally:
        sys.argv = old
    return code, capsys.readouterr().out.strip()


def test_cli_runs_list_and_show(tmp_path, capsys):
    _write_run(tmp_path, "cli-run", checkpoint={"scopes": {"inputs": {"a": 1}}})
    code, out = _run_cli(["runs", "list", "--artifacts", str(tmp_path)], capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload["runs"][0]["runId"] == "cli-run"

    code, out = _run_cli(["runs", "show", "cli-run", "--artifacts", str(tmp_path)], capsys)
    assert code == 0
    detail = json.loads(out)
    assert detail["inputs"] == {"a": 1}


def test_cli_runs_show_missing_returns_2(tmp_path, capsys):
    code, out = _run_cli(["runs", "show", "ghost", "--artifacts", str(tmp_path)], capsys)
    assert code == 2
    assert json.loads(out)["error"] == "RUN_NOT_FOUND"
