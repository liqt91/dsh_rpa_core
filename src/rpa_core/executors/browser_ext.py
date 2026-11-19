"""自研扩展执行会话（M15 Phase 1）：browser.* 命令的扩展通道底层操作集。

一等公民通道的执行侧：本模块只做「op 封装」（tabs / page / cookies），命令 →
op 的映射与结果整形在 `browser.PlaywrightExecutor`（扩展单通道宿主的旧称，沿用类名不变）。
通信走 `rpa_core.extension_exec.ExtensionExecClient`（HTTP → devserver → 扩展
background → content/scripting），阻塞语义由调用方放线程池调度（规则 11）。

会话模型：扩展运行在**用户真实浏览器**里，没有「启动浏览器」概念——一个 rpa_core
会话 = 一个浏览器标签页句柄（`tabId`）；`navigate goto` 新建标签页，
`attach` 绑定已有标签页。权限默认「整个浏览器」（见 extension_exec.DEFAULT_PERMISSIONS）。
"""

from typing import Any

from rpa_core.extension_exec import (
    DEFAULT_COMMAND_TIMEOUT_SECONDS,
    ChannelMetrics,
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
    # 阶段 D 补齐：DOM 读取/写入原语
    "queryAll",
    "getPosition",
    "getScrollPosition",
    "getSelectOptions",
    "setValue",
    "setAttribute",
    "drag",
)


