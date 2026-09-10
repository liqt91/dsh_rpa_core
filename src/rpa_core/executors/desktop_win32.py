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

from .base import CommandExecutor, resolve_session_id


@dataclass
class _Win32Session:
    process_id: int
    window_handle: int
    elements: dict[str, dict[str, Any]]


class Win32DesktopExecutor(CommandExecutor):
    def __init__(self, operation_timeout_seconds: float = 15.0):
        self.operation_timeout_seconds = operation_timeout_seconds
        self._sessions: dict[str, _Win32Session] = {}
        # 记录最近激活的会话，供省略 sessionId 的命令默认使用
        self._last_session_id: str | None = None
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
        _no_session_commands = (
            "desktop.win32.attachWindow",
            "desktop.win32.getWindowList",
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
                        max_workers=1, thread_name_prefix="rpa-desktop-win32"
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
        command = invocation.command_id
        inputs = invocation.inputs
        if command == "desktop.win32.attachWindow":
            title = str(inputs.get("title") or "")
            class_name = inputs.get("className")
            handle = inputs.get("handle")
            process_id = inputs.get("processId")
            match_mode = inputs.get("matchMode", "exact")
            timeout_ms = int(inputs.get("timeoutMs") or 0)
            deadline = time.monotonic() + timeout_ms / 1000.0
            while True:
                if handle is not None:
                    windows = Desktop(backend="win32").windows(handle=int(handle))
                else:
                    all_wins = Desktop(backend="win32").windows()
                    if title:
                        all_wins = self._filter_windows(all_wins, title, match_mode)
                    if class_name:
                        all_wins = [
                            w for w in all_wins if w.class_name() == class_name
                        ]
                    if process_id is not None:
                        all_wins = [
                            w for w in all_wins if w.process_id() == process_id
                        ]
                    windows = all_wins
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
            # 新建的会话即默认会话，后续命令可省略 sessionId
            self._last_session_id = session_id
            return CommandResult.success(
                outputs={
                    "sessionId": session_id,
                    "resourceType": "windowHandle",
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
        if command == "desktop.win32.closeSession":
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
                        resource=f"desktop.win32.session:{session_id}:window:{session.window_handle}",
                        details={"operation": "closeSession"},
                    )
                    ]
                )

        if command == "desktop.win32.getWindowList":
            import ctypes
            import re
            title_pattern = inputs.get("titlePattern")
            match_mode = inputs.get("matchMode", "contains")
            user32 = ctypes.windll.user32
            result_windows = []

            def _on_window(hwnd: Any, _lparam: Any) -> bool:
                if not user32.IsWindowVisible(hwnd):
                    return True
                buf = ctypes.create_unicode_buffer(512)
                user32.GetWindowTextW(hwnd, buf, 512)
                win_title = buf.value
                if not win_title:
                    return True
                if title_pattern:
                    if match_mode == "exact" and win_title != title_pattern:
                        return True
                    elif match_mode == "contains" and title_pattern not in win_title:
                        return True
                    elif match_mode == "regex" and not re.search(title_pattern, win_title):
                        return True
                cls_buf = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, cls_buf, 256)
                pid = ctypes.c_ulong()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                result_windows.append({
                    "title": win_title,
                    "className": cls_buf.value,
                    "processId": pid.value,
                })
                return True

            callback = ctypes.WINFUNCTYPE(
                ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p
            )(_on_window)
            user32.EnumWindows(callback, None)
            return CommandResult.success(
                outputs={"windows": result_windows},
                effects=[
                    EffectRecord.committed(
                        invocation,
                        kind=EffectKind.READ,
                        resource="desktop.win32.windows",
                        details={"operation": "getWindowList", "count": len(result_windows)},
                    )
                ],
            )

        if command == "desktop.win32.activateWindow":
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = int(session.window_handle)
            user32.SetForegroundWindow(hwnd)
            return CommandResult.success(
                effects=[
                    EffectRecord.committed(
                        invocation,
                        kind=EffectKind.UNSAFE_WRITE,
                        resource=f"desktop.win32.session:{session_id}:window:{session.window_handle}",
                        details={"operation": "activateWindow"},
                    )
                ]
            )

        if command == "desktop.win32.setWindowState":
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = int(session.window_handle)
            state = inputs.get("state", "normal")
            SW = {"maximized": 3, "minimized": 6, "normal": 1}
            user32.ShowWindow(hwnd, SW.get(state, 1))
            return CommandResult.success(
                effects=[
                    EffectRecord.committed(
                        invocation,
                        kind=EffectKind.UNSAFE_WRITE,
                        resource=f"desktop.win32.session:{session_id}:window:{session.window_handle}",
                        details={"operation": "setWindowState", "state": state},
                    )
                ]
            )

        if command == "desktop.win32.setWindowVisible":
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
                        resource=f"desktop.win32.session:{session_id}:window:{session.window_handle}",
                        details={"operation": "setWindowVisible", "visible": visible},
                    )
                ]
            )

        if command == "desktop.win32.moveWindow":
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
                        resource=f"desktop.win32.session:{session_id}:window:{session.window_handle}",
                        details={"operation": "moveWindow"},
                    )
                ]
            )

        if command == "desktop.win32.resizeWindow":
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = int(session.window_handle)
            rect = ctypes.wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            user32.MoveWindow(
                hwnd, rect.left, rect.top,
                int(inputs["width"]), int(inputs["height"]), True
            )
            return CommandResult.success(
                effects=[
                    EffectRecord.committed(
                        invocation,
                        kind=EffectKind.UNSAFE_WRITE,
                        resource=f"desktop.win32.session:{session_id}:window:{session.window_handle}",
                        details={"operation": "resizeWindow"},
                    )
                ]
            )

        if command == "desktop.win32.getWindowTitle":
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
                        resource=f"desktop.win32.session:{session_id}:window:{session.window_handle}",
                        details={"operation": "getWindowTitle"},
                    )
                ],
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
            mode = inputs.get("mode", "simulateHuman")
            text_val = str(inputs["text"])
            append = inputs.get("append", False)
            key_interval = inputs.get("keyIntervalMs", 50)
            click_first = inputs.get("clickBeforeInput", False)
            post_delay = inputs.get("postDelayMs", 0)

            if click_first:
                if hasattr(element, "click_input"):
                    element.click_input()
                elif hasattr(element, "invoke"):
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
        if command == "desktop.win32.click":
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
                        pywinauto.keyboard.key_down(_MOD_MAP.get(mod, mod))
                    try:
                        element.click_input(**click_kwargs)
                    finally:
                        for mod in reversed(modifiers):
                            pywinauto.keyboard.key_up(_MOD_MAP.get(mod, mod))
                else:
                    element.click_input(**click_kwargs)
            elif hasattr(element, "invoke"):
                element.invoke()
            else:
                element.click_input()

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
        if command == "desktop.win32.getSelectedText":
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
        if command == "desktop.win32.screenshot":
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
        if command == "desktop.win32.select":
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
        if command == "desktop.win32.drag":
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

    @staticmethod
    def _filter_windows(windows: list[Any], title: str, match_mode: str) -> list[Any]:
        import re
        if match_mode == "contains":
            return [w for w in windows if title in (w.window_text() or "")]
        if match_mode == "regex":
            pattern = re.compile(title)
            return [w for w in windows if pattern.search(w.window_text() or "")]
        return [w for w in windows if (w.window_text() or "") == title]

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
