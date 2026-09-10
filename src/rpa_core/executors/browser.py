import asyncio
import time
import uuid
from typing import Any

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)
from playwright.async_api import (
    TimeoutError as PlaywrightTimeoutError,
)

from rpa_core.bsk_client import BskError, BskSessionGoneError
from rpa_core.extension_exec import CHROMIUM_FAMILY, ExtensionChannelError, channel_matches_host
from rpa_core.model.command import (
    CommandInvocation,
    CommandResult,
    EffectKind,
    EffectRecord,
)
from rpa_core.model.errors import ErrorCode

from .base import CommandExecutor
from .browser_bsk import BskSession
from .browser_ext import ExtensionExecSession

# 滚动 JS：window 全页滚动 / 元素内部滚动（position: top|bottom|point|page）
_SCROLL_WINDOW_JS = """([position, behavior, x, y]) => {
    if (position === 'top') { window.scrollTo({top: 0, behavior}); }
    else if (position === 'bottom') {
        window.scrollTo({top: document.documentElement.scrollHeight, behavior});
    }
    else if (position === 'point') { window.scrollTo({top: y, left: x, behavior}); }
    else { window.scrollBy({top: window.innerHeight, behavior}); }
    return window.scrollY;
}"""

_SCROLL_ELEMENT_JS = """(el, [position, behavior, x, y]) => {
    if (position === 'top') { el.scrollTo({top: 0, behavior}); }
    else if (position === 'bottom') { el.scrollTo({top: el.scrollHeight, behavior}); }
    else if (position === 'point') { el.scrollTo({top: y, left: x, behavior}); }
    else { el.scrollBy({top: el.clientHeight, behavior}); }
    return el.scrollTop;
}"""

# 扩展通道：命令 → page.call 原语（DOM 操作走扩展注入函数，规避页面 CSP 对 eval 的限制）
_EXT_PAGE_METHODS = {
    "browser.click": "click",
    "browser.hover": "hover",
    "browser.input": "input",
    "browser.scroll": "scroll",
    "browser.select": "select",
    "browser.check": "check",
}


