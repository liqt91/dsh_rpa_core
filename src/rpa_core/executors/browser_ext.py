"""自研扩展执行会话（M15 Phase 1）：browser.* 命令的扩展通道底层操作集。

一等公民通道的执行侧：本模块只做「op 封装」（tabs / page / cookies），命令 →
op 的映射与结果整形在 `browser.PlaywrightExecutor`（两条浏览器通道共用）。
通信走 `rpa_core.extension_exec.ExtensionExecClient`（HTTP → devserver → 扩展
background → content/scripting），阻塞语义由调用方放线程池调度（规则 11）。

会话模型：扩展运行在**用户真实浏览器**里，没有「启动浏览器」概念——一个 rpa_core
会话 = 一个浏览器标签页句柄（`tabId`）；`navigate goto` 新建标签页，
`attach` 绑定已有标签页。权限默认「整个浏览器」（见 extension_exec.DEFAULT_PERMISSIONS）。
"""

from typing import Any

from rpa_core.extension_exec import (
    DEFAULT_COMMAND_TIMEOUT_SECONDS,
    ExtensionExecClient,
)

# page.call 支持的原语（DOM 操作统一走扩展注入函数，避免页面 CSP 限制 eval）
PAGE_CALL_METHODS = (
    "click",
    "hover",
    "input",
    "getText",
    "count",
    "select",
    "check",
    "scroll",
)


class ExtensionExecSession:
    """扩展执行客户端包装（同步；执行器经 asyncio.to_thread 调用）。"""

    def __init__(self, *, client: ExtensionExecClient | None = None):
        self._client = client or ExtensionExecClient()

    @property
    def client(self) -> ExtensionExecClient:
        return self._client

    def online(self) -> bool:
        return self._client.online()

    def status(self) -> dict[str, Any]:
        """通道状态（含 host：扩展宿主浏览器）；探测失败返回 {"online": False}。"""
        return self._client.status_cached()

    def host(self) -> dict[str, Any] | None:
        """扩展宿主浏览器信息（browser/version/userAgent/platform）；未上报返回 None。"""
        return self._client.host()

    # -- 能力探测 ------------------------------------------------------------

    def ping(self, *, timeout_seconds: float = 5.0) -> dict[str, Any]:
        return self._client.submit("ping", {}, timeout_seconds=timeout_seconds)

    # -- 标签页（chrome.tabs） -----------------------------------------------

    def tabs_list(self, *, timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS) -> list[dict]:
        payload = self._client.submit("tabs.list", {}, timeout_seconds=timeout_seconds)
        tabs = payload.get("tabs")
        return [dict(t) for t in tabs] if isinstance(tabs, list) else []

    def tabs_create(
        self,
        url: str,
        *,
        active: bool = True,
        timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        return self._client.submit(
            "tabs.create",
            {"url": url, "active": active},
            timeout_seconds=timeout_seconds,
        )

    def tabs_navigate(
        self, tab_id: str, url: str, *, timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS
    ) -> dict[str, Any]:
        return self._client.submit(
            "tabs.navigate",
            {"tabId": tab_id, "url": url},
            timeout_seconds=timeout_seconds,
        )

    def tabs_history(
        self,
        tab_id: str,
        action: str,
        *,
        timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        return self._client.submit(
            "tabs.history",
            {"tabId": tab_id, "action": action},
            timeout_seconds=timeout_seconds,
        )

    def tabs_close(
        self, tab_id: str, *, timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS
    ) -> dict[str, Any]:
        return self._client.submit(
            "tabs.close", {"tabId": tab_id}, timeout_seconds=timeout_seconds
        )

    # -- 页面（scripting 注入，主 frame） ------------------------------------

    def page_call(
        self,
        tab_id: str,
        selector: str,
        method: str,
        *,
        args: dict[str, Any] | None = None,
        timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        if method not in PAGE_CALL_METHODS:
            raise ValueError(f"unsupported page method: {method}")
        return self._client.submit(
            "page.call",
            {"tabId": tab_id, "selector": selector, "method": method, "args": args or {}},
            timeout_seconds=timeout_seconds,
        )

    def page_eval(
        self,
        tab_id: str,
        script: str,
        *,
        args: list[Any] | None = None,
        timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        return self._client.submit(
            "page.eval",
            {"tabId": tab_id, "script": script, "args": args or []},
            timeout_seconds=timeout_seconds,
        )

    # -- Cookie（chrome.cookies，浏览器级） ----------------------------------

    def cookies_get_all(self, *, filters: dict[str, Any] | None = None,
                        timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS) -> list[dict]:
        payload = self._client.submit(
            "cookies.getAll", dict(filters or {}), timeout_seconds=timeout_seconds
        )
        cookies = payload.get("cookies")
        return [dict(c) for c in cookies] if isinstance(cookies, list) else []

    def cookies_get(self, name: str, *, url: str | None = None,
                    timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS) -> str | None:
        args: dict[str, Any] = {"name": name}
        if url:
            args["url"] = url
        payload = self._client.submit("cookies.get", args, timeout_seconds=timeout_seconds)
        value = payload.get("value")
        return None if value is None else str(value)

    def cookies_set(self, cookies: list[dict], *,
                    timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS) -> int:
        payload = self._client.submit(
            "cookies.set", {"cookies": cookies}, timeout_seconds=timeout_seconds
        )
        return int(payload.get("count") or 0)

    def cookies_remove(self, name: str, *, url: str | None = None,
                       timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS) -> int:
        args: dict[str, Any] = {"name": name}
        if url:
            args["url"] = url
        payload = self._client.submit("cookies.remove", args, timeout_seconds=timeout_seconds)
        return int(payload.get("count") or 0)
