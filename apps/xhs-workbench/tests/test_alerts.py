"""告警单测：新增/下架/变动判定、不足两次不报、去重持久化。"""

from __future__ import annotations

from pathlib import Path

from src.snapshots import Product, Snapshot, SnapshotStore

from src import alerts


def _snap(keyword: str, run_id: str, items: list[dict]) -> Snapshot:
    return Snapshot(
        keyword=keyword,
        run_id=run_id,
        collected_at=run_id,
        items=[Product.from_raw(i) for i in items],
    )


def test_new_item_detected() -> None:
    old = _snap("k", "r1", [{"href": "https://xhs.com/a", "title": "A", "text": "1"}])
    new = _snap(
        "k", "r2",
        [
            {"href": "https://xhs.com/a", "title": "A", "text": "1"},
            {"href": "https://xhs.com/b", "title": "B", "text": "2"},
        ],
    )
    result = alerts.diff_snapshots(old, new)
    kinds = {a.href: a.kind for a in result}
    assert kinds == {"https://xhs.com/b": "new"}


def test_removed_item_detected() -> None:
    old = _snap(
        "k", "r1",
        [
            {"href": "https://xhs.com/a", "title": "A", "text": "1"},
            {"href": "https://xhs.com/b", "title": "B", "text": "2"},
        ],
    )
    new = _snap("k", "r2", [{"href": "https://xhs.com/a", "title": "A", "text": "1"}])
    result = alerts.diff_snapshots(old, new)
    assert [a.href for a in result] == ["https://xhs.com/b"]
    assert result[0].kind == "removed"


def test_changed_text_detected() -> None:
    old = _snap("k", "r1", [{"href": "https://xhs.com/a", "title": "A", "text": "1"}])
    new = _snap("k", "r2", [{"href": "https://xhs.com/a", "title": "A（改）", "text": "999"}])
    result = alerts.diff_snapshots(old, new)
    assert len(result) == 1
    assert result[0].kind == "changed"
    assert "999" in result[0].detail


def test_no_change_no_alert() -> None:
    items = [{"href": "https://xhs.com/a", "title": "A", "text": "1"}]
    old = _snap("k", "r1", items)
    new = _snap("k", "r2", [dict(items[0])])
    assert alerts.diff_snapshots(old, new) == []


def test_single_run_reports_nothing(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path / "data")
    store.record("k", "r1", [{"href": "https://xhs.com/a", "title": "A", "text": "1"}])
    assert alerts.detect_for_keyword(store, "k") == []


def test_detect_for_keyword_uses_last_two(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path / "data")
    store.record("k", "r1", [{"href": "https://xhs.com/a", "title": "A", "text": "1"}])
    store.record("k", "r2", [{"href": "https://xhs.com/a", "title": "A", "text": "1"}])
    # 第三次引入新商品（保留 a）：应与 r2 比对，只报 b 新增
    store.record(
        "k", "r3",
        [
            {"href": "https://xhs.com/a", "title": "A", "text": "1"},
            {"href": "https://xhs.com/b", "title": "B", "text": "2"},
        ],
    )
    result = alerts.detect_for_keyword(store, "k")
    assert [a.href for a in result] == ["https://xhs.com/b"]


def test_alert_log_dedups_by_key(tmp_path: Path) -> None:
    log = alerts.AlertLog(tmp_path / "data")
    a = alerts.Alert("k", "https://xhs.com/b", "new", "B", "首次出现", "2026-01-01T00:00:00+00:00")
    assert log.record([a]) == 1
    # 同 key 再写一次：去重，不新增
    assert log.record([a]) == 0
    assert len(log.all()) == 1
    # 不同 key（不同 kind）仍新增
    b = alerts.Alert(
        "k", "https://xhs.com/b", "removed", "B", "已下架", "2026-01-02T00:00:00+00:00"
    )
    assert log.record([b]) == 1
    assert len(log.all()) == 2
