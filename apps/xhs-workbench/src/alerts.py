"""前后比对告警：对同一 keyword 的相邻快照做差异比对，产出新增 / 下架 / 变动告警。

纯逻辑，不碰网络、不碰浏览器、不 import 运行时内部模块。商品身份 = ``href``。
告警日志（AlertLog）负责去重持久化，避免每次采集都把同一处变化重复报一遍。

变动判定：标题或正文（正文含点赞/销量数字）任一不同即算 ``changed``。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.snapshots import Snapshot, SnapshotStore


@dataclass(frozen=True)
class Alert:
    keyword: str
    href: str
    kind: str  # "new" | "removed" | "changed"
    title: str
    detail: str
    detected_at: str

    @property
    def key(self) -> str:
        """去重键：同一 keyword 的同一商品同一类变化只报一次。"""
        return f"{self.keyword}|{self.href}|{self.kind}"

    def to_json(self) -> dict[str, str]:
        return {
            "keyword": self.keyword,
            "href": self.href,
            "kind": self.kind,
            "title": self.title,
            "detail": self.detail,
            "detected_at": self.detected_at,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Alert:
        return cls(
            keyword=str(data["keyword"]),
            href=str(data["href"]),
            kind=str(data["kind"]),
            title=str(data["title"]),
            detail=str(data["detail"]),
            detected_at=str(data["detected_at"]),
        )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def diff_snapshots(old: Snapshot, new: Snapshot) -> list[Alert]:
    """比较同一 keyword 的相邻两次快照，返回本这次新出现的变化。"""
    now = _now()
    old_by_href = {item.href: item for item in old.items}
    new_by_href = {item.href: item for item in new.items}
    alerts: list[Alert] = []

    for href, item in new_by_href.items():
        if href not in old_by_href:
            alerts.append(
                Alert(new.keyword, href, "new", item.title, "首次出现", now)
            )
        else:
            prev = old_by_href[href]
            if prev.title != item.title or prev.text != item.text:
                alerts.append(
                    Alert(
                        new.keyword,
                        href,
                        "changed",
                        item.title,
                        f"标题/正文变化：旧「{prev.text}」→ 新「{item.text}」",
                        now,
                    )
                )

    for href, item in old_by_href.items():
        if href not in new_by_href:
            alerts.append(
                Alert(new.keyword, href, "removed", item.title, "已下架/搜不到", now)
            )
    return alerts


def detect_for_keyword(store: SnapshotStore, keyword: str) -> list[Alert]:
    """对某个 keyword 取最近两次快照做差异；不足两次返回空。"""
    history = store.history(keyword)
    if len(history) < 2:
        return []
    return diff_snapshots(history[-2], history[-1])


def detect_all(store: SnapshotStore) -> list[Alert]:
    """所有 keyword 的相邻快照差异汇总。"""
    return [a for kw in store.all_keywords() for a in detect_for_keyword(store, kw)]


class AlertLog:
    """告警日志：追加并去重持久化到 ``data/alerts.json``。"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._path = self.root / "alerts.json"

    def _read(self) -> list[dict[str, Any]]:
        if not self._path.is_file():
            return []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return data if isinstance(data, list) else []

    def record(self, alerts: list[Alert]) -> int:
        """写入新告警，跳过已存在的去重键；返回实际新增条数。"""
        existing = {Alert.from_json(d).key for d in self._read()}
        fresh = [a for a in alerts if a.key not in existing]
        if not fresh:
            return 0
        merged = self._read() + [a.to_json() for a in fresh]
        self._path.write_text(
            json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return len(fresh)

    def all(self) -> list[Alert]:
        return [Alert.from_json(d) for d in self._read()]

    def clear(self) -> None:
        if self._path.is_file():
            self._path.unlink()
