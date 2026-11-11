"""监控池状态机：被跟踪搜索/商品的状态与全暂停/全恢复。

状态：``pending``（已登记未排程）/ ``scheduled``（已排程待跑）/ ``running``（采集中）/
``ok``（上次成功）/ ``failed``（上次失败）/ ``paused``（被暂停）。

设计取向（与仓内其它状态机一致）：**非法迁移直接红**，不静默吞掉。
``paused`` 通过记住暂停前的状态实现恢复，所以全暂停/全恢复不会丢失各自原来的状态。
不 import 运行时内部模块。状态持久化到 ``data/monitor.json``。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

STATES = frozenset({"pending", "scheduled", "running", "ok", "failed", "paused"})
_RUNNABLE_FROM = {"pending", "scheduled", "ok", "failed"}  # 允许进入 running 的前态


@dataclass
class MonitorEntry:
    keyword: str
    state: str = "pending"
    last_run_id: str | None = None
    last_error: str | None = None
    _prev: str | None = None  # 暂停前的状态，用于 resume

    def to_json(self) -> dict[str, Any]:
        return {
            "keyword": self.keyword,
            "state": self.state,
            "last_run_id": self.last_run_id,
            "last_error": self.last_error,
            "_prev": self._prev,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> MonitorEntry:
        return cls(
            keyword=str(data["keyword"]),
            state=str(data.get("state", "pending")),
            last_run_id=data.get("last_run_id"),
            last_error=data.get("last_error"),
            _prev=data.get("_prev"),
        )


class MonitorPool:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._path = self.root / "monitor.json"
        self._entries: dict[str, MonitorEntry] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.is_file():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(data, dict):
            for raw in data.values():
                if isinstance(raw, dict) and "keyword" in raw:
                    self._entries[raw["keyword"]] = MonitorEntry.from_json(raw)

    def _save(self) -> None:
        payload = {e.keyword: e.to_json() for e in self._entries.values()}
        self._path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def register(self, keyword: str) -> MonitorEntry:
        if not keyword or not keyword.strip():
            raise ValueError("keyword 不能为空")
        if keyword in self._entries:
            return self._entries[keyword]
        entry = MonitorEntry(keyword=keyword, state="pending")
        self._entries[keyword] = entry
        self._save()
        return entry

    def get(self, keyword: str) -> MonitorEntry | None:
        return self._entries.get(keyword)

    def all(self) -> list[MonitorEntry]:
        return list(self._entries.values())

    def counts_by_state(self) -> dict[str, int]:
        counts: dict[str, int] = {s: 0 for s in STATES}
        for entry in self._entries.values():
            counts[entry.state] = counts.get(entry.state, 0) + 1
        return counts

    def _require(self, keyword: str) -> MonitorEntry:
        entry = self._entries.get(keyword)
        if entry is None:
            raise KeyError(f"未跟踪的 keyword：{keyword}")
        return entry

    def set_scheduled(self, keyword: str) -> None:
        entry = self._require(keyword)
        if entry.state == "paused":
            raise ValueError(f"{keyword} 已暂停，先 resume 再排程")
        entry.state = "scheduled"
        self._save()

    def set_running(self, keyword: str, run_id: str) -> None:
        entry = self._require(keyword)
        if entry.state not in _RUNNABLE_FROM:
            raise ValueError(f"{keyword} 当前状态 {entry.state} 不能进入 running")
        entry.state = "running"
        entry.last_run_id = run_id
        entry.last_error = None
        self._save()

    def set_ok(self, keyword: str, run_id: str) -> None:
        entry = self._require(keyword)
        if entry.state != "running":
            raise ValueError(f"{keyword} 当前状态 {entry.state}，只有 running 能转 ok")
        entry.state = "ok"
        entry.last_run_id = run_id
        entry.last_error = None
        self._save()

    def set_failed(self, keyword: str, error: str) -> None:
        entry = self._require(keyword)
        if entry.state != "running":
            raise ValueError(f"{keyword} 当前状态 {entry.state}，只有 running 能转 failed")
        entry.state = "failed"
        entry.last_error = str(error)
        self._save()

    def run_paused(self, keyword: str) -> None:
        """运行被控制通道暂停（run 进程在节点边界收口成 paused 并退出）。

        与 ``pause()`` 不同：这是**运行层事实**而不是用户意图，所以记 ``last_error``
        说明「可在编辑器里继续或停止」，``_prev`` 记 running 供 resume 还原。
        """
        entry = self._require(keyword)
        if entry.state != "running":
            raise ValueError(f"{keyword} 当前状态 {entry.state}，只有 running 能转 run_paused")
        entry._prev = "running"
        entry.state = "paused"
        entry.last_error = "本次采集已暂停（可在编辑器里继续或停止）"
        self._save()

    def pause(self, keyword: str) -> None:
        entry = self._require(keyword)
        if entry.state == "paused":
            raise ValueError(f"{keyword} 已经处于 paused")
        entry._prev = entry.state
        entry.state = "paused"
        self._save()

    def resume(self, keyword: str) -> None:
        entry = self._require(keyword)
        if entry.state != "paused":
            raise ValueError(f"{keyword} 当前状态 {entry.state}，不是 paused，无法 resume")
        entry.state = entry._prev or "pending"
        entry._prev = None
        self._save()

    def pause_all(self) -> int:
        n = 0
        for entry in self._entries.values():
            if entry.state != "paused":
                entry._prev = entry.state
                entry.state = "paused"
                n += 1
        if n:
            self._save()
        return n

    def resume_all(self) -> int:
        n = 0
        for entry in self._entries.values():
            if entry.state == "paused":
                entry.state = entry._prev or "pending"
                entry._prev = None
                n += 1
        if n:
            self._save()
        return n
