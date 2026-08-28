import asyncio
import json
import sys

from rpa_core.model.command import CommandInvocation, CommandResult
from rpa_core.model.errors import ErrorCode

from .base import CommandExecutor


class PythonWorkerExecutor(CommandExecutor):
    def __init__(self, terminate_grace_seconds: float = 1.0):
        self.terminate_grace_seconds = terminate_grace_seconds
        self._processes: dict[asyncio.subprocess.Process, asyncio.Task] = {}

    @property
    def active_process_count(self) -> int:
        return len(self._processes)

    async def execute(
        self, invocation: CommandInvocation, cancellation: asyncio.Event
    ) -> CommandResult:
        if cancellation.is_set():
            return CommandResult(status="cancelled")
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "rpa_core.workers.python_worker",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        payload = invocation.model_dump_json().encode("utf-8")
        communicate = asyncio.create_task(process.communicate(payload))
        self._processes[process] = communicate
        cancel_wait = asyncio.create_task(cancellation.wait())
        try:
            done, _ = await asyncio.wait(
                {communicate, cancel_wait}, return_when=asyncio.FIRST_COMPLETED
            )
            if cancel_wait in done and cancellation.is_set():
                await self._stop_process(process, communicate)
                return CommandResult(status="cancelled")
            stdout, stderr = await communicate
            if process.returncode != 0:
                return CommandResult.failure(
                    ErrorCode.SCRIPT_FAILED,
                    stderr.decode("utf-8", errors="replace") or "Python worker failed",
                )
            try:
                return CommandResult.model_validate(json.loads(stdout.decode("utf-8")))
            except Exception as exc:
                return CommandResult.failure(
                    ErrorCode.SCRIPT_FAILED, f"Invalid worker response: {exc}"
                )
        except asyncio.CancelledError:
            await self._stop_process(process, communicate)
            raise
        finally:
            cancel_wait.cancel()
            await asyncio.gather(cancel_wait, return_exceptions=True)
            if process.returncode is not None and communicate.done():
                self._processes.pop(process, None)

    async def _stop_process(
        self,
        process: asyncio.subprocess.Process,
        communicate: asyncio.Task,
    ) -> None:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=self.terminate_grace_seconds)
            except TimeoutError:
                process.kill()
                await process.wait()
        communicate.cancel()
        await asyncio.gather(communicate, return_exceptions=True)
        self._processes.pop(process, None)

    async def close(self) -> None:
        for process, communicate in list(self._processes.items()):
            await self._stop_process(process, communicate)
        self._processes.clear()
