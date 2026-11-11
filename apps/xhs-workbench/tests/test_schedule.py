"""频率配置：数值真的可改、并发上限真的不可改。"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from src.schedule import (
    MAX_CONCURRENT_RUNS,
    estimate,
    load_schedule,
    parse_schedule,
    validate_schedule,
)

CONFIG = Path(__file__).resolve().parents[1] / "config" / "schedule.json"


def raw_config() -> dict:
    return copy.deepcopy(json.loads(CONFIG.read_text(encoding="utf-8"))["schedule"])


def test_default_preset_loads() -> None:
    s = load_schedule(CONFIG)
    assert s.mode == "daily"
    assert s.start_at == "08:00"
    assert s.window_seconds == 12 * 3600
    assert s.batch_size == 20
    assert s.per_item_interval_seconds == (3.0, 6.0)
    assert MAX_CONCURRENT_RUNS == 1


def test_every_number_is_customizable() -> None:
    raw = raw_config()
    raw.update(
        {
            "mode": "interval",
            "startAt": "09:30",
            "windowHours": 6,
            "cron": "*/15 * * * *",
            "intervalMinutes": 15,
            "batchSize": 7,
            "perItemIntervalSeconds": [1, 2],
        }
    )
    s = parse_schedule(raw)
    assert (s.mode, s.start_at, s.cron) == ("interval", "09:30", "*/15 * * * *")
    assert s.window_hours == 6
    assert s.window_seconds == 6 * 3600
    assert s.interval_minutes == 15
    assert s.batch_size == 7
    assert s.per_item_interval_seconds == (1.0, 2.0)


@pytest.mark.parametrize(
    ("mutate", "fragment"),
    [
        (lambda r: r.update({"maxConcurrentRuns": 2}), "maxConcurrentRuns"),
        (lambda r: r.pop("maxConcurrentRuns"), "maxConcurrentRuns"),
        (lambda r: r.update({"mode": "whenever"}), "mode"),
        (lambda r: r.update({"startAt": "25:99"}), "startAt"),
        (lambda r: r.update({"windowHours": 0}), "windowHours"),
        (lambda r: r.update({"windowHours": 30}), "windowHours"),
        (lambda r: r.update({"batchSize": 0}), "batchSize"),
        (lambda r: r.update({"intervalMinutes": -1}), "intervalMinutes"),
        (lambda r: r.update({"perItemIntervalSeconds": [6, 3]}), "perItemIntervalSeconds"),
        (lambda r: r.update({"perItemIntervalSeconds": [3]}), "perItemIntervalSeconds"),
    ],
)
def test_invalid_configs_are_rejected(mutate, fragment: str) -> None:
    raw = raw_config()
    mutate(raw)
    errors = validate_schedule(raw)
    assert any(fragment in e for e in errors), errors
    with pytest.raises(ValueError):
        parse_schedule(raw)


def test_cron_mode_requires_expression() -> None:
    raw = raw_config()
    raw.update({"mode": "cron", "cron": ""})
    assert any("cron" in e for e in validate_schedule(raw))


def test_estimate_reflects_config() -> None:
    s = parse_schedule(raw_config())
    e = estimate(s, 120)
    assert e["runsPerDay"] == 120
    assert e["requestsPerDay"] == 120 * 20
    assert e["peakQps"] == pytest.approx(1 / 3)
    assert e["windowSeconds"] == 12 * 3600


def test_estimate_manual_and_empty_pool() -> None:
    raw = raw_config()
    raw["mode"] = "manual"
    assert estimate(parse_schedule(raw), 120)["runsPerDay"] == 0
    assert estimate(load_schedule(CONFIG), 0)["requestsPerDay"] == 0


def test_zero_interval_reports_unbounded_qps() -> None:
    raw = raw_config()
    raw["perItemIntervalSeconds"] = [0, 0]
    assert estimate(parse_schedule(raw), 1)["peakQps"] == float("inf")
