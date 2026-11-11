"""错峰排程：纯函数，不启线程、不做 IO。

参考工作台把 120 件商品铺进 12 小时窗口，每件有固定时刻。这里用稳定 hash 做
同一件事：同一 item_id 在窗口内的偏移永远相同，于是每天同一时刻采，
跨天快照才对得齐；随机 jitter 只加在批内间隔上，不打乱固定时刻。

单 worker 语义不靠这里的循环保证，而靠 schedule.MAX_CONCURRENT_RUNS == 1：
调度器产出的是一串有序时刻，执行层一次只取一个。
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass

from .schedule import Schedule


@dataclass(frozen=True)
class PlannedItem:
    item_id: str
    offset_seconds: int
    batch_index: int


def stable_offset(item_id: str, window_seconds: int) -> int:
    """sha256 前 8 字节取模，结果与列表顺序、调用次数、进程都无关。"""
    if window_seconds <= 0:
        return 0
    digest = hashlib.sha256(item_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % window_seconds


def plan_window(item_ids: Sequence[str], schedule: Schedule) -> list[PlannedItem]:
    """返回按固定时刻升序的排程；manual 模式不排。重复 id 只保留一次。"""
    if schedule.mode == "manual":
        return []
    window = schedule.window_seconds
    if window <= 0:
        return []
    unique = sorted(set(item_ids))
    planned = [
        PlannedItem(
            item_id=item_id,
            offset_seconds=stable_offset(item_id, window),
            batch_index=index // schedule.batch_size,
        )
        for index, item_id in enumerate(unique)
    ]
    return sorted(planned, key=lambda item: (item.offset_seconds, item.item_id))


def next_due(planned: Sequence[PlannedItem], elapsed_seconds: int) -> PlannedItem | None:
    """单 worker 取下一个到期项；没到就返回 None，由调用方自己等。"""
    return next(
        (item for item in planned if item.offset_seconds <= elapsed_seconds),
        None,
    )
