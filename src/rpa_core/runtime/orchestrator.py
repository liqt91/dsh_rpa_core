from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jsonschema import Draft202012Validator

from rpa_core.catalog import CommandCatalog
from rpa_core.compiler import ExecutionPlan

# ExecutorRegistry 仅作为类型标注引用（由 __future__.annotations 延迟求值），
# 实际运行时由 cli.py 在 run() 内部导入后传入。避免 import orchestrator →
# import executors → pywinauto 提前初始化 COM，阻断 GUI 路径的 OLE 拖放。
if TYPE_CHECKING:
    from rpa_core.executors import ExecutorRegistry
from rpa_core.model.command import (
    CommandInvocation,
    CommandResult,
    EffectKind,
    EffectStatus,
    ReplayPolicy,
)
from rpa_core.model.errors import ErrorCode, RpaError
from rpa_core.model.runtime import RunResult, RunStatus
from rpa_core.model.workflow import (
    ActionNode,
    ForEachNode,
    IfNode,
    ReturnNode,
    SequenceNode,
    TryNode,
    WorkflowNode,
)

from .checkpoint import (
    CheckpointError,
    CheckpointStore,
    RecoveryRequiredError,
    checkpoint_payload,
)
from .events import EventWriter, RunPersistenceError
from .resolver import ReferenceError as ResolverReferenceError
from .resolver import evaluate, resolve, resolve_with_modes

# python 模式表达式求值在 worker 子进程执行，给一个独立于命令超时的上限
PYTHON_EVAL_TIMEOUT_SECONDS = 30.0


class WorkflowReturn(Exception):
    def __init__(self, value: Any):
        self.value = value


class PauseSignal(Exception):
    def __init__(self, node_id: str, reason: str = "user"):
        super().__init__(f"Pause requested before node {node_id}")
        self.node_id = node_id
        self.reason = reason


class RunControl:
    """单次 run 的「停」控制面（M24）：暂停开关 + 断点集合 + 单步预算。

    为什么把它作为一个对象往下传（而不是继续只传 `asyncio.Event`）：断点命中、
    单步、用户暂停三者共用同一个判定点（节点边界），且都需要记录「为什么停」；
    打包成一个对象后，递归执行链的参数个数不变（只是类型从 Event 换成 RunControl）。

    三个字段的语义：
    - `breakpoints`：本次 run 的断点节点 id（随检查点持久化——resume 是新进程，
      控制文件会被 reset，断点不能只放在控制通道里）；
    - `consumed`：**已命中过**的断点（防「resume 后同一断点反复命中」死循环）；
    - `step_pending`：单步预算——放行一个节点后立即请求暂停。
    """

    def __init__(self, breakpoints: Iterable[str] = (), consumed: Iterable[str] = ()):
        self.pause = asyncio.Event()
        self.breakpoints: set[str] = {str(item) for item in breakpoints if item}
        self.consumed: set[str] = {str(item) for item in consumed if item}
        self.step_pending = False
        self.step_after = False
        self.pause_reason: str | None = None

    def stop_reason(self, node_id: str) -> str | None:
        """节点**执行前**判定是否要停；返回暂停原因，None 表示继续执行。

        判定顺序（与 ADR 0005「取消 > 暂停 > 超时」的优先级一致，取消在外层处理）：
        1. 已有暂停请求 → 按请求原因（缺省 user）；
        2. 单步预算 → 放行本节点，并登记「执行完这个节点后停」；
        3. 单步后停（`step_after`）→ 返回 step（**不设 pause 事件**，避免被同一节点
           内的重试边界检查提前拦下）；
        4. 命中断点（且未消费过）→ 记为已消费并停。
        """
        if self.pause.is_set():
            return self.pause_reason or "user"
        if self.step_pending:
            self.step_pending = False
            self.step_after = True
            return None
        if self.step_after:
            self.step_after = False
            self.pause_reason = "step"
            return "step"
        if node_id in self.breakpoints and node_id not in self.consumed:
            self.consumed.add(node_id)
            self.pause_reason = "breakpoint"
            return "breakpoint"
        return None


class IndeterminateOutcome(RpaError):
    pass


