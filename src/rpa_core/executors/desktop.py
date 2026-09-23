import asyncio
import ctypes
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

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
    UIA_SESSION_RESOURCE_PREFIX,
    CommandExecutor,
    click_with_modifiers,
    desktop_sessions_from_scopes,
    desktop_window_alive,
    plan_click_for_element,
    resolve_session_id,
    wait_for_element,
)


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
            # getWindowList 曾被声明为免会话，但实现把会话解析放在命令分派之前、
            # 无会话必报 SESSION_NOT_FOUND（实测见 M38 任务单 §1.5/§1.7）——
            # S2.3 把声明对齐实现：它和其它命令一样需要会话。
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
                # 命令声明的元素等待预算必须等得到：操作超时是防挂死的兜底，
                # 不能反过来把用户显式给出的等待预算掐断（否则 30s 的等待会在 15s 报 TIMEOUT,
                # 而用户看到的原因不是「元素没出现」）。字面量元组是为参数消费门禁留的切片锚点。
                if invocation.command_id in ("desktop.click", "desktop.getText", "desktop.input"):
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
        import pythoncom
        from comtypes import COMError

        pythoncom.CoInitialize()
        try:
            command = invocation.command_id
            inputs = invocation.inputs
            if command == "desktop.attachWindow":
                # 至少要有一个筛选条件：一个都不给会退化成「枚举全桌面」，
                # 报错落在 ELEMENT_AMBIGUOUS（把输入错误伪装成「窗口不唯一」）。
                # 口径与 win32 侧一致（见 tests/contract/test_desktop_attach_window.py）。
                title = str(inputs.get("title") or "")
                class_name = inputs.get("className")
                class_name_re = inputs.get("classNameRe")
                if class_name and class_name_re:
                    return CommandResult.failure(
                        ErrorCode.INVALID_INPUT,
                        "className and classNameRe are mutually exclusive",
                    )
                if not title and not class_name and not class_name_re:
                    return CommandResult.failure(
                        ErrorCode.INVALID_INPUT,
                        "title, className or classNameRe is required",
                    )
                process_id = inputs.get("processId")
                match_mode = inputs.get("matchMode", "exact")
                timeout_ms = int(inputs.get("timeoutMs") or 0)
                deadline = time.monotonic() + timeout_ms / 1000.0
                while True:
                    windows = self._find_windows_by_title(
                        title, process_id, match_mode, class_name, class_name_re
                    )
                    if len(windows) == 1:
                        break
                    if len(windows) > 1:
                        return CommandResult.failure(
                            ErrorCode.ELEMENT_AMBIGUOUS,
                            "Desktop window matched multiple targets",
                            details={
                                "title": title,
                                "className": class_name,
                                "classNameRe": class_name_re,
                                "matchedCount": len(windows),
                            },
                        )
                    if time.monotonic() >= deadline:
                        return CommandResult.failure(
                            ErrorCode.ELEMENT_NOT_FOUND,
                            "Desktop window did not match",
                            details={
                                "title": title,
                                "className": class_name,
                                "classNameRe": class_name_re,
                                "matchedCount": 0,
                            },
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
                try:
                    locator = DesktopLocator.model_validate(inputs["locator"])
                except ValidationError as exc:
                    # 结构错误（如 UIA 身份字段一个都不给）是**用户输入问题**，
                    # 不该被外层 catch-all 渲染成 EXECUTOR_FAILED（像内部崩了）。
                    return CommandResult.failure(
                        ErrorCode.INVALID_INPUT,
                        str(exc.errors()[0].get("msg", exc)) if exc.errors() else str(exc),
                    )
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
                            # 定位器进 effect details（M38 S2.1）：`elementId` 是进程内
                            # 标识，「继续」= 新进程（ADR 0005），不把「这个 id 指向什么」
                            # 写进快照，暂停点之后引用它的命令就无从还原。
                            # 放 details 而不是 outputs：快照里「资源绑定」的权威位置
                            # 一向是 resource + details（M21 浏览器侧的 tabId 就在这里），
                            # 且 outputs 是面向用户与表达式的公开面（x-outputs 会进 GUI），
                            # 把内部定位器塞进去等于把它升格成契约。
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
            resource = f"desktop.session:{session_id}:element:{element_id}"
            window = self._window_by_handle(session.window_handle)
            if window is None:
                return CommandResult.failure(
                    ErrorCode.SESSION_NOT_FOUND, "Desktop window not found"
                )
            element_locator = DesktopLocator.model_validate(locator_data)
            # 只有会等元素的命令才消费 timeoutMs（等待目标元素存在的最长时间）。
            # 字面量元组不只是风格：参数消费门禁按 `command in (...)` 的字面量切片做审计，
            # 换成变量它会看不见（见 .harness/scripts/check_param_consumption.py 的已知盲区）。
            wait_budget_ms = 0
            if command in ("desktop.click", "desktop.getText", "desktop.input"):
                wait_budget_ms = int(inputs.get("timeoutMs") or 0)
            found = wait_for_element(lambda: self._find(window, element_locator), wait_budget_ms)
            if not found.matched:
                return CommandResult.failure(
                    ErrorCode.ELEMENT_NOT_FOUND,
                    "Desktop element not found",
                    details=found.details(),
                )
            element = found.matches[0]
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
                        # pywinauto 的 click_input 没有 click_count 参数（基础包装类与
                        # controls/common_controls 的包装类都没有），此前写 click_count=2
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
            if command == "desktop.getText":
                value = self._read_element_text(element)
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
                # SelectionPattern 自身没有 Select；选中动作在**列表项**的
                # SelectionItemPattern 上（iface_selection_item.Select()）。
                # label/value 分支也不能用 GetCurrentSelection()——那返回的是
                # 「当前已选中项」不是全部选项，旧实现把三个 selectBy 分支都变成
                # 静默假成功（实测见 M38 任务单 §1.5）。改：枚举 ListItem 子项
                # 逐个匹配后 Select()；无子项 / 无匹配显式报错，不再吞错。
                items = element.descendants(control_type="ListItem")
                target: Any | None = None
                if select_by == "index":
                    try:
                        idx = int(value)
                    except ValueError:
                        idx = -1
                    if 0 <= idx < len(items):
                        target = items[idx]
                else:
                    # label 与 value 都按列表项文本匹配：包装对象没有
                    # GetCurrentPropertyValue（那是裸 IUIAutomationElement 的方法），
                    # 而 WinForms ListItem 的 Value 属性实测恒空（30006 = ''），
                    # 语义上列表项的 value 就是它的文本。
                    for item in items:
                        try:
                            if item.window_text() == value:
                                target = item
                                break
                        except Exception:
                            continue
                if target is None:
                    return CommandResult.failure(
                        ErrorCode.ELEMENT_NOT_FOUND,
                        f"no list item matches selectBy={select_by!r}, value={value!r}",
                        details={
                            "operation": "selectOption",
                            "enumerableItems": len(items),
                        },
                    )
                target.iface_selection_item.Select()
                # 选中态读回（证据面）：UIA 的 Select() 改的是 ListBox 的选中项，
                # 但**不触发** WinForms 的 SelectedIndexChanged（实测 round5：
                # 选中=beta 而 listStatus 回显仍是 'none'）——所以状态回显 Label
                # 不是这条命令的有效读侧；把选中项读回写进 details 才能被断言。
                selected_name = ""
                try:
                    sel_items = element.iface_selection.GetCurrentSelection()
                    if sel_items is not None and sel_items.Length:
                        selected_name = str(sel_items.GetElement(0).CurrentName or "")
                except Exception:
                    pass
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
        self,
        title: str,
        process_id: int | None,
        match_mode: str,
        class_name: str | None = None,
        class_name_re: str | None = None,
    ) -> list[Any]:
        """通过 title / className / classNameRe / processId 查找窗口，优先走 Win32 路径
        避免全桌面 UIA 枚举。

        过滤口径与 `desktop.win32.attachWindow`（`_filter_windows` + className/processId）
        对齐：**无条件等值比较**（className 不支持 contains/regex，`matchMode` 只作用于 title），
        `title` / `className` / `classNameRe` / `processId` 之间是 AND。`classNameRe` 是
        `search`（非整串锚定），用于跨机器匹配含动态哈希的类名。

        exact 模式：FindWindowW（毫秒级）→ UIAWrapper 单窗口构造。
        contains/regex 模式：EnumWindows 枚举句柄 → 逐个 UIAWrapper。
        两种路径都不触发 Desktop(backend="uia").windows() 全桌面遍历，
        避免慢 UIA provider（游戏等）导致首次初始化 ~60s 阻塞。

        **exact 路径的固有局限**：`FindWindowW` 只返回第一个匹配句柄，因此同标题同类名的多个
        窗口不会被发现（不报 ELEMENT_AMBIGUOUS，静默附着第一个）。这是有意取舍——exact 的价值
        就是绕开全桌面枚举。需要歧义检测请用 contains/regex（走 EnumWindows 看全量候选）。

        title 为空串时：exact 模式改用 FindWindowW(class_name, None)（按类名找窗口，
        与 win32 侧「只给 className 也能 attach」对齐）；contains/regex 模式下
        「空串 in 标题」恒真，等价于不按标题过滤。

        `classNameRe` 存在时**不走 exact 的 FindWindowW 类名参数**（它只收字面类名），
        直接走 EnumWindows 逐句柄正则过滤。
        """
        from pywinauto.controls.uiawrapper import UIAWrapper
        from pywinauto.uia_element_info import UIAElementInfo

        user32 = ctypes.windll.user32

        def _class_matches(hwnd: int) -> bool:
            """类名过滤（等值 or 正则 search），与 win32 侧同口径。"""
            if not class_name and not class_name_re:
                return True
            buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, buf, 256)
            if class_name_re:
                import re
                return bool(re.search(class_name_re, buf.value))
            return buf.value == class_name

        if match_mode == "exact" and not class_name_re:
            # 类名过滤交给 FindWindowW 的 lpClassName（比事后 GetClassNameW 更省），
            # 但它**不做逐字节等值**：Win32 会按「类名或类名前缀」匹配，且忽略大小写。
            # 因此拿到的句柄还要用 GetClassNameW 复核一次，保证与 win32 侧口径一致。
            hwnd = user32.FindWindowW(class_name or None, title or None)
            if not hwnd:
                return []
            if not _class_matches(hwnd):
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

        # contains / regex / classNameRe — EnumWindows 枚举句柄，逐个包装
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
            else:
                # exact 落到这条 EnumWindows 路径，只可能是「给了 classNameRe」——
                # 此时 title（若非空）必须**等值**才算命中；空 title 等价于不按标题过滤
                # （与 FindWindowW(None, None) 的「不筛」语义对齐）。
                # 漏掉这一支会让 exact+classNameRe 退化成「标题 contains」，
                # 标题略有不符（大小写/后缀）就静默匹配不到。
                matched = not title or title == win_title
            if not matched:
                return True
            if not _class_matches(hwnd):
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
    def _read_element_text(element: Any) -> str:
        """读元素文本：有 ValuePattern 时优先走它。

        `window_text()` 对没有 AccessibleName 的 WinForms Edit / ListBox 会按 MSAA
        的 labeled-by 规则回落到**相邻 Label** 的文本（实测 `queryInput` 读到旁边的
        `'Name'`、`readOnlyNote` 读到 `'Drag'`，M38 任务单 §1.5）——Edit 的真实文本
        在 Value 属性里，先问 ValuePattern，不支持该模式的控件（Label 等）再回落。
        """
        try:
            # 注意取 CurrentValue **属性**——comtypes 生成的 IUIAutomationValuePattern
            # 没有 GetCurrentValue() 方法（实测 AttributeError，M38 任务单 §1.7）。
            return str(element.iface_value.CurrentValue)
        except Exception:
            return element.window_text()

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

    def restore_from_scopes(self, scopes: Any) -> None:
        """resume 时按快照重建桌面会话（M38 S2.1 跨进程续接，由 `ExecutorRegistry` 调用）。

        暂停 = 干净收口 + 进程退出（ADR 0005），「继续」是 `rpa-core resume` 起新进程
        （GUI 的「继续」在暂停已落地时就走 `RunManager.resume` 这条路）。**窗口本身不随
        run 进程退出而消失**，消失的只是新进程里那张句柄表——不还原的话，暂停点之后第一个
        会话类命令会以 `SESSION_NOT_FOUND` 失败，而用户的窗口明明还在那儿。

        会话与**元素定位器缓存**都要还原：`elementId` 是 `findElement` 在旧进程里发出的，
        它在快照里（`findElement` 的 effect details 带上了 `locator`），但新进程的
        `session.elements` 是空的——只补会话的话，暂停点之后引用旧 `elementId` 的命令
        会 ELEMENT_NOT_FOUND（实测：补上会话后同一个节点正好从 SESSION_NOT_FOUND 变成这个码）。

        窗口已不存在（或句柄被系统回收复用给了别的进程）时不还原：后续命令照旧报
        `SESSION_NOT_FOUND`，错误码与浏览器侧一致，但不会把命令指到别人的窗口上——
        这条与浏览器侧**刻意不同**，理由见 `base.desktop_window_alive`。
        """
        sessions, last_sid = desktop_sessions_from_scopes(
            scopes, resource_prefix=UIA_SESSION_RESOURCE_PREFIX
        )
        for session_id, snapshot in sessions.items():
            if not desktop_window_alive(snapshot.process_id, snapshot.window_handle):
                continue
            self._sessions[session_id] = _DesktopSession(
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
