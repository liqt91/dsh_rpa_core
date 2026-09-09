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
from rpa_core.model.command import (
    CommandInvocation,
    CommandResult,
    EffectKind,
    EffectRecord,
)
from rpa_core.model.errors import ErrorCode

from .base import CommandExecutor
from .browser_bsk import BskSession


class PlaywrightExecutor(CommandExecutor):
    def __init__(self, bsk_runner=None):
        self._playwright: Playwright | None = None
        self._sessions: dict[str, tuple[Browser, BrowserContext, Page]] = {}
        self._bsk_sessions: dict[str, BskSession] = {}
        self._bsk_keep_open: dict[str, bool] = {}
        self._bsk_runner = bsk_runner

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
            if command == "browser.navigate":
                # 打开网页 = 启动浏览器 + 导航（不单独维护浏览器实例指令）
                if inputs.get("transport") == "bsk":
                    return await self._open_bsk(invocation, inputs, started)
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
                try:
                    await page.goto(
                        str(inputs["url"]), wait_until="domcontentloaded", timeout=timeout_ms
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
                locator = page.locator(str(inputs["selector"]))
                await locator.first.wait_for(state="visible", timeout=timeout_ms)
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
                value = await locator.first.inner_text(timeout=timeout_ms)
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
            return CommandResult.failure(
                ErrorCode.COMMAND_NOT_FOUND, f"Unsupported command: {command}"
            )
        except LookupError:
            return CommandResult.failure(ErrorCode.SESSION_NOT_FOUND, "Browser session not found")
        except PlaywrightTimeoutError as exc:
            return CommandResult.failure(ErrorCode.TIMEOUT, str(exc), retryable=True)
        except Exception as exc:
            return CommandResult.failure(ErrorCode.EXECUTOR_FAILED, str(exc))

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
