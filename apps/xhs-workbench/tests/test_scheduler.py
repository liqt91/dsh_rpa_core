"""错峰排程：固定时刻稳定、铺得开、manual 不排、单 worker 只取一个。"""

from __future__ import annotations

from pathlib import Path

from src.schedule import Schedule, load_schedule
from src.scheduler import PlannedItem, next_due, plan_window, stable_offset

CONFIG = Path(__file__).resolve().parents[1] / "config" / "schedule.json"


def test_offset_is_stable_across_order_and_calls() -> None:
    ids = [f"item-{n}" for n in range(120)]
    schedule = load_schedule(CONFIG)
    first = {p.item_id: p.offset_seconds for p in plan_window(ids, schedule)}
    reversed_ids = list(reversed(ids))
    second = {p.item_id: p.offset_seconds for p in plan_window(reversed_ids, schedule)}
    assert first == second
    for item_id, offset in first.items():
        assert stable_offset(item_id, 12 * 3600) == offset


def test_offsets_are_sorted_and_inside_window() -> None:
    s = load_schedule(CONFIG)
    planned = plan_window([f"item-{n}" for n in range(120)], s)
    assert len(planned) == 120
    assert [p.offset_seconds for p in planned] == sorted(p.offset_seconds for p in planned)
    assert all(0 <= p.offset_seconds < s.window_seconds for p in planned)


def test_duplicates_are_deduped() -> None:
    planned = plan_window(["a", "a", "b"], load_schedule(CONFIG))
    assert [p.item_id for p in planned] == ["a", "b"]


def test_batch_index_uses_configured_size() -> None:
    s = Schedule("daily", "08:00", 12, "", 60, 5, (3, 6))
    planned = plan_window([f"item-{n}" for n in range(12)], s)
    assert sorted({p.batch_index for p in planned}) == [0, 1, 2]
    assert [p.batch_index for p in planned].count(0) == 5


def test_manual_mode_produces_no_plan() -> None:
    s = Schedule("manual", "08:00", 12, "", 60, 20, (3, 6))
    assert plan_window(["a", "b"], s) == []


def test_window_change_actually_moves_items() -> None:
    ids = [f"item-{n}" for n in range(40)]
    wide_schedule = Schedule("daily", "08:00", 12, "", 60, 20, (3, 6))
    narrow_schedule = Schedule("daily", "08:00", 1, "", 60, 20, (3, 6))
    wide = {p.item_id: p.offset_seconds for p in plan_window(ids, wide_schedule)}
    narrow = {p.item_id: p.offset_seconds for p in plan_window(ids, narrow_schedule)}
    assert all(narrow[k] < 3600 for k in narrow)
    assert wide != narrow


def test_next_due_returns_only_one_item() -> None:
    planned = [
        PlannedItem("late", 100, 0),
        PlannedItem("early", 10, 0),
    ]
    assert next_due(planned, 0) is None
    assert next_due(planned, 10).item_id == "early"
    assert next_due(planned, 999).item_id == "late"
