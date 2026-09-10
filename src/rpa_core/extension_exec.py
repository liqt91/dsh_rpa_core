"""自研扩展执行通道（M15 Phase 1）：协议 + 宿主端队列 + 执行器端客户端。

战略前提（维护者决策）：浏览器自动化优先走**自研 MV3 扩展**（一等公民），
`playwright` / `bsk` 为二等（回退保留）。本模块是该通道的唯一定义点：

- `ExtensionExecHub`：devserver 进程持有，命令队列 + 长轮询下发 + 结果回收。
- `ExtensionExecClient`：执行器侧（`rpa-core run` 子进程）经 HTTP 提交命令等结果。
- 权限：**默认整个浏览器**（`{"mode": "browser"}`）；`tabs` / `origins` 两种收窄模式
  为预留接口（协议与校验点先立，收窄只是配置变更，不改协议）——参考 bsk 的
  session/tab borrow 粒度，但默认不限制范围。

分层：devserver 与 executors 均可 import（本模块不入 model/，不在 devserver 隔离
禁列表内）。传输协议：HTTP + JSON（与捕获通道同构，复用 TOFU token 配对）。
"""

import json
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

# 扩展 background 与执行器都经 devserver 的 /api/ext/* 通信
HUB_URL_ENV = "RPA_EXT_HUB_URL"
DEFAULT_HUB_URL = "http://127.0.0.1:8765"

# 权限：默认「整个浏览器」（全部窗口/标签页/Cookie），与一等公民定位一致
DEFAULT_PERMISSIONS: dict[str, Any] = {"mode": "browser"}
PERMISSION_MODES = ("browser", "tabs", "origins")

# 长轮询保持时长（扩展侧 next / 执行器侧 submit 的默认 hold）
DEFAULT_POLL_SECONDS = 20.0
DEFAULT_COMMAND_TIMEOUT_SECONDS = 30.0
# 在线判定窗口：必须 **大于** 扩展侧单次长轮询 hold（extension.EXEC_HOLD_S = 20），
# 否则两次轮询之间的 15~20 秒会被误判离线，导致缺省通道时偶发回退 playwright。
ONLINE_WINDOW_SECONDS = 30.0

# 浏览器类型取值与 commands/browser/navigate.json 的 channel 对齐
BROWSER_CHANNELS = ("chromium", "chrome", "msedge", "firefox", "webkit")

# Chromium 内核家族（扩展宿主只要落在这个集合里，channel=chromium 即视为匹配）
CHROMIUM_FAMILY = frozenset({"chrome", "msedge", "brave", "opera", "vivaldi", "chromium"})


def browser_name_from_user_agent(user_agent: str) -> str | None:
    """从 UA 推断宿主浏览器标识（取值与 navigate.channel 对齐；无法识别返回 None）。

    顺序敏感：Edge / Opera / Brave 的 UA 里都含 `Chrome/`，必须先判这些壳。
    """
    ua = str(user_agent or "")
    if not ua:
        return None
    if "Edg/" in ua or "EdgA/" in ua or "EdgiOS/" in ua or "EdgDev/" in ua:
        return "msedge"
    if "OPR/" in ua or "Opera" in ua:
        return "opera"
    if "Brave" in ua:
        return "brave"
    if "Vivaldi" in ua:
        return "vivaldi"
    if "Firefox/" in ua or "FxiOS/" in ua:
        return "firefox"
    if "Chrome/" in ua or "CriOS/" in ua:
        return "chrome"
    if "Safari/" in ua:
        return "safari"
    return None


def channel_matches_host(channel: str, host_browser: str | None) -> bool:
    """扩展通道下判定「期望浏览器」与「扩展实际宿主浏览器」是否一致。

    语义（扩展通道没有"启动浏览器"概念，浏览器 = 扩展装在哪）：
    - channel 为空 → True（跟随宿主，不校验）
    - channel=chromium → 宿主为任意 Chromium 内核发行版即通过
    - 其余 → 必须同名（chrome / msedge 等）
    - 宿主未上报（旧版扩展）→ False（无法确认，不假装成功）
    """
    expected = str(channel or "").strip().lower()
    if not expected:
        return True
    actual = str(host_browser or "").strip().lower()
    if not actual:
        return False
    if expected == actual:
        return True
    if expected == "chromium":
        return actual in CHROMIUM_FAMILY
    return False


class ExtensionChannelError(RuntimeError):
    """扩展通道不可用（devserver 未运行 / 扩展未安装或休眠 / 超时）。"""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


