import asyncio
import ctypes
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from rpa_core.model.command import CommandInvocation, CommandResult, EffectKind, EffectRecord
from rpa_core.model.desktop import DesktopLocator
from rpa_core.model.errors import ErrorCode

from .base import CommandExecutor, resolve_session_id


@dataclass
class _DesktopSession:
    process_id: int
    window_handle: int
    elements: dict[str, dict[str, Any]]


class DesktopExecutor(CommandExecutor):
    def __init__(self, operation_timeout_seconds: float = 15.0):
        self.operation_timeout_seconds = operation_timeout_seconds
        self._sessions: dict[str, _DesktopSession] = {}
        # 记录最近激活的会话，供省略 sessionId 的命令默认使用
        self._last_session_id: str | None = None
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
        _no_session_commands = (
            "desktop.attachWindow",
            "desktop.getWindowList",
        )
        if invocation.command_id not in _no_session_commands:
            # sessionId 可省略：默认作用于最近激活（或唯一）的桌面会话
            session_id = resolve_session_id(
                invocation.inputs.get("sessionId"),
                self._sessions,
                self._last_session_id,
            )
            if not session_id or session_id not in self._sessions:
                return CommandResult.failure(
                    ErrorCode.SESSION_NOT_FOUND, "Desktop session not found"
                )
            self._last_session_id = session_id
        try:
            async with self._lock:
                if self._thread_pool is None:
                    self._thread_pool = ThreadPoolExecutor(
                        max_workers=1, thread_name_prefix="rpa-desktop"
                    )
                op_timeout_ms = invocation.inputs.get("operationTimeoutMs")
                operation_timeout = (
                    float(op_timeout_ms) / 1000.0
                    if op_timeout_ms
                    else self.operation_timeout_seconds
                )
                operation = asyncio.get_running_loop().run_in_executor(
                    self._thread_pool, self._execute_sync, invocation
                )
                return await asyncio.wait_for(operation, timeout=operation_timeout)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            return CommandResult.failure(ErrorCode.TIMEOUT, "Desktop operation timed out")
        except Exception as exc:
            return CommandResult.failure(ErrorCode.EXECUTOR_FAILED, str(exc))

    def _execute_sync(self, invocation: CommandInvocation) -> CommandResult:
        import pythoncom
        from comtypes import COMError

        pythoncom.CoInitialize()
        try:
            command = invocation.command_id
            inputs = invocation.inputs
            if command == "desktop.attachWindow":
                title = str(inputs["title"])
                process_id = inputs.get("processId")
                match_mode = inputs.get("matchMode", "exact")
                timeout_ms = int(inputs.get("timeoutMs") or 0)
                deadline = time.monotonic() + timeout_ms / 1000.0
                while True:
                    windows = self._find_windows_by_title(
                        title, process_id, match_mode
                    )
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
                handle = int(window.handle)
                pid = int(window.process_id())
                self._sessions[session_id] = _DesktopSession(pid, handle, {})
                # 新建的会话即默认会话，后续命令可省略 sessionId
                self._last_session_id = session_id
                return CommandResult.success(
                    outputs={
                        "sessionId": session_id,
                        "resourceType": "windowHandle",
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

            session_id = resolve_session_id(
                inputs.get("sessionId"), self._sessions, self._last_session_id
            )
            if session_id:
                self._last_session_id = session_id
            session = self._sessions.get(session_id)
            if session is None:
                return CommandResult.failure(
                    ErrorCode.SESSION_NOT_FOUND, "Desktop session not found"
                )
            if command == "desktop.closeSession":
                force_kill = inputs.get("forceKill", False)
                if force_kill:
                    try:
                        import ctypes
                        ctypes.windll.kernel32.TerminateProcess(
                            ctypes.windll.kernel32.OpenProcess(
                                0x0400, False, session.process_id
                            ), 1
                        )
                    except Exception:
                        pass
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
            if command == "desktop.getWindowList":
                title_pattern = inputs.get("titlePattern")
                match_mode = inputs.get("matchMode", "contains")
                import re

                from pywinauto import Desktop as PyDesktop
                all_wins = PyDesktop(backend="uia").windows()
                result_windows = []
                for w in all_wins:
                    try:
                        win_title = w.window_text() or ""
                        cls_name = w.class_name() or ""
                        pid = w.process_id()
                    except Exception:
                        continue
                    if not win_title:
                        continue
                    if title_pattern:
                        if match_mode == "exact":
                            if win_title != title_pattern:
                                continue
                        elif match_mode == "contains":
                            if title_pattern not in win_title:
                                continue
                        elif match_mode == "regex":
                            if not re.search(title_pattern, win_title):
                                continue
                    result_windows.append({
                        "title": win_title,
                        "className": cls_name,
                        "processId": pid,
                    })
                return CommandResult.success(
                    outputs={"windows": result_windows},
                    effects=[
                        EffectRecord.committed(
                            invocation,
                            kind=EffectKind.READ,
                            resource="desktop.windows",
                            details={"operation": "getWindowList", "count": len(result_windows)},
                        )
                    ],
                )
            if command == "desktop.activateWindow":
                window = self._window_by_handle(session.window_handle)
                if window is None:
                    return CommandResult.failure(
                        ErrorCode.SESSION_NOT_FOUND, "Desktop window not found"
                    )
                try:
                    window.set_focus()
                except Exception:
                    import ctypes
                    user32 = ctypes.windll.user32
                    user32.SetForegroundWindow(int(session.window_handle))
                return CommandResult.success(
                    effects=[
                        EffectRecord.committed(
                            invocation,
                            kind=EffectKind.UNSAFE_WRITE,
                            resource=f"desktop.session:{session_id}:window:{session.window_handle}",
                            details={"operation": "activateWindow"},
                        )
                    ]
                )
            if command == "desktop.setWindowState":
                window = self._window_by_handle(session.window_handle)
                if window is None:
                    return CommandResult.failure(
                        ErrorCode.SESSION_NOT_FOUND, "Desktop window not found"
                    )
                state = inputs.get("state", "normal")
                import ctypes
                user32 = ctypes.windll.user32
                hwnd = int(session.window_handle)
                SW = {"maximized": 3, "minimized": 6, "normal": 1}
                user32.ShowWindow(hwnd, SW.get(state, 1))
                return CommandResult.success(
                    effects=[
                        EffectRecord.committed(
                            invocation,
                            kind=EffectKind.UNSAFE_WRITE,
                            resource=f"desktop.session:{session_id}:window:{session.window_handle}",
                            details={"operation": "setWindowState", "state": state},
                        )
                    ]
                )
            if command == "desktop.setWindowVisible":
                import ctypes
                user32 = ctypes.windll.user32
                hwnd = int(session.window_handle)
                visible = inputs.get("visible", True)
                user32.ShowWindow(hwnd, 5 if visible else 0)
                return CommandResult.success(
                    effects=[
                        EffectRecord.committed(
                            invocation,
                            kind=EffectKind.UNSAFE_WRITE,
                            resource=f"desktop.session:{session_id}:window:{session.window_handle}",
                            details={"operation": "setWindowVisible", "visible": visible},
                        )
                    ]
                )
            if command == "desktop.moveWindow":
                import ctypes
                user32 = ctypes.windll.user32
                hwnd = int(session.window_handle)
                rect = ctypes.wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
                w = rect.right - rect.left
                h = rect.bottom - rect.top
                user32.MoveWindow(hwnd, int(inputs["x"]), int(inputs["y"]), w, h, True)
                return CommandResult.success(
                    effects=[
                        EffectRecord.committed(
                            invocation,
                            kind=EffectKind.UNSAFE_WRITE,
                            resource=f"desktop.session:{session_id}:window:{session.window_handle}",
                            details={"operation": "moveWindow"},
                        )
                    ]
                )
            if command == "desktop.resizeWindow":
                import ctypes
                user32 = ctypes.windll.user32
                hwnd = int(session.window_handle)
                rect = ctypes.wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
                x = rect.left
                y = rect.top
                user32.MoveWindow(hwnd, x, y, int(inputs["width"]), int(inputs["height"]), True)
                return CommandResult.success(
                    effects=[
                        EffectRecord.committed(
                            invocation,
                            kind=EffectKind.UNSAFE_WRITE,
                            resource=f"desktop.session:{session_id}:window:{session.window_handle}",
                            details={"operation": "resizeWindow"},
                        )
                    ]
                )
            if command == "desktop.getWindowTitle":
                import ctypes
                user32 = ctypes.windll.user32
                hwnd = int(session.window_handle)
                buf = ctypes.create_unicode_buffer(512)
                user32.GetWindowTextW(hwnd, buf, 512)
                title = buf.value
                cls_buf = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, cls_buf, 256)
                class_name = cls_buf.value
                return CommandResult.success(
                    outputs={
                        "title": title,
                        "className": class_name,
                        "processId": session.process_id,
                    },
                    effects=[
                        EffectRecord.committed(
                            invocation,
                            kind=EffectKind.READ,
                            resource=f"desktop.session:{session_id}:window:{session.window_handle}",
                            details={"operation": "getWindowTitle"},
                        )
                    ],
                )
            if command == "desktop.findElement":
                locator = DesktopLocator.model_validate(inputs["locator"])
                window = self._window_by_handle(session.window_handle)
                if window is None:
                    return CommandResult.failure(
                        ErrorCode.SESSION_NOT_FOUND, "Desktop window not found"
                    )
                timeout_ms = int(inputs.get("timeoutMs") or 0)
                deadline = time.monotonic() + timeout_ms / 1000.0
                while True:
                    try:
                        matches = self._find(window, locator)
                    except COMError:
                        matches = []
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
                            details={
                                "locator": locator.model_dump(by_alias=True),
                                "matchedCount": 0,
                            },
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
                mode = inputs.get("mode", "simulateHuman")
                text_val = str(inputs["text"])
                append = inputs.get("append", False)
                key_interval = inputs.get("keyIntervalMs", 50)
                click_first = inputs.get("clickBeforeInput", False)
                post_delay = inputs.get("postDelayMs", 0)

                if click_first:
                    if hasattr(element, "click_input"):
                        element.click_input()
                    else:
                        element.invoke()
                    time.sleep(0.05)

                if mode == "clipboard":
                    if hasattr(element, "set_focus"):
                        element.set_focus()
                    import win32clipboard
                    import win32con
                    win32clipboard.OpenClipboard()
                    try:
                        win32clipboard.EmptyClipboard()
                        win32clipboard.SetClipboardText(
                            text_val, win32con.CF_UNICODETEXT
                        )
                    finally:
                        win32clipboard.CloseClipboard()
                    import pywinauto
                    pywinauto.keyboard.send_keys("^v")
                elif mode == "simulateHuman":
                    if hasattr(element, "set_focus"):
                        element.set_focus()
                    if append and hasattr(element, "get_value"):
                        current = element.get_value()
                        text_val = current + text_val
                    if hasattr(element, "type_keys"):
                        element.type_keys(
                            text_val, with_spaces=True,
                            pause=key_interval / 1000.0,
                        )
                    else:
                        element.set_edit_text(text_val)
                else:
                    if hasattr(element, "set_edit_text"):
                        if append:
                            current = element.get_edit_text() or ""
                            element.set_edit_text(current + text_val)
                        else:
                            element.set_edit_text(text_val)

                if post_delay > 0:
                    time.sleep(post_delay / 1000)

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
                click_type = inputs.get("clickType", "single")
                button = inputs.get("button", "left")
                modifiers = inputs.get("modifiers", [])
                post_delay = inputs.get("postDelayMs", 0)

                if hasattr(element, "click_input"):
                    click_kwargs: dict = {"button": button}
                    if click_type == "double":
                        click_kwargs["click_count"] = 2
                    if modifiers:
                        import pywinauto
                        _MOD_MAP = {
                            "Alt": "menu", "Ctrl": "control",
                            "Shift": "shift", "Win": "win",
                        }
                        for mod in modifiers:
                            pywinauto.keyboard.key_down(
                                _MOD_MAP.get(mod, mod)
                            )
                        try:
                            element.click_input(**click_kwargs)
                        finally:
                            for mod in reversed(modifiers):
                                pywinauto.keyboard.key_up(
                                    _MOD_MAP.get(mod, mod)
                                )
                    else:
                        element.click_input(**click_kwargs)
                else:
                    element.invoke()

                if post_delay > 0:
                    time.sleep(post_delay / 1000)

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
            if command == "desktop.getSelectedText":
                try:
                    sel = element.iface_value.GetSelection()
                    if sel:
                        text = sel[0].iface_value.GetCurrentValue() or ""
                    else:
                        text = ""
                except Exception:
                    text = ""
                return CommandResult.success(
                    outputs={"text": text},
                    effects=[
                        EffectRecord.committed(
                            invocation,
                            kind=EffectKind.READ,
                            resource=resource,
                            details={"operation": "getSelectedText"},
                        )
                    ],
                )
            if command == "desktop.screenshot":
                save_path = str(inputs["savePath"])
                try:
                    bmp = element.iface_value.GetCurrentPropertyValue(30013)
                    if bmp:
                        import io

                        from PIL import Image
                        img = Image.open(io.BytesIO(bytes(bmp)))
                        img.save(save_path)
                    else:
                        raise RuntimeError("No image data")
                except Exception:
                    window = self._window_by_handle(session.window_handle)
                    if window:
                        window.capture_as_image().save(save_path)
                return CommandResult.success(
                    outputs={"filePath": save_path},
                    effects=[
                        EffectRecord.committed(
                            invocation,
                            kind=EffectKind.IDEMPOTENT_WRITE,
                            resource=resource,
                            details={"operation": "screenshot", "filePath": save_path},
                        )
                    ],
                )
            if command == "desktop.select":
                value = str(inputs["value"])
                select_by = inputs.get("selectBy", "value")
                try:
                    sel = element.iface_selection
                    if select_by == "index":
                        sel.Select(int(value))
                    elif select_by == "label":
                        items = sel.GetCurrentSelection()
                        for item in items:
                            if item.GetCurrentPropertyValue(30005) == value:
                                item.Select()
                                break
                    else:
                        items = sel.GetCurrentSelection()
                        for item in items:
                            if item.GetCurrentPropertyValue(30006) == value:
                                item.Select()
                                break
                except Exception:
                    pass
                return CommandResult.success(
                    effects=[
                        EffectRecord.committed(
                            invocation,
                            kind=EffectKind.UNSAFE_WRITE,
                            resource=resource,
                            details={"operation": "selectOption"},
                        )
                    ]
                )
            if command == "desktop.drag":
                target_x = int(inputs["targetX"])
                target_y = int(inputs["targetY"])
                try:
                    rect = element.rectangle()
                    start_x = rect.left + (rect.right - rect.left) // 2
                    start_y = rect.top + (rect.bottom - rect.top) // 2
                except Exception:
                    start_x, start_y = 0, 0
                import ctypes
                user32 = ctypes.windll.user32
                user32.SetCursorPos(start_x, start_y)
                user32.mouse_event(0x0002, 0, 0, 0, 0)
                steps = 10
                for i in range(1, steps + 1):
                    cx = start_x + (target_x - start_x) * i // steps
                    cy = start_y + (target_y - start_y) * i // steps
                    user32.SetCursorPos(cx, cy)
                    time.sleep(0.01)
                user32.mouse_event(0x0004, 0, 0, 0, 0)
                return CommandResult.success(
                    effects=[
                        EffectRecord.committed(
                            invocation,
                            kind=EffectKind.UNSAFE_WRITE,
                            resource=resource,
                            details={"operation": "drag"},
                        )
                    ]
                )
            return CommandResult.failure(
                ErrorCode.COMMAND_NOT_FOUND, f"Unsupported command: {command}"
            )
        finally:
            pythoncom.CoUninitialize()

    def _find_windows_by_title(
        self, title: str, process_id: int | None, match_mode: str
    ) -> list[Any]:
        """通过 title 查找窗口，优先走 Win32 路径避免全桌面 UIA 枚举。

        exact 模式：FindWindowW（毫秒级）→ UIAWrapper 单窗口构造。
        contains/regex 模式：EnumWindows 枚举句柄 → 逐个 UIAWrapper。
        两种路径都不触发 Desktop(backend="uia").windows() 全桌面遍历，
        避免慢 UIA provider（游戏等）导致首次初始化 ~60s 阻塞。
        """
        from pywinauto.controls.uiawrapper import UIAWrapper
        from pywinauto.uia_element_info import UIAElementInfo

        user32 = ctypes.windll.user32

        if match_mode == "exact":
            hwnd = user32.FindWindowW(None, title)
            if not hwnd:
                return []
            if process_id is not None:
                owner_pid = ctypes.c_ulong()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner_pid))
                if owner_pid.value != process_id:
                    return []
            try:
                return [UIAWrapper(UIAElementInfo(hwnd))]
            except Exception:
                return []

        # contains / regex — EnumWindows 枚举句柄，逐个包装
        handles: list[int] = []

        def _on_window(hwnd: Any, _lparam: Any) -> bool:
            buf = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, buf, 512)
            win_title = buf.value
            if not user32.IsWindowVisible(hwnd):
                return True
            matched = False
            if match_mode == "contains":
                matched = title in win_title
            elif match_mode == "regex":
                import re
                matched = bool(re.search(title, win_title))
            if not matched:
                return True
            if process_id is not None:
                owner_pid = ctypes.c_ulong()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner_pid))
                if owner_pid.value != process_id:
                    return True
            handles.append(int(hwnd) if isinstance(hwnd, int) else int(str(hwnd), 0))
            return True

        callback = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(
            _on_window
        )
        user32.EnumWindows(callback, None)
        windows: list[Any] = []
        for h in handles:
            try:
                windows.append(UIAWrapper(UIAElementInfo(h)))
            except Exception:
                continue
        return windows

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
        """直接从 HWND 构造 UIAWrapper，避免 Desktop(backend="uia").windows()
        触发全桌面枚举（慢 UIA provider 可达 ~60s）。"""
        from pywinauto.controls.uiawrapper import UIAWrapper
        from pywinauto.uia_element_info import UIAElementInfo

        try:
            return UIAWrapper(UIAElementInfo(handle))
        except Exception:
            return None

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