class RunHandle:
    def __init__(
        self,
        run_id: str,
        task: asyncio.Task[RunResult],
        cancellation: asyncio.Event,
        control: RunControl,
    ):
        self.run_id = run_id
        self.task = task
        self._cancellation = cancellation
        self._control = control

    def cancel(self) -> None:
        self._cancellation.set()

    def pause(self) -> None:
        self._control.pause_reason = self._control.pause_reason or "user"
        self._control.pause.set()

    def resume(self) -> None:
        """撤销尚未生效的暂停请求（ADR 0005「请求不排队、可被覆盖」的另一半）。

        生效点之前撤销才会被看见：run 一旦在节点边界抛出 `PauseSignal` 就进入
        收口流程，此后再撤销不会把 run 拉回来——那时「继续」是 `resume`（新进程，
        从检查点起），不是这个开关。取消请求不受影响（取消优先级更高，且独立）。
        """
        self._control.pause.clear()
        self._control.pause_reason = None

    def request_step(self) -> None:
        """请求单步（M24）：执行一个节点后在下个边界停下。

        只对**仍在运行**的 run 有意义（与 `resume` 同理）；已收口的 run 要单步，
        走 `Orchestrator.resume(..., step=True)`（新进程，预算随检查点带入）。
        """
        self._control.pause.clear()
        self._control.pause_reason = None
        self._control.step_pending = True

    @property
    def breakpoints(self) -> frozenset[str]:
        """本次 run 的断点节点 id（含 resume 从检查点恢复的）。"""
        return frozenset(self._control.breakpoints)

    @property
    def pause_reason(self) -> str | None:
        """最近一次「停」的原因：user / breakpoint / step（未停过为 None）。"""
        return self._control.pause_reason

    async def cancel_and_wait(self) -> RunResult:
        self.cancel()
        return await self.wait()

    async def pause_and_wait(self) -> RunResult:
        self.pause()
        return await self.wait()

    async def wait(self) -> RunResult:
        return await self.task


def _node_path_key(path: list[str]) -> str:
    return "/".join(path)


