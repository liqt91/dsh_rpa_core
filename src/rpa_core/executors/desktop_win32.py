import asyncio
import re
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from pywinauto import Desktop
from pywinauto.keyboard import send_keys

from rpa_core.model.command import (
    WAIT_BUDGET_SLACK_SECONDS,
    CommandInvocation,
    CommandResult,
    EffectKind,
    EffectRecord,
)
from rpa_core.model.desktop import DesktopLocator
from rpa_core.model.errors import ErrorCode

from .base import (
    WIN32_SESSION_RESOURCE_PREFIX,
    CommandExecutor,
    click_with_modifiers,
    desktop_sessions_from_scopes,
    desktop_window_alive,
    plan_click_for_element,
    resolve_session_id,
    wait_for_element,
)


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
            # getWindowList 声明对齐实现（同 uia 侧说明，M38 任务单 §1.7）。
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
                # 命令声明的元素等待预算必须等得到：操作超时是防挂死的兜底，不能反过来把
                # 用户显式给出的等待预算掐断（否则 30s 的等待会在 15s 报 TIMEOUT，而用户看到的
                # 原因不是「元素没出现」）。字面量元组是为参数消费门禁留的切片锚点。
                if invocation.command_id in (
                    "desktop.win32.click",
                    "desktop.win32.getText",
                    "desktop.win32.input",
                ):
                    operation_timeout = max(
                        operation_timeout,
                        int(invocation.inputs.get("timeoutMs") or 0) / 1000.0
                        + WAIT_BUDGET_SLACK_SECONDS,
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
            class_name_re = inputs.get("classNameRe")
            handle = inputs.get("handle")
            process_id = inputs.get("processId")
            match_mode = inputs.get("matchMode", "exact")
            if class_name and class_name_re:
                return CommandResult.failure(
                    ErrorCode.INVALID_INPUT,
                    "className and classNameRe are mutually exclusive",
                )
            # 一个筛选条件都不给 = 枚举全桌面，报错会落在 ELEMENT_AMBIGUOUS（原因误导）；
            # 与 uia 侧同口径，前置报 INVALID_INPUT（见 test_desktop_attach_window.py）
            if (
                handle is None
                and not title
                and not class_name
                and not class_name_re
                and process_id is None
            ):
                return CommandResult.failure(
                    ErrorCode.INVALID_INPUT,
                    "title, className, classNameRe, handle or processId is required",
                )
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
                    if class_name_re:
                        pattern = re.compile(class_name_re)
                        all_wins = [
                            w for w in all_wins if pattern.search(w.class_name() or "")
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
            try:
                locator = DesktopLocator.model_validate(inputs["locator"])
            except ValidationError as exc:
                # 模型层把「两个类名字段同时给」等结构错误抛成 ValidationError，
                # 外层 catch-all 会把它渲染成 EXECUTOR_FAILED（像内部崩了）。
                # 这是**用户的输入问题**，显式报 INVALID_INPUT，与 attachWindow 的前置检查同口径。
                return CommandResult.failure(
                    ErrorCode.INVALID_INPUT,
                    str(exc.errors()[0].get("msg", exc)) if exc.errors() else str(exc),
                )
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
                        # 定位器进 effect details（M38 S2.1）：理由见 uia 侧同处的注释
                        # ——续跑要靠它还原元素缓存，而 details 是快照里资源绑定的权威位置。
                        details={
                            "operation": "findElement",
                            "locator": locator.model_dump(by_alias=True),
                        },
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
        # 只有会等元素的命令才消费 timeoutMs（等待目标元素存在的最长时间）。
        # 字面量元组不只是风格：参数消费门禁按 `command in (...)` 的字面量切片做审计，
        # 换成变量它会看不见（见 .harness/scripts/check_param_consumption.py 的已知盲区）。
        wait_budget_ms = 0
        if command in ("desktop.win32.click", "desktop.win32.getText", "desktop.win32.input"):
            wait_budget_ms = int(inputs.get("timeoutMs") or 0)
        found = wait_for_element(lambda: self._find(window, locator), wait_budget_ms)
        if not found.matched:
            return CommandResult.failure(
                ErrorCode.ELEMENT_NOT_FOUND,
                "Desktop element not found",
                details=found.details(),
            )
        element = self._pick(found.matches, locator.found_index)
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

            try:
                plan = plan_click_for_element(
                    element,
                    simulate_human=bool(inputs.get("simulateHuman", True)),
                    click_position=str(inputs.get("clickPosition") or "center"),
                    click_type=click_type,
                    button=button,
                    modifiers=modifiers,
                )
            except ValueError as exc:
                # 参数互斥 / 无路可走 → 显式失败，不静默挑一条路走
                return CommandResult.failure(ErrorCode.INVALID_INPUT, str(exc))

            if plan.path == "invoke":
                element.invoke()
            else:
                click_kwargs: dict = {"button": button}
                if click_type == "double":
                    # pywinauto 的 click_input 没有 click_count 参数，此前写 click_count=2
                    # 会直接 TypeError —— 双击在这两个后端上一直是坏的。
                    click_kwargs["double"] = True
                if plan.coords is not None:
                    click_kwargs["coords"] = plan.coords
                click_with_modifiers(element, click_kwargs, modifiers)

            if post_delay > 0:
                time.sleep(post_delay / 1000)

            return CommandResult.success(
                effects=[
                    EffectRecord.committed(
                        invocation,
                        kind=EffectKind.UNSAFE_WRITE,
                        resource=resource,
                        details={"operation": "click", **plan.evidence()},
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
            # win32 包装元素没有 UIA 的 iface_* 接口族（对子控件取 iface_value 一律抛
            # 异常），旧实现 except 后静默返回空串——「恒空的成功」比显式失败更坏
            # （实测见 M38 任务单 §1.5）。现改走**原生消息**：pywinauto 的
            # ListBoxWrapper / ComboBoxWrapper 已封装 LB_GETCURSEL / LB_GETTEXT
            # 与跨进程缓冲区，实测能直接吃 WinForms 的 ListBox / ComboBox
            # （`.harness/spike/probe_win32_native_select_e2e.py`）。
            from pywinauto.controls.win32_controls import (
                ComboBoxWrapper,
                ListBoxWrapper,
            )

            if not isinstance(element, (ListBoxWrapper, ComboBoxWrapper)):
                return CommandResult.failure(
                    ErrorCode.EXECUTOR_FAILED,
                    "getSelectedText needs a ListBox or ComboBox on the win32 "
                    f"backend, got class {element.class_name()!r}",
                    details={
                        "operation": "getSelectedText",
                        "className": element.class_name(),
                    },
                )
            _, text = self._list_selection(element)
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
            # 与 getSelectedText 同因：win32 包装元素没有 iface_selection（UIA
            # SelectionPattern），旧实现 except 后照常返回 success——三个 selectBy
            # 分支全部静默假成功（实测见 M38 任务单 §1.5）。现走原生消息
            # LB_SETCURSEL / CB_SETCURSEL（pywinauto 包装类已封装跨进程缓冲区）。
            #
            # 与 uia 侧的**一个重要差别**：pywinauto 的 select() 在原生消息之后会
            # `notify_parent(LBN_SELCHANGE / CBN_SELCHANGE)`（post 一个 WM_COMMAND），
            # 所以 WinForms 的 SelectedIndexChanged **会**触发、状态回显控件会跟着变
            # （实测：listStatus 逐步从 none → list:1:beta → list:2:gamma → list:0:alpha）。
            # uia 那条 SelectionItemPattern.Select() 不触发 SelectedIndexChanged，
            # 故 uia 侧只能靠 details 读回；两者都写 `selectedItem` 以统一证据面。
            from pywinauto.controls.win32_controls import (
                ComboBoxWrapper,
                ListBoxWrapper,
            )

            value = str(inputs["value"])
            select_by = inputs.get("selectBy", "value")
            if not isinstance(element, (ListBoxWrapper, ComboBoxWrapper)):
                return CommandResult.failure(
                    ErrorCode.EXECUTOR_FAILED,
                    "select needs a ListBox or ComboBox on the win32 backend, got "
                    f"class {element.class_name()!r}",
                    details={
                        "operation": "selectOption",
                        "selectBy": select_by,
                        "className": element.class_name(),
                    },
                )
            items = element.item_texts()
            if select_by == "index":
                try:
                    index = int(value)
                except ValueError:
                    index = -1
            else:
                # label 与 value 同口径：列表项的文本就是它的值（与 uia 侧一致——
                # WinForms ListItem 的 Value 属性实测恒空）。
                index = items.index(value) if value in items else -1
            if not 0 <= index < len(items):
                return CommandResult.failure(
                    ErrorCode.ELEMENT_NOT_FOUND,
                    f"no list item matches selectBy={select_by!r}, value={value!r}",
                    details={
                        "operation": "selectOption",
                        "enumerableItems": len(items),
                    },
                )
            element.select(index)
            # 选中态读回（证据面）：与 uia 侧同一键名 `selectedItem`，
            # 断言口径因此可以跨后端复用。
            _, selected_name = self._list_selection(element)
            return CommandResult.success(
                effects=[
                    EffectRecord.committed(
                        invocation,
                        kind=EffectKind.UNSAFE_WRITE,
                        resource=resource,
                        details={
                            "operation": "selectOption",
                            "selectBy": select_by,
                            "selectedItem": selected_name,
                        },
                    )
                ],
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
        matches = window.descendants(**criteria)
        if locator.class_name_re:
            # `descendants(class_name=...)` 只做等值比较，没有正则口子；classNameRe
            # 自己按完整类名过一遍（与尾巴 #2 的定案一致：新增口子而不削弱等值）。
            pattern = re.compile(locator.class_name_re)
            matches = [m for m in matches if pattern.search(m.class_name() or "")]
        if locator.control_id is not None:
            # pywinauto 的 descendants/children 路径**不消费 control_id**
            # （children 只读 class_name/title/control_type，实测任何取值都返回
            # 全部子控件——manifest 声明的过滤条件被静默忽略，M38 任务单 §1.5；
            # findwindows.find_elements 虽然支持，但这里已拿到列表）。
            # 注意走 ElementInfo 的 control_id（property，GetDlgCtrlID）——
            # 包装元素上的 control_id 在 0.6.9 是 deprecated 的**方法**，比出来恒 False。
            matches = [
                m for m in matches if m.element_info.control_id == locator.control_id
            ]
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
    def _list_selection(element: Any) -> tuple[int, str]:
        """ListBox / ComboBox 的 `(选中索引, 选中文本)`；无选中或非列表控件 → `(-1, "")`。

        **不直接用 `selected_text()`**：它内部是 `item_texts()[selected_index()]`，
        而 `CB_GETCURSEL` / `LB_GETCURSEL` 在无选中时返回 -1，Python 的负索引会把
        它变成**最后一项**（静默错答）。`selected_indices()` 无选中时给 `(-1,)`，
        同款陷阱。这里显式取索引 + `>= 0` 边界判断。
        """
        from pywinauto.controls.win32_controls import (
            ComboBoxWrapper,
            ListBoxWrapper,
        )

        if isinstance(element, ListBoxWrapper):
            indices = element.selected_indices()
            index = indices[0] if indices else -1
        elif isinstance(element, ComboBoxWrapper):
            index = element.selected_index()
        else:
            return (-1, "")
        items = element.item_texts()
        if 0 <= index < len(items):
            return (index, items[index])
        return (-1, "")

    @staticmethod
    def _window_by_handle(handle: int) -> Any | None:
        windows = Desktop(backend="win32").windows(handle=handle)
        if len(windows) != 1:
            return None
        return windows[0]

    @staticmethod
    def _menu_select(hwnd: int, menu_path: list[str]) -> None:
        Desktop(backend="win32").window(handle=hwnd).menu_select("->".join(menu_path))

    def restore_from_scopes(self, scopes: Any) -> None:
        """resume 时按快照重建桌面会话（M38 S2.1 跨进程续接，由 `ExecutorRegistry` 调用）。

        与 uia 后端同一口径（见 `DesktopExecutor.restore_from_scopes` 的完整说明）：
        窗口不随 run 进程退出而消失，消失的是新进程里的句柄表；会话与元素定位器都要还原；
        句柄已失效/被回收时不还原，让后续命令报 `SESSION_NOT_FOUND`，
        而不是把命令指到别人的窗口上。
        """
        sessions, last_sid = desktop_sessions_from_scopes(
            scopes, resource_prefix=WIN32_SESSION_RESOURCE_PREFIX
        )
        for session_id, snapshot in sessions.items():
            if not desktop_window_alive(snapshot.process_id, snapshot.window_handle):
                continue
            self._sessions[session_id] = _Win32Session(
                snapshot.process_id, snapshot.window_handle, dict(snapshot.elements)
            )
        if last_sid is not None and last_sid in self._sessions:
            self._last_session_id = last_sid

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
