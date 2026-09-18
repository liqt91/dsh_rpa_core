"""扩展 bridge host（M20 / ADR 0015）：Native Messaging ↔ 本地端点的中继子进程。

由浏览器按需拉起（扩展 ``chrome.runtime.connectNative``），stdin/stdout 走 Native
Messaging 帧（4 字节小端长度前缀 + JSON，与 ``local_transport`` 同构）；host 与执行器侧
（``rpa-core run`` 子进程 / GUI / devserver）走本地端点（Windows 命名管道 / POSIX Unix
域套接字）。

生命周期：host 随扩展 port 存活——stdin EOF（扩展断开/浏览器退出）即清理端点并退出，
因此「端点可连接」即「扩展在线」，无需心跳窗口。

消息面
------
扩展 → host（stdin）：
- ``hello``：首帧，携带 ``browser`` / ``instanceId``（端点名据此生成**定长 token**）
- ``result``：命令结果，按 ``id`` 路由回发起客户端（host 会补带本实例 ``instanceId``）
- 其它（``capture_result`` 等）：广播给全部客户端

客户端 → host（端点）：
- ``submit``：下发命令（host 负责超时；超时回 ``TIMEOUT``）
- ``status``：查询宿主身份
- ``capture_arm`` / ``capture_disarm`` / ``capture_result`` / ``cancel``：透传扩展

host → 扩展：``ready`` / ``command`` / ``cancel`` / ``capture_*``
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any

from rpa_core.local_transport import (
    Channel,
    LocalEndpointServer,
    LocalTransportError,
    endpoint_diagnostics,
    endpoint_name,
    read_message,
    write_message,
)

_DEBUG = bool(os.environ.get("RPA_EXT_BRIDGE_DEBUG"))
_PASSTHROUGH_TO_EXTENSION = {"capture_arm", "capture_disarm", "capture_result", "cancel"}

# 诊断日志文件：显式路径可覆盖（测试隔离本机 ~/.rpa-core）
_LOG_ENV = "RPA_EXT_BRIDGE_LOG"
_LOG_MAX_BYTES = 256 * 1024


def _log_path() -> Path:
    override = os.environ.get(_LOG_ENV)
    if override:
        return Path(override)
    return Path.home() / ".rpa-core" / "logs" / "ext-host.log"


def _trim_log(path: Path) -> None:
    """超过上限时保留尾部一半（长期运行不无限增长）。"""
    try:
        size = path.stat().st_size
        if size <= _LOG_MAX_BYTES:
            return
        with path.open("rb") as handle:
            handle.seek(size - _LOG_MAX_BYTES // 2)
            tail = handle.read()
        path.write_bytes(b"[log trimmed]\n" + tail)
    except OSError:  # pragma: no cover - 日志失败不影响主流程
        pass


def _log(message: str) -> None:
    """诊断输出：**必须落盘**。

    旧实现只在 ``RPA_EXT_BRIDGE_DEBUG=1`` 时写 stderr，而 host 的 stderr 由浏览器接管
    （无人读取）——于是「起不来」在用户侧只剩一句「插件离线」，没有任何可查的现场。
    因此默认追加写日志文件；``RPA_EXT_BRIDGE_DEBUG=1`` 时同时打 stderr（开发用）。
    """
    stamp = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} [{os.getpid()}] {message}"
    if _DEBUG:
        print(f"[ext_bridge] {message}", file=sys.stderr, flush=True)
    try:
        path = _log_path()
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _trim_log(path)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(stamp + "\n")
    except OSError:  # pragma: no cover - 磁盘/权限异常不得影响通道
        pass


def _set_binary_stdio() -> None:
    """Windows 下 Native Messaging 必须二进制模式，否则 CRLF 翻译会破坏帧。"""
    if sys.platform != "win32":
        return
    import msvcrt

    msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
    msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)


class ExtBridgeHost:
    """把扩展的 Native Messaging 连接暴露为本地端点，并中继命令/结果。"""

    def __init__(self, stdin: Any, stdout: Any):
        self._stdin = stdin
        self._stdout = stdout
        self._write_lock = threading.Lock()
        self._clients_lock = threading.Lock()
        self._clients: dict[int, Channel] = {}
        self._client_seq = 0
        self._pending: dict[str, int] = {}
        self._pending_lock = threading.Lock()
        self._timers: dict[str, threading.Timer] = {}
        self._server: LocalEndpointServer | None = None
        self._closed = threading.Event()
        self.identity: dict[str, Any] = {}

    # -- 扩展侧 --------------------------------------------------------------

    def _send_to_extension(self, payload: dict[str, Any]) -> None:
        with self._write_lock:
            try:
                write_message(self._stdout, payload)
            except (LocalTransportError, OSError, ValueError) as exc:
                _log(f"extension write failed: {exc}")

    def _read_hello(self) -> dict[str, Any]:
        first = read_message(self._stdin)
        if first is None:
            raise LocalTransportError("extension closed before hello")
        if first.get("type") != "hello":
            raise LocalTransportError(f"expected hello, got {first.get('type')!r}")
        return first

    def _extension_loop(self) -> None:
        while not self._closed.is_set():
            try:
                message = read_message(self._stdin)
            except LocalTransportError as exc:
                _log(f"extension read failed: {exc}")
                break
            if message is None:
                break  # 扩展断开/浏览器退出
            _log(f"extension -> {message.get('type')!r}")
            self._route_from_extension(message)
        self.shutdown()

    def _with_instance_id(self, message: dict[str, Any]) -> dict[str, Any]:
        """给扩展结果补带本实例的真实 ``instanceId``（缺则补，已有不覆盖）。

        扩展的 ``tabs.create`` 等结果里没有实例标识，而执行器要把会话绑到具体实例
        （``browserExt.browserInstance`` → 后续 ``target_host`` 精确路由）。宿主是唯一
        知道自身真实 instanceId 的角色（它来自 hello），故由宿主补带。
        """
        instance_id = str(self.identity.get("instanceId") or "")
        if not instance_id or message.get("instanceId"):
            return message
        return {**message, "instanceId": instance_id}

    def _route_from_extension(self, message: dict[str, Any]) -> None:
        kind = str(message.get("type") or "")
        if kind == "result":
            command_id = str(message.get("id") or "")
            with self._pending_lock:
                client_id = self._pending.pop(command_id, None)
                timer = self._timers.pop(command_id, None)
            if timer is not None:
                timer.cancel()
            if client_id is not None:
                self._send_to_client(client_id, self._with_instance_id(message))
                return
            _log(f"dropping result for unknown command {command_id}")
            return
        self._broadcast(message)

    # -- 客户端侧 ------------------------------------------------------------

    def _client_id(self) -> int:
        with self._clients_lock:
            self._client_seq += 1
            return self._client_seq

    def _send_to_client(self, client_id: int, payload: dict[str, Any]) -> None:
        with self._clients_lock:
            channel = self._clients.get(client_id)
        if channel is None:
            _log(f"client {client_id} gone")
            return
        try:
            channel.send(payload)
            _log(f"client {client_id} <- {payload.get('type')!r}")
        except (LocalTransportError, OSError) as exc:
            _log(f"client {client_id} write failed: {exc}")
            self._drop_client(client_id)

    def _broadcast(self, payload: dict[str, Any]) -> None:
        with self._clients_lock:
            client_ids = list(self._clients)
        for client_id in client_ids:
            self._send_to_client(client_id, payload)

    def _drop_client(self, client_id: int) -> None:
        with self._clients_lock:
            channel = self._clients.pop(client_id, None)
        if channel is not None:
            channel.close()

    def _accept_loop(self) -> None:
        assert self._server is not None
        while not self._closed.is_set():
            try:
                channel = self._server.accept(timeout=0.5)
            except LocalTransportError as exc:
                if self._closed.is_set():
                    return
                _log(f"accept failed: {exc}")
                time.sleep(0.05)
                continue
            if channel is None:
                continue
            client_id = self._client_id()
            _log(f"client {client_id} accepted")
            with self._clients_lock:
                self._clients[client_id] = channel
            threading.Thread(
                target=self._client_loop, args=(client_id, channel), daemon=True
            ).start()

    def _client_loop(self, client_id: int, channel: Channel) -> None:
        try:
            while not self._closed.is_set():
                try:
                    message = channel.recv()
                except LocalTransportError as exc:
                    _log(f"client {client_id} read failed: {exc}")
                    break
                if message is None:
                    break
                _log(f"client {client_id} -> {message.get('type')!r}")
                self._handle_client_message(client_id, message)
        finally:
            self._drop_client(client_id)

    def _handle_client_message(self, client_id: int, message: dict[str, Any]) -> None:
        kind = str(message.get("type") or "")
        if kind == "status":
            self._send_to_client(
                client_id, {"type": "status", "online": True, **self.identity}
            )
            return
        if kind == "submit":
            command_id = str(message.get("id") or "")
            if not command_id:
                self._send_to_client(
                    client_id,
                    {
                        "type": "result",
                        "id": "",
                        "ok": False,
                        "error": {"code": "INVALID_INPUT", "message": "submit needs id"},
                    },
                )
                return
            timeout = float(message.get("timeoutSeconds") or 0) or None
            with self._pending_lock:
                self._pending[command_id] = client_id
            if timeout:
                timer = threading.Timer(
                    timeout, self._on_timeout, args=(command_id,)
                )
                timer.daemon = True
                with self._pending_lock:
                    self._timers[command_id] = timer
                timer.start()
            self._send_to_extension({**message, "type": "command"})
            return
        if kind in _PASSTHROUGH_TO_EXTENSION:
            self._send_to_extension(message)
            return
        self._send_to_client(
            client_id,
            {
                "type": "error",
                "ok": False,
                "error": {
                    "code": "INVALID_INPUT",
                    "message": f"unsupported message type: {kind!r}",
                },
            },
        )

    def _on_timeout(self, command_id: str) -> None:
        with self._pending_lock:
            client_id = self._pending.pop(command_id, None)
            self._timers.pop(command_id, None)
        if client_id is None:
            return
        self._send_to_client(
            client_id,
            self._with_instance_id(
                {
                    "type": "result",
                    "id": command_id,
                    "ok": False,
                    "timedOut": True,
                    "error": {
                        "code": "TIMEOUT",
                        "message": f"{command_id}: extension did not return a result",
                    },
                }
            ),
        )
        # best-effort：让扩展放弃仍在执行的那条命令
        self._send_to_extension({"type": "cancel", "id": command_id})

    # -- 生命周期 ------------------------------------------------------------

    def run(self) -> int:
        try:
            hello = self._read_hello()
        except LocalTransportError as exc:
            _log(f"handshake failed: {exc}")
            return 2
        browser = str(hello.get("browser") or "unknown")
        instance_id = str(hello.get("instanceId") or "")
        name = endpoint_name(browser, instance_id)
        # 启动自检：先算最终端点路径与字节数（对照平台上限）再 bind。
        # 长度类问题由此在日志里一眼可见，不再依赖 bind 的裸 traceback。
        diagnostics = endpoint_diagnostics(name)
        _log(f"endpoint self-check: {json.dumps(diagnostics, ensure_ascii=False)}")
        if not diagnostics.get("ok"):
            _log(f"endpoint rejected before bind: {json.dumps(diagnostics, ensure_ascii=False)}")
            return 3
        try:
            self._server = LocalEndpointServer(name)
        except (LocalTransportError, OSError) as exc:
            # OSError 是兜底：`bind()` 抛的是裸 OSError，只捕领域异常会让 host
            # 带 traceback 静默退出（浏览器侧只看到「离线」）
            _log(f"endpoint bind failed: {type(exc).__name__}: {exc}")
            return 3
        self.identity = {
            "browser": browser,
            "instanceId": instance_id,
            "endpoint": name,
            "pid": os.getpid(),
            "startedAt": time.time(),
            "extension": {
                key: hello.get(key)
                for key in ("version", "extVersion", "platform", "userAgent")
                if hello.get(key) is not None
            },
        }
        _log(f"hosting {name}")
        self._send_to_extension({"type": "ready", **self.identity})
        threading.Thread(target=self._accept_loop, daemon=True).start()
        self._extension_loop()
        return 0

    def shutdown(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        with self._pending_lock:
            for timer in self._timers.values():
                timer.cancel()
            self._timers.clear()
            self._pending.clear()
        with self._clients_lock:
            channels = list(self._clients.values())
            self._clients.clear()
        for channel in channels:
            channel.close()
        if self._server is not None:
            self._server.close()
            self._server = None


def main() -> int:
    _set_binary_stdio()
    _log(
        f"host starting: pid={os.getpid()} platform={sys.platform} "
        f"python={sys.version.split()[0]}"
    )
    try:
        host = ExtBridgeHost(sys.stdin.buffer, sys.stdout.buffer)
        return host.run()
    except Exception:  # noqa: BLE001 - 顶层兜底：任何逃逸异常都必须留痕
        # host 由浏览器拉起，stderr 无人接收 —— 不落盘就等于没有现场
        _log("host crashed:\n" + traceback.format_exc())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
