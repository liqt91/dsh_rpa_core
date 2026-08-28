import asyncio
from pathlib import Path

from rpa_core.model.runtime import EventRecord, RunResult


class RunPersistenceError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        result: RunResult | None = None,
        cause: OSError | None = None,
    ):
        super().__init__(message)
        self.result = result
        self.cause = cause


class EventWriter:
    def __init__(self, run_dir: Path, run_id: str):
        self.run_dir = run_dir
        self.run_id = run_id
        self.path = run_dir / "events.jsonl"
        self._seq = 0
        self._write_lock = asyncio.Lock()

    async def initialize(self) -> None:
        await asyncio.to_thread(self.run_dir.mkdir, parents=True, exist_ok=True)

    async def append(
        self, event_type: str, *, node_id: str | None = None, payload: dict | None = None
    ) -> EventRecord:
        async with self._write_lock:
            self._seq += 1
            event = EventRecord(
                seq=self._seq,
                run_id=self.run_id,
                type=event_type,
                node_id=node_id,
                payload=payload or {},
            )
            line = event.model_dump_json() + "\n"
            try:
                await asyncio.to_thread(self._append_line, line)
            except OSError as exc:
                raise RunPersistenceError(
                    f"Failed to append run event {event_type!r}", cause=exc
                ) from exc
            return event

    def _append_line(self, line: str) -> None:
        with self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(line)
