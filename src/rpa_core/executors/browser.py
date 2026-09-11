import asyncio
import base64
import re
import time
import uuid
from pathlib import Path
from typing import Any

from rpa_core.extension_exec import ExtensionChannelError
from rpa_core.extension_launch import (
    BrowserLaunchError,
    find_extension_dir,
    launch_browser,
)
from rpa_core.model.command import (
    CommandInvocation,
    CommandResult,
    EffectKind,
    EffectRecord,
)
from rpa_core.model.errors import ErrorCode

from .base import CommandExecutor
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


# 已带 scheme（http(s)/file/data/about 等）则原样保留；否则默认补 https（对用户友好）
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")

# 首次创建标签页时的等待下限：待覆盖插件 MV3 service worker 的唤醒周期（约 30s），
# 否则刚进入休眠的插件会在等待窗口内无人领取，被误判「离线」而去拉起新浏览器。
_EXT_SW_WAKE_SECONDS = 45.0


def _ensure_scheme(url: str) -> str:
    stripped = url.strip()
    if not stripped:
        return url
    if _SCHEME_RE.match(stripped):
        return stripped
    return f"https://{stripped}"


class PlaywrightExecutor(CommandExecutor):
    def __init__(self, ext_session=None):
        # 自研扩展单通道：会话 = 用户真实浏览器里的一个标签页句柄
        self._ext = ext_session or ExtensionExecSession()
        self._ext_sessions: dict[str, str] = {}  # sessionId -> tabId
        # sessionId -> 宿主浏览器（创建会话时绑定），后续元素操作据此路由到正确浏览器实例
        self._ext_session_hosts: dict[str, str] = {}

    async def execute(
        self, invocation: CommandInvocation, cancellation: asyncio.Event
    ) -> CommandResult:
        if cancellation.is_set():
            return CommandResult(status="cancelled")
        started = time.monotonic()
        command = invocation.command_id
        inputs = invocation.inputs
        try:
            ext_session_id = str(inputs.get("sessionId") or "")
            # 打开网页（action=goto）且尚无会话 → 在用户真实浏览器里新建标签页会话
            if (
                command == "browser.navigate"
                and str(inputs.get("action") or "goto") == "goto"
                and (not ext_session_id or ext_session_id not in self._ext_sessions)
            ):
                if not inputs.get("url"):
                    return CommandResult.failure(
                        ErrorCode.INVALID_INPUT, "url is required when action=goto"
                    )
                return await self._open_extension(invocation, inputs, started)
            # 其余命令（含既有会话上的 navigate goto/back/forward/reload）走扩展会话
            if ext_session_id and ext_session_id in self._ext_sessions:
                return await self._execute_extension(
                    command, invocation, inputs, ext_session_id, started, cancellation
                )
            # 无有效扩展会话：优先给「扩展离线」的可操作错误，其次报「缺少会话」
            offline = await self._extension_preflight(inputs)
            if offline is not None:
                return offline
            return CommandResult.failure(
                ErrorCode.EXECUTOR_FAILED,
                f"browser 命令 {command} 缺少有效会话：请先执行 browser.navigate 打开网页，"
                "并确保自研扩展在线。",
                details={
                    "requiredField": "sessionId",
                    "sessionId": ext_session_id or None,
                },
            )
        except ExtensionChannelError as exc:
            return self._ext_channel_failure(exc)
    def _ext_offline_result(self) -> CommandResult:
        """扩展通道离线：给出可照着做的排查步骤（不白等 timeoutMs）。"""
        hint = (
            "请依次确认 ① 目标浏览器（Chrome/Edge）已打开；② 扩展已加载并启用"
            "（edge://extensions 或 chrome://extensions 里点「重新加载」）；"
            "③ devserver 正在运行且扩展指向的端口一致"
        )
        return CommandResult.failure(
            ErrorCode.EXECUTOR_FAILED,
            f"浏览器执行通道当前离线：没有检测到浏览器里的自研插件在轮询命令。{hint}。"
            "浏览器执行已统一收敛到自研扩展单通道，请确认扩展已安装并启用。",
            details={"reason": "channel_offline"},
        )

    async def _extension_preflight(self, inputs: dict[str, Any]) -> CommandResult | None:
        """扩展通道前置检查：扩展必须在线（离线立即失败，不白等整段命令超时）。

        为什么必须前置：扩展通道的命令是「入队等扩展来领」，扩展不在线时命令会在队列里
        躺满 timeoutMs（默认 30s）才以 TIMEOUT 返回。用户看到的现象只是「执行后没有打开
        浏览器」，完全无从判断是插件没装、浏览器没开。这里提前 <1s 给出可操作的失败原因。
        """
        try:
            status = await asyncio.to_thread(self._ext.status)
        except Exception:  # noqa: BLE001 - 探测失败一律按离线处理
            status = {"online": False}
        if status.get("online"):
            return None
        return self._ext_offline_result()

    async def _fresh_hosts(self) -> list[str]:
        """获取当前在线浏览器清单（非缓存，供自启后轮询）。"""
        try:
            status = await asyncio.to_thread(self._ext.client.status)
        except Exception:  # noqa: BLE001 - 探测失败一律按离线处理
            status = {}
        hosts = status.get("hosts")
        return [str(h) for h in hosts] if isinstance(hosts, list) else []

    async def _is_any_extension_active(self) -> bool:
        """近窗口内是否有任意浏览器插件在轮询（含 SW 休眠中的活跃宿主）。"""
        try:
            status = await asyncio.to_thread(self._ext.client.status)
        except Exception:  # noqa: BLE001 - 探测失败一律视为无活跃宿主
            return False
        if status.get("online"):
            return True
        hosts = status.get("hosts")
        return isinstance(hosts, list) and bool(hosts)

    async def _create(
        self, url: str, timeout_s: float, target_host: str | None
    ) -> dict[str, Any] | CommandResult:
        """发一次 tabs.create；通道错误收敛为可读 CommandResult（含失败）。"""
        try:
            return await asyncio.to_thread(
                self._ext.tabs_create, url,
                timeout_seconds=timeout_s, target_host=target_host,
            )
        except ExtensionChannelError as exc:
            return self._ext_channel_failure(exc)

    async def _launch_then_create(
        self, target_host: str, inputs: dict[str, Any], url: str, timeout_s: float
    ) -> dict[str, Any] | CommandResult:
        """拉起目标浏览器 → 等待插件上线 → 重试创建标签页。"""
        launched = await self._launch_target(target_host, inputs, url)
        if isinstance(launched, CommandResult):
            return launched
        return await self._create(url, timeout_s, target_host)

    async def _open_with_launch(
        self, target_host: str | None, inputs: dict[str, Any], url: str, timeout_s: float
    ) -> dict[str, Any] | CommandResult:
        """命令级驱动地创建标签页：直接把 tabs.create 入队由目标插件领取。

        不用 hosts「在线窗口」预判放弃（插件 MV3 SW 休眠约 30s，窗口判在线/离线会抖动）。
        分两条路：
        - 完全无插件在跑（online False）→ 无对象可唤醒，直接离线失败或拉起浏览器（不空等）。
        - 有活跃宿主（其中可能有闲置到休眠的目标插件）→ 命令入队等其领取，等待覆盖 SW
          唤醒周期；只有确实无人领取（TIMEOUT）且指定了 browserType 才拉起后重试一次。
        """
        if not await self._is_any_extension_active():
            if not target_host:
                return self._ext_offline_result()
            return await self._launch_then_create(target_host, inputs, url, timeout_s)
        first_wait = max(timeout_s, _EXT_SW_WAKE_SECONDS)
        try:
            return await asyncio.to_thread(
                self._ext.tabs_create, url,
                timeout_seconds=first_wait, target_host=target_host,
            )
        except ExtensionChannelError as exc:
            # 仅「无人领取（超时）或目标不在线」才值得尝试拉起；其它错误原样上抛
            if exc.code not in ("TIMEOUT", "TARGET_HOST_OFFLINE"):
                return self._ext_channel_failure(exc)
        if not target_host:
            return self._ext_offline_result()
        return await self._launch_then_create(target_host, inputs, url, timeout_s)

    async def _launch_target(
        self, target_host: str, inputs: dict[str, Any], url: str
    ) -> CommandResult | None:
        """自动拉起目标浏览器并等待其插件上线；成功返回 None，否则返回可操作报错。"""
        browser = target_host
        command_line_args = inputs.get("commandLineArgs")
        if not isinstance(command_line_args, list):
            command_line_args = []
        argv_extra = [str(a) for a in command_line_args if isinstance(a, str)]
        # 是否显式走了独立用户数据目录（临时注入插件），决定拉起失败后如何引导
        isolated_dir = any(a.startswith("--user-data-dir") for a in argv_extra)
        extension_dir = find_extension_dir()
        try:
            await asyncio.to_thread(
                launch_browser, browser, url,
                extension_dir=extension_dir, argv_extra=argv_extra,
            )
        except BrowserLaunchError as exc:
            return CommandResult.failure(
                ErrorCode.EXECUTOR_FAILED,
                f"目标浏览器 {browser} 离线且自动拉起失败：{exc}",
                details={"reason": "browser_launch_failed", "browser": browser},
            )
        # 拉起后轮询目标浏览器上线（自启 + 插件注册通常 < 15s）
        if await self._wait_target_online(browser, 15.0):
            return None
        if isolated_dir:
            msg = (
                f"已用独立目录临时拉起 {browser}，但在限时内仍未检测到其自研插件上线。"
                "请确认 ①--user-data-dir=<目录> 可写；②自研扩展已被注入（--load-extension 生效）；"
                "③devserver 端口与插件一致（默认 127.0.0.1:8765）。"
            )
        else:
            msg = (
                f"已尝试自动拉起 {browser}（默认配置），但在限时内未检测到其自研插件在线。"
                "默认配置下命令行注入会被忽略，需在目标浏览器默认配置里预装并启用自研插件："
                f"打开 edge://extensions 或 chrome://extensions，开启「开发者模式」加载扩展目录；"
                "或在本指令「命令行参数」里传 --user-data-dir=<独立目录> 以独立目录临时加载插件。"
            )
        return CommandResult.failure(
            ErrorCode.EXECUTOR_FAILED,
            msg,
            details={
                "reason": "browser_launch_no_host",
                "browser": browser,
                "isolatedDir": isolated_dir,
            },
        )

    async def _wait_target_online(self, target: str, timeout_s: float) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if target in await self._fresh_hosts():
                return True
            await asyncio.sleep(0.4)
        return False

    async def _open_extension(
        self, invocation: CommandInvocation, inputs: dict[str, Any], started: float
    ) -> CommandResult:
        """打开网页（扩展通道）= 在目标浏览器里新建标签页；目标浏览器离线时先自动拉起。"""
        url = _ensure_scheme(str(inputs["url"]))
        timeout_s = int(inputs.get("timeoutMs", 30_000)) / 1000.0
        target_host = str(inputs.get("browserType") or "").strip().lower() or None
        # 命令级驱动：把 tabs.create 入队由目标插件领取（含等待 SW 唤醒），
        # 命令确实无人领取且指定了 browserType 时才自动拉起新浏览器。
        opened = await self._open_with_launch(target_host, inputs, url, timeout_s)
        if isinstance(opened, CommandResult):
            return opened
        on_timeout = str(inputs.get("onTimeout") or "error").strip().lower()
        # 页面未在 timeoutMs 内加载完成时，按影刀「加载超时后执行」策略处理：
        # stop=停止网页加载并继续；error（默认）=视为加载超时失败，走引擎错误处理。
        if bool(opened.get("timedOut")):
            if on_timeout == "stop":
                await asyncio.to_thread(
                    self._ext.stop_loading, str(opened.get("tabId") or ""),
                    target_host=target_host,
                )
            else:
                return CommandResult.failure(
                    ErrorCode.TIMEOUT,
                    f"页面加载超时（timeoutMs={int(inputs.get('timeoutMs', 30_000))}ms）："
                    "网页未在限时内加载完成。可设「加载超时后」为停止网页加载，或调大 timeoutMs。",
                    details={"reason": "navigate_load_timeout", "url": url},
                )
        session_id = str(uuid.uuid4())
        tab_id = str(opened.get("tabId") or "")
        self._ext_sessions[session_id] = tab_id
        # 绑定会话所属实例：优先取「实际创建该标签页的浏览器实例」的 instanceId（由 Hub 补带），
        # 拿不到才回退浏览器名（browserType / 自动）；后续元素操作按实例精确路由。
        instance_id = str(opened.get("instanceId") or "") or ""
        self._ext_session_hosts[session_id] = instance_id or (target_host or "")
        browser_type = str(inputs.get("browserType") or "").strip().lower() or "msedge"
        final_url = str(opened.get("url") or url)
        return CommandResult.success(
            outputs={
                "sessionId": session_id,
                "url": final_url,
                "resourceType": "webPage",
                "browserInstance": instance_id or None,
                "browserType": browser_type,
                "tabId": tab_id,
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
        # 会话所属浏览器：后续每个 tab/浏览器级操作都路由到它，多浏览器并存时不串台
        host = self._ext_session_hosts.get(session_id, "")
        try:
            if command == "browser.close":
                # 扩展会话的「关闭」= 解绑：用户浏览器里的标签页留给用户，不代关
                self._ext_sessions.pop(session_id, None)
                self._ext_session_hosts.pop(session_id, None)
                return self._ext_success(
                    invocation, EffectKind.SESSION, resource, {"operation": "detach"},
                    outputs={"sessionId": session_id},
                )
            # 关闭（解绑）不依赖扩展在线；其余命令前先确认扩展还在轮询，
            # 否则会白等 timeoutMs（默认 30s）才报 TIMEOUT。
            offline = await self._extension_preflight(inputs)
            if offline is not None:
                return offline
            if command == "browser.navigate":
                action = str(inputs.get("action") or "goto")
                if action == "goto":
                    result = await asyncio.to_thread(
                        self._ext.tabs_navigate, tab_id, str(inputs["url"]),
                        timeout_seconds=timeout_s, target_host=host,
                    )
                else:
                    result = await asyncio.to_thread(
                        self._ext.tabs_history, tab_id, action,
                        timeout_seconds=timeout_s, target_host=host,
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
                self._ext_session_hosts[new_session] = ""  # attach 页签所在浏览器未知，按任意路由
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
                    args=list(inputs.get("args") or []),
                    timeout_seconds=timeout_s, target_host=host,
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
                    args={"infoType": info_type},
                    timeout_seconds=timeout_s, target_host=host,
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
                    invocation, inputs, session_id, tab_id, selector,
                    timeout_s, host, cancellation,
                )
            return await self._ext_phase_d(
                command, invocation, inputs, session_id, tab_id, selector, timeout_s, host
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
        target_host: str,
    ) -> CommandResult:
        method = _EXT_PAGE_METHODS[command]
        resource = f"browser.session:{session_id}:selector:{selector}"
        payload = await asyncio.to_thread(
            self._ext.page_call, tab_id, selector, method,
            args=self._ext_method_args(command, inputs),
            timeout_seconds=timeout_s, target_host=target_host,
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

    async def _ext_phase_d(
        self,
        command: str,
        invocation: CommandInvocation,
        inputs: dict[str, Any],
        session_id: str,
        tab_id: str,
        selector: str,
        timeout_s: float,
        target_host: str,
    ) -> CommandResult:
        """阶段 D：扩展补齐命令路由（DOM 读取/写入原语 + cookie + 浏览器级原语）。

        原 13 条（_EXT_PAGE_METHODS）仍复用 _ext_page_command；未实现的 upload/download/
        handleDialog 落「该能力扩展暂未实现」的显式错误（保留 manifest 字段，不硬失败）。
        """
        if command in _EXT_PAGE_METHODS:
            return await self._ext_page_command(
                command, invocation, inputs, session_id, tab_id, selector,
                timeout_s, target_host,
            )
        resource = f"browser.session:{session_id}"
        # -- DOM 读取/写入原语（page.call） -----------------------------------
        page_methods = {
            "browser.queryAll": ("queryAll", EffectKind.READ),
            "browser.getPosition": ("getPosition", EffectKind.READ),
            "browser.getScrollPosition": ("getScrollPosition", EffectKind.READ),
            "browser.getSelectOptions": ("getSelectOptions", EffectKind.READ),
            "browser.setValue": ("setValue", EffectKind.UNSAFE_WRITE),
            "browser.setAttribute": ("setAttribute", EffectKind.UNSAFE_WRITE),
            "browser.drag": ("drag", EffectKind.UNSAFE_WRITE),
        }
        if command in page_methods:
            method, effect = page_methods[command]
            payload = await asyncio.to_thread(
                self._ext.page_call, tab_id, selector, method,
                args=self._phase_d_method_args(command, inputs),
                timeout_seconds=timeout_s, target_host=target_host,
            )
            count = int(payload.get("matchedCount") or 0)
            if count == 0:
                return self._ext_not_found(inputs)
            result = payload.get("result")
            op = command.rsplit(".", 1)[-1]
            if command == "browser.queryAll":
                items = [str(v) for v in result] if isinstance(result, list) else []
                return self._ext_success(
                    invocation, effect, resource + f":selector:{selector}",
                    {"operation": op, "matchedCount": count},
                    outputs={"items": items, "count": count, "sessionId": session_id},
                )
            if command == "browser.getPosition":
                box = dict(result) if isinstance(result, dict) else {}
                return self._ext_success(
                    invocation, effect, resource + f":selector:{selector}",
                    {"operation": op, "matchedCount": count},
                    outputs={**box, "sessionId": session_id},
                )
            if command == "browser.getScrollPosition":
                pos = dict(result) if isinstance(result, dict) else {}
                return self._ext_success(
                    invocation, effect, resource + f":selector:{selector}",
                    {"operation": op, "matchedCount": count},
                    outputs={"scrollX": pos.get("scrollX", 0), "scrollY": pos.get("scrollY", 0)},
                )
            if command == "browser.getSelectOptions":
                data = dict(result) if isinstance(result, dict) else {}
                options = data.get("options") if isinstance(data.get("options"), list) else []
                return self._ext_success(
                    invocation, effect, resource + f":selector:{selector}",
                    {"operation": op, "matchedCount": count},
                    outputs={"options": options, "count": int(data.get("count") or len(options))},
                )
            return self._ext_success(
                invocation, effect, resource + f":selector:{selector}",
                {"operation": op, "matchedCount": count},
                outputs={"matchedCount": count, "sessionId": session_id},
            )
        # -- Cookie（chrome.cookies，作用域 url 由扩展按 tab 当前页推导） -------
        if command == "browser.cookieGetAll":
            cookies = await asyncio.to_thread(
                self._ext.cookies_get_all, timeout_seconds=timeout_s, target_host=target_host,
            )
            return self._ext_success(
                invocation, EffectKind.READ, resource,
                {"operation": "cookieGetAll", "count": len(cookies)},
                outputs={"cookies": cookies, "count": len(cookies)},
            )
        if command == "browser.cookieGet":
            value = await asyncio.to_thread(
                self._ext.cookies_get, str(inputs["name"]),
                timeout_seconds=timeout_s, target_host=target_host,
            )
            return self._ext_success(
                invocation, EffectKind.READ, resource,
                {"operation": "cookieGet", "name": inputs.get("name")},
                outputs={"value": value if value is not None else ""},
            )
        if command == "browser.cookieSet":
            count = await asyncio.to_thread(
                self._ext.cookies_set, list(inputs.get("cookies") or []),
                timeout_seconds=timeout_s, target_host=target_host,
            )
            return self._ext_success(
                invocation, EffectKind.IDEMPOTENT_WRITE, resource,
                {"operation": "cookieSet", "count": count},
                outputs={"count": count},
            )
        if command == "browser.cookieRemove":
            await asyncio.to_thread(
                self._ext.cookies_remove, str(inputs.get("name") or ""),
                timeout_seconds=timeout_s, target_host=target_host,
            )
            return self._ext_success(
                invocation, EffectKind.UNSAFE_WRITE, resource,
                {"operation": "cookieRemove"},
                outputs={"sessionId": session_id},
            )
        # -- 浏览器级原语 ------------------------------------------------------
        if command == "browser.stopLoading":
            result = await asyncio.to_thread(
                self._ext.stop_loading, tab_id,
                timeout_seconds=timeout_s, target_host=target_host,
            )
            return self._ext_success(
                invocation, EffectKind.UNSAFE_WRITE, resource,
                {"operation": "stopLoading"},
                outputs={"url": str(result.get("url") or "")},
            )
        if command == "browser.waitLoad":
            result = await asyncio.to_thread(
                self._ext.wait_load, tab_id,
                timeout_ms=int(inputs.get("timeoutMs", 30_000)),
                timeout_seconds=timeout_s, target_host=target_host,
            )
            return self._ext_success(
                invocation, EffectKind.READ, resource,
                {"operation": "waitLoad"},
                outputs={"url": str(result.get("url") or "")},
            )
        if command == "browser.screenshot":
            save_path = str(inputs["savePath"])
            result = await asyncio.to_thread(
                self._ext.screenshot, tab_id,
                timeout_seconds=timeout_s, target_host=target_host,
            )
            data_url = str(result.get("dataUrl") or "")
            try:
                b64 = data_url.split(",", 1)[1] if "," in data_url else ""
                raw = base64.b64decode(b64) if b64 else b""
            except Exception:  # noqa: BLE001 - 扩展截图失败按落盘失败处理
                raw = b""
            if not raw:
                return CommandResult.failure(
                    ErrorCode.EXECUTOR_FAILED,
                    "screenshot: 扩展未返回有效截图数据",
                    details={"channel": "extension"},
                )
            file_path = await asyncio.to_thread(self._write_screenshot, save_path, raw)
            return self._ext_success(
                invocation, EffectKind.SESSION, resource,
                {"operation": "screenshot", "filePath": file_path},
                outputs={"filePath": file_path},
            )
        # -- 能力暂未实现（保留 manifest 字段，给出可操作错误） ----------------
        if command in ("browser.upload", "browser.download", "browser.handleDialog"):
            return CommandResult.failure(
                ErrorCode.COMMAND_NOT_FOUND,
                f"{command}：该能力扩展暂未实现（自研扩展单通道下本期未覆盖）。",
                details={"command": command},
            )
        return CommandResult.failure(
            ErrorCode.COMMAND_NOT_FOUND,
            f"Unsupported command on extension channel: {command}",
        )

    def _write_screenshot(self, save_path: str, raw: bytes) -> str:
        """截图落盘（同步，经 asyncio.to_thread 调用；返回绝对路径字符串）。"""
        target = Path(save_path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        return str(target)

    def _phase_d_method_args(self, command: str, inputs: dict[str, Any]) -> dict[str, Any]:
        """阶段 D DOM 命令参数 → 扩展 page.call 参数。"""
        if command == "browser.setValue":
            return {"setWay": inputs.get("setWay") or "value",
                    "value": "" if inputs.get("value") is None else str(inputs["value"])}
        if command == "browser.setAttribute":
            return {"name": str(inputs.get("name") or ""),
                    "value": "" if inputs.get("value") is None else str(inputs["value"])}
        if command == "browser.drag":
            return {"targetSelector": str(inputs.get("targetSelector") or "")}
        return {}

    async def _ext_wait_for(
        self,
        invocation: CommandInvocation,
        inputs: dict[str, Any],
        session_id: str,
        tab_id: str,
        selector: str,
        timeout_s: float,
        target_host: str,
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
                args={"visible": want_visible},
                timeout_seconds=min(10.0, timeout_s), target_host=target_host,
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
        """扩展通道错误整形：把协议码翻译成用户能照着做的说明。"""
        if exc.code == "TIMEOUT":
            return CommandResult.failure(
                ErrorCode.TIMEOUT,
                "extension channel: 命令已下发但扩展未在时限内回应——浏览器可能已关闭、"
                "扩展被禁用，或浏览器被挂起（MV3 service worker 休眠）。"
                "请确认目标浏览器仍在运行且扩展已启用后重试。",
                details={"channel": "extension", "code": exc.code, "reason": "no_response"},
            )
        if exc.code == "CHANNEL_OFFLINE":
            return CommandResult.failure(
                ErrorCode.EXECUTOR_FAILED,
                "extension channel: 连不上本机 devserver 的命令端点（RPA_EXT_HUB_URL）——"
                "请确认 devserver 正在运行；重启过端口变化时需一并重启 devserver，"
                "让 run 子进程拿到正确端口。",
                details={"channel": "extension", "code": exc.code, "reason": "hub_unreachable"},
            )
        if exc.code == "TARGET_HOST_OFFLINE":
            return CommandResult.failure(
                ErrorCode.EXECUTOR_FAILED,
                f"目标浏览器的自研插件未在线（{exc}）——请在浏览器类型指定的 Chrome/Edge 里"
                "安装并启用插件后重试，或在指令里改选已装插件的浏览器。",
                details={"channel": "extension", "code": exc.code, "reason": "target_host_offline"},
            )
        return CommandResult.failure(
            ErrorCode.EXECUTOR_FAILED,
            f"extension channel: {exc}",
            details={"channel": "extension", "code": exc.code},
        )

    async def close(self) -> None:
        # 扩展单通道（M15）：只解绑，不动用户浏览器里的标签页（默认整浏览器权限≠代管生命周期）
        self._ext_sessions.clear()