class PlaywrightExecutor(CommandExecutor):
    def __init__(self, bsk_runner=None, ext_session=None):
        self._playwright: Playwright | None = None
        self._sessions: dict[str, tuple[Browser, BrowserContext, Page]] = {}
        self._bsk_sessions: dict[str, BskSession] = {}
        self._bsk_keep_open: dict[str, bool] = {}
        self._bsk_runner = bsk_runner
        # 自研扩展通道（一等公民）：会话 = 用户浏览器里的一个标签页句柄
        self._ext = ext_session or ExtensionExecSession()
        self._ext_sessions: dict[str, str] = {}  # sessionId -> tabId

    async def _ensure_runtime(self) -> Playwright:
        if self._playwright is None:
            self._playwright = await async_playwright().start()
        return self._playwright

    def _session(self, inputs: dict[str, Any]) -> tuple[str, Browser, BrowserContext, Page]:
        session_id = str(inputs.get("sessionId") or "")
        if not session_id or session_id not in self._sessions:
            raise LookupError(session_id)
        browser, context, page = self._sessions[session_id]
        return session_id, browser, context, page

    def _bsk_session(self, inputs: dict[str, Any]) -> tuple[str, BskSession]:
        session_id = str(inputs.get("sessionId") or "")
        session = self._bsk_sessions.get(session_id)
        if session is None:
            raise LookupError(session_id)
        return session_id, session

    async def _run_bsk(self, func, *args):
        """bsk 子进程调用放线程池，保持 asyncio 取消响应（规则 11）。"""
        return await asyncio.to_thread(func, *args)

    async def execute(
        self, invocation: CommandInvocation, cancellation: asyncio.Event
    ) -> CommandResult:
        if cancellation.is_set():
            return CommandResult(status="cancelled")
        started = time.monotonic()
        command = invocation.command_id
        inputs = invocation.inputs
        try:
            # 一等公民通道：sessionId 属于扩展会话 → 全程走自研扩展
            ext_session_id = str(inputs.get("sessionId") or "")
            if ext_session_id and ext_session_id in self._ext_sessions:
                return await self._execute_extension(
                    command, invocation, inputs, ext_session_id, started, cancellation
                )
            if command == "browser.navigate":
                nav_action = str(inputs.get("action") or "goto")
                if nav_action != "goto":
                    # 会话内跳转（对标影刀「跳转至新网址」：后退/前进/刷新）
                    return await self._navigate_action(nav_action, invocation, inputs, started)
                if not inputs.get("url"):
                    return CommandResult.failure(
                        ErrorCode.INVALID_INPUT, "url is required when action=goto"
                    )
                # 打开网页 = 启动浏览器 + 导航（不单独维护浏览器实例指令）
                if inputs.get("transport") == "bsk":
                    return await self._open_bsk(invocation, inputs, started)
                if await self._use_extension(inputs):
                    return await self._open_extension(invocation, inputs, started)
                runtime = await self._ensure_runtime()
                user_agent = inputs.get("userAgent")
                user_data_dir = inputs.get("userDataDir")
                launch_kwargs = {
                    "headless": bool(inputs.get("headless", True)),
                    "ignore_default_args": ["--enable-automation"],
                }
                channel = inputs.get("channel")
                if channel and channel != "chromium":
                    launch_kwargs["channel"] = str(channel)
                extra_args = inputs.get("args")
                if extra_args:
                    launch_kwargs["args"] = [str(a) for a in extra_args]
                if user_agent:
                    launch_kwargs["user_agent"] = str(user_agent)
                if user_data_dir:
                    context = await runtime.chromium.launch_persistent_context(
                        str(user_data_dir), **launch_kwargs
                    )
                    browser = None
                    page = context.pages[0] if context.pages else await context.new_page()
                else:
                    browser = await runtime.chromium.launch(**launch_kwargs)
                    context = await browser.new_context(
                        user_agent=str(user_agent) if user_agent else None
                    )
                    page = await context.new_page()
                session_id = str(uuid.uuid4())
                self._sessions[session_id] = (browser, context, page)
                timeout_ms = int(inputs.get("timeoutMs", 30_000))
                wait_until = str(inputs.get("waitUntil") or "domcontentloaded")
                try:
                    await page.goto(
                        str(inputs["url"]), wait_until=wait_until, timeout=timeout_ms
                    )
                except Exception:
                    # 导航失败必须释放本次新建的浏览器（规则 11：attempt 资源先释放再退出）
                    self._sessions.pop(session_id, None)
                    try:
                        if browser is not None:
                            await browser.close()
                        else:
                            await context.close()
                    except Exception:
                        pass
                    raise
                return CommandResult.success(
                    outputs={
                        "sessionId": session_id,
                        "url": page.url,
                        "resourceType": "webPage",
                    },
                    effects=[
                        EffectRecord.committed(
                            invocation,
                            kind=EffectKind.SESSION,
                            resource=f"browser.session:{session_id}",
                            details={"operation": "navigate", "url": page.url},
                        )
                    ],
                    diagnostics={"durationMs": int((time.monotonic() - started) * 1000)},
                )

            session_ref = str(inputs.get("sessionId") or "")
            if session_ref in self._bsk_sessions:
                return await self._execute_bsk(
                    command, invocation, inputs, session_ref, started, cancellation
                )

            session_id, browser, context, page = self._session(inputs)
            timeout_ms = int(inputs.get("timeoutMs", 30_000))

            if command == "browser.click":
                locator = page.locator(str(inputs["selector"]))
                count = await locator.count()
                if count == 0:
                    return CommandResult.failure(
                        ErrorCode.ELEMENT_NOT_FOUND,
                        "Target element did not match",
                        details={"selector": inputs["selector"], "matchedCount": 0},
                    )
                import random as _random
                click_type = inputs.get("clickType", "single")
                button = inputs.get("button", "left")
                modifiers_raw = inputs.get("modifiers", [])
                simulate = inputs.get("simulateHuman", True)
                click_pos = inputs.get("clickPosition", "center")
                post_delay = inputs.get("postDelayMs", 0)

                pw_modifiers = []
                for m in modifiers_raw:
                    if m == "Win":
                        pw_modifiers.append("Meta")
                    else:
                        pw_modifiers.append(m)

                click_kwargs: dict[str, Any] = {
                    "button": button,
                    "click_count": 2 if click_type == "double" else 1,
                    "timeout": timeout_ms,
                }
                if pw_modifiers:
                    click_kwargs["modifiers"] = pw_modifiers

                if click_pos == "random":
                    box = await locator.first.bounding_box()
                    if box:
                        rx = box["x"] + _random.uniform(0, box["width"])
                        ry = box["y"] + _random.uniform(0, box["height"])
                        click_kwargs["position"] = {"x": rx - box["x"], "y": ry - box["y"]}

                if not simulate:
                    click_kwargs["force"] = True

                await locator.first.click(**click_kwargs)
                if post_delay > 0:
                    await asyncio.sleep(post_delay / 1000)
                return CommandResult.success(
                    outputs={"matchedCount": count, "sessionId": session_id},
                    effects=[
                        EffectRecord.committed(
                            invocation,
                            kind=EffectKind.UNSAFE_WRITE,
                            resource=f"browser.session:{session_id}:selector:{inputs['selector']}",
                            details={"operation": "click", "matchedCount": count},
                        )
                    ],
                )
            if command == "browser.input":
                locator = page.locator(str(inputs["selector"]))
                count = await locator.count()
                if count == 0:
                    return CommandResult.failure(
                        ErrorCode.ELEMENT_NOT_FOUND,
                        "Target element did not match",
                        details={"selector": inputs["selector"], "matchedCount": 0},
                    )
                mode = inputs.get("mode", "fill")
                text_val = str(inputs["text"])
                append = inputs.get("append", False)
                press_enter = inputs.get("pressEnter", False)
                key_interval = inputs.get("keyIntervalMs", 50)
                click_first = inputs.get("clickBeforeInput", False)
                post_delay = inputs.get("postDelayMs", 0)

                if click_first:
                    await locator.first.click(timeout=timeout_ms)

                if mode == "type":
                    if append:
                        await locator.first.press("End")
                    await locator.first.type(text_val, delay=key_interval, timeout=timeout_ms)
                elif mode == "clipboard":
                    await page.evaluate(
                        "(t) => navigator.clipboard.writeText(t)", text_val
                    )
                    if append:
                        await locator.first.press("End")
                    await locator.first.press("Control+v")
                else:
                    if append:
                        current = await locator.first.input_value()
                        await locator.first.fill(
                            current + text_val, timeout=timeout_ms
                        )
                    else:
                        await locator.first.fill(text_val, timeout=timeout_ms)

                if press_enter:
                    await locator.first.press("Enter")

                if post_delay > 0:
                    await asyncio.sleep(post_delay / 1000)

                return CommandResult.success(
                    outputs={"matchedCount": count, "sessionId": session_id},
                    effects=[
                        EffectRecord.committed(
                            invocation,
                            kind=EffectKind.UNSAFE_WRITE,
                            resource=f"browser.session:{session_id}:selector:{inputs['selector']}",
                            details={"operation": "input", "matchedCount": count},
                        )
                    ],
                )
            if command == "browser.hover":
                locator = page.locator(str(inputs["selector"]))
                count = await locator.count()
                if count == 0:
                    return CommandResult.failure(
                        ErrorCode.ELEMENT_NOT_FOUND,
                        "Target element did not match",
                        details={"selector": inputs["selector"], "matchedCount": 0},
                    )
                await locator.first.hover(timeout=timeout_ms)
                return CommandResult.success(
                    outputs={"matchedCount": count, "sessionId": session_id},
                    effects=[
                        EffectRecord.committed(
                            invocation,
                            kind=EffectKind.UNSAFE_WRITE,
                            resource=f"browser.session:{session_id}:selector:{inputs['selector']}",
                            details={"operation": "hover", "matchedCount": count},
                        )
                    ],
                )
            if command == "browser.waitFor":
                wait_state = str(inputs.get("state") or "visible")
                if wait_state not in ("attached", "visible", "hidden", "detached"):
                    return CommandResult.failure(
                        ErrorCode.INVALID_INPUT, f"Unsupported wait state: {wait_state}"
                    )
                locator = page.locator(str(inputs["selector"]))
                await locator.first.wait_for(state=wait_state, timeout=timeout_ms)
                count = await locator.count()
                return CommandResult.success(
                    outputs={"matchedCount": count},
                    effects=[
                        EffectRecord.committed(
                            invocation,
                            kind=EffectKind.READ,
                            resource=f"browser.session:{session_id}:selector:{inputs['selector']}",
                            details={"operation": "waitFor", "matchedCount": count},
                        )
                    ],
                )
            if command == "browser.getText":
                locator = page.locator(str(inputs["selector"]))
                count = await locator.count()
                if count == 0:
                    return CommandResult.failure(
                        ErrorCode.ELEMENT_NOT_FOUND,
                        "Target element did not match",
                        details={"selector": inputs["selector"], "matchedCount": 0},
                    )
                value = await self._read_element_info(page, locator.first, inputs, timeout_ms)
                return CommandResult.success(
                    value=value,
                    outputs={"value": value},
                    effects=[
                        EffectRecord.committed(
                            invocation,
                            kind=EffectKind.READ,
                            resource=f"browser.session:{session_id}:selector:{inputs['selector']}",
                            details={"operation": "getText"},
                        )
                    ],
                )
            if command == "browser.queryAll":
                values = await page.locator(str(inputs["selector"])).all_inner_texts()
                return CommandResult.success(
                    value=values,
                    outputs={"items": values, "count": len(values)},
                    effects=[
                        EffectRecord.committed(
                            invocation,
                            kind=EffectKind.READ,
                            resource=f"browser.session:{session_id}:selector:{inputs['selector']}",
                            details={"operation": "queryAll", "count": len(values)},
                        )
                    ],
                )
            if command == "browser.close":
                force_kill = inputs.get("forceKill", False)
                if force_kill and browser is not None:
                    try:
                        proc = browser._impl_obj._browser_process
                        if proc and proc.pid:
                            import os
                            import signal
                            try:
                                os.kill(proc.pid, signal.SIGTERM)
                            except OSError:
                                pass
                    except Exception:
                        pass
                    try:
                        await browser.close()
                    except Exception:
                        pass
                elif browser is not None:
                    await browser.close()
                else:
                    await context.close()
                self._sessions.pop(session_id, None)
                return CommandResult.success(
                    effects=[
                        EffectRecord.committed(
                            invocation,
                            kind=EffectKind.SESSION,
                            resource=f"browser.session:{session_id}",
                            details={"operation": "close"},
                        )
                    ]
                )
            if command == "browser.executeScript":
                script = str(inputs["script"])
                args = inputs.get("args", [])
                result = await page.evaluate(script, args)
                return CommandResult.success(
                    outputs={"result": result, "sessionId": session_id},
                    effects=[
                        EffectRecord.committed(
                            invocation,
                            kind=EffectKind.UNSAFE_WRITE,
                            resource=f"browser.session:{session_id}:script",
                            details={"operation": "executeScript"},
                        )
                    ],
                )
            if command == "browser.screenshot":
                save_path = str(inputs["savePath"])
                selector = inputs.get("selector")
                full_page = inputs.get("fullPage", False)
                if selector:
                    locator = page.locator(selector)
                    await locator.first.wait_for(state="visible", timeout=timeout_ms)
                    await locator.first.screenshot(path=save_path)
                else:
                    await page.screenshot(path=save_path, full_page=full_page)
                return CommandResult.success(
                    outputs={"filePath": save_path},
                    effects=[
                        EffectRecord.committed(
                            invocation,
                            kind=EffectKind.IDEMPOTENT_WRITE,
                            resource=f"browser.session:{session_id}:screenshot",
                            details={"operation": "screenshot", "filePath": save_path},
                        )
                    ],
                )
            if command == "browser.select":
                selector = str(inputs["selector"])
                value = str(inputs["value"])
                select_by = inputs.get("selectBy", "value")
                locator = page.locator(selector)
                count = await locator.count()
                if count == 0:
                    return CommandResult.failure(
                        ErrorCode.ELEMENT_NOT_FOUND,
                        "Target element did not match",
                        details={"selector": selector, "matchedCount": 0},
                    )
                if select_by == "index":
                    await locator.first.select_option(index=int(value), timeout=timeout_ms)
                elif select_by == "label":
                    await locator.first.select_option(label=value, timeout=timeout_ms)
                else:
                    await locator.first.select_option(value=value, timeout=timeout_ms)
                return CommandResult.success(
                    outputs={"matchedCount": count, "sessionId": session_id},
                    effects=[
                        EffectRecord.committed(
                            invocation,
                            kind=EffectKind.UNSAFE_WRITE,
                            resource=f"browser.session:{session_id}:selector:{selector}",
                            details={"operation": "selectOption", "matchedCount": count},
                        )
                    ],
                )
            if command == "browser.upload":
                selector = str(inputs["selector"])
                files = [str(f) for f in inputs["files"]]
                locator = page.locator(selector)
                count = await locator.count()
                if count == 0:
                    return CommandResult.failure(
                        ErrorCode.ELEMENT_NOT_FOUND,
                        "Target element did not match",
                        details={"selector": selector, "matchedCount": 0},
                    )
                await locator.first.set_input_files(files, timeout=timeout_ms)
                return CommandResult.success(
                    outputs={"matchedCount": count, "sessionId": session_id},
                    effects=[
                        EffectRecord.committed(
                            invocation,
                            kind=EffectKind.UNSAFE_WRITE,
                            resource=f"browser.session:{session_id}:selector:{selector}",
                            details={"operation": "upload", "matchedCount": count},
                        )
                    ],
                )
            if command == "browser.download":
                save_dir = str(inputs["saveDir"])
                timeout_ms_dl = int(inputs.get("timeoutMs", 60_000))
                async with page.expect_download(timeout=timeout_ms_dl) as download_info:
                    pass
                download = await download_info.value
                import os
                os.makedirs(save_dir, exist_ok=True)
                target = os.path.join(save_dir, download.suggested_filename)
                await download.save_as(target)
                return CommandResult.success(
                    outputs={"filePath": target},
                    effects=[
                        EffectRecord.committed(
                            invocation,
                            kind=EffectKind.UNSAFE_WRITE,
                            resource=f"browser.session:{session_id}:download",
                            details={"operation": "download", "filePath": target},
                        )
                    ],
                )
            if command == "browser.handleDialog":
                action = inputs.get("action", "accept")
                prompt_text = inputs.get("promptText")
                dialog_event = getattr(self, "_pending_dialog", None)
                if dialog_event and hasattr(dialog_event, "value"):
                    dlg = dialog_event.value
                    if action == "accept":
                        await dlg.accept(prompt_text)
                    else:
                        await dlg.dismiss()
                return CommandResult.success(
                    effects=[
                        EffectRecord.committed(
                            invocation,
                            kind=EffectKind.UNSAFE_WRITE,
                            resource=f"browser.session:{session_id}:dialog",
                            details={"operation": "handleDialog", "action": action},
                        )
                    ],
                )
            if command == "browser.waitLoad":
                load_state = str(inputs.get("state") or "load")
                if load_state not in ("load", "domcontentloaded", "networkidle"):
                    return CommandResult.failure(
                        ErrorCode.INVALID_INPUT, f"Unsupported load state: {load_state}"
                    )
                await page.wait_for_load_state(load_state, timeout=timeout_ms)
                return CommandResult.success(
                    outputs={"url": page.url},
                    effects=[
                        EffectRecord.committed(
                            invocation,
                            kind=EffectKind.READ,
                            resource=f"browser.session:{session_id}",
                            details={"operation": "waitLoad", "state": load_state},
                        )
                    ],
                )
            if command == "browser.scroll":
                position = str(inputs.get("position") or "bottom")
                if position not in ("top", "bottom", "point", "page"):
                    return CommandResult.failure(
                        ErrorCode.INVALID_INPUT, f"Unsupported scroll position: {position}"
                    )
                behavior = "smooth" if inputs.get("smooth", False) else "auto"
                x = float(inputs.get("x") or 0)
                y = float(inputs.get("y") or 0)
                args = [position, behavior, x, y]
                selector = inputs.get("selector")
                if selector:
                    locator = page.locator(str(selector))
                    count = await locator.count()
                    if count == 0:
                        return CommandResult.failure(
                            ErrorCode.ELEMENT_NOT_FOUND,
                            "Target element did not match",
                            details={"selector": str(selector), "matchedCount": 0},
                        )
                    scroll_y = await locator.first.evaluate(
                        _SCROLL_ELEMENT_JS, args
                    )
                    resource_extra = f":selector:{selector}"
                else:
                    scroll_y = await page.evaluate(_SCROLL_WINDOW_JS, args)
                    resource_extra = ""
                return CommandResult.success(
                    outputs={"scrollY": scroll_y},
                    effects=[
                        EffectRecord.committed(
                            invocation,
                            kind=EffectKind.UNSAFE_WRITE,
                            resource=f"browser.session:{session_id}:scroll{resource_extra}",
                            details={"operation": "scroll", "position": position},
                        )
                    ],
                )
            if command == "browser.check":
                operation = str(inputs.get("operation") or "check")
                if operation not in ("check", "uncheck", "toggle"):
                    return CommandResult.failure(
                        ErrorCode.INVALID_INPUT, f"Unsupported check operation: {operation}"
                    )
                locator = page.locator(str(inputs["selector"]))
                count = await locator.count()
                if count == 0:
                    return CommandResult.failure(
                        ErrorCode.ELEMENT_NOT_FOUND,
                        "Target element did not match",
                        details={"selector": inputs["selector"], "matchedCount": 0},
                    )
                is_checked = await locator.first.is_checked()
                if operation == "check":
                    if not is_checked:
                        await locator.first.check(timeout=timeout_ms)
                    final_checked = True
                elif operation == "uncheck":
                    if is_checked:
                        await locator.first.uncheck(timeout=timeout_ms)
                    final_checked = False
                else:  # toggle
                    if is_checked:
                        await locator.first.uncheck(timeout=timeout_ms)
                        final_checked = False
                    else:
                        await locator.first.check(timeout=timeout_ms)
                        final_checked = True
                return CommandResult.success(
                    outputs={"checked": final_checked, "matchedCount": count},
                    effects=[
                        EffectRecord.committed(
                            invocation,
                            kind=EffectKind.UNSAFE_WRITE,
                            resource=f"browser.session:{session_id}:selector:{inputs['selector']}",
                            details={"operation": "check", "final": final_checked},
                        )
                    ],
                )
            if command == "browser.cookieSet":
                cookies_raw = inputs.get("cookies") or []
                cookies = []
                for item in cookies_raw:
                    if not isinstance(item, dict) or "name" not in item or "value" not in item:
                        return CommandResult.failure(
                            ErrorCode.INVALID_INPUT,
                            "each cookie requires name and value",
                            details={"cookie": item},
                        )
                    cookies.append(item)
                await context.add_cookies(cookies)
                return CommandResult.success(
                    outputs={"count": len(cookies)},
                    effects=[
                        EffectRecord.committed(
                            invocation,
                            kind=EffectKind.IDEMPOTENT_WRITE,
                            resource=f"browser.session:{session_id}:cookies",
                            details={"operation": "cookieSet", "count": len(cookies)},
                        )
                    ],
                )
            if command == "browser.cookieGetAll":
                all_cookies = await context.cookies()
                name_f = inputs.get("name")
                domain_f = inputs.get("domain")
                path_f = inputs.get("path")
                filtered = [
                    c for c in all_cookies
                    if (name_f is None or c.get("name") == name_f)
                    and (domain_f is None or str(domain_f) in str(c.get("domain", "")))
                    and (path_f is None or str(path_f) in str(c.get("path", "")))
                ]
                return CommandResult.success(
                    value=filtered,
                    outputs={"cookies": filtered, "count": len(filtered)},
                    effects=[
                        EffectRecord.committed(
                            invocation,
                            kind=EffectKind.READ,
                            resource=f"browser.session:{session_id}:cookies",
                            details={"operation": "cookieGetAll", "count": len(filtered)},
                        )
                    ],
                )
            if command == "browser.cookieGet":
                cookie_name = str(inputs["name"])
                all_cookies = await context.cookies()
                match = next(
                    (c for c in all_cookies if c.get("name") == cookie_name), None
                )
                if match is None:
                    return CommandResult.failure(
                        ErrorCode.ELEMENT_NOT_FOUND,
                        "Cookie not found",
                        details={"name": cookie_name},
                    )
                value = str(match.get("value", ""))
                return CommandResult.success(
                    value=value,
                    outputs={"value": value},
                    effects=[
                        EffectRecord.committed(
                            invocation,
                            kind=EffectKind.READ,
                            resource=f"browser.session:{session_id}:cookies",
                            details={"operation": "cookieGet", "name": cookie_name},
                        )
                    ],
                )
            if command == "browser.cookieRemove":
                remove_name = inputs.get("name")
                if remove_name:
                    await context.clear_cookies(name=str(remove_name))
                else:
                    await context.clear_cookies()
                return CommandResult.success(
                    outputs={"sessionId": session_id},
                    effects=[
                        EffectRecord.committed(
                            invocation,
                            kind=EffectKind.UNSAFE_WRITE,
                            resource=f"browser.session:{session_id}:cookies",
                            details={"operation": "cookieRemove", "name": remove_name},
                        )
                    ],
                )
            if command == "browser.attach":
                match_by = str(inputs.get("matchBy") or "url")
                pattern = str(inputs["pattern"])
                use_regex = bool(inputs.get("useRegex", False))
                target_page = None
                for candidate in context.pages:
                    if match_by == "title":
                        candidate_value = await candidate.title()
                    else:
                        candidate_value = candidate.url
                    if use_regex:
                        import re as _re

                        hit = _re.search(pattern, candidate_value) is not None
                    else:
                        hit = pattern in candidate_value
                    if hit:
                        target_page = candidate
                        break
                if target_page is None:
                    return CommandResult.failure(
                        ErrorCode.ELEMENT_NOT_FOUND,
                        "No open page matched",
                        details={"matchBy": match_by, "pattern": pattern},
                    )
                new_session_id = str(uuid.uuid4())
                self._sessions[new_session_id] = (browser, context, target_page)
                return CommandResult.success(
                    outputs={
                        "sessionId": new_session_id,
                        "url": target_page.url,
                        "resourceType": "webPage",
                    },
                    effects=[
                        EffectRecord.committed(
                            invocation,
                            kind=EffectKind.SESSION,
                            resource=f"browser.session:{new_session_id}",
                            details={"operation": "attach", "url": target_page.url},
                        )
                    ],
                )
            if command == "browser.listPages":
                pages_info = []
                for idx, candidate in enumerate(context.pages):
                    pages_info.append(
                        {"index": idx, "url": candidate.url, "title": await candidate.title()}
                    )
                return CommandResult.success(
                    outputs={"pages": pages_info, "count": len(pages_info)},
                    effects=[
                        EffectRecord.committed(
                            invocation,
                            kind=EffectKind.READ,
                            resource=f"browser.session:{session_id}",
                            details={"operation": "listPages", "count": len(pages_info)},
                        )
                    ],
                )
            if command == "browser.drag":
                source = page.locator(str(inputs["selector"]))
                source_count = await source.count()
                if source_count == 0:
                    return CommandResult.failure(
                        ErrorCode.ELEMENT_NOT_FOUND,
                        "Source element did not match",
                        details={"selector": inputs["selector"], "matchedCount": 0},
                    )
                target = page.locator(str(inputs["targetSelector"]))
                if await target.count() == 0:
                    return CommandResult.failure(
                        ErrorCode.ELEMENT_NOT_FOUND,
                        "Target element did not match",
                        details={"targetSelector": inputs["targetSelector"], "matchedCount": 0},
                    )
                await source.first.drag_to(target.first, timeout=timeout_ms)
                return CommandResult.success(
                    outputs={"matchedCount": source_count, "sessionId": session_id},
                    effects=[
                        EffectRecord.committed(
                            invocation,
                            kind=EffectKind.UNSAFE_WRITE,
                            resource=f"browser.session:{session_id}:selector:{inputs['selector']}",
                            details={"operation": "drag"},
                        )
                    ],
                )
            return CommandResult.failure(
                ErrorCode.COMMAND_NOT_FOUND, f"Unsupported command: {command}"
            )
        except LookupError:
            return CommandResult.failure(ErrorCode.SESSION_NOT_FOUND, "Browser session not found")
        except PlaywrightTimeoutError as exc:
            return CommandResult.failure(ErrorCode.TIMEOUT, str(exc), retryable=True)
        except Exception as exc:
            return CommandResult.failure(ErrorCode.EXECUTOR_FAILED, str(exc))

    # -- 会话内导航（navigate action=back/forward/reload） --------------------

    async def _navigate_action(
        self, action: str, invocation: CommandInvocation, inputs: dict[str, Any], started: float
    ) -> CommandResult:
        session_ref = str(inputs.get("sessionId") or "")
        timeout_ms = int(inputs.get("timeoutMs", 30_000))
        if session_ref in self._bsk_sessions:
            session = self._bsk_sessions[session_ref]
            js_by_action = {
                "back": "history.back()",
                "forward": "history.forward()",
                "reload": "location.reload()",
            }
            js = js_by_action.get(action)
            if js is None:
                return CommandResult.failure(
                    ErrorCode.INVALID_INPUT, f"Unsupported navigate action: {action}"
                )
            try:
                await self._run_bsk(session.evaluate, js)
                final_url = await self._run_bsk(session.evaluate, "location.href")
            except BskError as exc:
                return CommandResult.failure(
                    ErrorCode.EXECUTOR_FAILED, str(exc), details={"code": exc.code}
                )
            return CommandResult.success(
                outputs={
                    "sessionId": session_ref,
                    "url": final_url,
                    "resourceType": "webPage",
                },
                effects=[
                    EffectRecord.committed(
                        invocation,
                        kind=EffectKind.UNSAFE_WRITE,
                        resource=f"browser.session:{session_ref}",
                        details={"operation": f"navigate:{action}", "transport": "bsk"},
                    )
                ],
                diagnostics={"durationMs": int((time.monotonic() - started) * 1000)},
            )
        try:
            _sid, _browser, _context, page = self._session(inputs)
        except LookupError:
            return CommandResult.failure(
                ErrorCode.INVALID_INPUT,
                f"navigate action={action} requires a valid sessionId",
            )
        try:
            if action == "back":
                await page.go_back(timeout=timeout_ms)
            elif action == "forward":
                await page.go_forward(timeout=timeout_ms)
            elif action == "reload":
                await page.reload(timeout=timeout_ms)
            else:
                return CommandResult.failure(
                    ErrorCode.INVALID_INPUT, f"Unsupported navigate action: {action}"
                )
        except PlaywrightTimeoutError as exc:
            return CommandResult.failure(ErrorCode.TIMEOUT, str(exc), retryable=True)
        return CommandResult.success(
            outputs={
                "sessionId": _sid,
                "url": page.url,
                "resourceType": "webPage",
            },
            effects=[
                EffectRecord.committed(
                    invocation,
                    kind=EffectKind.UNSAFE_WRITE,
                    resource=f"browser.session:{_sid}",
                    details={"operation": f"navigate:{action}", "url": page.url},
                )
            ],
            diagnostics={"durationMs": int((time.monotonic() - started) * 1000)},
        )

    @staticmethod
    async def _read_element_info(
        page: Page, locator: Any, inputs: dict[str, Any], timeout_ms: int
    ) -> str:
        """getText infoType 读取（对标影刀「获取元素信息」信息类型枚举）。"""
        info_type = str(inputs.get("infoType") or "text")
        if info_type == "html":
            return await locator.inner_html(timeout=timeout_ms)
        if info_type == "outerHTML":
            return await locator.evaluate("el => el.outerHTML")
        if info_type == "value":
            return await locator.input_value(timeout=timeout_ms)
        if info_type == "href":
            return str(await locator.get_attribute("href", timeout=timeout_ms) or "")
        return await locator.inner_text(timeout=timeout_ms)

    # -- 自研扩展通道（M15 一等公民） -----------------------------------------

    async def _use_extension(self, inputs: dict[str, Any]) -> bool:
        """通道解析：显式 transport 优先；缺省时扩展在线则优先走扩展。

        一等公民策略：装了扩展且在线 → 默认走扩展（用户真实登录态浏览器）；
        扩展离线（未安装/休眠/devserver 未运行）→ 静默回退 playwright，流程不阻塞。
        需要强制旧通道时显式 `transport=playwright`。

        浏览器类型（channel）参与缺省选择：扩展通道里浏览器 = 扩展宿主，无法"启动"目标
        浏览器；因此缺省通道 + 显式 channel 且宿主不匹配时让位给 playwright（只有它能
        真正按 channel 启动目标浏览器）。显式 `transport=extension` 时不在此让位，
        由 `_extension_channel_guard` 给出明确失败。
        """
        transport = inputs.get("transport")
        if transport:
            return str(transport) == "extension"
        try:
            status = await asyncio.to_thread(self._ext.status)
        except Exception:
            return False
        if not bool(status.get("online")):
            return False
        channel = str(inputs.get("channel") or "").strip().lower()
        if not channel:
            return True
        host = status.get("host") if isinstance(status.get("host"), dict) else {}
        return channel_matches_host(channel, str(host.get("browser") or "") or None)

    async def _extension_channel_guard(self, inputs: dict[str, Any]) -> CommandResult | None:
        """扩展通道下校验 channel 能否兑现（不能返回失败结果，能则返回 None）。

        扩展通道没有"启动浏览器"语义：能用的浏览器只有扩展宿主那一个。
        """
        channel = str(inputs.get("channel") or "").strip().lower()
        if not channel:
            return None
        if channel not in CHROMIUM_FAMILY:
            return CommandResult.failure(
                ErrorCode.INVALID_INPUT,
                f"extension 通道只能在 Chromium 内核浏览器里执行（扩展装在哪就用哪），"
                f"不支持 channel={channel}；如需 {channel} 请改用 transport=playwright",
                details={"transport": "extension", "channel": channel},
            )
        try:
            host = await asyncio.to_thread(self._ext.host)
        except Exception:
            host = None
        actual = str((host or {}).get("browser") or "")
        if channel_matches_host(channel, actual or None):
            return None
        if not actual:
            hint = "扩展未上报宿主浏览器（扩展版本过旧）：请在浏览器扩展页重载本扩展后重试"
        else:
            hint = (
                f"当前扩展宿主为 {actual}，与 channel={channel} 不符——"
                f"请把扩展安装到 {channel}，或改用 transport=playwright（可启动指定浏览器）"
            )
        return CommandResult.failure(
            ErrorCode.INVALID_INPUT,
            f"extension 通道无法在 {channel} 打开网页：{hint}",
            details={
                "transport": "extension",
                "channel": channel,
                "hostBrowser": actual or None,
            },
        )

    async def _open_extension(
        self, invocation: CommandInvocation, inputs: dict[str, Any], started: float
    ) -> CommandResult:
        """打开网页（扩展传输）= 在用户真实浏览器里新建标签页（无启动浏览器概念）。"""
        mismatch = await self._extension_channel_guard(inputs)
        if mismatch is not None:
            return mismatch
        url = str(inputs["url"])
        timeout_s = int(inputs.get("timeoutMs", 30_000)) / 1000.0
        try:
            opened = await asyncio.to_thread(
                self._ext.tabs_create, url, timeout_seconds=timeout_s
            )
        except ExtensionChannelError as exc:
            return self._ext_channel_failure(exc)
        session_id = str(uuid.uuid4())
        tab_id = str(opened.get("tabId") or "")
        self._ext_sessions[session_id] = tab_id
        final_url = str(opened.get("url") or url)
        return CommandResult.success(
            outputs={
                "sessionId": session_id,
                "url": final_url,
                "resourceType": "webPage",
            },
            effects=[
                EffectRecord.committed(
                    invocation,
                    kind=EffectKind.SESSION,
                    resource=f"browser.session:{session_id}",
                    details={
                        "operation": "navigate",
                        "transport": "extension",
                        "tabId": tab_id,
                        "url": final_url,
                    },
                )
            ],
            diagnostics={"durationMs": int((time.monotonic() - started) * 1000)},
        )

    async def _execute_extension(
        self,
        command: str,
        invocation: CommandInvocation,
        inputs: dict[str, Any],
        session_id: str,
        started: float,
        cancellation: asyncio.Event,
    ) -> CommandResult:
        """扩展会话内的命令执行（Phase 1 命令集）。"""
        tab_id = self._ext_sessions.get(session_id, "")
        selector = str(inputs.get("selector") or "")
        timeout_s = int(inputs.get("timeoutMs", 30_000)) / 1000.0
        resource = f"browser.session:{session_id}"
        try:
            if command == "browser.close":
                # 扩展会话的「关闭」= 解绑：用户浏览器里的标签页留给用户，不代关
                self._ext_sessions.pop(session_id, None)
                return self._ext_success(
                    invocation, EffectKind.SESSION, resource, {"operation": "detach"},
                    outputs={"sessionId": session_id},
                )
            if command == "browser.navigate":
                action = str(inputs.get("action") or "goto")
                if action == "goto":
                    result = await asyncio.to_thread(
                        self._ext.tabs_navigate, tab_id, str(inputs["url"]),
                        timeout_seconds=timeout_s,
                    )
                else:
                    result = await asyncio.to_thread(
                        self._ext.tabs_history, tab_id, action, timeout_seconds=timeout_s
                    )
                final_url = str(result.get("url") or "")
                return self._ext_success(
                    invocation, EffectKind.SESSION, resource,
                    {"operation": action, "transport": "extension", "url": final_url},
                    outputs={
                        "sessionId": session_id, "url": final_url, "resourceType": "webPage"
                    },
                )
            if command == "browser.listPages":
                tabs = await asyncio.to_thread(self._ext.tabs_list, timeout_seconds=timeout_s)
                pages = [
                    {
                        "index": index,
                        "url": str(tab.get("url") or ""),
                        "title": str(tab.get("title") or ""),
                    }
                    for index, tab in enumerate(tabs)
                ]
                return self._ext_success(
                    invocation, EffectKind.READ, resource,
                    {"operation": "listPages", "count": len(pages)},
                    outputs={"pages": pages, "count": len(pages)},
                )
            if command == "browser.attach":
                matched = await asyncio.to_thread(self._match_ext_tab, inputs, timeout_s)
                if matched is None:
                    return self._ext_not_found(inputs)
                new_session = str(uuid.uuid4())
                new_tab = str(matched.get("id") or "")
                self._ext_sessions[new_session] = new_tab
                matched_url = str(matched.get("url") or "")
                return self._ext_success(
                    invocation, EffectKind.SESSION, f"browser.session:{new_session}",
                    {"operation": "attach", "tabId": new_tab, "url": matched_url},
                    outputs={
                        "sessionId": new_session,
                        "url": matched_url,
                        "resourceType": "webPage",
                    },
                )
            if command == "browser.executeScript":
                payload = await asyncio.to_thread(
                    self._ext.page_eval, tab_id, str(inputs["script"]),
                    args=list(inputs.get("args") or []), timeout_seconds=timeout_s,
                )
                result_value = payload.get("result")
                return self._ext_success(
                    invocation, EffectKind.UNSAFE_WRITE, resource + ":script",
                    {"operation": "executeScript"},
                    outputs={"result": result_value, "sessionId": session_id},
                    value=result_value,
                )
            if command == "browser.getText":
                info_type = str(inputs.get("infoType") or "text")
                payload = await asyncio.to_thread(
                    self._ext.page_call, tab_id, selector, "getText",
                    args={"infoType": info_type}, timeout_seconds=timeout_s,
                )
                count = int(payload.get("matchedCount") or 0)
                if count == 0:
                    return self._ext_not_found(inputs)
                value = payload.get("result")
                return self._ext_success(
                    invocation, EffectKind.READ, resource + f":selector:{selector}",
                    {"operation": "getText", "infoType": info_type, "matchedCount": count},
                    outputs={
                        "value": "" if value is None else str(value),
                        "sessionId": session_id,
                    },
                    value=value,
                )
            if command == "browser.waitFor":
                return await self._ext_wait_for(
                    invocation, inputs, session_id, tab_id, selector, timeout_s, cancellation
                )
            if command in _EXT_PAGE_METHODS:
                return await self._ext_page_command(
                    command, invocation, inputs, session_id, tab_id, selector, timeout_s
                )
            return CommandResult.failure(
                ErrorCode.COMMAND_NOT_FOUND,
                f"Unsupported command on extension channel: {command}",
            )
        except ExtensionChannelError as exc:
            return self._ext_channel_failure(exc)

    async def _ext_page_command(
        self,
        command: str,
        invocation: CommandInvocation,
        inputs: dict[str, Any],
        session_id: str,
        tab_id: str,
        selector: str,
        timeout_s: float,
    ) -> CommandResult:
        method = _EXT_PAGE_METHODS[command]
        resource = f"browser.session:{session_id}:selector:{selector}"
        payload = await asyncio.to_thread(
            self._ext.page_call, tab_id, selector, method,
            args=self._ext_method_args(command, inputs), timeout_seconds=timeout_s,
        )
        count = int(payload.get("matchedCount") or 0)
        if command != "browser.scroll" and count == 0:
            return self._ext_not_found(inputs)
        if command == "browser.scroll":
            scroll_y = payload.get("result")
            return self._ext_success(
                invocation, EffectKind.UNSAFE_WRITE, resource,
                {"operation": "scroll", "scrollY": scroll_y},
                outputs={"scrollY": float(scroll_y or 0)},
            )
        if command == "browser.check":
            return self._ext_success(
                invocation, EffectKind.UNSAFE_WRITE, resource,
                {"operation": "check", "matchedCount": count},
                outputs={"checked": bool(payload.get("result")), "matchedCount": count},
            )
        effect = EffectKind.READ if command == "browser.hover" else EffectKind.UNSAFE_WRITE
        return self._ext_success(
            invocation, effect, resource,
            {"operation": command.rsplit(".", 1)[-1], "matchedCount": count},
            outputs={"matchedCount": count, "sessionId": session_id},
        )

    async def _ext_wait_for(
        self,
        invocation: CommandInvocation,
        inputs: dict[str, Any],
        session_id: str,
        tab_id: str,
        selector: str,
        timeout_s: float,
        cancellation: asyncio.Event,
    ) -> CommandResult:
        """元素状态等待：扩展侧按「命中数 ± 可见性」轮询（visible/hidden/attached/detached）。"""
        state = str(inputs.get("state") or "visible")
        want_visible = state in ("visible", "hidden")
        want_present = state in ("visible", "attached")
        deadline = time.monotonic() + timeout_s
        last_count = 0
        while True:
            payload = await asyncio.to_thread(
                self._ext.page_call, tab_id, selector, "count",
                args={"visible": want_visible}, timeout_seconds=min(10.0, timeout_s),
            )
            last_count = int(payload.get("matchedCount") or 0)
            hit = last_count > 0 if want_present else last_count == 0
            if hit:
                return self._ext_success(
                    invocation, EffectKind.READ,
                    f"browser.session:{session_id}:selector:{selector}",
                    {"operation": "waitFor", "state": state, "matchedCount": last_count},
                    outputs={"matchedCount": last_count},
                )
            if cancellation.is_set():
                return CommandResult(status="cancelled")
            if time.monotonic() >= deadline:
                return CommandResult.failure(
                    ErrorCode.TIMEOUT,
                    f"waitFor({state}) timed out on extension channel",
                    details={"selector": selector, "state": state},
                )
            await asyncio.sleep(0.3)

    def _ext_method_args(self, command: str, inputs: dict[str, Any]) -> dict[str, Any]:
        """命令参数 → 扩展 page.call 参数（未支持的字段在扩展侧忽略）。"""
        if command == "browser.click":
            return {
                "button": inputs.get("button") or "left",
                "clickType": inputs.get("clickType") or "single",
                "modifiers": inputs.get("modifiers") or [],
            }
        if command == "browser.input":
            return {
                "text": "" if inputs.get("text") is None else str(inputs["text"]),
                "mode": inputs.get("mode") or "type",
                "append": bool(inputs.get("append", False)),
                "pressEnter": bool(inputs.get("pressEnter", False)),
            }
        if command == "browser.scroll":
            return {
                "position": inputs.get("position") or "bottom",
                "x": inputs.get("x"),
                "y": inputs.get("y"),
                "smooth": bool(inputs.get("smooth", False)),
            }
        if command == "browser.select":
            return {
                "value": "" if inputs.get("value") is None else str(inputs["value"]),
                "selectBy": inputs.get("selectBy") or "value",
            }
        if command == "browser.check":
            return {"operation": inputs.get("operation") or "check"}
        return {}

    def _match_ext_tab(self, inputs: dict[str, Any], timeout_s: float) -> dict[str, Any] | None:
        """attach：在用户浏览器全部标签页里按 url/title 子串或正则匹配。"""
        import re

        pattern = str(inputs.get("pattern") or "")
        match_by = str(inputs.get("matchBy") or "url")
        use_regex = bool(inputs.get("useRegex", False))
        tabs = self._ext.tabs_list(timeout_seconds=timeout_s)
        for tab in tabs:
            field = str(tab.get("title") if match_by == "title" else tab.get("url") or "")
            if use_regex:
                try:
                    hit = re.search(pattern, field) is not None
                except re.error:
                    hit = False
            else:
                hit = pattern in field
            if hit:
                return tab
        return None

    def _ext_success(
        self,
        invocation: CommandInvocation,
        kind: EffectKind,
        resource: str,
        details: dict[str, Any],
        *,
        outputs: dict[str, Any] | None = None,
        value: Any = None,
    ) -> CommandResult:
        return CommandResult.success(
            value=value,
            outputs=outputs or {},
            effects=[
                EffectRecord.committed(
                    invocation, kind=kind, resource=resource, details=details
                )
            ],
        )

    def _ext_not_found(self, inputs: dict[str, Any]) -> CommandResult:
        return CommandResult.failure(
            ErrorCode.ELEMENT_NOT_FOUND,
            "Target element did not match",
            details={"selector": inputs.get("selector"), "matchedCount": 0},
        )

    def _ext_channel_failure(self, exc: ExtensionChannelError) -> CommandResult:
        code = ErrorCode.TIMEOUT if exc.code == "TIMEOUT" else ErrorCode.EXECUTOR_FAILED
        return CommandResult.failure(
            code,
            f"extension channel: {exc}",
            details={"channel": "extension", "code": exc.code},
        )

    # -- bsk 传输（M14a：用户真实浏览器，能力差异 CSS only / 仅主 frame） -----

    async def _open_bsk(
        self, invocation: CommandInvocation, inputs: dict[str, Any], started: float
    ) -> CommandResult:
        """打开网页（bsk 传输）= session start + navigate 一步完成。"""
        session = BskSession(
            browser_instance_id=(
                str(inputs["browserInstanceId"]) if inputs.get("browserInstanceId") else None
            ),
            runner=self._bsk_runner,
        )
        try:
            await self._run_bsk(session.start)
        except BskError as exc:
            return CommandResult.failure(ErrorCode.EXECUTOR_FAILED, str(exc))
        session_id = str(uuid.uuid4())
        keep_open = bool(inputs.get("keepOpen", False))
        self._bsk_sessions[session_id] = session
        self._bsk_keep_open[session_id] = keep_open
        try:
            final_url = await self._run_bsk(session.navigate, str(inputs["url"]))
        except BskError as exc:
            # 导航失败：回收刚建的 bsk 会话（规则 11）
            self._bsk_sessions.pop(session_id, None)
            self._bsk_keep_open.pop(session_id, None)
            try:
                await self._run_bsk(session.stop)
            except BskError:
                pass
            return CommandResult.failure(ErrorCode.EXECUTOR_FAILED, str(exc))
        return CommandResult.success(
            outputs={
                "sessionId": session_id,
                "url": final_url,
                "resourceType": "webPage",
            },
            effects=[
                EffectRecord.committed(
                    invocation,
                    kind=EffectKind.SESSION,
                    resource=f"browser.session:{session_id}",
                    details={"operation": "navigate", "transport": "bsk", "url": final_url},
                )
            ],
            diagnostics={"durationMs": int((time.monotonic() - started) * 1000)},
        )

    async def _execute_bsk(
        self,
        command: str,
        invocation: CommandInvocation,
        inputs: dict[str, Any],
        session_id: str,
        started: float,
        cancellation: asyncio.Event,
    ) -> CommandResult:
        session = self._bsk_sessions[session_id]
        selector = str(inputs.get("selector") or "")
        timeout_ms = int(inputs.get("timeoutMs", 30_000))
        timeout_s = timeout_ms / 1000.0
        effect_kind = EffectKind.READ
        resource = f"browser.session:{session_id}"

        try:
            if command == "browser.click":
                count = await self._run_bsk(session.count, selector)
                if count == 0:
                    return self._bsk_not_found(inputs)
                await self._run_bsk(session.click, selector)
                effect_kind = EffectKind.UNSAFE_WRITE
                resource += f":selector:{selector}"
                return self._bsk_success(
                    invocation, effect_kind, resource,
                    {"operation": "click", "matchedCount": count},
                    outputs={"matchedCount": count, "sessionId": session_id},
                )
            if command == "browser.input":
                count = await self._run_bsk(session.count, selector)
                if count == 0:
                    return self._bsk_not_found(inputs)
                await self._run_bsk(session.fill, selector, str(inputs["text"]))
                effect_kind = EffectKind.UNSAFE_WRITE
                resource += f":selector:{selector}"
                return self._bsk_success(
                    invocation, effect_kind, resource,
                    {"operation": "input", "matchedCount": count},
                    outputs={"matchedCount": count, "sessionId": session_id},
                )
            if command == "browser.hover":
                count = await self._run_bsk(session.count, selector)
                if count == 0:
                    return self._bsk_not_found(inputs)
                await self._run_bsk(session.hover, selector)
                effect_kind = EffectKind.UNSAFE_WRITE
                resource += f":selector:{selector}"
                return self._bsk_success(
                    invocation, effect_kind, resource,
                    {"operation": "hover", "matchedCount": count},
                    outputs={"matchedCount": count, "sessionId": session_id},
                )
            if command == "browser.waitFor":
                wait_state = str(inputs.get("state") or "visible")
                if wait_state != "visible":
                    return CommandResult.failure(
                        ErrorCode.EXECUTOR_FAILED,
                        f"bsk transport only supports waitFor state=visible, got: {wait_state}",
                    )
                found = await self._run_bsk(
                    session.wait_for, selector, timeout_s, cancellation.is_set
                )
                if cancellation.is_set():
                    return CommandResult(status="cancelled")
                if not found:
                    return CommandResult.failure(
                        ErrorCode.TIMEOUT,
                        f"selector did not appear within {timeout_ms}ms: {selector}",
                        retryable=True,
                        details={"selector": selector},
                    )
                count = await self._run_bsk(session.count, selector)
                resource += f":selector:{selector}"
                return self._bsk_success(
                    invocation, EffectKind.READ, resource,
                    {"operation": "waitFor", "matchedCount": count},
                    outputs={"matchedCount": count},
                )
            if command == "browser.getText":
                count = await self._run_bsk(session.count, selector)
                if count == 0:
                    return self._bsk_not_found(inputs)
                value = await self._run_bsk(session.inner_text, selector)
                resource += f":selector:{selector}"
                return self._bsk_success(
                    invocation, EffectKind.READ, resource,
                    {"operation": "getText"},
                    outputs={"value": value},
                    value=value,
                )
            if command == "browser.queryAll":
                values = await self._run_bsk(session.inner_texts, selector)
                resource += f":selector:{selector}"
                return self._bsk_success(
                    invocation, EffectKind.READ, resource,
                    {"operation": "queryAll", "count": len(values)},
                    outputs={"items": values, "count": len(values)},
                    value=values,
                )
            if command == "browser.close":
                await self._run_bsk(session.stop)
                self._bsk_sessions.pop(session_id, None)
                return self._bsk_success(
                    invocation, EffectKind.SESSION, resource,
                    {"operation": "close", "transport": "bsk"},
                )
            if command == "browser.executeScript":
                script = str(inputs["script"])
                result = await self._run_bsk(session.evaluate, script)
                return self._bsk_success(
                    invocation, EffectKind.UNSAFE_WRITE, resource,
                    {"operation": "executeScript"},
                    outputs={"result": result, "sessionId": session_id},
                    value=result,
                )
            if command == "browser.screenshot":
                save_path = str(inputs["savePath"])
                full_page = inputs.get("fullPage", False)
                import base64
                if full_page:
                    await self._run_bsk(
                        session.evaluate,
                        "document.body.style.overflow='hidden'"
                    )
                b64_data = await self._run_bsk(
                    session.evaluate,
                    "(() => { const c = document.createElement('canvas');"
                    " c.width = document.documentElement.clientWidth;"
                    " c.height = document.documentElement.clientHeight;"
                    " return c.toDataURL('image/png').split(',')[1]; })()"
                )
                if b64_data:
                    import os

                    os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
                    data = base64.b64decode(b64_data)
                    await asyncio.to_thread(
                        lambda: open(save_path, "wb").write(data)
                    )
                return self._bsk_success(
                    invocation, EffectKind.IDEMPOTENT_WRITE, resource,
                    {"operation": "screenshot", "filePath": save_path},
                    outputs={"filePath": save_path},
                )
            if command == "browser.select":
                import json as _json
                selector = str(inputs["selector"])
                value = str(inputs["value"])
                select_by = inputs.get("selectBy", "value")
                count = await self._run_bsk(session.count, selector)
                if count == 0:
                    return self._bsk_not_found(inputs)
                sel_js = _json.dumps(selector)
                val_js = _json.dumps(value)
                if select_by == "index":
                    js = (f"(() => {{ const s = document.querySelector({sel_js});"
                          f" s.selectedIndex = {int(value)};"
                          f" s.dispatchEvent(new Event('change')); }})()")
                elif select_by == "label":
                    js = (f"(() => {{ const s = document.querySelector({sel_js});"
                          f" for (const o of s.options) {{"
                          f" if (o.text === {val_js}) {{"
                          f" s.value = o.value; break; }} }}"
                          f" s.dispatchEvent(new Event('change')); }})()")
                else:
                    js = (f"(() => {{ const s = document.querySelector({sel_js});"
                          f" s.value = {val_js};"
                          f" s.dispatchEvent(new Event('change')); }})()")
                await self._run_bsk(session.evaluate, js)
                effect_kind = EffectKind.UNSAFE_WRITE
                resource += f":selector:{selector}"
                return self._bsk_success(
                    invocation, effect_kind, resource,
                    {"operation": "selectOption", "matchedCount": count},
                    outputs={"matchedCount": count, "sessionId": session_id},
                )
            return CommandResult.failure(
                ErrorCode.COMMAND_NOT_FOUND, f"Unsupported command: {command}"
            )
        except BskSessionGoneError as exc:
            self._bsk_sessions.pop(session_id, None)
            return CommandResult.failure(
                ErrorCode.SESSION_NOT_FOUND,
                f"bsk session lost: {exc}",
                details={"code": exc.code},
            )
        except BskError as exc:
            return CommandResult.failure(
                ErrorCode.EXECUTOR_FAILED, str(exc), details={"code": exc.code}
            )

    def _bsk_success(
        self,
        invocation: CommandInvocation,
        kind: EffectKind,
        resource: str,
        details: dict[str, Any],
        *,
        outputs: dict[str, Any] | None = None,
        value: Any = None,
    ) -> CommandResult:
        return CommandResult.success(
            value=value,
            outputs=outputs or {},
            effects=[
                EffectRecord.committed(invocation, kind=kind, resource=resource,
                                       details=details)
            ],
        )

    def _bsk_not_found(self, inputs: dict[str, Any]) -> CommandResult:
        return CommandResult.failure(
            ErrorCode.ELEMENT_NOT_FOUND,
            "Target element did not match",
            details={"selector": inputs.get("selector"), "matchedCount": 0},
        )

    async def close(self) -> None:
        # 扩展会话（M15）：只解绑，不动用户浏览器里的标签页（默认整浏览器权限≠代管生命周期）
        self._ext_sessions.clear()
        # bsk keepOpen 会话（流程结束仍保留 Agent Window 供人工继续，daemon 空闲超时兜底）
        # 不在此停；非 keepOpen 的一律回收（规则 11）
        for sid in list(self._bsk_sessions):
            if self._bsk_keep_open.get(sid):
                self._bsk_sessions.pop(sid, None)
                continue
            await self._run_bsk(self._bsk_sessions[sid].stop)
        self._bsk_sessions.clear()
        self._bsk_keep_open.clear()
        for browser, _context, _page in list(self._sessions.values()):
            await browser.close()
        self._sessions.clear()
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
