"""浏览器元素捕获会话（M10）。

两种传输（docs/capture-transport.md §3）：
- persistent: launch_persistent_context 专用 profile（M10a 主路线）
- user-browser: chrome-inspect-ws —— 读取日常浏览器 User Data 下的
  DevToolsActivePort 文件构造 WS URL，connect_over_cdp 挂真实浏览器（M10b，S1 已验证）

捕获交互：注入 picker（hover 高亮 + 点击采集），点击后生成 CSS selector
并在页面内回验命中数；cancel 会清理注入并结束等待。
"""

import asyncio
import os
import threading
import time
from pathlib import Path
from typing import Any

_PICKER_JS = r"""
() => {
  if (window.__rpaCapture) return true;
  window.__rpaCaptureResult = undefined;
  const box = document.createElement('div');
  box.style.cssText = 'position:fixed;pointer-events:none;border:2px solid #0969da;'
    + 'background:rgba(9,105,218,.12);z-index:2147483647;border-radius:4px;display:none;';
  document.documentElement.appendChild(box);
  function uniqueIn(sel) {
    try { return document.querySelectorAll(sel).length === 1; } catch (e) { return false; }
  }
  function nthOf(el) {
    const parent = el.parentElement;
    if (!parent) return '';
    const same = Array.from(parent.children).filter((c) => c.tagName === el.tagName);
    return same.length > 1 ? ':nth-of-type(' + (same.indexOf(el) + 1) + ')' : '';
  }
  function buildSelector(el) {
    if (el.id) {
      const sel = '#' + CSS.escape(el.id);
      if (uniqueIn(sel)) return sel;
    }
    const nameAttr = el.getAttribute && el.getAttribute('name');
    if (nameAttr) {
      const sel = el.tagName.toLowerCase() + '[name="' + nameAttr + '"]';
      if (uniqueIn(sel)) return sel;
    }
    const parts = [];
    let cur = el;
    let guard = 0;
    while (cur && cur.nodeType === 1 && cur !== document.documentElement && guard < 12) {
      if (cur.id) {
        const withId = '#' + CSS.escape(cur.id)
          + (parts.length ? ' > ' + parts.join(' > ') : '');
        if (uniqueIn(withId)) return withId;
      }
      parts.unshift(cur.tagName.toLowerCase() + nthOf(cur));
      const cand = parts.join(' > ');
      if (parts.length >= 2 && uniqueIn(cand)) return cand;
      cur = cur.parentElement;
      guard += 1;
    }
    return 'body > ' + parts.join(' > ');
  }
  function describe(el) {
    const selector = buildSelector(el);
    let count = 0;
    try { count = document.querySelectorAll(selector).length; } catch (e) { count = 0; }
    const rect = el.getBoundingClientRect();
    return {
      selector: selector,
      verifyCount: count,
      tag: el.tagName.toLowerCase(),
      id: el.id || '',
      classes: typeof el.className === 'string' ? el.className : '',
      text: ((el.innerText || el.textContent || '').trim()).slice(0, 120),
      rect: { x: Math.round(rect.x), y: Math.round(rect.y),
              w: Math.round(rect.width), h: Math.round(rect.height) },
    };
  }
  function onOver(e) {
    const rect = e.target.getBoundingClientRect();
    box.style.display = 'block';
    box.style.top = rect.top + 'px';
    box.style.left = rect.left + 'px';
    box.style.width = rect.width + 'px';
    box.style.height = rect.height + 'px';
  }
  function onClick(e) {
    e.preventDefault();
    e.stopPropagation();
    finish(e.target);
  }
  function onKey(e) { if (e.key === 'Escape') finish(null); }
  function cleanup() {
    box.remove();
    document.removeEventListener('mouseover', onOver, true);
    document.removeEventListener('click', onClick, true);
    document.removeEventListener('keydown', onKey, true);
    window.__rpaCaptureCleanup = undefined;
    window.__rpaCapture = undefined;
  }
  function finish(el) {
    cleanup();
    window.__rpaCaptureResult = el ? describe(el) : 'cancelled';
  }
  document.addEventListener('mouseover', onOver, true);
  document.addEventListener('click', onClick, true);
  document.addEventListener('keydown', onKey, true);
  window.__rpaCaptureCleanup = cleanup;
  window.__rpaCapture = true;
  return true;
}
"""

_CANCEL_JS = r"""
() => {
  if (window.__rpaCaptureCleanup) window.__rpaCaptureCleanup();
  window.__rpaCaptureResult = 'cancelled';
}
"""

_DONE_JS = "() => window.__rpaCaptureResult !== undefined"
_RESULT_JS = "() => window.__rpaCaptureResult"
_CLEANUP_JS = "() => { if (window.__rpaCaptureCleanup) window.__rpaCaptureCleanup(); }"

_BROWSER_DATA_DIRS = {
    "edge": "Microsoft/Edge/User Data",
    "chrome": "Google/Chrome/User Data",
}


