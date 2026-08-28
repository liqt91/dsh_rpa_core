import asyncio
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from rpa_core.catalog import CommandCatalog
from rpa_core.compiler import ExecutionPlan
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

from .events import EventWriter, RunPersistenceError
from .resolver import ReferenceError as ResolverReferenceError
from .resolver import evaluate, resolve


class WorkflowReturn(Exception):
    def __init__(self, value: Any):
        self.value = value


class RunHandle:
    def __init__(self, run_id: str, task: asyncio.Task[RunResult], cancellation: asyncio.Event):
        self.run_id = run_id
        self.task = task
        self._cancellation = cancellation

    def cancel(self) -> None:
        self._cancellation.set()

    async def cancel_and_wait(self) -> RunResult:
        self.cancel()
        return await self.wait()

    async def wait(self) -> RunResult:
        return await self.task


class Orchestrator:
    def __init__(
        self,
        catalog: CommandCatalog,
        executors: ExecutorRegistry,
        artifacts_root: Path,
    ):
        self.catalog = catalog
        self.executors = executors
        self.artifacts_root = artifacts_root

    def start(self, plan: ExecutionPlan, inputs: dict[str, Any] | None = None) -> RunHandle:
        run_id = str(uuid.uuid4())
        cancellation = asyncio.Event()
        task = asyncio.create_task(self._run(run_id, plan, inputs or {}, cancellation))
        return RunHandle(run_id, task, cancellation)

    async def run(self, plan: ExecutionPlan, inputs: dict[str, Any] | None = None) -> RunResult:
        return await self.start(plan, inputs).wait()

    async def _run(
        self,
        run_id: str,
        plan: ExecutionPlan,
        inputs: dict[str, Any],
        cancellation: asyncio.Event,
    ) -> RunResult:
        run_dir = self.artifacts_root / run_id
        events = EventWriter(run_dir, run_id)
        started = datetime.now(UTC)
        workflow_deadline = time.monotonic() + plan.workflow.timeout_seconds
        scopes: dict[str, Any] = {
            "inputs": {**plan.workflow.inputs, **inputs},
            "steps": {},
            "loop": {},
        }
        try:
            await events.initialize()
            await events.append(
                "runStarted",
                payload={
                    "workflowId": plan.workflow.id,
                    "catalogDigest": plan.catalog_digest,
                    "deadlineMonotonic": workflow_deadline,
                },
            )
        except OSError as exc:
            raise RunPersistenceError("Failed to initialize run evidence", cause=exc) from exc

        status = RunStatus.SUCCEEDED
        return_value = None
        error: dict[str, Any] | None = None
        try:
            execution = self._execute_node(
                plan.workflow.root,
                run_id,
                scopes,
                events,
                cancellation,
                workflow_deadline,
            )
            async with asyncio.timeout_at(workflow_deadline):
                await execution
        except WorkflowReturn as returned:
            return_value = returned.value
        except RunPersistenceError:
            cancellation.set()
            raise
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
        return result

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
        run_id: str,
        scopes: dict[str, Any],
        events: EventWriter,
        cancellation: asyncio.Event,
        workflow_deadline: float,
    ) -> None:
        if cancellation.is_set():
            raise RpaError(ErrorCode.CANCELLED, "Run was cancelled")
        if isinstance(node, SequenceNode):
            for child in node.children:
                await self._execute_node(
                    child, run_id, scopes, events, cancellation, workflow_deadline
                )
            return
        if isinstance(node, ActionNode):
            await self._execute_action(
                node, run_id, scopes, events, cancellation, workflow_deadline
            )
            return
        if isinstance(node, IfNode):
            try:
                branch = node.then if evaluate(node.condition, scopes) else node.otherwise
            except ResolverReferenceError as exc:
                raise RpaError(ErrorCode.INVALID_REFERENCE, str(exc)) from exc
            for child in branch:
                await self._execute_node(
                    child, run_id, scopes, events, cancellation, workflow_deadline
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
                            run_id,
                            scopes,
                            events,
                            cancellation,
                            workflow_deadline,
                        )
            finally:
                scopes["loop"] = previous
            return
        if isinstance(node, TryNode):
            try:
                for child in node.children:
                    await self._execute_node(
                        child, run_id, scopes, events, cancellation, workflow_deadline
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
                            run_id,
                            scopes,
                            events,
                            cancellation,
                            workflow_deadline,
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

    async def _execute_action(
        self,
        node: ActionNode,
        run_id: str,
        scopes: dict[str, Any],
        events: EventWriter,
        cancellation: asyncio.Event,
        workflow_deadline: float,
    ) -> None:
        manifest = self.catalog[node.command]
        context = {"nodeId": node.id, "commandId": manifest.id, "attempt": 0}
        if node.retry_count > 0 and manifest.effect.replay == ReplayPolicy.UNSAFE:
            raise RpaError(
                ErrorCode.INVALID_INPUT,
                f"Unsafe replay command cannot be retried: {manifest.id}",
                details=context,
            )
        try:
            command_inputs = resolve(node.with_, scopes)
        except ResolverReferenceError as exc:
            raise RpaError(ErrorCode.INVALID_REFERENCE, str(exc), details=context) from exc

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
            retryable = bool(result.error and result.error.retryable and manifest.retryable)
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
