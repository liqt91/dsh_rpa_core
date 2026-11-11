"""快照库单测：追加、latest/历史、商品身份、跨 keyword 去重、落盘往返、从 run 结果落库。"""

from __future__ import annotations

import json
from pathlib import Path

from src.collector import RunOutcome

from src import snapshots


def _store(tmp_path: Path) -> snapshots.SnapshotStore:
    return snapshots.SnapshotStore(tmp_path / "data")


def _items(n: int) -> list[dict]:
    return [{"href": f"https://xhs.com/{i}", "title": f"t{i}", "text": f"x{i}"} for i in range(n)]


def test_record_then_latest_roundtrips_products(tmp_path: Path) -> None:
    store = _store(tmp_path)
    snap = store.record("AI效率工具", "run-1", _items(3))
    assert snap.count == 3
    latest = store.latest("AI效率工具")
    assert latest is not None
    assert latest.run_id == "run-1"
    assert [p.href for p in latest.items] == [f"https://xhs.com/{i}" for i in range(3)]


def test_history_is_ordered_oldest_first(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record("k", "r1", _items(1))
    store.record("k", "r2", _items(2))
    store.record("k", "r3", _items(3))
    history = store.history("k")
    assert [s.run_id for s in history] == ["r1", "r2", "r3"]
    assert history[-1].count == 3


def test_missing_keyword_returns_none(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.latest("不存在") is None
    assert store.history("不存在") == []


def test_product_requires_stable_href(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        store.record("k", "r", [{"title": "no href"}])
    except ValueError as exc:
        assert "href" in str(exc)
    else:
        raise AssertionError("缺少 href 的商品应当被拒")


def test_product_index_dedups_across_keywords_latest_wins(tmp_path: Path) -> None:
    store = _store(tmp_path)
    # collected_at 显式给出，确保「最新一次采集」胜出（而非迭代顺序）
    store.record(
        "kw-a", "a1", [{"href": "https://xhs.com/dup", "title": "A旧", "text": "a"}],
        collected_at="2026-01-01T00:00:00+00:00",
    )
    store.record(
        "kw-b", "b1", [{"href": "https://xhs.com/dup", "title": "B中", "text": "b"}],
        collected_at="2026-01-02T00:00:00+00:00",
    )
    store.record(
        "kw-a", "a2", [{"href": "https://xhs.com/dup", "title": "A更新", "text": "a2"}],
        collected_at="2026-01-03T00:00:00+00:00",
    )
    index = store.product_index()
    # 同一 href 跨 keyword 去重，collected_at 最新（kw-a 的 a2）覆盖
    assert index["https://xhs.com/dup"].title == "A更新"


def test_persistence_survives_new_store_instance(tmp_path: Path) -> None:
    store1 = _store(tmp_path)
    store1.record("k", "r1", _items(2))
    store2 = snapshots.SnapshotStore(tmp_path / "data")  # 同目录，新实例
    assert store2.latest("k") is not None
    assert store2.latest("k").count == 2


def test_all_latest_and_keywords(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record("kw-a", "a1", _items(1))
    store.record("kw-b", "b1", _items(1))
    assert set(store.all_keywords()) == {"kw-a", "kw-b"}
    latest = store.all_latest()
    assert set(latest) == {"kw-a", "kw-b"}


def test_snapshot_from_outcome_parses_json_string(tmp_path: Path) -> None:
    store = _store(tmp_path)
    outcome = RunOutcome(
        run_id="run-x",
        status="succeeded",
        group="ok",
        outputs={"grabItems": {"outputs": {"result": json.dumps(_items(4), ensure_ascii=False)}}},
        error=None,
    )
    snap = snapshots.snapshot_from_outcome(store, "kw", outcome)
    assert snap.count == 4
    assert store.latest("kw").run_id == "run-x"


def test_snapshot_from_outcome_rejects_non_json(tmp_path: Path) -> None:
    store = _store(tmp_path)
    outcome = RunOutcome(
        run_id="run-x",
        status="succeeded",
        group="ok",
        outputs={"grabItems": {"outputs": {"result": "not json"}}},
        error=None,
    )
    try:
        snapshots.snapshot_from_outcome(store, "kw", outcome)
    except ValueError:
        pass
    else:
        raise AssertionError("非 JSON 的 grabItems 结果应当被拒")
