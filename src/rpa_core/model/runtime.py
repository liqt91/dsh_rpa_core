from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class RunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ABANDONED = "abandoned"


class EventRecord(BaseModel):
    seq: int = Field(ge=1)
    run_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    type: str
    node_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class RunResult(BaseModel):
    run_id: str
    workflow_id: str
    status: RunStatus
    started_at: datetime
    ended_at: datetime
    outputs: dict[str, Any] = Field(default_factory=dict)
    return_value: Any = None
    error: dict[str, Any] | None = None
    catalog_digest: str
