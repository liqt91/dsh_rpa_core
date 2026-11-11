"""采集频率配置：数值全部可改，只有并发上限不可改。

ADR 0015：扩展侧串行化 connectNative，同时跑两个浏览器 run 会拉起多个 native
host 进程。所以 maxConcurrentRuns 恒为 1 —— 写成别的值、或者干脆删掉这个键，
都判为非法配置，绝不静默改成 1 之后继续跑。

「频率可自定义」= 改触发时刻、窗口、批次、间隔和模式，不是放开并发数。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MODES = frozenset({"manual", "daily", "interval", "cron"})
MAX_CONCURRENT_RUNS = 1
_START_AT_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


@dataclass(frozen=True)
class Schedule:
    mode: str
    start_at: str
    window_hours: float
    cron: str
    interval_minutes: int
    batch_size: int
    per_item_interval_seconds: tuple[float, float]

    @property
    def window_seconds(self) -> int:
        return int(self.window_hours * 3600)


def load_schedule(path: str | Path) -> Schedule:
    """从配置文件读 schedule 段；文件里其他键（schema / note）不参与校验。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "schedule" not in data:
        raise ValueError("配置文件缺 schedule 段")
    return parse_schedule(data["schedule"])


def parse_schedule(raw: dict[str, Any]) -> Schedule:
    errors = validate_schedule(raw)
    if errors:
        raise ValueError("; ".join(errors))
    pair = raw["perItemIntervalSeconds"]
    return Schedule(
        mode=raw["mode"],
        start_at=raw["startAt"],
        window_hours=float(raw["windowHours"]),
        cron=str(raw.get("cron", "")),
        interval_minutes=int(raw["intervalMinutes"]),
        batch_size=int(raw["batchSize"]),
        per_item_interval_seconds=(float(pair[0]), float(pair[1])),
    )


def validate_schedule(raw: dict[str, Any]) -> list[str]:
    """返回错误列表而不是抛异常，方便 UI 把每一条都显示出来。"""
    errors: list[str] = []
    if not isinstance(raw, dict):
        return ["schedule 必须是对象"]

    if raw.get("maxConcurrentRuns") != MAX_CONCURRENT_RUNS:
        errors.append(
            f"maxConcurrentRuns 必须恰为 {MAX_CONCURRENT_RUNS}（ADR 0015），"
            f"当前是 {raw.get('maxConcurrentRuns')!r}"
        )
    if raw.get("mode") not in MODES:
        errors.append(f"mode 必须是 {sorted(MODES)} 之一，当前是 {raw.get('mode')!r}")
    if not isinstance(raw.get("startAt"), str) or not _START_AT_RE.fullmatch(raw["startAt"]):
        errors.append(f"startAt 必须是 HH:MM，当前是 {raw.get('startAt')!r}")
    if raw.get("mode") == "cron" and not str(raw.get("cron", "")).strip():
        errors.append("mode=cron 时 cron 表达式不能为空")

    window = raw.get("windowHours")
    if not isinstance(window, (int, float)) or isinstance(window, bool) or not 0 < window <= 24:
        errors.append(f"windowHours 必须落在 (0, 24]，当前是 {window!r}")
    for key in ("intervalMinutes", "batchSize"):
        value = raw.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            errors.append(f"{key} 必须是正整数，当前是 {value!r}")

    pair = raw.get("perItemIntervalSeconds")
    if (
        not isinstance(pair, list)
        or len(pair) != 2
        or any(not isinstance(x, (int, float)) or isinstance(x, bool) for x in pair)
        or pair[0] < 0
        or pair[1] < pair[0]
    ):
        errors.append(f"perItemIntervalSeconds 必须是 [min, max] 且 0 ≤ min ≤ max，当前是 {pair!r}")
    return errors


def estimate(schedule: Schedule, item_count: int) -> dict[str, float | int]:
    """按当前配置估算每日请求数与峰值 QPS，让 UI 在用户调间隔前给出后果。

    口径：每件商品一个 run，每个 run 内最多抓 batchSize 条；峰值按两次 run 之间
    最小间隔的倒数算。这是保守上界，不是实测值。
    """
    if item_count < 0:
        raise ValueError("item_count 不能为负")
    if schedule.mode == "manual" or item_count == 0:
        return {"runsPerDay": 0, "requestsPerDay": 0, "peakQps": 0.0, "windowSeconds": 0}
    lo = schedule.per_item_interval_seconds[0]
    peak = float("inf") if lo == 0 else 1.0 / lo
    return {
        "runsPerDay": item_count,
        "requestsPerDay": item_count * schedule.batch_size,
        "peakQps": peak,
        "windowSeconds": schedule.window_seconds,
    }
