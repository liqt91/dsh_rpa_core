"""监控池状态机单测：状态迁移合法性、暂停/恢复、全暂停/全恢复、计数、持久化。"""

from __future__ import annotations

from pathlib import Path

import pytest
from src.monitor import STATES, MonitorPool


def _pool(tmp_path: Path) -> MonitorPool:
    return MonitorPool(tmp_path / "data")


def test_register_defaults_pending(tmp_path: Path) -> None:
    entry = _pool(tmp_path).register("AI效率工具")
    assert entry.state == "pending"
    assert _pool(tmp_path).get("AI效率工具") is not None


def test_register_is_idempotent(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    pool.register("k")
    again = pool.register("k")
    assert again.state == "pending"
    assert len(pool.all()) == 1


def test_set_running_requires_valid_prior_state(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    pool.register("k")
    pool.set_running("k", "run-1")
    assert pool.get("k").state == "running"
    with pytest.raises(ValueError):
        pool.set_running("k", "run-2")


def test_ok_only_from_running(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    pool.register("k")
    with pytest.raises(ValueError):
        pool.set_ok("k", "run-1")
    pool.set_running("k", "run-1")
    pool.set_ok("k", "run-1")
    assert pool.get("k").state == "ok"
    assert pool.get("k").last_run_id == "run-1"


def test_failed_only_from_running_and_records_error(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    pool.register("k")
    pool.set_running("k", "run-1")
    pool.set_failed("k", "timeout")
    entry = pool.get("k")
    assert entry.state == "failed"
    assert entry.last_error == "timeout"


def test_pause_resume_restores_previous_state(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    pool.register("k")
    pool.set_running("k", "run-1")
    pool.set_ok("k", "run-1")
    pool.pause("k")
    assert pool.get("k").state == "paused"
    pool.resume("k")
    assert pool.get("k").state == "ok"


def test_double_pause_rejected(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    pool.register("k")
    pool.pause("k")
    with pytest.raises(ValueError):
        pool.pause("k")


def test_pause_blocks_scheduling(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    pool.register("k")
    pool.pause("k")
    with pytest.raises(ValueError):
        pool.set_scheduled("k")


def test_pause_all_and_resume_all(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    pool.register("a")
    pool.register("b")
    pool.set_running("a", "r1")
    pool.set_ok("a", "r1")
    pool.set_running("b", "r2")
    assert pool.pause_all() == 2
    assert all(e.state == "paused" for e in pool.all())
    assert pool.resume_all() == 2
    states = {e.keyword: e.state for e in pool.all()}
    assert states == {"a": "ok", "b": "running"}


def test_counts_by_state(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    pool.register("a")
    pool.register("b")
    pool.register("c")
    pool.set_running("a", "r1")
    pool.set_ok("a", "r1")
    pool.set_running("b", "r2")
    pool.set_failed("b", "x")
    counts = pool.counts_by_state()
    assert counts["ok"] == 1 and counts["failed"] == 1 and counts["pending"] == 1
    assert set(counts) == STATES


def test_persistence_across_instances(tmp_path: Path) -> None:
    p1 = _pool(tmp_path)
    p1.register("k")
    p1.set_running("k", "r1")
    p1.set_ok("k", "r1")
    p2 = MonitorPool(tmp_path / "data")
    assert p2.get("k").state == "ok"
