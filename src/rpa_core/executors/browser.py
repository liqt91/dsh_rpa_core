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

from rpa_core.model.command import (
    CommandInvocation,
    CommandResult,
    EffectKind,
    EffectRecord,
)
from rpa_core.model.errors import ErrorCode

from .base import CommandExecutor


class PlaywrightExecutor(CommandExecutor):
    def __init__(self):
        self._playwright: Playwright | None = None
        self._sessions: dict[str, tuple[Browser, BrowserContext, Page]] = {}

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
                runtime = await self._ensure_runtime()
                browser = await runtime.chromium.launch(headless=bool(inputs.get("headless", True)))
                context = await browser.new_context()
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
                await browser.close()
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

    async def close(self) -> None:
        for browser, _context, _page in list(self._sessions.values()):
            await browser.close()
        self._sessions.clear()
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