class ExtensionExecSession:
    """扩展执行客户端包装（同步；执行器经 asyncio.to_thread 调用）。"""

    def __init__(self, *, client: ExtensionExecClient | None = None):
        self._client = client or ExtensionExecClient()

    @property
    def client(self) -> ExtensionExecClient:
        return self._client

    @property
    def metrics(self) -> ChannelMetrics | None:
        """通道往返度量器（M28 S4）；客户端不是真 ``ExtensionExecClient`` 时返回 None。"""
        return getattr(self._client, "metrics", None)

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

    def tabs_list(
        self,
        *,
        timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
        target_host: str | None = None,
    ) -> list[dict]:
        payload = self._client.submit(
            "tabs.list", {}, timeout_seconds=timeout_seconds, target_host=target_host,
        )
        tabs = payload.get("tabs")
        return [dict(t) for t in tabs] if isinstance(tabs, list) else []

    def tabs_create(
        self,
        url: str,
        *,
        active: bool = True,
        timeout_ms: int | None = None,
        timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
        target_host: str | None = None,
    ) -> dict[str, Any]:
        # timeoutMs 必须进 args：扩展侧 waitComplete 读它做「加载等待」预算，
        # 只抬信封超时会变成「声明了但加载等待恒 30s」（M38 S4.4 真机探针实测抓到）。
        args: dict[str, Any] = {"url": url, "active": active}
        if timeout_ms is not None:
            args["timeoutMs"] = int(timeout_ms)
        return self._client.submit(
            "tabs.create",
            args,
            timeout_seconds=timeout_seconds,
            target_host=target_host,
        )

    def tabs_navigate(
        self,
        tab_id: str,
        url: str,
        *,
        timeout_ms: int | None = None,
        timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
        target_host: str | None = None,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {"tabId": tab_id, "url": url}
        if timeout_ms is not None:
            args["timeoutMs"] = int(timeout_ms)
        return self._client.submit(
            "tabs.navigate",
            args,
            timeout_seconds=timeout_seconds,
            target_host=target_host,
        )

    def tabs_history(
        self,
        tab_id: str,
        action: str,
        *,
        timeout_ms: int | None = None,
        timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
        target_host: str | None = None,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {"tabId": tab_id, "action": action}
        if timeout_ms is not None:
            args["timeoutMs"] = int(timeout_ms)
        return self._client.submit(
            "tabs.history",
            args,
            timeout_seconds=timeout_seconds,
            target_host=target_host,
        )

    def tabs_close(
        self,
        tab_id: str,
        *,
        timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
        target_host: str | None = None,
    ) -> dict[str, Any]:
        return self._client.submit(
            "tabs.close", {"tabId": tab_id},
            timeout_seconds=timeout_seconds, target_host=target_host,
        )

    def tabs_close_many(
        self,
        *,
        tab_ids: list[int] | None = None,
        close_all: bool = False,
        window_id: int | None = None,
        ignore_before_unload: bool | None = None,
        timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
        target_host: str | None = None,
    ) -> dict[str, Any]:
        """批量关闭标签页（M32 S1）：显式 `tab_ids` 或 `close_all`（当前窗口全部）。

        返回 `{"closedTabIds": [...], "failedTabIds": [...]}`——**逐个如实记账**，
        不把「部分失败」折叠成一个布尔值：调用方需要区分「都关了」与「关了一半」。
        `ignore_before_unload`（M37）：None = 未指定，省略不发给扩展——缺省语义由
        扩展兜底（remove 前注入 MAIN world 脚本清除 beforeunload 拦截，best-effort）。
        """
        args: dict[str, Any] = {}
        if tab_ids:
            args["tabIds"] = [int(tab_id) for tab_id in tab_ids]
        if close_all:
            args["all"] = True
        if window_id is not None:
            args["windowId"] = int(window_id)
        if ignore_before_unload is not None:
            args["ignoreBeforeUnload"] = bool(ignore_before_unload)
        return self._client.submit(
            "tabs.closeMany", args,
            timeout_seconds=timeout_seconds, target_host=target_host,
        )

    def tabs_list_windows(
        self,
        *,
        timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
        target_host: str | None = None,
    ) -> list[dict]:
        """窗口清单（只含 id/状态/标签数，不含页面内容）。"""
        payload = self._client.submit(
            "tabs.listWindows", {},
            timeout_seconds=timeout_seconds, target_host=target_host,
        )
        windows = payload.get("windows")
        return [dict(window) for window in windows] if isinstance(windows, list) else []

    # -- 页面（scripting 注入，主 frame） ------------------------------------

    def page_call(
        self,
        tab_id: str,
        selector: str,
        method: str,
        *,
        args: dict[str, Any] | None = None,
        timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
        target_host: str | None = None,
    ) -> dict[str, Any]:
        if method not in PAGE_CALL_METHODS:
            raise ValueError(f"unsupported page method: {method}")
        return self._client.submit(
            "page.call",
            {"tabId": tab_id, "selector": selector, "method": method, "args": args or {}},
            timeout_seconds=timeout_seconds,
            target_host=target_host,
        )

    def page_eval(
        self,
        tab_id: str,
        script: str,
        *,
        args: list[Any] | None = None,
        timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
        target_host: str | None = None,
    ) -> dict[str, Any]:
        return self._client.submit(
            "page.eval",
            {"tabId": tab_id, "script": script, "args": args or []},
            timeout_seconds=timeout_seconds,
            target_host=target_host,
        )

    def cdp_click(
        self,
        tab_id: str,
        selector: str,
        *,
        args: dict[str, Any] | None = None,
        timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
        target_host: str | None = None,
    ) -> dict[str, Any]:
        """CDP 真事件点击（M107 第 2 层）：事件由浏览器自己生成（``isTrusted=true``）。

        与 ``page_call(click)`` 的差别只在事件来源：这里经 ``chrome.debugger`` 的
        ``Input.dispatchMouseEvent`` 派发，既不走页面 ``dispatchEvent``（合成事件），
        也不走 OS 输入队列（SendInput）——所以既躲得开「识别合成事件」的页面，
        也躲得开企业 EDR 对合成输入的拦截。

        坐标与预检仍由页面侧**同一份** ``actionPoint`` 算（扩展内部完成），
        调用方只给选择器，避免 Python/JS 各实现一套选点逻辑。
        参数平铺（扩展 ``cdpClick`` 直接读顶层字段），不嵌在 ``args`` 里。
        """
        payload: dict[str, Any] = {"tabId": tab_id, "selector": selector}
        payload.update(args or {})
        return self._client.submit(
            "cdp.click", payload,
            timeout_seconds=timeout_seconds, target_host=target_host,
        )

    def ext_version(self, target_host: str | None = None) -> str | None:
        """目标浏览器上**在线**扩展的版本号；取不到就返回 ``None``。

        用途只有一个：在使用「只有新版本才有」的 op 之前先对账。M107 实测踩过——
        同一个人在 Chrome 里装的是 0.9.2、Edge 里是 0.9.7，而流程写的是
        ``browserType=chrome``，于是 ``cdp.click`` 被旧扩展回一句
        ``unsupported op: cdp.click``，看起来像「通道坏了」。

        ★ **取不到就返回 None、由调用方决定放行**：这里不做「未知即失败」的判断，
        否则一次状态探测抖动就会把本来能跑的流程拦下来。
        """
        try:
            status = self._client.status()
        except Exception:  # noqa: BLE001 - 探测失败按「未知」处理，不改变主流程
            return None
        for instance in status.get("instances") or []:
            if not isinstance(instance, dict):
                continue
            if target_host and instance.get("browser") != target_host:
                continue
            version = instance.get("extVersion")
            if version:
                return str(version)
        return None

    # -- Cookie（chrome.cookies，浏览器级） ----------------------------------

    # Cookie：作用域 url 由扩展按**会话绑定的标签页**推导（`args.url || tabUrl(args.tabId)`），
    # 所以 `tab_id` 必须传下去——M29 S2 之前它从来没传过，扩展拿到 `undefined` → `""`，
    # chrome.cookies.get/set/remove 等于拿空 url 调 API（cookieGetAll 则退化成浏览器级全量）。
    def cookies_get_all(self, *, filters: dict[str, Any] | None = None, tab_id: str | None = None,
                        timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
                        target_host: str | None = None) -> list[dict]:
        args = dict(filters or {})
        if tab_id:
            args["tabId"] = tab_id
        payload = self._client.submit(
            "cookies.getAll", args,
            timeout_seconds=timeout_seconds, target_host=target_host,
        )
        cookies = payload.get("cookies")
        return [dict(c) for c in cookies] if isinstance(cookies, list) else []

    def cookies_get(self, name: str, *, url: str | None = None, tab_id: str | None = None,
                    timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
                    target_host: str | None = None) -> str | None:
        args: dict[str, Any] = {"name": name}
        if url:
            args["url"] = url
        if tab_id:
            args["tabId"] = tab_id
        payload = self._client.submit(
            "cookies.get", args, timeout_seconds=timeout_seconds, target_host=target_host,
        )
        value = payload.get("value")
        return None if value is None else str(value)

    def cookies_set(self, cookies: list[dict], *, tab_id: str | None = None,
                    timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
                    target_host: str | None = None) -> int:
        args: dict[str, Any] = {"cookies": cookies}
        if tab_id:
            args["tabId"] = tab_id
        payload = self._client.submit(
            "cookies.set", args,
            timeout_seconds=timeout_seconds, target_host=target_host,
        )
        return int(payload.get("count") or 0)

    def cookies_remove(self, name: str, *, url: str | None = None, tab_id: str | None = None,
                       timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
                       target_host: str | None = None) -> int:
        args: dict[str, Any] = {"name": name}
        if url:
            args["url"] = url
        if tab_id:
            args["tabId"] = tab_id
        payload = self._client.submit(
            "cookies.remove", args, timeout_seconds=timeout_seconds, target_host=target_host,
        )
        return int(payload.get("count") or 0)

    # -- 阶段 D 补齐：浏览器级原语（background 扩展 API） ---------------------

    def screenshot(
        self,
        tab_id: str,
        *,
        quality: int | None = None,
        timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
        target_host: str | None = None,
    ) -> dict[str, Any]:
        """整页/可见区截图（background 经 chrome.tabs.captureVisibleTab）：
        返回 `{"dataUrl": "data:image/png;base64,..."}`，由执行器落盘。
        """
        args: dict[str, Any] = {"tabId": tab_id}
        if quality is not None:
            args["quality"] = quality
        return self._client.submit(
            "screenshot", args, timeout_seconds=timeout_seconds, target_host=target_host,
        )

    def stop_loading(
        self,
        tab_id: str,
        *,
        timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
        target_host: str | None = None,
    ) -> dict[str, Any]:
        """停止当前页加载（background 对 tab 调 window.stop），返回当前 url。"""
        return self._client.submit(
            "tabs.stopLoading", {"tabId": tab_id},
            timeout_seconds=timeout_seconds, target_host=target_host,
        )

    def wait_load(
        self,
        tab_id: str,
        *,
        timeout_ms: int = 30_000,
        timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
        target_host: str | None = None,
    ) -> dict[str, Any]:
        """等待页面加载完成（background 复用 waitComplete），返回当前 url。"""
        return self._client.submit(
            "tabs.waitLoad", {"tabId": tab_id, "timeoutMs": timeout_ms},
            timeout_seconds=timeout_seconds, target_host=target_host,
        )
