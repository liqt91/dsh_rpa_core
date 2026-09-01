from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CHECKPOINT_VERSION = 1
_REQUIRED_SCOPES = ("inputs", "steps", "loop")


class CheckpointError(RuntimeError):
    pass


class RecoveryRequiredError(CheckpointError):
    pass


@dataclass(frozen=True)
class CheckpointStore:
    path: Path

    def read(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CheckpointError(f"Failed to read checkpoint: {self.path}") from exc
        return validate_checkpoint(data)

    def write(self, data: dict[str, Any]) -> None:
        validate_checkpoint(data)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=True, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as exc:
            raise CheckpointError(f"Failed to write checkpoint: {self.path}") from exc


def validate_checkpoint(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise CheckpointError("Checkpoint must be a JSON object")
    if data.get("version") != CHECKPOINT_VERSION:
        raise CheckpointError(f"Unsupported checkpoint version: {data.get('version')!r}")
    for field in ("workflowId", "catalogDigest"):
        if not isinstance(data.get(field), str) or not data[field]:
            raise CheckpointError(f"Checkpoint field {field!r} must be a non-empty string")
    completed = data.get("completedSteps")
    if (
        not isinstance(completed, list)
        or any(not isinstance(key, str) or not key for key in completed)
        or len(set(completed)) != len(completed)
    ):
        raise CheckpointError("Checkpoint field 'completedSteps' must be unique string keys")
    scopes = data.get("scopes")
    if not isinstance(scopes, dict) or any(
        not isinstance(scopes.get(name), dict) for name in _REQUIRED_SCOPES
    ):
        raise CheckpointError(
            "Checkpoint field 'scopes' must include object fields "
            f"{', '.join(_REQUIRED_SCOPES)}"
        )
    if scopes.get("workflowId") != data["workflowId"]:
        raise CheckpointError("Checkpoint scopes do not match the recorded workflowId")
    if scopes.get("catalogDigest") != data["catalogDigest"]:
        raise CheckpointError("Checkpoint scopes do not match the recorded catalogDigest")
    if "returnValue" not in data:
        raise CheckpointError("Checkpoint field 'returnValue' is missing")
    return data


def checkpoint_payload(
    *,
    workflow_id: str,
    catalog_digest: str,
    completed_steps: list[str],
    scopes: dict[str, Any],
    return_value: Any,
) -> dict[str, Any]:
    return {
        "version": CHECKPOINT_VERSION,
        "workflowId": workflow_id,
        "catalogDigest": catalog_digest,
        "completedSteps": completed_steps,
        "scopes": scopes,
        "returnValue": return_value,
    }
