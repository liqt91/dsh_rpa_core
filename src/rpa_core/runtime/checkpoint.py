from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CHECKPOINT_VERSION = 1
_REQUIRED_SCOPES = ("inputs", "steps", "loop")

# 暂停原因（M24 断点/单步）：user=用户暂停，breakpoint=命中断点，step=单步停下
_PAUSE_REASONS = ("user", "breakpoint", "step")


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
    # M24 调试字段（可选，旧检查点缺省为空）：断点集合、已消费断点、暂停原因与停点。
    for field in ("breakpoints", "consumedBreakpoints"):
        value = data.get(field, [])
        if (
            not isinstance(value, list)
            or any(not isinstance(item, str) or not item for item in value)
            or len(set(value)) != len(value)
        ):
            raise CheckpointError(f"Checkpoint field {field!r} must be unique string keys")
    reason = data.get("pauseReason")
    if reason is not None and reason not in _PAUSE_REASONS:
        raise CheckpointError(f"Checkpoint field 'pauseReason' is invalid: {reason!r}")
    paused_at = data.get("pausedAtNode")
    if paused_at is not None and (not isinstance(paused_at, str) or not paused_at):
        raise CheckpointError("Checkpoint field 'pausedAtNode' must be a non-empty string")
    return data


def checkpoint_payload(
    *,
    workflow_id: str,
    catalog_digest: str,
    completed_steps: list[str],
    scopes: dict[str, Any],
    return_value: Any,
    breakpoints: list[str] | None = None,
    consumed_breakpoints: list[str] | None = None,
    pause_reason: str | None = None,
    paused_at_node: str | None = None,
) -> dict[str, Any]:
    payload = {
        "version": CHECKPOINT_VERSION,
        "workflowId": workflow_id,
        "catalogDigest": catalog_digest,
        "completedSteps": completed_steps,
        "scopes": scopes,
        "returnValue": return_value,
        # 断点集合必须随检查点持久化：resume 起的是新进程，控制文件里的请求
        # 已被 reset（见 control_channel 模块文档第 3 条），断点不能依赖它。
        "breakpoints": sorted(breakpoints or []),
        "consumedBreakpoints": sorted(consumed_breakpoints or []),
    }
    if pause_reason is not None:
        payload["pauseReason"] = pause_reason
    if paused_at_node is not None:
        payload["pausedAtNode"] = paused_at_node
    return payload
