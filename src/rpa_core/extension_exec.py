"""自研扩展执行通道（M15 起，M20/ADR 0015 改 Native Messaging）。

战略前提（维护者决策）：浏览器自动化统一收敛到**自研 MV3 扩展**（一等公民、唯一执行通道）。
本模块是该通道执行器侧的唯一定义点：

- 传输：扩展经 ``chrome.runtime.connectNative`` 连**浏览器按需拉起的 bridge host**
  （``rpa_core.workers.ext_bridge``），host 与本模块经**本地端点**
  （Windows 命名管道 / POSIX Unix 域套接字，见 ``rpa_core.local_transport``）通信。
  无 8765 常驻服务、无 HTTP 长轮询、无心跳窗口——**端点存在即可用**。
- ``ExtensionExecClient``：执行器侧（``rpa-core run`` 子进程）连端点提交命令等结果。
- 权限：**默认整个浏览器**（``{"mode": "browser"}``）；``tabs`` / ``origins`` 收窄模式为
  预留接口（校验点在扩展侧 ``assertAllowed``）。

分层：executors 可 import（本模块不在 devserver 隔离禁列表内）。
"""

from __future__ import annotations

import queue
import threading
import time
import uuid
from typing import Any

from rpa_core import local_transport

# 端点命名：<prefix><browser>_<instanceId>（见 local_transport.endpoint_name；
# 前缀由 RPA_EXT_ENDPOINT_PREFIX 覆盖，测试据此隔离本机真实端点）
_DEFAULT_ENDPOINT_PREFIX = local_transport._ENDPOINT_PREFIX

# 权限：默认「整个浏览器」（全部窗口/标签页/Cookie），与一等公民定位一致
DEFAULT_PERMISSIONS: dict[str, Any] = {"mode": "browser"}
PERMISSION_MODES = ("browser", "tabs", "origins")

DEFAULT_COMMAND_TIMEOUT_SECONDS = 30.0
_DEFAULT_STATUS_TTL_SECONDS = 2.0


class ExtensionChannelError(RuntimeError):
    """扩展通道不可用（无 bridge 端点 / 扩展未安装或休眠 / 超时 / 目标浏览器离线）。"""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


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


def endpoint_prefix() -> str:
    """端点名前缀（与 host 同源；环境变量可隔离，避免误连本机真实浏览器）。"""
    return local_transport.endpoint_prefix()


def endpoint_name(browser: str, instance_id: str) -> str:
    """按当前生效前缀构造端点名（与 host 的命名规则一致）。"""
    return local_transport.endpoint_name(browser, instance_id)


def list_extension_endpoints(browser: str | None = None) -> list[str]:
    """当前在线的扩展 bridge 端点（``browser`` 非空时只列该浏览器）。"""
    prefix = endpoint_prefix()
    if browser:
        prefix = f"{prefix}{str(browser).strip().lower()}_"
    return list(local_transport.list_endpoints(prefix))


def _endpoint_browser(name: str) -> str:
    remainder = name[len(endpoint_prefix()):]
    return remainder.partition("_")[0]


def _endpoint_instance(name: str) -> str:
    remainder = name[len(endpoint_prefix()):]
    return remainder.partition("_")[2]


