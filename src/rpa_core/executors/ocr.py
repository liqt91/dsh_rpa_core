"""OCR 指令族执行器：管理承载 ddddocr 的**常驻** worker 子进程。

分工（与 ``executors/python_worker.py`` 同一套思路）：

- 执行器**不** import ddddocr —— 那会把 1.11s 的 import 与大块模型内存带进编排进程；
- 执行器只负责：子进程生命周期、请求串行化、取消/关闭、响应 → ``CommandResult`` 映射。

与 ``python_worker`` 的差别是**生命周期**：那边「一命令一进程」，这边**常驻**一个进程，
因为 ddddocr 的 import + 首次构造 + 首次推理有一次性开销（见 ``workers/ocr_worker.py``）。

★ 已知边界（登记，不在本切片处理）：取消时子进程状态不可知（请求可能已进入推理、
无法中途打断），因此**取消即丢弃并重建**该进程。
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from rpa_core.model.command import CommandInvocation, CommandResult
from rpa_core.model.errors import ErrorCode

from .base import CommandExecutor


def _worker_argv() -> list[str]:
    """OCR worker 子进程的启动命令。

    开发态走 ``python -m``；**打包（frozen）态不认 ``-m``**，需改成「同一 exe +
    argv 分发」。真正打包时只改这里一处（见
    ``.harness/tasks/M61-standalone-runner-packaging.md`` §4.3）。
    """
    if getattr(sys, "frozen", False):  # pragma: no cover - 打包态专用分支
        return [sys.executable, "--ocr-worker"]
    return [sys.executable, "-m", "rpa_core.workers.ocr_worker"]


class _WorkerGone(RuntimeError):
    """worker 进程提前退出（读到 EOF）⇒ 无法完成任务。"""


def _error_code(raw: Any) -> ErrorCode:
    """worker 回传的字符串码 → ``ErrorCode``；未登记的一律 ``EXECUTOR_FAILED``。"""
    try:
        return ErrorCode(str(raw))
    except ValueError:
        return ErrorCode.EXECUTOR_FAILED


class OcrExecutor(CommandExecutor):
    """持有至多一个常驻 OCR worker 子进程的执行器。"""

    def __init__(self) -> None:
        self._process: asyncio.subprocess.Process | None = None
        self._request_id = 0
        # 同一时刻只允许一次往返：协议是一请求一响应，复用同一对管道。
        self._lock = asyncio.Lock()

    @property
    def active_process_count(self) -> int:
        """存活子进程数（判据用：取消/关闭后必须归零）。"""
        if self._process is None or self._process.returncode is not None:
            return 0
        return 1

    async def _ensure_process(self) -> asyncio.subprocess.Process:
        process = self._process
        if process is not None and process.returncode is None:
            return process
        process = await asyncio.create_subprocess_exec(
            *_worker_argv(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._process = process
        return process

    async def _roundtrip(self, request: dict[str, Any]) -> dict[str, Any]:
        process = await self._ensure_process()
        assert process.stdin is not None  # PIPE 已显式指定
        assert process.stdout is not None
        process.stdin.write(json.dumps(request, ensure_ascii=False).encode("utf-8") + b"\n")
        await process.stdin.drain()
        raw = await process.stdout.readline()
        if not raw:
            detail = ""
            if process.stderr is not None:
                detail = (await process.stderr.read()).decode("utf-8", errors="replace").strip()
            raise _WorkerGone(detail or "OCR worker exited before responding")
        return json.loads(raw.decode("utf-8"))

    async def execute(
        self, invocation: CommandInvocation, cancellation: asyncio.Event
    ) -> CommandResult:
        if cancellation.is_set():
            return CommandResult(status="cancelled")
        async with self._lock:
            if cancellation.is_set():
                return CommandResult(status="cancelled")
            self._request_id += 1
            request = {
                "id": self._request_id,
                "commandId": invocation.command_id,
                "inputs": invocation.inputs,
            }
            try:
                # 取消必须能在「等待 worker 响应」期间生效：ddddocr 首次推理可达秒级，
                # 直接 await 会把取消信号吞掉（python_worker 用同一套 asyncio.wait 竞争）。
                roundtrip = asyncio.create_task(self._roundtrip(request))
                cancel_wait = asyncio.create_task(cancellation.wait())
                try:
                    done, _ = await asyncio.wait(
                        {roundtrip, cancel_wait}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if cancel_wait in done and cancellation.is_set():
                        roundtrip.cancel()
                        await asyncio.gather(roundtrip, return_exceptions=True)
                        # 请求可能已被 worker 读走并进入推理 ⇒ 进程状态不可知，丢弃重建。
                        await self._discard()
                        return CommandResult(status="cancelled")
                    response = await roundtrip
                finally:
                    cancel_wait.cancel()
                    await asyncio.gather(cancel_wait, return_exceptions=True)
            except asyncio.CancelledError:
                await self._discard()
                raise
            except _WorkerGone as exc:
                await self._discard()
                return CommandResult.failure(ErrorCode.EXECUTOR_FAILED, str(exc))
            except (json.JSONDecodeError, OSError) as exc:
                await self._discard()
                return CommandResult.failure(
                    ErrorCode.EXECUTOR_FAILED, f"invalid OCR worker response: {exc}"
                )
        if not response.get("ok"):
            return CommandResult.failure(
                _error_code(response.get("code")),
                str(response.get("message") or "OCR command failed"),
            )
        outputs = response.get("outputs") or {}
        return CommandResult.success(outputs=outputs)

    async def _discard(self) -> None:
        """杀掉并丢弃当前子进程（下次调用会重新拉起）。"""
        process = self._process
        self._process = None
        if process is None:
            return
        if process.returncode is None:
            process.kill()
        await process.wait()

    async def close(self) -> None:
        await self._discard()
