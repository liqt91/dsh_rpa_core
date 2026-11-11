"""运行记录视图：把 ``run_artifacts/<run_id>/`` 里的 result.json + events.jsonl
摊平成「步骤时间线」，供控制台的流程面板定位「卡在哪一步、每步花了多久、输出了什么」。

只读文件系统，不跑任何命令；``run_artifacts`` 根可注入，单测用临时目录。

为什么需要它：一次采集失败/零结果时，唯一能回答「哪一步出的问题」的证据就是这两个文件。
控制台原来只能看到监控池的 ``last_error`` 一句话，看不到步骤级事实。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

# 这些文件是运行时自己的记账，不算「本次 run 的产物」
_RUNTIME_FILES = {"result.json", "events.jsonl", "checkpoint.json"}


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def read_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return events
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            events.append(item)
    return events


def _ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _duration(start: Any, end: Any) -> float | None:
    a, b = _ts(start), _ts(end)
    if a is None or b is None:
        return None
    return round((b - a).total_seconds(), 3)


def _item_count(result: dict[str, Any]) -> int:
    raw = ((result.get("outputs") or {}).get("grabItems") or {}).get("outputs", {}).get("result")
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return 0
        return len(parsed) if isinstance(parsed, list) else 0
    if isinstance(raw, list):
        return len(raw)
    return 0


def _timeline(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """events → 步骤行。重试只累加 attempts，不重复出行。"""
    rows: list[dict[str, Any]] = []
    index: dict[str, dict[str, Any]] = {}
    for event in events:
        node_id = str(event.get("node_id") or "")
        if not node_id:
            continue
        kind = event.get("type")
        payload = event.get("payload") or {}
        row = index.get(node_id)
        if kind == "stepStarted":
            if row is None:
                row = {
                    "id": node_id,
                    "command": str(payload.get("command") or ""),
                    "status": "pending",
                    "started_at": event.get("timestamp"),
                    "finished_at": None,
                    "duration_s": None,
                    "attempts": 0,
                    "outputs": {},
                    "error": None,
                }
                index[node_id] = row
                rows.append(row)
        elif kind == "stepAttemptStarted" and row is not None:
            row["attempts"] = max(row["attempts"], int(payload.get("attempt") or 0))
        elif kind == "stepCompleted" and row is not None:
            row["status"] = "ok"
            row["finished_at"] = event.get("timestamp")
            row["outputs"] = payload.get("outputs") or {}
        elif kind == "stepFailed" and row is not None:
            row["status"] = "failed"
            row["finished_at"] = event.get("timestamp")
            row["error"] = payload.get("error")
    for row in rows:
        row["duration_s"] = _duration(row["started_at"], row["finished_at"])
    return rows


def summarize_run(run_dir: str | Path) -> dict[str, Any]:
    """单次 run 的摘要 + 步骤时间线。``run_dir`` 直接指向 ``run_artifacts/<run_id>``。"""
    fp = Path(run_dir)
    result = _read_json(fp / "result.json") or {}
    events = read_events(fp / "events.jsonl")
    steps = _timeline(events)
    known = {row["id"] for row in steps}

    # result.json 是终态事实源：events 缺行（被截断/老版本）时用它补齐步骤与输出。
    for step_id, node_out in (result.get("outputs") or {}).items():
        outputs = (node_out or {}).get("outputs") or {}
        if step_id in known:
            for row in steps:
                if row["id"] == step_id and not row["outputs"]:
                    row["outputs"] = outputs
            continue
        steps.append(
            {
                "id": step_id,
                "command": "",
                "status": "ok" if result.get("status") == "succeeded" else "unknown",
                "started_at": None,
                "finished_at": None,
                "duration_s": None,
                "attempts": 0,
                "outputs": outputs,
                "error": None,
            }
        )

    started = next((e.get("timestamp") for e in events if e.get("type") == "runStarted"), None)
    terminals = {"runFinished", "runFailed"}
    finished = next(
        (e.get("timestamp") for e in reversed(events) if e.get("type") in terminals),
        None,
    )
    artifacts = _artifacts(fp)
    return {
        "run_id": str(result.get("run_id") or fp.name),
        "status": str(result.get("status") or "unknown"),
        "error": result.get("error"),
        "started_at": started,
        "finished_at": finished,
        "duration_s": _duration(started, finished),
        "items": _item_count(result),
        "step_total": len(steps),
        "step_ok": sum(1 for r in steps if r["status"] == "ok"),
        "step_failed": sum(1 for r in steps if r["status"] == "failed"),
        "steps": steps,
        "artifacts": artifacts,
    }


def _artifacts(run_dir: Path) -> dict[str, Any]:
    shots: list[str] = []
    files: list[dict[str, Any]] = []
    try:
        entries = sorted(run_dir.iterdir())
    except OSError:
        return {"screenshots": shots, "files": files}
    for path in entries:
        if not path.is_file() or path.name in _RUNTIME_FILES:
            continue
        if path.suffix.lower() in {".png", ".jpg", ".jpeg"}:
            shots.append(path.name)
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        files.append({"name": path.name, "size": size})
    return {"screenshots": shots, "files": files}


def _run_dirs(root: Path) -> list[Path]:
    try:
        return [p for p in root.iterdir() if p.is_dir() and (p / "result.json").is_file()]
    except OSError:
        return []


def list_runs(root: str | Path, limit: int = 20) -> list[dict[str, Any]]:
    """按修改时间倒序列出最近若干次 run（只读 result.json 与 events 的行数级信息）。"""
    dirs = _run_dirs(Path(root))
    dirs.sort(key=lambda p: (p / "result.json").stat().st_mtime, reverse=True)
    out: list[dict[str, Any]] = []
    for run_dir in dirs[: max(1, limit)]:
        summary = summarize_run(run_dir)
        summary.pop("steps", None)
        out.append(summary)
    return out


def local_safe_run_id(run_id: str) -> str:
    """run_id 只允许单段文件名字符，避免 ``..`` 之类穿越到 artifacts 之外。"""
    cleaned = run_id.strip()
    if not cleaned or cleaned in {".", ".."}:
        raise ValueError("run_id 不能为空")
    if any(sep in cleaned for sep in ("/", "\\", "\x00")) or ".." in cleaned:
        raise ValueError(f"非法 run_id：{run_id!r}")
    return cleaned


def run_dir_of(root: str | Path, run_id: str) -> Path:
    base = Path(root).resolve()
    target = (base / local_safe_run_id(run_id)).resolve()
    if target.parent != base:
        raise ValueError(f"run_id 越界：{run_id!r}")
    return target