def devtools_active_port_url(browser_type: str = "edge", user_data_dir: str | None = None) -> str:
    """从 DevToolsActivePort 文件构造 WS URL（S1 结论：无需 UI 交互）。"""
    if user_data_dir:
        base = Path(user_data_dir)
    else:
        key = browser_type if browser_type in _BROWSER_DATA_DIRS else "edge"
        base = Path(os.environ.get("LOCALAPPDATA", "")) / _BROWSER_DATA_DIRS[key]
    port_file = base / "DevToolsActivePort"
    lines = port_file.read_text(encoding="utf-8").splitlines()
    if len(lines) < 2:
        raise RuntimeError(f"DevToolsActivePort file is malformed: {port_file}")
    return f"ws://127.0.0.1:{lines[0].strip()}{lines[1].strip()}"


class BrowserCaptureSession:
    """带独立事件循环线程的浏览器捕获会话；pick/cancel 从任意线程调用。"""

    def __init__(
        self,
        *,
        transport: str,
        user_data_dir: str | None = None,
        headless: bool = False,
        user_agent: str | None = None,
        browser_type: str = "edge",
        page_url: str | None = None,
        start_url: str | None = None,
    ):
        self.transport = transport
        self._config = {
            "user_data_dir": user_data_dir,
            "headless": headless,
            "user_agent": user_agent,
            "browser_type": browser_type,
            "page_url": page_url,
            "start_url": start_url,
        }
        self._cancelled = False
        self._closed = False
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)

    def _run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def start(self) -> list[str]:
        self._thread.start()
        return self._run(self._start_async())

    async def _start_async(self) -> list[str]:
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        config = self._config
        if self.transport == "persistent":
            user_data_dir = config["user_data_dir"] or str(
                Path.home() / ".rpa_core" / "capture-profile"
            )
            kwargs: dict[str, Any] = {
                "headless": bool(config["headless"]),
                "ignore_default_args": ["--enable-automation"],
            }
            if config["user_agent"]:
                kwargs["user_agent"] = config["user_agent"]
            self._context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir, **kwargs
            )
            self._browser = None
        else:
            ws_url = devtools_active_port_url(
                config["browser_type"], config["user_data_dir"]
            )
            self._browser = await self._playwright.chromium.connect_over_cdp(ws_url)
            contexts = self._browser.contexts
            self._context = contexts[0] if contexts else await self._browser.new_context()

        pages = self._context.pages
        page = None
        if config["page_url"]:
            for candidate in reversed(pages):
                if config["page_url"] in candidate.url:
                    page = candidate
                    break
        if page is None:
            page = pages[-1] if pages else await self._context.new_page()
        self._page = page
        if config["start_url"]:
            await page.goto(config["start_url"], wait_until="domcontentloaded")
        return [candidate.url for candidate in pages]

    @property
    def page_url(self) -> str:
        return self._page.url

    def pick(self, timeout_seconds: float = 60.0, click_css: str | None = None) -> dict:
        return self._run(self._pick_async(timeout_seconds, click_css))

    async def _pick_async(self, timeout_seconds: float, click_css: str | None) -> dict:
        await self._page.evaluate(_PICKER_JS)
        if click_css:
            async def click_later() -> None:
                await self._page.wait_for_timeout(300)
                try:
                    await self._page.evaluate(
                        "(sel) => { const el = document.querySelector(sel);"
                        " if (el) el.dispatchEvent(new MouseEvent('click', {bubbles: true})); }",
                        click_css,
                    )
                except Exception:
                    pass

            asyncio.ensure_future(click_later())
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self._cancelled or self._closed:
                await self._safe(_CANCEL_JS)
                return {"cancelled": True}
            try:
                await self._page.wait_for_function(_DONE_JS, timeout=500)
            except Exception:
                continue
            data = await self._page.evaluate(_RESULT_JS)
            await self._safe(_CLEANUP_JS)
            if data == "cancelled":
                return {"cancelled": True}
            if not isinstance(data, dict):
                continue
            data["kind"] = "browser"
            data["selector"] = {"css": data.get("selector")}
            data["metadata"] = {
                key: data.pop(key)
                for key in ("tag", "id", "classes", "text", "rect")
                if key in data
            }
            data["url"] = self._page.url
            return data
        await self._safe(_CLEANUP_JS)
        return {"timeout": True}

    async def _safe(self, expression: str) -> None:
        try:
            await self._page.evaluate(expression)
        except Exception:
            pass

    def cancel(self) -> None:
        self._cancelled = True

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._cancelled = True

        async def _close_async() -> None:
            try:
                if getattr(self, "_browser", None) is not None:
                    await self._browser.close()
                elif getattr(self, "_context", None) is not None:
                    await self._context.close()
            except Exception:
                pass
            try:
                await self._playwright.stop()
            except Exception:
                pass

        if self._thread.is_alive():
            self._run(_close_async())
            self._loop.call_soon_threadsafe(self._loop.stop)
