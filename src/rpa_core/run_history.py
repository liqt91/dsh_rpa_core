"""运行历史（M25）：只读扫描 `run_artifacts`，列出与查看历史运行。

为什么放在顶层而不是 `runtime/`：CLI、devserver、GUI 三方共用；devserver 受架构
检查约束不得 import `rpa_core.runtime`（ADR 0011），因此本模块只依赖 stdlib。

设计约束（M25 计划）：
- **只读**：不引入新的持久化格式，直接读既有的 `result.json` / `events.jsonl` /
  `checkpoint.json`；不建数据库（AGENTS 规则 10）。
- **容错优先**：目录里任何文件缺失、半写、损坏都不得让列表/详情整体失败——跳过该
  字段或该运行即可（这些目录由 run 子进程随时写入，读取方与写入方天然并发）。
- **懒加载**：列表只解析 `result.json` 的头部字段与少量 stat，不读事件大文件；
  详情才解析事件与检查点。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

RESULT_FILE = "result.json"
EVENTS_FILE = "events.jsonl"
CHECKPOINT_FILE = "checkpoint.json"
CONTROL_FILE = "control.json"

DEFAULT_LIMIT = 50


class RunNotFoundError(RuntimeError):
    """请求的历史运行不存在（或不是一次运行目录）。"""


def _read_json(path: Path) -> dict[str, Any] | None:
    """容错读 JSON 对象；缺失/损坏一律返回 None。"""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _duration_ms(started: Any, ended: Any) -> int | None:
    """由 ISO 起止时间算耗时（毫秒）；任一不可解析返回 None。"""
    if not isinstance(started, str) or not isinstance(ended, str):
        return None
    try:
        start = datetime.fromisoformat(started)
        end = datetime.fromisoformat(ended)
    except ValueError:
        return None
    return max(0, int((end - start).total_seconds() * 1000))


def _run_dirs(artifacts_root: Path) -> list[Path]:
    """候选运行目录：含任一证据文件的直接子目录。"""
    try:
        entries = list(Path(artifacts_root).iterdir())
    except OSError:
        return []
    dirs: list[Path] = []
    for entry in entries:
        if not entry.is_dir():
            continue
        if any(
            (entry / name).is_file()
            for name in (RESULT_FILE, EVENTS_FILE, CHECKPOINT_FILE)
        ):
            dirs.append(entry)
    return dirs


def _summarize(run_dir: Path) -> dict[str, Any]:
    """把一次运行的证据压成摘要（只读 result.json + stat，不碰事件文件）。"""
    result = _read_json(run_dir / RESULT_FILE) or {}
    checkpoint = _read_json(run_dir / CHECKPOINT_FILE) or {}
    error = result.get("error") if isinstance(result.get("error"), dict) else {}
    started = result.get("started_at")
    ended = result.get("ended_at")
    summary: dict[str, Any] = {
        "runId": run_dir.name,
        "workflowId": result.get("workflow_id") or checkpoint.get("workflowId") or "",
        "status": result.get("status") or "unknown",
        "startedAt": started,
        "endedAt": ended,
        "durationMs": _duration_ms(started, ended),
        "errorCode": error.get("code"),
        "errorMessage": error.get("message"),
        # 能否「继续/单步」：有检查点即可（是否 paused 由 status 判断）
        "resumable": (run_dir / CHECKPOINT_FILE).is_file(),
        "pauseReason": checkpoint.get("pauseReason"),
        "pausedAtNode": checkpoint.get("pausedAtNode"),
        "completedSteps": len(checkpoint.get("completedSteps") or []),
    }
    try:
        summary["mtime"] = run_dir.stat().st_mtime
    except OSError:
        summary["mtime"] = 0.0
    return summary


def list_runs(artifacts_root: Path, *, limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
    """列出历史运行摘要，按时间倒序（结束时间优先，缺省回退目录 mtime）。

    `limit<=0` 表示不限制（调用方自行控制展示）。
    """
    runs = [_summarize(run_dir) for run_dir in _run_dirs(artifacts_root)]

    def sort_key(item: dict[str, Any]) -> float:
        ended = item.get("endedAt")
        if isinstance(ended, str):
            try:
                return datetime.fromisoformat(ended).timestamp()
            except ValueError:
                pass
        return float(item.get("mtime") or 0.0)

    runs.sort(key=sort_key, reverse=True)
    if limit and limit > 0:
        return runs[:limit]
    return runs


def _read_events(run_dir: Path) -> list[dict[str, Any]]:
    """逐行读 events.jsonl；坏行跳过（写入方可能正在追加最后一行）。"""
    events: list[dict[str, Any]] = []
    try:
        lines = (run_dir / EVENTS_FILE).read_text(encoding="utf-8").splitlines()
    except OSError:
        return events
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def read_run(artifacts_root: Path, run_id: str) -> dict[str, Any]:
    """读一次运行的详情：摘要 + 输入 + 检查点调试信息 + 事件时间线。"""
    run_dir = Path(artifacts_root) / run_id
    if not run_dir.is_dir():
        raise RunNotFoundError(f"运行不存在：{run_id}")
    if not any(
        (run_dir / name).is_file()
        for name in (RESULT_FILE, EVENTS_FILE, CHECKPOINT_FILE)
    ):
        raise RunNotFoundError(f"不是一次运行目录：{run_id}")

    checkpoint = _read_json(run_dir / CHECKPOINT_FILE) or {}
    scopes = checkpoint.get("scopes") if isinstance(checkpoint.get("scopes"), dict) else {}
    events = _read_events(run_dir)
    detail = _summarize(run_dir)
    detail.update(
        {
            "inputs": (scopes or {}).get("inputs") or {},
            "breakpoints": checkpoint.get("breakpoints") or [],
            "consumedBreakpoints": checkpoint.get("consumedBreakpoints") or [],
            "events": events,
            "eventCount": len(events),
        }
    )
    return detail
