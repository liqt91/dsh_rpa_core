import hashlib
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .errors import ErrorCode


class CommandKind(StrEnum):
    ACTION = "action"
    QUERY = "query"
    TRANSFORM = "transform"
    LIFECYCLE = "lifecycle"


class CommandRisk(StrEnum):
    READ = "read"
    WORKFLOW_EDIT = "workflow-edit"
    BROWSER_CONTROL = "browser-control"
    DESKTOP_CONTROL = "desktop-control"
    PROCESS_START = "process-start"
    DEVELOPER = "developer"


class Stability(StrEnum):
    STABLE = "stable"
    EXPERIMENTAL = "experimental"
    DEVELOPER = "developer"
    DEPRECATED = "deprecated"


class EffectKind(StrEnum):
    PURE = "pure"
    READ = "read"
    IDEMPOTENT_WRITE = "idempotent-write"
    UNSAFE_WRITE = "unsafe-write"
    SESSION = "session"


class ReplayPolicy(StrEnum):
    SAFE = "safe"
    IDEMPOTENT = "idempotent"
    UNSAFE = "unsafe"


class IdempotencyPolicy(StrEnum):
    NONE = "none"
    DERIVED = "derived"
    REQUIRED = "required"


class EffectStatus(StrEnum):
    PREPARED = "prepared"
    COMMITTED = "committed"
    FAILED = "failed"
    UNKNOWN = "unknown"


class EffectPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: EffectKind
    replay: ReplayPolicy
    idempotency: IdempotencyPolicy = IdempotencyPolicy.NONE

    @model_validator(mode="after")
    def validate_policy(self) -> "EffectPolicy":
        if self.replay == ReplayPolicy.IDEMPOTENT and self.idempotency == IdempotencyPolicy.NONE:
            raise ValueError("idempotent replay requires an idempotency policy")
        if self.kind in {EffectKind.UNSAFE_WRITE, EffectKind.SESSION}:
            if self.replay != ReplayPolicy.UNSAFE:
                raise ValueError(f"{self.kind} effects must use unsafe replay")
        if self.kind in {EffectKind.PURE, EffectKind.READ}:
            if self.replay != ReplayPolicy.SAFE:
                raise ValueError(f"{self.kind} effects must use safe replay")
        if self.kind == EffectKind.IDEMPOTENT_WRITE:
            if self.replay != ReplayPolicy.IDEMPOTENT:
                raise ValueError("idempotent-write effects must use idempotent replay")
        return self


class Implementation(BaseModel):
    handler: str = Field(pattern=r"^[a-zA-Z_][\w.]*:[a-zA-Z_]\w*$")


class CommandManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-zA-Z0-9]*(?:\.[a-z][a-zA-Z0-9]*)+$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    executor: str
    kind: CommandKind
    risk: CommandRisk
    capabilities: list[str] = Field(default_factory=list)
    resources: list[str] = Field(default_factory=list)
    stability: Stability
    effect: EffectPolicy
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    errors: list[ErrorCode]
    implementation: Implementation
    default_timeout_seconds: float = Field(default=30.0, gt=0, le=3600)
    retryable: bool = False
    x_outputs: dict[str, dict[str, Any]] | None = Field(
        default=None,
        alias="x-outputs",
        description="输出字段元数据，用于 UI 显示别名输入框",
    )
    x_fx: list[str] | None = Field(default=None, alias="x-fx")
    x_python: list[str] | None = Field(default=None, alias="x-python")
    x_depends: dict[str, dict[str, Any]] | None = Field(default=None, alias="x-depends")

    @model_validator(mode="after")
    def validate_retry_policy(self) -> "CommandManifest":
        if self.retryable and self.effect.replay == ReplayPolicy.UNSAFE:
            raise ValueError("unsafe replay commands cannot be retryable")
        return self


class CommandError(BaseModel):
    code: ErrorCode
    message: str
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class EffectRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    effect_id: str = Field(alias="effectId", min_length=16)
    kind: EffectKind
    status: EffectStatus
    resource: str = Field(min_length=1)
    idempotency_key: str | None = Field(default=None, alias="idempotencyKey")
    details: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def committed(
        cls,
        invocation: "CommandInvocation",
        *,
        kind: EffectKind,
        resource: str,
        index: int = 0,
        idempotency_key: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> "EffectRecord":
        identity = (
            f"{invocation.run_id}:{invocation.step_id}:{invocation.attempt}:"
            f"{index}:{kind}:{resource}"
        )
        effect_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return cls(
            effectId=effect_id,
            kind=kind,
            status=EffectStatus.COMMITTED,
            resource=resource,
            idempotencyKey=idempotency_key,
            details=details or {},
        )


class CommandResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(pattern=r"^(success|error|cancelled|timed_out)$")
    value: Any = None
    outputs: dict[str, Any] = Field(default_factory=dict)
    effects: list[EffectRecord] = Field(default_factory=list)
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    error: CommandError | None = None

    @classmethod
    def success(
        cls,
        *,
        value: Any = None,
        outputs: dict[str, Any] | None = None,
        effects: list[EffectRecord] | None = None,
        diagnostics: dict[str, Any] | None = None,
    ) -> "CommandResult":
        return cls(
            status="success",
            value=value,
            outputs=outputs or {},
            effects=effects or [],
            diagnostics=diagnostics or {},
        )

    @classmethod
    def failure(
        cls,
        code: ErrorCode,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
        effects: list[EffectRecord] | None = None,
    ) -> "CommandResult":
        return cls(
            status="error",
            effects=effects or [],
            error=CommandError(
                code=code,
                message=message,
                retryable=retryable,
                details=details or {},
            ),
        )


class CommandInvocation(BaseModel):
    command_id: str
    command_version: str
    run_id: str
    step_id: str
    attempt: int = Field(default=1, ge=1)
    inputs: dict[str, Any]
    deadline_monotonic: float | None = None
