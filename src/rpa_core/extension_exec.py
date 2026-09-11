"""自研扩展执行通道（M15 Phase 1）：协议 + 宿主端队列 + 执行器端客户端。

战略前提（维护者决策）：浏览器自动化统一收敛到**自研 MV3 扩展**（一等公民、唯一执行通道）。
本模块是该通道的唯一定义点：

- `ExtensionExecHub`：devserver 进程持有，命令队列 + 长轮询下发 + 结果回收。
- `ExtensionExecClient`：执行器侧（`rpa-core run` 子进程）经 HTTP 提交命令等结果。
- 权限：**默认整个浏览器**（`{"mode": "browser"}`）；`tabs` / `origins` 两种收窄模式
  为预留接口（协议与校验点先立，收窄只是配置变更，不改协议）——对应 persistent
  捕获的会话粒度先立，默认不限制范围。

分层：devserver 与 executors 均可 import（本模块不入 model/，不在 devserver 隔离
禁列表内）。传输协议：HTTP + JSON（与捕获通道同构；无 token 配对——devserver 仅
绑定 127.0.0.1，loopback 本地工具不做应用层鉴权）。
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
# 否则两次轮询之间的 15~20 秒会被误判离线。
# 单个宿主被判定为「仍在线」的最大无轮询时间（秒）。
# 必须显著大于插件 MV3 的 SW 回收休眠周期（约 30s，靠 30s 的 rpa-exec alarm 唤醒），
# 否则浏览器在跑、插件要休眠时会被误判离线，进而触发不必要的 `--new-window`（并入现有实例）。
ONLINE_WINDOW_SECONDS = 60.0


def browser_name_from_user_agent(user_agent: str) -> str | None:
    """从 UA 推断宿主浏览器标识（仅用于状态展示；无法识别返回 None）。

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
        # 扩展宿主身份（按实例唯一 id 归档，同浏览器不同 profile 各自独立，支持多实例路由）。
        # 结构：{key: {"record": {...}, "at": 心跳}}，key=instanceId（无 id 时退化为浏览器名）。
        self._hosts: dict[str, dict[str, Any]] = {}

    # -- 宿主身份 ------------------------------------------------------------

    @property
    def host(self) -> dict[str, Any] | None:
        """最近上报的非离线宿主浏览器信息；无在线宿主返回 None（兼容旧字段）。"""
        with self._cond:
            for record in self._hosts.values():
                if time.monotonic() - record["at"] < self._online_window:
                    return dict(record["record"])
            return None

    @property
    def hosts(self) -> list[str]:
        """在线扩展宿主浏览器名列表（去重，用于 targetHost=浏览器名路由与前端可选项）。"""
        return [r.get("browser") for r in self.instances() if r.get("browser")]

    def instances(self) -> list[dict[str, Any]]:
        """在线扩展宿主实例详情（按实例 id 归档；新版扩展带 instanceId，旧版按浏览器名退化）。"""
        window = self._online_window
        with self._cond:
            now = time.monotonic()
            return [
                dict(rec["record"])
                for rec in self._hosts.values()
                if now - rec["at"] < window
            ]

    def record_host(self, report: dict[str, Any] | None) -> dict[str, Any] | None:
        """记录扩展宿主（按实例唯一 id 归档；浏览器名作 label）。

        同时刷新该实例的在线心跳——扩展能上报身份即证明它在线。
        """
        if not report:
            return self.host
        user_agent = str(report.get("userAgent") or "")
        browser = str(report.get("browser") or "").strip().lower()
        if not browser:
            browser = browser_name_from_user_agent(user_agent) or ""
        instance_id = str(report.get("instanceId") or "").strip()
        key = instance_id or browser  # 旧版扩展无 instanceId 时退化为按浏览器名归档
        host = {
            "browser": browser or None,
            "instanceId": instance_id or None,
            "version": str(report.get("version") or "") or None,
            "userAgent": user_agent or None,
            "platform": str(report.get("platform") or "") or None,
            "reportedAt": time.time(),
        }
        with self._cond:
            self._last_poll = time.monotonic()
            if key:
                self._hosts[key] = {"record": host, "at": time.monotonic()}
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
        """下发命令并阻塞等待扩展结果（宿主侧仅用于 HTTP handler 线程）。

        `command` 可带顶层 `targetHost`（str|None）：明确时仅由该浏览器（或该实例）扩展领取；
        为空/`auto`/`None` = 任意在线扩展可领。

        命令级驱动：不再用 hosts「在线窗口」做下发前预检——插件是 MV3 service worker，
        闲置约 30s 会休眠、不轮询，hosts 窗口判定的在线/离线会随休眠抖动。
        这里直接入队，由实际轮询（或刚被唤醒）的匹配插件领取；无人领取才在 wait_seconds
        后以 TIMEOUT 返回，由调用方决定是否拉起浏览器。避免「睡着片刻被误判离线→误拉起新浏览器」。
        唯一例外：明确指定 targetHost 且**从未在线**（既非浏览器名也非实例 id）→ 快速失败
        TARGET_HOST_OFFLINE，不白等。
        """
        allowed, reason = self.allows(command)
        if not allowed:
            return {
                "ok": False,
                "error": {"code": "PERMISSION_DENIED", "message": reason},
            }
        target = str((command.get("targetHost") or "") or "").strip().lower()
        if target and target != "auto" and not self._target_known(target):
            return {
                "ok": False,
                "error": {
                    "code": "TARGET_HOST_OFFLINE",
                    "message": f"目标浏览器/实例 {target!r} 的自研插件未在线（从未报告或已离线）",
                },
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
            owner_key = entry.get("_hostInstanceId")
            # 仅 tabs.create 需要实例 id（会话绑定用），避免污染其余 op 的结果
            need_instance = str((entry.get("command") or {}).get("op") or "") == "tabs.create"
            self._inflight.pop(command_id, None)
            if result is not None and not isinstance(result, dict):
                result = {"value": result}  # 容错：非 dict 结果归一
            if isinstance(result, dict) and owner_key and need_instance:
                # 补带实例 id：让执行器知道这条命令由哪个实例执行（会话绑定用）
                value = result.get("value")
                if isinstance(value, dict) and not value.get("instanceId"):
                    value["instanceId"] = owner_key
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

    def _target_known(self, target: str) -> bool:
        """target（浏览器名或实例 id）是否已被任一在线实例匹配。"""
        window = self._online_window
        with self._cond:
            now = time.monotonic()
            for rec in self._hosts.values():
                if now - rec["at"] >= window:
                    continue
                record = rec["record"]
                if target == record.get("browser") or target == record.get("instanceId"):
                    return True
        return False

    def next_command(
        self, wait_seconds: float, host_report: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """扩展长轮询取命令；无匹配命令则保持到超时返回 None（同时作为在线心跳）。

        `host_report`：扩展可随每次轮询上报宿主身份（实例 id/浏览器名/版本/UA）——心跳即身份，
        避免额外一次 ping 往返。

        匹配规则：只领取 `targetHost` 为空/`auto`/等于本实例的浏览器名**或实例 id** 的命令；
        不匹配的命令留在队列等对应实例的扩展（或失去时效由 submit 超时撤回）。
        """
        browser = ""
        instance_id = ""
        if host_report:
            reported = str(host_report.get("browser") or "").strip().lower()
            browser = reported or (
                browser_name_from_user_agent(str(host_report.get("userAgent") or "")) or ""
            )
            instance_id = str(host_report.get("instanceId") or "").strip()
        if host_report:
            self.record_host(host_report)
        owner_key = instance_id or browser
        with self._cond:
            self._last_poll = time.monotonic()
            deadline = time.monotonic() + max(0.0, wait_seconds)
            while True:
                # 扫描队列找第一个匹配命令；无则等待，超时返回 None
                picked_id: str | None = None
                for i, cid in enumerate(self._queue):
                    entry = self._inflight.get(cid)
                    if not entry:
                        continue
                    target = (
                        str((entry.get("command") or {}).get("targetHost") or "").strip().lower()
                    )
                    matched = (
                        not target
                        or target == "auto"
                        or (browser and target == browser)
                        or (instance_id and target == instance_id)
                    )
                    if matched:
                        picked_id = cid
                        del self._queue[i]
                        # 记录领取实例：供 submit 给结果补带实例 id（会话绑定用）
                        entry["_hostInstanceId"] = owner_key
                        break
                if picked_id is not None:
                    return dict(self._inflight[picked_id]["command"])
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._cond.wait(remaining)

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
            online = idle < self._online_window
            now = time.monotonic()
            alive = [
                dict(rec["record"]) for rec in self._hosts.values()
                if now - rec["at"] < self._online_window
            ]
            # hosts = 去重的浏览器名（前端置灰 browserType + 自动拉起轮询用，保持兼容）
            hosts = []
            for r in alive:
                b = r.get("browser")
                if b and b not in hosts:
                    hosts.append(b)
            first = alive[0] if alive else None
            return {
                "online": online,
                "lastPollSecondsAgo": None if self._last_poll == 0 else round(idle, 1),
                "queued": len(self._queue),
                "inflight": len(self._inflight),
                "permissions": dict(self._permissions),
                "host": first,
                "hosts": hosts,
                "instances": alive,  # 实例级详情（含 instanceId/browser/version/...）
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
        target_host: str | None = None,
    ) -> dict[str, Any]:
        """提交一条扩展命令并等待结果；失败抛 ExtensionChannelError。

        `target_host`：目标浏览器（如 `msedge`/`chrome`），非空时命令仅由该浏览器扩展执行。
        """
        payload = {
            "op": op,
            "args": args or {},
            "timeoutSeconds": timeout_seconds,
            "tabId": (args or {}).get("tabId"),
            "url": (args or {}).get("url"),
        }
        if target_host:
            payload["targetHost"] = target_host
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
