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
            if command == "browser.launch":
                if inputs.get("transport") == "bsk":
                    return await self._launch_bsk(invocation, inputs, started)
                runtime = await self._ensure_runtime()
                user_agent = inputs.get("userAgent")
                user_data_dir = inputs.get("userDataDir")
                launch_kwargs = {
                    "headless": bool(inputs.get("headless", True)),
                    "ignore_default_args": ["--enable-automation"],
                }
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
                return CommandResult.success(
                    outputs={"sessionId": session_id},
                    effects=[
                        EffectRecord.committed(
                            invocation,
                            kind=EffectKind.SESSION,
                            resource=f"browser.session:{session_id}",
                            details={"operation": "launch"},
                        )
                    ],
                    diagnostics={"durationMs": int((time.monotonic() - started) * 1000)},
                )

            session_ref = str(inputs.get("sessionId") or "")
            if session_ref in self._bsk_sessions:
                return await self._execute_bsk(
                    command, invocation, inputs, session_ref, started, cancellation
                )

            session_id, browser, _context, page = self._session(inputs)
            timeout_ms = int(inputs.get("timeoutMs", 30_000))

            if command == "browser.navigate":
                await page.goto(
                    str(inputs["url"]), wait_until="domcontentloaded", timeout=timeout_ms
                )
                return CommandResult.success(
                    outputs={"url": page.url, "sessionId": session_id},
                    effects=[
                        EffectRecord.committed(
                            invocation,
                            kind=EffectKind.UNSAFE_WRITE,
                            resource=f"browser.session:{session_id}:url",
                            details={"operation": "navigate", "url": page.url},
                        )
                    ],
                )
            if command == "browser.click":
                locator = page.locator(str(inputs["selector"]))
                count = await locator.count()
                if count == 0:
                    return CommandResult.failure(
                        ErrorCode.ELEMENT_NOT_FOUND,
                        "Target element did not match",
                        details={"selector": inputs["selector"], "matchedCount": 0},
                    )
                await locator.first.click(timeout=timeout_ms)
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
                await locator.first.fill(str(inputs["text"]), timeout=timeout_ms)
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
                if browser is not None:
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

    async def _launch_bsk(
        self, invocation: CommandInvocation, inputs: dict[str, Any], started: float
    ) -> CommandResult:
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
        return CommandResult.success(
            outputs={"sessionId": session_id},
            effects=[
                EffectRecord.committed(
                    invocation,
                    kind=EffectKind.SESSION,
                    resource=f"browser.session:{session_id}",
                    details={"operation": "launch", "transport": "bsk"},
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
            if command == "browser.navigate":
                final_url = await self._run_bsk(session.navigate, str(inputs["url"]))
                effect_kind = EffectKind.UNSAFE_WRITE
                resource += ":url"
                return self._bsk_success(
                    invocation, effect_kind, resource,
                    {"operation": "navigate", "url": final_url},
                    outputs={"url": final_url, "sessionId": session_id},
                )
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