class ExtensionExecClient:
    """执行器侧客户端：连 bridge 端点提交命令并等结果（阻塞，调用方放线程池）。"""

    def __init__(
        self,
        *,
        browser: str | None = None,
        endpoint: str | None = None,
        status_ttl: float = _DEFAULT_STATUS_TTL_SECONDS,
    ):
        self.browser = (str(browser).strip().lower() or None) if browser else None
        self.endpoint = endpoint or None
        self._status_ttl = status_ttl
        self._status_cache: tuple[float, dict[str, Any]] | None = None

    # -- 端点选择 ------------------------------------------------------------

    def _targets(self, target_host: str | None) -> list[str]:
        """按 targetHost（浏览器名或实例 id）过滤端点；无 target 返回全部。"""
        if self.endpoint:
            return (
                [self.endpoint]
                if self.endpoint in local_transport.list_endpoints(endpoint_prefix())
                else []
            )
        names = list_extension_endpoints(self.browser)
        target = str(target_host or "").strip().lower()
        if not target or target == "auto":
            return names
        return [
            name
            for name in names
            if _endpoint_browser(name) == target or _endpoint_instance(name) == target
        ]

    # -- 传输 ----------------------------------------------------------------

    def _exchange(
        self,
        endpoint: str,
        payload: dict[str, Any],
        timeout: float,
        *,
        expect_type: str = "result",
        expect_id: str = "",
    ) -> dict[str, Any]:
        """连端点 → 发一条 → 等到期望消息（跳过 focus/capture_* 等广播）。

        读取在守护线程里做并带截止时间：host 若卡死也不会挂住执行器。
        """
        try:
            channel = local_transport.connect(endpoint, timeout=min(1.0, timeout))
        except local_transport.LocalTransportError as exc:
            raise ExtensionChannelError("CHANNEL_OFFLINE", str(exc)) from None
        deadline = time.monotonic() + timeout
        try:
            channel.send(payload)
            while True:
                message = self._recv_with_deadline(channel, deadline)
                if message.get("type") != expect_type:
                    continue  # 广播/无关消息
                if expect_id and str(message.get("id") or "") != expect_id:
                    continue
                return message
        finally:
            channel.close()

    @staticmethod
    def _recv_with_deadline(channel, deadline: float) -> dict[str, Any]:
        """带截止时间的单条读取（recv 本身在管道上不可超时）。"""
        box: queue.Queue = queue.Queue(maxsize=1)

        def reader() -> None:
            try:
                box.put(("ok", channel.recv()))
            except Exception as exc:  # noqa: BLE001 - 统一转 CHANNEL_OFFLINE
                box.put(("err", exc))

        threading.Thread(target=reader, daemon=True).start()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ExtensionChannelError("TIMEOUT", "extension channel timed out")
        try:
            kind, value = box.get(timeout=remaining)
        except queue.Empty:
            raise ExtensionChannelError("TIMEOUT", "extension channel timed out") from None
        if kind == "err":
            raise ExtensionChannelError("CHANNEL_OFFLINE", str(value)) from None
        if value is None:
            raise ExtensionChannelError(
                "CHANNEL_OFFLINE", "bridge host closed the connection"
            )
        return value

    def _status_of(self, endpoint: str, timeout: float = 1.5) -> dict[str, Any] | None:
        try:
            reply = self._exchange(
                endpoint, {"type": "status"}, timeout, expect_type="status"
            )
        except ExtensionChannelError:
            return None
        if not isinstance(reply, dict):
            return None
        extension = reply.get("extension") or {}
        record = {
            "browser": reply.get("browser") or _endpoint_browser(endpoint),
            "instanceId": reply.get("instanceId") or _endpoint_instance(endpoint),
            "endpoint": endpoint,
            "pid": reply.get("pid"),
            # 扩展身份字段平铺（前端/诊断消费方与旧 hub status 同形）
            "version": extension.get("version"),
            "extVersion": extension.get("extVersion"),
            "platform": extension.get("platform"),
            "userAgent": extension.get("userAgent"),
        }
        return record

    # -- 能力面 --------------------------------------------------------------

    def online(self) -> bool:
        """扩展是否在线（短 TTL 缓存，避免每步探测）。"""
        return bool(self.status_cached().get("online"))

    def status_cached(self, ttl: float | None = None) -> dict[str, Any]:
        """带 TTL 的通道状态（含宿主浏览器）；探测失败返回 ``{"online": False}``。"""
        now = time.monotonic()
        if self._status_cache and now - self._status_cache[0] < (ttl or self._status_ttl):
            return self._status_cache[1]
        payload = self.status()
        self._status_cache = (now, payload)
        return payload

    def status(self) -> dict[str, Any]:
        """聚合全部在线端点：online / host（首个实例）/ hosts / instances。"""
        instances: list[dict[str, Any]] = []
        for endpoint in self._targets(None):
            record = self._status_of(endpoint)
            if record is not None:
                instances.append(record)
        hosts: list[str] = []
        for record in instances:
            browser = record.get("browser")
            if browser and browser not in hosts:
                hosts.append(browser)
        return {
            "online": bool(instances),
            "host": instances[0] if instances else None,
            "hosts": hosts,
            "instances": instances,
        }

    def host(self) -> dict[str, Any] | None:
        """扩展宿主浏览器信息（未安装/离线返回 None）。"""
        host = self.status_cached().get("host")
        return dict(host) if isinstance(host, dict) else None

    def submit(
        self,
        op: str,
        args: dict[str, Any] | None = None,
        *,
        timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
        target_host: str | None = None,
    ) -> dict[str, Any]:
        """提交一条扩展命令并等待结果；失败抛 ``ExtensionChannelError``。

        ``target_host``：目标浏览器（如 `msedge`/`chrome`）或实例 id；非空时命令只发往
        匹配的端点，无匹配立即 ``TARGET_HOST_OFFLINE``（不白等）。
        """
        targets = self._targets(target_host)
        target = str(target_host or "").strip().lower()
        if not targets:
            if target and target != "auto":
                raise ExtensionChannelError(
                    "TARGET_HOST_OFFLINE",
                    f"目标浏览器/实例 {target!r} 的自研插件未在线（无对应 bridge 端点）",
                )
            raise ExtensionChannelError(
                "CHANNEL_OFFLINE",
                "没有在线的扩展 bridge 端点：请确认已注册 host（rpa-core install-extension）"
                "且扩展已加载并连接",
            )
        command_id = f"ext-{uuid.uuid4().hex[:12]}"
        payload = {
            "type": "submit",
            "id": command_id,
            "op": op,
            "args": args or {},
            "timeoutSeconds": max(0.1, timeout_seconds),
        }
        last_error: ExtensionChannelError | None = None
        for endpoint in targets:
            try:
                result = self._exchange(
                    endpoint,
                    payload,
                    timeout_seconds + 5.0,
                    expect_type="result",
                    expect_id=command_id,
                )
            except ExtensionChannelError as exc:
                last_error = exc
                continue
            if not result.get("ok"):
                error = result.get("error") or {}
                raise ExtensionChannelError(
                    str(error.get("code") or "EXECUTOR_FAILED"),
                    str(error.get("message") or "extension command failed"),
                )
            value = result.get("value")
            payload_value = value if isinstance(value, dict) else result
            # 会话绑定：tabs.create 需带回实例 id（与端点命名同源）
            if op == "tabs.create" and isinstance(payload_value, dict):
                instance = _endpoint_instance(endpoint)
                if instance and not payload_value.get("instanceId"):
                    payload_value = {**payload_value, "instanceId": instance}
            return payload_value
        raise last_error or ExtensionChannelError(
            "CHANNEL_OFFLINE", "extension channel unavailable"
        )
