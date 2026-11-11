"""采集快照库：按 keyword 追加式存储每次采集结果，并提供「最新 / 历史 / 商品库」视图。

这是 P1 数据层的地基，不碰网络、不碰浏览器、不 import 运行时内部模块。
存储是纯 JSON，每个 keyword 一个文件（`data/snapshots/<keyword>.json`），文件内是
按时间升序的 run 列表。根目录可注入，所以单测用临时目录即可，不污染真实数据。

商品身份 = ``href``（小红书笔记/商品链接稳定）。同一 href 跨次采集的变动由 alerts 层比对。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.collector import step_output

_UNSAFE = re.compile(r"[^\w一-鿿-]+")


def _safe_name(keyword: str) -> str:
    """keyword 落到文件名：保留中文与字母数字，其余折叠成单个下划线。"""
    kept = _UNSAFE.sub("_", keyword).strip("_")
    return kept or "untitled"


@dataclass(frozen=True)
class Product:
    href: str
    title: str
    text: str

    @classmethod
    def from_raw(cls, raw: Any) -> Product:
        if not isinstance(raw, dict):
            raise ValueError(f"商品记录必须是对象，收到 {type(raw).__name__}")
        href = str(raw.get("href") or "").strip()
        if not href:
            raise ValueError("商品记录缺少稳定身份 href")
        title = str(raw.get("title") or "").strip()
        text = str(raw.get("text") or "").strip()
        return cls(href=href, title=title, text=text)

    def to_json(self) -> dict[str, str]:
        return {"href": self.href, "title": self.title, "text": self.text}


@dataclass(frozen=True)
class Snapshot:
    keyword: str
    run_id: str
    collected_at: str
    items: list[Product] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.items)

    def to_json(self) -> dict[str, Any]:
        return {
            "keyword": self.keyword,
            "run_id": self.run_id,
            "collected_at": self.collected_at,
            "items": [item.to_json() for item in self.items],
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Snapshot:
        if not isinstance(data, dict):
            raise ValueError("snapshot 必须是对象")
        items = [Product.from_raw(r) for r in (data.get("items") or [])]
        return cls(
            keyword=str(data.get("keyword", "")),
            run_id=str(data.get("run_id", "")),
            collected_at=str(data.get("collected_at", "")),
            items=items,
        )


class SnapshotStore:
    """按 keyword 分文件的快照库。``root`` 是 ``data/`` 目录，可注入临时目录测试。"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, keyword: str) -> Path:
        return self.root / f"{_safe_name(keyword)}.json"

    def _read_runs(self, keyword: str) -> list[dict[str, Any]]:
        path = self._path(keyword)
        if not path.is_file():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"快照文件损坏 {path}: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("runs"), list):
            raise ValueError(f"快照文件形状错误 {path}")
        return data["runs"]

    def _write_runs(self, keyword: str, runs: list[dict[str, Any]]) -> None:
        path = self._path(keyword)
        path.write_text(
            json.dumps({"keyword": keyword, "runs": runs}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def record(
        self,
        keyword: str,
        run_id: str,
        items: list[Any],
        collected_at: str | None = None,
    ) -> Snapshot:
        """追加一次采集；``items`` 是原始 dict 列表（来自 executeScript 返回值）。"""
        if not keyword or not keyword.strip():
            raise ValueError("keyword 不能为空")
        if not run_id:
            raise ValueError("run_id 不能为空")
        products = [Product.from_raw(r) for r in items]
        stamp = collected_at or datetime.now(UTC).isoformat(timespec="seconds")
        snap = Snapshot(keyword=keyword, run_id=run_id, collected_at=stamp, items=products)
        runs = self._read_runs(keyword)
        runs.append(snap.to_json())
        self._write_runs(keyword, runs)
        return snap

    def latest(self, keyword: str) -> Snapshot | None:
        runs = self._read_runs(keyword)
        return Snapshot.from_json(runs[-1]) if runs else None

    def history(self, keyword: str) -> list[Snapshot]:
        return [Snapshot.from_json(r) for r in self._read_runs(keyword)]

    def all_keywords(self) -> list[str]:
        seen: list[str] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            kw = data.get("keyword") if isinstance(data, dict) else None
            if isinstance(kw, str) and kw:
                seen.append(kw)
        return seen

    def all_latest(self) -> dict[str, Snapshot]:
        return {kw: snap for kw in self.all_keywords() if (snap := self.latest(kw)) is not None}

    def product_index(self) -> dict[str, Product]:
        """商品库视图：跨所有 keyword 的最新快照，按 href 去重。

        同一 href 可能出现在多个 keyword 的快照里，取 ``collected_at`` 最新的一次
        （ISO-8601 字符串可直接按字典序比较 chronology），避免被迭代顺序（keyword 名）
        决定胜负。
        """
        index: dict[str, Product] = {}
        pairs = [
            (snap.collected_at, item)
            for snap in self.all_latest().values()
            for item in snap.items
        ]
        # 同秒采集的两个商品 collected_at 相等，必须再按 href 排序；
        # 直接 sorted(pairs) 会在首键相等时比较 Product（不可排序）而崩。
        for _, item in sorted(pairs, key=lambda p: (p[0], p[1].href)):
            index[item.href] = item
        return index


def snapshot_from_outcome(store: SnapshotStore, keyword: str, outcome: Any) -> Snapshot:
    """从 collector.RunOutcome 直接落库：取 grabItems 的 JSON 字符串结果并解析。"""
    raw = step_output(outcome, "grabItems", "result")
    if isinstance(raw, str):
        try:
            items = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"grabItems 返回值不是 JSON：{exc}") from exc
    elif isinstance(raw, list):
        items = raw
    else:
        raise ValueError(f"grabItems 返回值形状未知：{type(raw).__name__}")
    return store.record(keyword, outcome.run_id, items, collected_at=None)