class ExtensionExecHub:
    """devserver 侧：扩展执行命令队列（线程安全，长轮询）。"""

    def __init__(
        self,
        *,
        permissions: dict[str, Any] | None = None,
        online_window_seconds: float = ONLINE_WINDOW_SECONDS,
    ):
        self._cond = threading.Condition(threading.Lock())
        self._queue: list[str] = []
        self._inflight: dict[str, dict[str, Any]] = {}
        self._permissions = dict(permissions or DEFAULT_PERMISSIONS)
        self._online_window = online_window_seconds
        self._last_poll = 0.0
        self._seq = 0
        # 扩展宿主浏览器身份（由扩展侧长轮询/ping 上报）：决定"指定 Edge"能否兑现
        self._host: dict[str, Any] | None = None
        # 鉴权失败观测：扩展与本机 devserver token 不匹配时，扩展侧只静默重试，
        # 表现是"插件装了却永远不在线"；留痕后 /api/ext/status 才能解释这个状态。
        self._auth_failures = 0
        self._last_auth_failure: dict[str, Any] | None = None

    # -- 宿主身份 ------------------------------------------------------------

    @property
    def host(self) -> dict[str, Any] | None:
        """最近一次上报的宿主浏览器信息；未上报返回 None。"""
        with self._cond:
            return dict(self._host) if self._host else None

    def record_host(self, report: dict[str, Any] | None) -> dict[str, Any] | None:
        """记录扩展宿主浏览器（浏览器名优先用上报值，缺省从 UA 推断）。

        同时刷新在线心跳——扩展能上报身份即证明它在线。
        """
        if not report:
            return self.host
        user_agent = str(report.get("userAgent") or "")
        browser = str(report.get("browser") or "").strip().lower()
        if not browser:
            browser = browser_name_from_user_agent(user_agent) or ""
        host = {
            "browser": browser or None,
            "version": str(report.get("version") or "") or None,
            "userAgent": user_agent or None,
            "platform": str(report.get("platform") or "") or None,
            "reportedAt": time.time(),
        }
        with self._cond:
            self._last_poll = time.monotonic()
            self._host = host
        return dict(host)

    # -- 权限（预留接口：默认整个浏览器） ------------------------------------

    @property
    def permissions(self) -> dict[str, Any]:
        return dict(self._permissions)

    def set_permissions(self, config: dict[str, Any]) -> dict[str, Any]:
        """收窄/放宽执行范围。默认 `{"mode": "browser"}`（整个浏览器）。"""
        mode = str((config or {}).get("mode") or "")
        if mode not in PERMISSION_MODES:
            raise ValueError(f"unknown permission mode: {mode!r}")
        normalized: dict[str, Any] = {"mode": mode}
        if mode == "tabs":
            normalized["tabIds"] = [str(t) for t in config.get("tabIds") or []]
        elif mode == "origins":
            normalized["allow"] = [str(o) for o in config.get("allow") or []]
        with self._cond:
            self._permissions = normalized
        return self.permissions

    def allows(self, command: dict[str, Any]) -> tuple[bool, str]:
        """命令级放行判定。mode=browser 时一律放行（默认）。"""
        mode = str(self._permissions.get("mode") or "browser")
        if mode == "browser":
            return True, ""
        args = command.get("args") or {}
        if mode == "tabs":
            allowed = {str(t) for t in self._permissions.get("tabIds") or []}
            tab_id = str(args.get("tabId") or "")
            if tab_id and tab_id in allowed:
                return True, ""
            return False, f"tab {tab_id or '(any)'} outside allowed tab scope"
        if mode == "origins":
            allow = [str(o) for o in self._permissions.get("allow") or []]
            url = str(args.get("url") or "")
            if any(url.startswith(origin) for origin in allow):
                return True, ""
            return False, f"url {url or '(none)'} outside allowed origins"
        return False, f"unknown permission mode: {mode}"

    # -- 命令下发 / 结果回收 --------------------------------------------------

    def submit(self, command: dict[str, Any], wait_seconds: float) -> dict[str, Any]:
        """下发命令并阻塞等待扩展结果（宿主侧仅用于 HTTP handler 线程）。"""
        allowed, reason = self.allows(command)
        if not allowed:
            return {
                "ok": False,
                "error": {"code": "PERMISSION_DENIED", "message": reason},
            }
        command_id = f"ext-{uuid.uuid4().hex[:12]}"
        with self._cond:
            self._seq += 1
            entry: dict[str, Any] = {"command": {**command, "id": command_id}, "result": None}
            self._inflight[command_id] = entry
            self._queue.append(command_id)
            self._cond.notify_all()
            deadline = time.monotonic() + max(0.0, wait_seconds)
            while entry["result"] is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._cond.wait(remaining)
            result = entry["result"]
            self._inflight.pop(command_id, None)
            if result is not None:
                return result
            if command_id in self._queue:  # 未下发：直接撤回
                self._queue.remove(command_id)
                detail = "extension not polling"
            else:  # 已下发但扩展未回（页面/标签页卡住或实例离线）
                detail = "extension did not return a result"
            self._cond.notify_all()
        return {
            "ok": False,
            "timedOut": True,
            "error": {"code": "TIMEOUT", "message": f"{command_id}: {detail}"},
        }

    def next_command(
        self, wait_seconds: float, host_report: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """扩展长轮询取命令；无命令则保持到超时返回 None（同时作为在线心跳）。

        `host_report`：扩展可随每次轮询上报宿主身份（浏览器名/版本/UA）——心跳即身份，
        避免额外一次 ping 往返。
        """
        if host_report:
            self.record_host(host_report)
        with self._cond:
            self._last_poll = time.monotonic()
            # 能成功轮询 = token 已配对，清掉历史鉴权失败留痕
            self._auth_failures = 0
            self._last_auth_failure = None
            deadline = time.monotonic() + max(0.0, wait_seconds)
            while not self._queue:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._cond.wait(remaining)
            command_id = self._queue.pop(0)
            entry = self._inflight.get(command_id)
            return dict(entry["command"]) if entry else None

    def record_auth_failure(self, reason: str) -> None:
        """记录一次 token 校验失败（扩展装了但与本机 devserver 未配对）。

        这是"插件明明重载了却不在线"的最隐蔽原因：扩展侧 403 后只退避重试，
        用户毫无感知。留痕后前端可提示"重装/重置配对"。
        """
        with self._cond:
            self._auth_failures += 1
            self._last_auth_failure = {"reason": reason, "at": time.time()}

    def deliver_result(self, payload: dict[str, Any]) -> bool:
        """扩展回传结果；未知 id / 已超时返回 False（前端记录用，不报错）。"""
        command_id = str((payload or {}).get("id") or "")
        with self._cond:
            entry = self._inflight.get(command_id)
            if entry is None:
                self._cond.notify_all()
                return False
            entry["result"] = dict(payload)
            self._cond.notify_all()
            return True

    def status(self) -> dict[str, Any]:
        with self._cond:
            idle = time.monotonic() - self._last_poll
            return {
                "online": self._last_poll > 0 and idle < self._online_window,
                "lastPollSecondsAgo": None if self._last_poll == 0 else round(idle, 1),
                "queued": len(self._queue),
                "inflight": len(self._inflight),
                "permissions": dict(self._permissions),
                "host": dict(self._host) if self._host else None,
                "authFailures": self._auth_failures,
                "lastAuthFailure": (
                    dict(self._last_auth_failure) if self._last_auth_failure else None
                ),
            }


class ExtensionExecClient:
    """执行器侧客户端：HTTP 提交命令并等待结果（阻塞，调用方放线程池）。"""

    def __init__(self, *, base_url: str | None = None, hub_online_ttl: float = 2.0):
        self.base_url = (base_url or os.environ.get(HUB_URL_ENV) or DEFAULT_HUB_URL).rstrip("/")
        self._online_ttl = hub_online_ttl
        self._status_cache: tuple[float, dict[str, Any]] | None = None

    # -- 传输 ----------------------------------------------------------------

    def _request(self, method: str, path: str, payload: dict | None, timeout: float) -> dict:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            method=method,
            headers={"Content-Type": "application/json"} if data else {},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ExtensionChannelError("HUB_HTTP_ERROR", f"{exc.code}: {detail}") from None
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            raise ExtensionChannelError("CHANNEL_OFFLINE", str(exc)) from None

    # -- 能力面 --------------------------------------------------------------

    def online(self) -> bool:
        """扩展是否在线（短 TTL 缓存，避免每步探测）。"""
        return bool(self.status_cached().get("online"))

    def status_cached(self, ttl: float | None = None) -> dict[str, Any]:
        """带 TTL 的通道状态（含宿主浏览器）；探测失败返回 `{"online": False}`。"""
        now = time.monotonic()
        if self._status_cache and now - self._status_cache[0] < (ttl or self._online_ttl):
            return self._status_cache[1]
        try:
            payload = dict(self._request("GET", "/api/ext/status", None, timeout=1.5))
        except ExtensionChannelError:
            payload = {"online": False, "host": None}
        self._status_cache = (now, payload)
        return payload

    def host(self) -> dict[str, Any] | None:
        """扩展宿主浏览器信息（未安装/离线/旧版扩展返回 None）。"""
        host = self.status_cached().get("host")
        return dict(host) if isinstance(host, dict) else None

    def status(self) -> dict[str, Any]:
        return self._request("GET", "/api/ext/status", None, timeout=3.0)

    def submit(
        self,
        op: str,
        args: dict[str, Any] | None = None,
        *,
        timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """提交一条扩展命令并等待结果；失败抛 ExtensionChannelError。"""
        payload = {
            "op": op,
            "args": args or {},
            "timeoutSeconds": timeout_seconds,
            "tabId": (args or {}).get("tabId"),
            "url": (args or {}).get("url"),
        }
        result = self._request(
            "POST",
            "/api/ext/command/submit",
            payload,
            timeout=timeout_seconds + 10.0,
        )
        if not result.get("ok"):
            error = result.get("error") or {}
            raise ExtensionChannelError(
                str(error.get("code") or "EXECUTOR_FAILED"),
                str(error.get("message") or "extension command failed"),
            )
        return result.get("value") if isinstance(result.get("value"), dict) else result
