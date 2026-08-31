import asyncio
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from rpa_core.model.command import CommandInvocation, CommandResult, EffectKind, EffectRecord
from rpa_core.model.desktop import DesktopLocator
from rpa_core.model.errors import ErrorCode

from .base import CommandExecutor


@dataclass
class _DesktopSession:
    process_id: int
    window_handle: int
    elements: dict[str, dict[str, Any]]


class DesktopExecutor(CommandExecutor):
    def __init__(self, operation_timeout_seconds: float = 15.0):
        self.operation_timeout_seconds = operation_timeout_seconds
        self._sessions: dict[str, _DesktopSession] = {}
        self._lock = asyncio.Lock()
        self._thread_pool: ThreadPoolExecutor | None = None
        self._closed = False
        self._worker_loop = None

    @property
    def active_session_count(self) -> int:
        return len(self._sessions)

    async def execute(
        self, invocation: CommandInvocation, cancellation: asyncio.Event
    ) -> CommandResult:
        if sys.platform != "win32":
            return CommandResult.failure(
                ErrorCode.PLATFORM_UNSUPPORTED,
                "Windows UI Automation is only available on Windows",
            )
        if cancellation.is_set():
            return CommandResult(status="cancelled")
        if invocation.command_id != "desktop.attachWindow":
            session_id = str(invocation.inputs.get("sessionId") or "")
            if not session_id or session_id not in self._sessions:
                return CommandResult.failure(
                    ErrorCode.SESSION_NOT_FOUND, "Desktop session not found"
                )
        try:
            async with self._lock:
                if self._thread_pool is None:
                    self._thread_pool = ThreadPoolExecutor(
                        max_workers=1, thread_name_prefix="rpa-desktop"
                    )
                operation = asyncio.get_running_loop().run_in_executor(
                    self._thread_pool, self._execute_sync, invocation
                )
                return await asyncio.wait_for(operation, timeout=self.operation_timeout_seconds)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            return CommandResult.failure(ErrorCode.TIMEOUT, "Desktop operation timed out")
        except Exception as exc:
            return CommandResult.failure(ErrorCode.EXECUTOR_FAILED, str(exc))

    def _execute_sync(self, invocation: CommandInvocation) -> CommandResult:
        import pythoncom
        from pywinauto import Desktop

        pythoncom.CoInitialize()
        try:
            command = invocation.command_id
            inputs = invocation.inputs
            if command == "desktop.attachWindow":
                title = str(inputs["title"])
                process_id = inputs.get("processId")
                windows = Desktop(backend="uia").windows(title=title)
                if process_id is not None:
                    windows = [window for window in windows if window.process_id() == process_id]
                if len(windows) == 0:
                    return CommandResult.failure(
                        ErrorCode.ELEMENT_NOT_FOUND,
                        "Desktop window did not match",
                        details={"title": title, "matchedCount": 0},
                    )
                if len(windows) > 1:
                    return CommandResult.failure(
                        ErrorCode.ELEMENT_AMBIGUOUS,
                        "Desktop window matched multiple targets",
                        details={"title": title, "matchedCount": len(windows)},
                    )
                window = windows[0]
                session_id = str(uuid.uuid4())
                handle = int(window.handle)
                pid = int(window.process_id())
                self._sessions[session_id] = _DesktopSession(pid, handle, {})
                return CommandResult.success(
                    outputs={
                        "sessionId": session_id,
                        "processId": pid,
                        "workWindowId": str(handle),
                    },
                    effects=[
                        EffectRecord.committed(
                            invocation,
                            kind=EffectKind.SESSION,
                            resource=f"desktop.session:{session_id}:window:{handle}",
                            details={"operation": "attachWindow", "processId": pid},
                        )
                    ],
                )

            session_id = str(inputs.get("sessionId") or "")
            session = self._sessions.get(session_id)
            if session is None:
                return CommandResult.failure(
                    ErrorCode.SESSION_NOT_FOUND, "Desktop session not found"
                )
            if command == "desktop.closeSession":
                self._sessions.pop(session_id, None)
                return CommandResult.success(
                    effects=[
                        EffectRecord.committed(
                            invocation,
                            kind=EffectKind.SESSION,
                            resource=f"desktop.session:{session_id}:window:{session.window_handle}",
                            details={"operation": "closeSession"},
                        )
                    ]
                )
            if command == "desktop.findElement":
                locator = DesktopLocator.model_validate(inputs["locator"])
                window = self._window_by_handle(session.window_handle)
                if window is None:
                    return CommandResult.failure(
                        ErrorCode.SESSION_NOT_FOUND, "Desktop window not found"
                    )
                matches = self._find(window, locator)
                if len(matches) == 0:
                    return CommandResult.failure(
                        ErrorCode.ELEMENT_NOT_FOUND,
                        "Desktop element did not match",
                        details={"locator": locator.model_dump(by_alias=True), "matchedCount": 0},
                    )
                if len(matches) > 1:
                    return CommandResult.failure(
                        ErrorCode.ELEMENT_AMBIGUOUS,
                        "Desktop element matched multiple targets",
                        details={
                            "locator": locator.model_dump(by_alias=True),
                            "matchedCount": len(matches),
                        },
                    )
                element_id = str(uuid.uuid4())
                session.elements[element_id] = locator.model_dump(by_alias=True)
                return CommandResult.success(
                    outputs={"elementId": element_id, "matchedCount": 1},
                    effects=[
                        EffectRecord.committed(
                            invocation,
                            kind=EffectKind.READ,
                            resource=f"desktop.session:{session_id}:element:{element_id}",
                            details={"operation": "findElement"},
                        )
                    ],
                )
            element_id = str(inputs.get("elementId") or "")
            locator_data = session.elements.get(element_id)
            if locator_data is None:
                return CommandResult.failure(
                    ErrorCode.ELEMENT_NOT_FOUND, "Desktop element not found"
                )
            resource = f"desktop.session:{session_id}:element:{element_id}"
            window = self._window_by_handle(session.window_handle)
            if window is None:
                return CommandResult.failure(
                    ErrorCode.SESSION_NOT_FOUND, "Desktop window not found"
                )
            element_locator = DesktopLocator.model_validate(locator_data)
            matches = self._find(window, element_locator)
            if len(matches) == 0:
                return CommandResult.failure(
                    ErrorCode.ELEMENT_NOT_FOUND, "Desktop element not found"
                )
            element = matches[0]
            if command == "desktop.input":
                element.set_edit_text(str(inputs["text"]))
                return CommandResult.success(
                    effects=[
                        EffectRecord.committed(
                            invocation,
                            kind=EffectKind.UNSAFE_WRITE,
                            resource=resource,
                            details={"operation": "input"},
                        )
                    ]
                )
            if command == "desktop.click":
                element.invoke()
                return CommandResult.success(
                    effects=[
                        EffectRecord.committed(
                            invocation,
                            kind=EffectKind.UNSAFE_WRITE,
                            resource=resource,
                            details={"operation": "click"},
                        )
                    ]
                )
            if command == "desktop.getText":
                value = element.window_text()
                return CommandResult.success(
                    value=value,
                    outputs={"value": value},
                    effects=[
                        EffectRecord.committed(
                            invocation,
                            kind=EffectKind.READ,
                            resource=resource,
                            details={"operation": "getText"},
                        )
                    ],
                )
            return CommandResult.failure(
                ErrorCode.COMMAND_NOT_FOUND, f"Unsupported command: {command}"
            )
        finally:
            pythoncom.CoUninitialize()

    @staticmethod
    def _find(window: Any, locator: DesktopLocator) -> list[Any]:
        criteria = {}
        if locator.control_type:
            criteria["control_type"] = locator.control_type
        if locator.name:
            criteria["title"] = locator.name
        matches = window.descendants(**criteria)
        if locator.automation_id:
            matches = [
                match
                for match in matches
                if getattr(match.element_info, "automation_id", None)
                == locator.automation_id
            ]
        return matches

    @staticmethod
    def _window_by_handle(handle: int) -> Any | None:
        from pywinauto import Desktop

        windows = Desktop(backend="uia").windows(handle=handle)
        if len(windows) != 1:
            return None
        return windows[0]

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._sessions.clear()
            self._closed = True
        if self._thread_pool is not None:
            await asyncio.get_running_loop().run_in_executor(self._thread_pool, lambda: None)
            self._thread_pool.shutdown(wait=True, cancel_futures=True)
            self._thread_pool = None
