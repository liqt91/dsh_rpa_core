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


def test_snapshot_from_outcome_skips_items_without_href_and_reports_count(tmp_path: Path) -> None:
    """真机踩到：`section.note-item` 会命中不含链接的容器节点，缺 href 的记录让整轮采集报错。

    现在改为丢弃 + 计数上报（Snapshot.skipped），既不静默吞掉，也不升级成整轮失败。
    """
    store = _store(tmp_path)
    raw = _items(3) + [{"title": "没有链接的容器", "text": "x"}]
    outcome = RunOutcome(
        run_id="run-y",
        status="succeeded",
        group="ok",
        outputs={"grabItems": {"outputs": {"result": json.dumps(raw, ensure_ascii=False)}}},
        error=None,
    )
    snap = snapshots.snapshot_from_outcome(store, "kw", outcome)
    assert snap.count == 3
    assert snap.skipped == 1
    # 落盘往返后 skipped 仍在，UI 才能把「丢了 1 条」说明白
    assert store.latest("kw").skipped == 1


def test_record_keeps_strict_identity_for_direct_callers(tmp_path: Path) -> None:
    """宽松只发生在 outcome → store 这一层；底层 record 仍拒绝无身份记录。"""
    store = _store(tmp_path)
    try:
        store.record("kw", "r", [{"title": "无链接"}])
    except ValueError:
        return
    raise AssertionError("record 应当拒绝缺 href 的记录")


def test_post_detail_fields_survive_roundtrip(tmp_path: Path) -> None:
    """帖子详情（赞/藏/评 + 逐条评论）必须能从 grabItems 结果一路带到商品库。

    只断言「count 对」是假绿灯：字段在 Product 里被丢掉时条数照样是 1。
    """
    store = _store(tmp_path)
    post = {
        "href": "https://www.xiaohongshu.com/explore/abc",
        "title": "一篇笔记",
        "text": "正文内容",
        "likes": "1.2万",
        "collects": "345",
        "comments": "88",
        "commentList": [
            {"author": "甲", "text": "第一条"},
            {"author": "", "text": "第二条"},
            "纯文本评论",
            {"text": ""},  # 脏记录要被跳过，不能塞空行进库
            42,
        ],
    }
    outcome = RunOutcome(
        run_id="run-detail",
        status="succeeded",
        group="ok",
        outputs={"grabItems": {"outputs": {"result": json.dumps([post], ensure_ascii=False)}}},
        error=None,
    )
    snap = snapshots.snapshot_from_outcome(store, "kw", outcome)
    assert snap.count == 1
    item = store.latest("kw").items[0]  # 落盘往返，不只是内存对象
    assert item.likes == "1.2万" and item.collects == "345" and item.comments == "88"
    assert item.comment_list == [
        {"author": "甲", "text": "第一条"},
        {"author": "", "text": "第二条"},
        {"author": "", "text": "纯文本评论"},
    ]
    exported = store.product_index()[post["href"]].to_json()
    assert set(exported) == {
        "href", "title", "text", "likes", "collects", "comments", "commentList",
    }
    assert exported["commentList"] == item.comment_list


def test_legacy_snapshot_without_detail_fields_still_loads(tmp_path: Path) -> None:
    """老数据只有三个字段，读出来补空值而不是炸（开发期不做数据迁移）。"""
    store = _store(tmp_path)
    store.record("kw", "r1", [{"href": "https://xhs.com/old", "title": "旧", "text": "t"}])
    item = store.latest("kw").items[0]
    assert (item.likes, item.collects, item.comments, item.comment_list) == ("", "", "", [])
