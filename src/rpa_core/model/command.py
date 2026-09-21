import hashlib
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .errors import ErrorCode

# 命令声明的「等待目标元素存在」预算参数名（单位：毫秒）。
# manifest 里声明了它，语义就是「等待目标元素出现/变为可操作的最长时间」——沿用影刀同名参数
# 与浏览器通道的既有实现（`browser.py` 的共享定位器把它当作等待选择器的超时）。
WAIT_BUDGET_INPUT = "timeoutMs"

# 等待预算之上留给「结果回传与收尾」的余量。
# 等待必须等得到，但引擎超时是兜底，不该反过来掐断用户显式给出的等待预算——
# 否则用户设 `timeoutMs=30s` 会在引擎默认的 15s 收到 TIMEOUT，而报错说的是「超时」、
# 不是「元素没出现」，等于同一个参数在两个层里打架（M30 要消灭的正是这种事）。
WAIT_BUDGET_SLACK_SECONDS = 1.0


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
    x_var_write: dict[str, Any] | None = Field(
        default=None,
        alias="x-var-write",
        description=(
            "变量写入声明：{\"field\": \"varName\"} 表示该命令的 varName 输入是"
            "目标变量名，运行值写入 scopes.variables（允许覆盖同名变量，即重赋值）"
        ),
    )
    x_palette_order: int | None = Field(
        default=None,
        alias="x-palette-order",
        description=(
            "命令在左侧命令面板同命名空间内的展示顺序权重"
            "（升序，缺省按命令 id 字母序兜底）"
        ),
    )
    x_runtime: dict[str, Any] | None = Field(
        default=None,
        alias="x-runtime",
        description=(
            "运行时注入声明：{\"inject\": [\"flowDir\"]} 表示执行前由 orchestrator"
            "把对应运行时量注入到 command_inputs（如 flowDir = 流程目录绝对路径）"
        ),
    )

    @model_validator(mode="after")
    def validate_retry_policy(self) -> "CommandManifest":
        if self.retryable and self.effect.replay == ReplayPolicy.UNSAFE:
            raise ValueError("unsafe replay commands cannot be retryable")
        return self

    def declares_wait_budget(self) -> bool:
        """是否声明了元素等待预算（`timeoutMs`）。

        声明即承诺：执行器必须真的拿这个值去等目标元素（M30 的口径——声明了不生效是缺陷）。
        """
        properties = (self.input_schema or {}).get("properties") or {}
        return WAIT_BUDGET_INPUT in properties

    def wait_budget_ms(self, inputs: dict[str, Any] | None) -> int:
        """节点给出的等待预算（毫秒）。未声明该参数、未给值、值非法或为负 → 0（= 不等待）。"""
        if not self.declares_wait_budget():
            return 0
        raw = (inputs or {}).get(WAIT_BUDGET_INPUT)
        if raw is None:
            return 0
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return 0
        return max(value, 0)


def resolve_node_timeout_seconds(
    manifest: CommandManifest,
    inputs: dict[str, Any] | None,
    declared_node_timeout: float | None,
) -> float:
    """节点实际可用的超时（秒）：节点显式值 > manifest 默认值，且不低于命令声明的元素等待预算。

    「不低于」这一条是刻意的：`timeoutMs` 是用户为一等公民操作显式给出的等待预算，
    而节点超时是防挂死的兜底。兜底反咬预算会产生误导性错误（见 `WAIT_BUDGET_SLACK_SECONDS`）。
    工作流级 deadline 仍由调用方取 min 兜住，不在这里放宽。
    """
    base = declared_node_timeout or manifest.default_timeout_seconds
    budget_seconds = manifest.wait_budget_ms(inputs) / 1000.0
    if budget_seconds <= 0:
        return base
    return max(base, budget_seconds + WAIT_BUDGET_SLACK_SECONDS)


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
