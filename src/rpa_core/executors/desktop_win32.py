import asyncio
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from pywinauto import Desktop
from pywinauto.keyboard import send_keys

from rpa_core.model.command import CommandInvocation, CommandResult, EffectKind, EffectRecord
from rpa_core.model.desktop import DesktopLocator
from rpa_core.model.errors import ErrorCode

from .base import CommandExecutor


@dataclass
class _Win32Session:
    process_id: int
    window_handle: int
    elements: dict[str, dict[str, Any]]


class Win32DesktopExecutor(CommandExecutor):
    def __init__(self, operation_timeout_seconds: float = 15.0):
        self.operation_timeout_seconds = operation_timeout_seconds
        self._sessions: dict[str, _Win32Session] = {}
        self._lock = asyncio.Lock()
        self._thread_pool: ThreadPoolExecutor | None = None
        self._closed = False

    async def execute(
        self, invocation: CommandInvocation, cancellation: asyncio.Event
    ) -> CommandResult:
        if sys.platform != "win32":
            return CommandResult.failure(
                ErrorCode.PLATFORM_UNSUPPORTED,
                "Windows Win32 automation is only available on Windows",
            )
        if cancellation.is_set():
            return CommandResult(status="cancelled")
        if invocation.command_id != "desktop.win32.attachWindow":
            session_id = str(invocation.inputs.get("sessionId") or "")
            if not session_id or session_id not in self._sessions:
                return CommandResult.failure(
                    ErrorCode.SESSION_NOT_FOUND, "Desktop session not found"
                )
        try:
            async with self._lock:
                if self._thread_pool is None:
                    self._thread_pool = ThreadPoolExecutor(
                        max_workers=1, thread_name_prefix="rpa-desktop-win32"
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
        command = invocation.command_id
        inputs = invocation.inputs
        if command == "desktop.win32.attachWindow":
            title = str(inputs.get("title") or "")
            class_name = inputs.get("className")
            handle = inputs.get("handle")
            process_id = inputs.get("processId")
            timeout_ms = int(inputs.get("timeoutMs") or 0)
            deadline = time.monotonic() + timeout_ms / 1000.0
            while True:
                if handle is not None:
                    windows = Desktop(backend="win32").windows(handle=int(handle))
                else:
                    windows = (
                        Desktop(backend="win32").windows(title=title)
                        if title
                        else Desktop(backend="win32").windows()
                    )
                    if class_name:
                        windows = [
                            window for window in windows if window.class_name() == class_name
                        ]
                    if process_id is not None:
                        windows = [
                            window for window in windows if window.process_id() == process_id
                        ]
                if len(windows) == 1:
                    break
                if len(windows) > 1:
                    return CommandResult.failure(
                        ErrorCode.ELEMENT_AMBIGUOUS,
                        "Desktop window matched multiple targets",
                        details={"title": title, "matchedCount": len(windows)},
                    )
                if time.monotonic() >= deadline:
                    return CommandResult.failure(
                        ErrorCode.ELEMENT_NOT_FOUND,
                        "Desktop window did not match",
                        details={"title": title, "matchedCount": 0},
                    )
                time.sleep(0.1)
            window = windows[0]
            session_id = str(uuid.uuid4())
            native_handle = int(window.handle)
            pid = int(window.process_id())
            self._sessions[session_id] = _Win32Session(pid, native_handle, {})
            return CommandResult.success(
                outputs={
                    "sessionId": session_id,
                    "processId": pid,
                    "workWindowId": str(native_handle),
                },
                effects=[
                    EffectRecord.committed(
                        invocation,
                        kind=EffectKind.SESSION,
                        resource=f"desktop.win32.session:{session_id}:window:{native_handle}",
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
        if command == "desktop.win32.closeSession":
            self._sessions.pop(session_id, None)
            return CommandResult.success(
                effects=[
                    EffectRecord.committed(
                        invocation,
                        kind=EffectKind.SESSION,
                        resource=f"desktop.win32.session:{session_id}:window:{session.window_handle}",
                        details={"operation": "closeSession"},
                    )
                    ]
                )

        if command == "desktop.win32.menuSelect":
            path = inputs.get("menuPath") or []
            if not path:
                return CommandResult.failure(ErrorCode.INVALID_INPUT, "menuPath is required")
            self._menu_select(session.window_handle, [str(part) for part in path])
            return CommandResult.success(
                effects=[
                    EffectRecord.committed(
                        invocation,
                        kind=EffectKind.UNSAFE_WRITE,
                        resource=f"desktop.win32.session:{session_id}:window:{session.window_handle}",
                        details={"operation": "menuSelect"},
                    )
                ]
            )

        if command == "desktop.win32.hotkey":
            send_keys(str(inputs["keys"]))
            return CommandResult.success(
                effects=[
                    EffectRecord.committed(
                        invocation,
                        kind=EffectKind.UNSAFE_WRITE,
                        resource=f"desktop.win32.session:{session_id}:window:{session.window_handle}",
                        details={"operation": "hotkey"},
                    )
                ]
            )

        window = self._window_by_handle(session.window_handle)
        if window is None:
            return CommandResult.failure(
                ErrorCode.SESSION_NOT_FOUND, "Desktop window not found"
            )

        if command == "desktop.win32.findElement":
            locator = DesktopLocator.model_validate(inputs["locator"])
            timeout_ms = int(inputs.get("timeoutMs") or 0)
            deadline = time.monotonic() + timeout_ms / 1000.0
            while True:
                matches = self._find(window, locator)
                if len(matches) == 1:
                    break
                if len(matches) > 1:
                    return CommandResult.failure(
                        ErrorCode.ELEMENT_AMBIGUOUS,
                        "Desktop element matched multiple targets",
                        details={
                            "locator": locator.model_dump(by_alias=True),
                            "matchedCount": len(matches),
                        },
                    )
                if time.monotonic() >= deadline:
                    return CommandResult.failure(
                        ErrorCode.ELEMENT_NOT_FOUND,
                        "Desktop element did not match",
                        details={"locator": locator.model_dump(by_alias=True), "matchedCount": 0},
                    )
                time.sleep(0.1)
            element_id = str(uuid.uuid4())
            session.elements[element_id] = locator.model_dump(by_alias=True)
            return CommandResult.success(
                outputs={"elementId": element_id, "matchedCount": 1},
                effects=[
                    EffectRecord.committed(
                        invocation,
                        kind=EffectKind.READ,
                        resource=f"desktop.win32.session:{session_id}:element:{element_id}",
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
        locator = DesktopLocator.model_validate(locator_data)
        matches = self._find(window, locator)
        if len(matches) == 0:
            return CommandResult.failure(
                ErrorCode.ELEMENT_NOT_FOUND, "Desktop element not found"
            )
        element = self._pick(matches, locator.found_index)
        if element is None:
            return CommandResult.failure(ErrorCode.ELEMENT_NOT_FOUND, "Desktop element not found")

        resource = f"desktop.win32.session:{session_id}:element:{element_id}"
        if command == "desktop.win32.input":
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
        if command == "desktop.win32.click":
            if hasattr(element, "invoke"):
                element.invoke()
            else:
                element.click_input()
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
        if command == "desktop.win32.getText":
            texts = element.texts() if hasattr(element, "texts") else []
            value = texts[0] if texts else element.window_text()
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

    @staticmethod
    def _find(window: Any, locator: DesktopLocator) -> list[Any]:
        criteria = {}
        if locator.title:
            criteria["title"] = locator.title
        if locator.class_name:
            criteria["class_name"] = locator.class_name
        if locator.control_id is not None:
            criteria["control_id"] = locator.control_id
        matches = window.descendants(**criteria)
        if locator.found_index is not None:
            if locator.found_index >= len(matches):
                return []
            return [matches[locator.found_index]]
        return matches

    @staticmethod
    def _pick(matches: list[Any], found_index: int | None) -> Any | None:
        if not matches:
            return None
        if found_index is None:
            return matches[0]
        return matches[found_index] if found_index < len(matches) else None

    @staticmethod
    def _window_by_handle(handle: int) -> Any | None:
        windows = Desktop(backend="win32").windows(handle=handle)
        if len(windows) != 1:
            return None
        return windows[0]

    @staticmethod
    def _menu_select(hwnd: int, menu_path: list[str]) -> None:
        Desktop(backend="win32").window(handle=hwnd).menu_select("->".join(menu_path))

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