class Orchestrator:
    def __init__(
        self,
        catalog: CommandCatalog,
        executors: ExecutorRegistry,
        artifacts_root: Path,
        flow_dir: Path | None = None,
    ):
        self.catalog = catalog
        self.executors = executors
        self.artifacts_root = artifacts_root
        # 流程目录（workflow.json 所在目录）：供 data.table.* 等命令按 manifest
        # x-runtime.inject 声明注入到 command_inputs
        self._flow_dir = flow_dir

    def resume(
        self,
        plan: ExecutionPlan,
        run_id: str,
        *,
        allow_indeterminate: bool = False,
        step: bool = False,
    ) -> RunHandle:
        """从检查点起新进程续跑；`step=True` 时只前进一个节点（M24 单步）。

        断点与「已消费断点」都从检查点恢复——控制文件在 resume 前被 reset，
        断点不能依赖控制通道（见 `RunControl` 文档）。
        """
        checkpoint = self._load_resume_checkpoint(plan, run_id, allow_indeterminate)
        cancellation = asyncio.Event()
        control = RunControl(
            checkpoint.get("breakpoints") or [],
            checkpoint.get("consumedBreakpoints") or [],
        )
        if step:
            control.step_pending = True
        task = asyncio.create_task(
            self._resume(run_id, plan, checkpoint, cancellation, control)
        )
        return RunHandle(run_id, task, cancellation, control)

    def start(
        self,
        plan: ExecutionPlan,
        inputs: dict[str, Any] | None = None,
        *,
        breakpoints: Iterable[str] = (),
    ) -> RunHandle:
        run_id = str(uuid.uuid4())
        cancellation = asyncio.Event()
        control = RunControl(breakpoints)
        task = asyncio.create_task(
            self._run(run_id, plan, inputs or {}, cancellation, control)
        )
        return RunHandle(run_id, task, cancellation, control)

    async def run(self, plan: ExecutionPlan, inputs: dict[str, Any] | None = None) -> RunResult:
        return await self.start(plan, inputs).wait()

    def _load_resume_checkpoint(
        self, plan: ExecutionPlan, run_id: str, allow_indeterminate: bool
    ) -> dict[str, Any]:
        run_dir = self.artifacts_root / run_id
        checkpoint = CheckpointStore(run_dir / "checkpoint.json").read()
        if checkpoint is None:
            raise CheckpointError(f"Missing checkpoint: {run_dir / 'checkpoint.json'}")
        if checkpoint["workflowId"] != plan.workflow.id:
            raise CheckpointError("Workflow mismatch during resume")
        if checkpoint["catalogDigest"] != plan.catalog_digest:
            raise CheckpointError("Catalog digest mismatch during resume")
        self._require_manual_recovery(run_dir, allow_indeterminate)
        return checkpoint

    @staticmethod
    def _require_manual_recovery(run_dir: Path, allow_indeterminate: bool) -> None:
        result_path = run_dir / "result.json"
        if not result_path.exists():
            return
        try:
            previous = RunResult.model_validate_json(result_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if previous.status == RunStatus.INDETERMINATE and not allow_indeterminate:
            raise RecoveryRequiredError(
                f"Run {previous.run_id} ended indeterminate: an external effect outcome is "
                "unknown. Manual recovery is required; pass allow_indeterminate=True to "
                "confirm and continue."
            )

    async def _resume(
        self,
        run_id: str,
        plan: ExecutionPlan,
        checkpoint: dict[str, Any],
        cancellation: asyncio.Event,
        control: RunControl,
    ) -> RunResult:
        events = EventWriter(self.artifacts_root / run_id, run_id)
        started = datetime.now(UTC)
        scopes = checkpoint["scopes"]
        completed = set(checkpoint["completedSteps"])
        try:
            await events.initialize()
            await events.append(
                "runResumed",
                payload={
                    "workflowId": plan.workflow.id,
                    "catalogDigest": plan.catalog_digest,
                    "completedSteps": sorted(completed),
                },
            )
        except OSError as exc:
            raise RunPersistenceError("Failed to initialize run evidence", cause=exc) from exc
        # 跨进程资源续接（M21）：让执行器从快照重建「进程内句柄 → 外部资源」的绑定。
        # 扩展通道的浏览器会话（用户浏览器里的标签页）不随 run 进程退出而消失，
        # 靠这里接回来，否则暂停后「继续」会因会话失效而失败。
        self.executors.restore_from_scopes(scopes)
        return await self._run_to_terminal(
            run_id,
            plan,
            scopes,
            completed,
            events,
            cancellation,
            control,
            started,
            checkpoint["returnValue"],
        )

    async def _run(
        self,
        run_id: str,
        plan: ExecutionPlan,
        inputs: dict[str, Any],
        cancellation: asyncio.Event,
        control: RunControl,
    ) -> RunResult:
        run_dir = self.artifacts_root / run_id
        events = EventWriter(run_dir, run_id)
        started = datetime.now(UTC)
        scopes: dict[str, Any] = {
            "inputs": {**plan.workflow.inputs, **inputs},
            "steps": {},
            "loop": {},
            "workflowId": plan.workflow.id,
            "catalogDigest": plan.catalog_digest,
        }
        try:
            await events.initialize()
            await events.append(
                "runStarted",
                payload={
                    "workflowId": plan.workflow.id,
                    "catalogDigest": plan.catalog_digest,
                },
            )
        except OSError as exc:
            raise RunPersistenceError("Failed to initialize run evidence", cause=exc) from exc
        return await self._run_to_terminal(
            run_id, plan, scopes, set(), events, cancellation, control, started, None
        )

    async def _run_to_terminal(
        self,
        run_id: str,
        plan: ExecutionPlan,
        scopes: dict[str, Any],
        completed: set[str],
        events: EventWriter,
        cancellation: asyncio.Event,
        control: RunControl,
        started: datetime,
        return_value: Any,
    ) -> RunResult:
        run_dir = events.run_dir
        workflow_deadline = time.monotonic() + plan.workflow.timeout_seconds
        status = RunStatus.SUCCEEDED
        error: dict[str, Any] | None = None
        paused_at: str | None = None
        try:
            execution = self._execute_node(
                plan.workflow.root,
                [plan.workflow.root.id],
                run_id,
                scopes,
                events,
                cancellation,
                control,
                workflow_deadline,
                completed,
            )
            async with asyncio.timeout_at(workflow_deadline):
                await execution
        except WorkflowReturn as returned:
            return_value = returned.value
        except RunPersistenceError:
            cancellation.set()
            raise
        except PauseSignal as exc:
            status = RunStatus.PAUSED
            paused_at = exc.node_id
            await events.append(
                "pauseRequested",
                payload={"nodeId": exc.node_id, "reason": exc.reason},
            )
            await events.append(
                "runPaused",
                payload={
                    "nodeId": exc.node_id,
                    "reason": exc.reason,
                    "completedSteps": sorted(completed),
                },
            )
        except IndeterminateOutcome as exc:
            cancellation.set()
            status = RunStatus.INDETERMINATE
            error = self._error_payload(
                exc.code, exc.message, retryable=exc.retryable, details=exc.details
            )
            await events.append("runIndeterminate", payload=error)
        except CheckpointError as exc:
            cancellation.set()
            status = RunStatus.RECOVERY_REQUIRED
            error = self._error_payload(
                ErrorCode.PERSISTENCE_FAILED,
                "Failed to persist checkpoint at node boundary",
                details={"cause": str(exc)},
            )
            await events.append("runRecoveryRequired", payload=error)
        except TimeoutError:
            cancellation.set()
            status = RunStatus.FAILED
            error = self._error_payload(ErrorCode.TIMEOUT, "Workflow timed out")
            await events.append("runTimedOut", payload=error)
        except asyncio.CancelledError:
            cancellation.set()
            status = RunStatus.CANCELLED
            error = self._error_payload(ErrorCode.CANCELLED, "Run was cancelled")
        except RpaError as exc:
            status = RunStatus.CANCELLED if exc.code == ErrorCode.CANCELLED else RunStatus.FAILED
            error = self._error_payload(
                exc.code,
                exc.message,
                retryable=exc.retryable,
                details=exc.details,
            )
        except Exception as exc:
            status = RunStatus.FAILED
            error = self._error_payload(ErrorCode.INTERNAL_ERROR, str(exc))

        result = RunResult(
            run_id=run_id,
            workflow_id=plan.workflow.id,
            status=status,
            started_at=started,
            ended_at=datetime.now(UTC),
            outputs=scopes["steps"],
            return_value=return_value,
            error=error,
            catalog_digest=plan.catalog_digest,
        )
        try:
            await events.append("runFinished", payload={"status": status, "error": error})
            await self._write_result(run_dir, result)
        except (OSError, RunPersistenceError) as exc:
            cause = exc.cause if isinstance(exc, RunPersistenceError) else exc
            failed = result.model_copy(
                update={
                    "status": RunStatus.FAILED,
                    "ended_at": datetime.now(UTC),
                    "error": self._error_payload(
                        ErrorCode.PERSISTENCE_FAILED,
                        "Failed to persist terminal run evidence",
                        details={"cause": str(cause or exc)},
                    ),
                }
            )
            raise RunPersistenceError(
                "Failed to persist terminal run evidence", result=failed, cause=cause
            ) from exc
        await self._write_final_checkpoint(
            run_dir, plan, scopes, completed, return_value, control, paused_at
        )
        return result

    async def _write_final_checkpoint(
        self,
        run_dir: Path,
        plan: ExecutionPlan,
        scopes: dict[str, Any],
        completed: set[str],
        return_value: Any,
        control: RunControl,
        paused_at: str | None = None,
    ) -> None:
        try:
            await asyncio.to_thread(
                CheckpointStore(run_dir / "checkpoint.json").write,
                checkpoint_payload(
                    workflow_id=plan.workflow.id,
                    catalog_digest=plan.catalog_digest,
                    completed_steps=sorted(completed),
                    scopes=scopes,
                    return_value=return_value,
                    # 断点与「已消费断点」随检查点落盘：resume 是新进程，靠它恢复
                    breakpoints=sorted(control.breakpoints),
                    consumed_breakpoints=sorted(control.consumed),
                    pause_reason=control.pause_reason if paused_at else None,
                    paused_at_node=paused_at,
                ),
            )
        except CheckpointError:
            return

    async def _write_result(self, run_dir: Path, result: RunResult) -> None:
        result_json = result.model_dump_json(indent=2)
        await asyncio.to_thread(
            (run_dir / "result.json").write_text,
            result_json,
            encoding="utf-8",
        )

    @staticmethod
    def _error_payload(
        code: ErrorCode,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "code": code,
            "message": message,
            "retryable": retryable,
            "details": details or {},
        }

    async def _execute_node(
        self,
        node: WorkflowNode,
        path: list[str],
        run_id: str,
        scopes: dict[str, Any],
        events: EventWriter,
        cancellation: asyncio.Event,
        control: RunControl,
        workflow_deadline: float,
        completed: set[str],
    ) -> None:
        if cancellation.is_set():
            raise RpaError(ErrorCode.CANCELLED, "Run was cancelled")
        if _node_path_key(path) in completed:
            return
        if isinstance(node, SequenceNode):
            for child in node.children:
                await self._execute_node(
                    child,
                    [*path, child.id],
                    run_id,
                    scopes,
                    events,
                    cancellation,
                    control,
                    workflow_deadline,
                    completed,
                )
            return
        if isinstance(node, ActionNode):
            await self._execute_action(
                node,
                path,
                run_id,
                scopes,
                events,
                cancellation,
                control,
                workflow_deadline,
                completed,
            )
            return
        if isinstance(node, IfNode):
            try:
                branch = node.then if evaluate(node.condition, scopes) else node.otherwise
            except ResolverReferenceError as exc:
                raise RpaError(ErrorCode.INVALID_REFERENCE, str(exc)) from exc
            for child in branch:
                await self._execute_node(
                    child,
                    [*path, child.id],
                    run_id,
                    scopes,
                    events,
                    cancellation,
                    control,
                    workflow_deadline,
                    completed,
                )
            return
        if isinstance(node, ForEachNode):
            try:
                items = resolve(node.items, scopes)
            except ResolverReferenceError as exc:
                raise RpaError(ErrorCode.INVALID_REFERENCE, str(exc)) from exc
            if isinstance(items, tuple):
                items = list(items)
            if not isinstance(items, list):
                raise RpaError(
                    ErrorCode.INVALID_INPUT, f"forEach {node.id} requires a list or tuple"
                )
            previous = dict(scopes["loop"])
            try:
                for index, item in enumerate(items):
                    scopes["loop"].update({node.item_var: item, "index": index})
                    for child in node.children:
                        await self._execute_node(
                            child,
                            [*path, f"#{index}", child.id],
                            run_id,
                            scopes,
                            events,
                            cancellation,
                            control,
                            workflow_deadline,
                            completed,
                        )
            finally:
                scopes["loop"] = previous
            return
        if isinstance(node, TryNode):
            try:
                for child in node.children:
                    await self._execute_node(
                        child,
                        [*path, child.id],
                        run_id,
                        scopes,
                        events,
                        cancellation,
                        control,
                        workflow_deadline,
                        completed,
                    )
            except RpaError as exc:
                if exc.code == ErrorCode.CANCELLED:
                    raise
                sentinel = object()
                previous_error = scopes.get(node.error_var, sentinel)
                scopes[node.error_var] = {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                }
                try:
                    for child in node.catch:
                        await self._execute_node(
                            child,
                            [*path, child.id],
                            run_id,
                            scopes,
                            events,
                            cancellation,
                            control,
                            workflow_deadline,
                            completed,
                        )
                finally:
                    if previous_error is sentinel:
                        scopes.pop(node.error_var, None)
                    else:
                        scopes[node.error_var] = previous_error
            return
        if isinstance(node, ReturnNode):
            try:
                value = resolve(node.value, scopes)
            except ResolverReferenceError as exc:
                raise RpaError(ErrorCode.INVALID_REFERENCE, str(exc)) from exc
            raise WorkflowReturn(value)

    async def _evaluate_python_fields(
        self,
        fields: dict[str, str],
        scopes: dict[str, Any],
        run_id: str,
        node_id: str,
        cancellation: asyncio.Event,
        input_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """python 模式字段求值：在 worker 子进程执行，本次赋值的变量写回流程变量。

        变量写回由 orchestrator 执行（executor 只回传数据），handler 不直接改动作用域。
        """
        executor = self.executors.get("python.worker")
        if executor is None:
            raise RpaError(
                ErrorCode.EXECUTOR_FAILED,
                "python.worker executor is not registered",
                details={"nodeId": node_id},
            )
        variables = scopes.setdefault("variables", {})
        invocation = CommandInvocation(
            command_id="python.evalExpression",
            command_version="1.0.0",
            run_id=run_id,
            step_id=node_id,
            attempt=1,
            inputs={
                "expressions": fields,
                "variables": {key: value for key, value in variables.items()},
            },
            deadline_monotonic=time.monotonic() + PYTHON_EVAL_TIMEOUT_SECONDS,
        )
        result = await executor.execute(invocation, cancellation)
        if result.status != "success":
            raise RpaError(
                ErrorCode.SCRIPT_FAILED,
                result.error.message if result.error else "Python evaluation failed",
                details={"nodeId": node_id, "commandId": "python.evalExpression"},
            )
        assigned = result.outputs.get("assignedVariables") or {}
        variables.update(assigned)
        values = result.outputs.get("values") or {}
        # string 字段：求值结果收敛为文本（对齐 fx 模式行为），其余字段保留 Python 原类型
        properties = (input_schema or {}).get("properties", {})
        for key, value in values.items():
            schema = properties.get(key)
            if isinstance(schema, dict) and schema.get("type") == "string":
                if value is None:
                    values[key] = ""
                elif not isinstance(value, str):
                    values[key] = str(value)
        return values

    async def _execute_action(
        self,
        node: ActionNode,
        path: list[str],
        run_id: str,
        scopes: dict[str, Any],
        events: EventWriter,
        cancellation: asyncio.Event,
        control: RunControl,
        workflow_deadline: float,
        completed: set[str],
    ) -> None:
        # 节点边界的「停」判定（M24）：用户暂停 / 单步 / 命中断点三合一，见 RunControl。
        stop_reason = control.stop_reason(node.id)
        if stop_reason is not None:
            raise PauseSignal(node.id, stop_reason)
        manifest = self.catalog[node.command]
        context = {"nodeId": node.id, "commandId": manifest.id, "attempt": 0}
        if node.retry_count > 0 and manifest.effect.replay == ReplayPolicy.UNSAFE:
            raise RpaError(
                ErrorCode.INVALID_INPUT,
                f"Unsafe replay command cannot be retried: {manifest.id}",
                details=context,
            )
        modes = node.expr_modes or {}
        # python 模式字段走子进程求值（隔离执行），其余按 fx / 普通模式解析
        python_fields = {
            key: value
            for key, value in node.with_.items()
            if modes.get(key) == "python" and isinstance(value, str) and value.strip()
        }
        plain_inputs = {
            key: value for key, value in node.with_.items() if key not in python_fields
        }
        try:
            command_inputs = resolve_with_modes(plain_inputs, modes, scopes)
        except ResolverReferenceError as exc:
            raise RpaError(ErrorCode.INVALID_REFERENCE, str(exc), details=context) from exc
        if python_fields:
            evaluated = await self._evaluate_python_fields(
                python_fields, scopes, run_id, node.id, cancellation, manifest.input_schema
            )
            command_inputs.update(evaluated)

        # 运行时注入：按 manifest x-runtime.inject 声明，把 flowDir 等运行时量注入 input
        # （必须在 schema 校验前，否则 additionalProperties:false 会拒绝注入字段）
        for runtime_name in (manifest.x_runtime or {}).get("inject", []) or []:
            if runtime_name == "flowDir" and self._flow_dir is not None:
                command_inputs["flowDir"] = str(self._flow_dir)

        errors = sorted(
            Draft202012Validator(manifest.input_schema).iter_errors(command_inputs),
            key=lambda item: list(item.path),
        )
        if errors:
            raise RpaError(
                ErrorCode.INVALID_INPUT,
                errors[0].message,
                details={**context, "path": list(errors[0].path)},
            )

        await events.append("stepStarted", node_id=node.id, payload={"command": node.command})
        executor = self.executors.get(manifest.executor)
        attempts = node.retry_count + 1
        result: CommandResult | None = None
        final_attempt = 0
        for attempt in range(1, attempts + 1):
            if control.pause.is_set():
                raise PauseSignal(node.id, control.pause_reason or "user")
            final_attempt = attempt
            remaining = workflow_deadline - time.monotonic()
            if remaining <= 0:
                raise RpaError(ErrorCode.TIMEOUT, "Workflow deadline exhausted", details=context)
            timeout = min(
                node.timeout_seconds or manifest.default_timeout_seconds,
                remaining,
            )
            attempt_deadline = time.monotonic() + timeout
            invocation = CommandInvocation(
                command_id=manifest.id,
                command_version=manifest.version,
                run_id=run_id,
                step_id=node.id,
                attempt=attempt,
                inputs=command_inputs,
                deadline_monotonic=attempt_deadline,
            )
            await events.append(
                "stepAttemptStarted",
                node_id=node.id,
                payload={"command": node.command, "attempt": attempt, "timeout": timeout},
            )
            result = await self._execute_attempt(executor, invocation, cancellation, timeout)
            if result.status == "success":
                break
            attempt_context = {**context, "attempt": attempt}
            if result.status == "cancelled":
                raise RpaError(ErrorCode.CANCELLED, "Step was cancelled", details=attempt_context)
            unknown_outcome = any(
                effect.status == EffectStatus.UNKNOWN for effect in result.effects
            )
            retryable = (
                bool(result.error and result.error.retryable and manifest.retryable)
                and not unknown_outcome
            )
            if attempt < attempts and retryable:
                delay = min(
                    node.retry_backoff_seconds * (2 ** (attempt - 1)),
                    node.retry_backoff_max_seconds,
                )
                await events.append(
                    "stepRetried",
                    node_id=node.id,
                    payload={
                        "attempt": attempt,
                        "nextAttempt": attempt + 1,
                        "backoffSeconds": delay,
                    },
                )
                await self._sleep_until_retry(delay, cancellation, workflow_deadline)
                continue
            if result.error is None:
                raise RpaError(
                    ErrorCode.INTERNAL_ERROR,
                    f"Executor returned status {result.status!r} without an error",
                    details=attempt_context,
                )
            failure_effects = [
                effect.model_dump(mode="json", by_alias=True) for effect in result.effects
            ]
            await events.append(
                "stepFailed",
                node_id=node.id,
                payload={
                    "attempt": attempt,
                    "error": result.error.model_dump(mode="json"),
                    "effects": failure_effects,
                },
            )
            if unknown_outcome:
                raise IndeterminateOutcome(
                    result.error.code,
                    result.error.message,
                    details={
                        **result.error.details,
                        **attempt_context,
                        "effects": failure_effects,
                    },
                )
            raise RpaError(
                result.error.code,
                result.error.message,
                retryable=result.error.retryable,
                details={
                    **result.error.details,
                    **attempt_context,
                    "effects": failure_effects,
                },
            )

        if result is None:
            raise RpaError(
                ErrorCode.INTERNAL_ERROR,
                f"Executor produced no result for {manifest.id}",
                details={**context, "attempt": final_attempt},
            )
        if manifest.effect.kind != EffectKind.PURE:
            if not result.effects:
                raise RpaError(
                    ErrorCode.INVALID_OUTPUT,
                    f"Command {manifest.id} returned no effect evidence",
                    details={**context, "attempt": final_attempt},
                )
            invalid_effects = [
                effect
                for effect in result.effects
                if effect.kind != manifest.effect.kind or effect.status != EffectStatus.COMMITTED
            ]
            if invalid_effects:
                raise RpaError(
                    ErrorCode.INVALID_OUTPUT,
                    f"Command {manifest.id} returned effect evidence that violates its policy",
                    details={**context, "attempt": final_attempt},
                )
        output_errors = sorted(
            Draft202012Validator(manifest.output_schema).iter_errors(result.outputs),
            key=lambda item: list(item.path),
        )
        if output_errors:
            details = {
                **context,
                "attempt": final_attempt,
                "path": list(output_errors[0].path),
            }
            await events.append(
                "stepFailed",
                node_id=node.id,
                payload={
                    "attempt": final_attempt,
                    "error": {
                        "code": ErrorCode.INVALID_OUTPUT,
                        "message": output_errors[0].message,
                        "details": details,
                    },
                },
            )
            raise RpaError(
                ErrorCode.INVALID_OUTPUT,
                output_errors[0].message,
                details=details,
            )
        effect_records = [
            effect.model_dump(mode="json", by_alias=True) for effect in result.effects
        ]
        scopes["steps"][node.id] = {
            "value": result.value,
            "outputs": result.outputs,
            "effects": effect_records,
            "diagnostics": result.diagnostics,
        }
        output_aliases = getattr(node, "output_aliases", None) or {}
        for field, alias in output_aliases.items():
            if field in result.outputs:
                scopes.setdefault("variables", {})[alias] = result.outputs[field]
        # 变量写入命令（manifest x-var-write）：varName 输入即目标变量名，运行值写入
        # scopes.variables。与 output_aliases 不同，这里允许覆盖同名变量（重赋值），
        # 因此编译期不对其做重复别名拦截。
        manifest = self.catalog.get(node.command)
        var_write = manifest.x_var_write if manifest else None
        if var_write and var_write.get("field"):
            name_field = var_write["field"]
            target = result.outputs.get(name_field)
            if isinstance(target, str) and target:
                scopes.setdefault("variables", {})[target] = result.outputs.get("value")
        step_key = _node_path_key(path)
        await asyncio.to_thread(
            CheckpointStore(events.run_dir / "checkpoint.json").write,
            checkpoint_payload(
                workflow_id=scopes.get("workflowId") or "",
                catalog_digest=scopes.get("catalogDigest") or "",
                completed_steps=sorted({*completed, step_key}),
                scopes=scopes,
                return_value=None,
                # 断点状态随每个节点边界落盘：单步/断点在下一个边界收口时，
                # 检查点里已经带上最新集合（避免收口前进程被杀丢状态）
                breakpoints=sorted(control.breakpoints),
                consumed_breakpoints=sorted(control.consumed),
            ),
        )
        completed.add(step_key)
        await events.append(
            "stepCompleted",
            node_id=node.id,
            payload={
                "attempt": final_attempt,
                "outputs": result.outputs,
                "effects": effect_records,
            },
        )

    async def _execute_attempt(
        self,
        executor,
        invocation: CommandInvocation,
        cancellation: asyncio.Event,
        attempt_timeout: float,
    ) -> CommandResult:
        attempt_cancellation = asyncio.Event()
        task = asyncio.create_task(executor.execute(invocation, attempt_cancellation))
        cancel_wait = asyncio.create_task(cancellation.wait())
        try:
            done, _ = await asyncio.wait(
                {task, cancel_wait},
                timeout=attempt_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if task in done:
                return await task
            attempt_cancellation.set()
            await self._cancel_and_await(task)
            if cancel_wait in done and cancellation.is_set():
                return CommandResult(status="cancelled")
            return CommandResult.failure(ErrorCode.TIMEOUT, "Step timed out", retryable=True)
        except asyncio.CancelledError:
            attempt_cancellation.set()
            await self._cancel_and_await(task)
            raise
        finally:
            cancel_wait.cancel()
            await asyncio.gather(cancel_wait, return_exceptions=True)

    @staticmethod
    async def _cancel_and_await(task: asyncio.Task) -> None:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    @staticmethod
    async def _sleep_until_retry(
        delay: float, cancellation: asyncio.Event, workflow_deadline: float
    ) -> None:
        remaining = workflow_deadline - time.monotonic()
        if remaining <= delay:
            raise RpaError(ErrorCode.TIMEOUT, "Workflow deadline exhausted before retry")
        try:
            await asyncio.wait_for(cancellation.wait(), timeout=delay)
        except TimeoutError:
            return
        raise RpaError(ErrorCode.CANCELLED, "Run was cancelled during retry backoff")
