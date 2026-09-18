"""本地 IPC 传输层（M20 / ADR 0015）：扩展 bridge host 与执行器侧的端点通信。

背景：扩展通道从 HTTP 长轮询（8765 常驻 hub）改为 Native Messaging——扩展经
``chrome.runtime.connectNative()`` 连浏览器按需拉起的 bridge host，host 与执行器侧
（``rpa-core run`` 子进程 / GUI / devserver）经本模块的**本地端点**通信。

协议：4 字节小端长度前缀 + UTF-8 JSON，与 Native Messaging 的 stdio 帧同构
（``encode_message`` / ``read_message`` / ``write_message`` 同时服务于 stdio）。

平台分派（实测 Windows Python 3.12 无 ``socket.AF_UNIX``）：

- win32：命名管道 ``\\\\.\\pipe\\<name>``（pywin32，overlapped I/O）
- 其它：Unix 域套接字 ``<runtime_dir>/<name>.sock``（stdlib socket）

**端点即可见性**：host 随扩展 port 存活，扩展断开即 host 退出、端点消失——「在线」
= 端点可连接，不再需要心跳窗口（对比旧 ``ONLINE_WINDOW_SECONDS``）。

端点路径长度（M22 真机修复）
--------------------------

POSIX 端点的名字最终落进 ``struct sockaddr_un.sun_path``——**104 字节含结尾 NUL，
即有效上限 103 字节**（1980 年代 BSD 遗留尺寸，Linux 的抽象命名空间在 macOS 不生效）。
超额时 ``bind()`` 抛 ``OSError: AF_UNIX path too long``。

两个刻意的设计（缺一就是「插件永远离线」）：

1. **端点名里的实例段是定长 token**（``instance_token``：``sha256(instanceId)[:16]``）。
   instanceId 是浏览器生成的 UUID（36 字符），长度与内容都不由我们控制，直接拼进名字
   会随浏览器实现漂移。
2. **macOS 的端点目录不再用 ``tempfile.gettempdir()``**。那是 per-user 私有临时区
   （``/var/folders/<2>/<22 位>/T``，实测 61 字节），叠加端点名会顶穿上限。改用
   ``/tmp`` 下的短目录——AF_UNIX **不解析符号链接**，``/tmp`` 只花 4 字节。

Windows 走内核命名空间（``\\\\.\\pipe\\``，上限 256 字符），不经路径解析，故不受此约束。

Windows 实现要点：管道句柄一律以 ``FILE_FLAG_OVERLAPPED`` 打开。同步（非 overlapped）
句柄上，一端挂起的阻塞 ``ReadFile`` 会阻塞另一线程的 ``WriteFile``（同一文件对象串行化），
而中继模型天然是「客户端线程阻塞读 + 扩展线程写回」并发，故必须 overlapped。
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import stat
import struct
import sys
import threading
import time
from pathlib import Path
from typing import Any, BinaryIO

MAX_MESSAGE_BYTES = 8 * 1024 * 1024
_ENDPOINT_PREFIX = "rpa_core_ext_"
# 前缀可被环境变量覆盖：测试用它把真实端点隔离掉（避免误连开发者本机浏览器）
_ENDPOINT_PREFIX_ENV = "RPA_EXT_ENDPOINT_PREFIX"
_PIPE_BUF = 64 * 1024
_IS_WINDOWS = sys.platform == "win32"

_SOCKET_SUFFIX = ".sock"
_ENDPOINT_DIR_NAME = "rpa_core_ext"
# 实例段定长（sha256 十六进制前缀字符数）：端点名长度必须与 instanceId 无关
_INSTANCE_TOKEN_CHARS = 16
# 浏览器段上限：真实浏览器名（chrome/msedge/vivaldi…）远小于此，留足余量的同时
# 让端点名最大长度完全可预测
_BROWSER_SEGMENT_MAX = 32
# POSIX sun_path 上限：104 字节含结尾 NUL（macOS 实测 104 字节即 bind 失败）
_POSIX_PATH_MAX = 103
# \\.\pipe\ 名字上限（Windows 走内核命名空间，不经路径解析）
_PIPE_NAME_MAX = 256
_ENDPOINT_DIR_MODE = 0o700
# 残留端点回收：只清「超期且连不上」的（活端点一定连得上）
_STALE_ENDPOINT_AGE_SECONDS = 300.0

# win32 错误码
_ERROR_FILE_NOT_FOUND = 2
_ERROR_BROKEN_PIPE = 109
_ERROR_PIPE_BUSY = 231
_ERROR_NO_DATA = 232
_ERROR_OPERATION_ABORTED = 995
_ERROR_IO_INCOMPLETE = 996
_ERROR_IO_PENDING = 997
_ERROR_PIPE_CONNECTED = 535


class LocalTransportError(RuntimeError):
    """本地端点不可用（不存在 / 被占用 / 协议错误 / 已关闭）。"""


# -- 帧编码（stdio 与端点共用） ------------------------------------------------


def encode_message(payload: dict[str, Any]) -> bytes:
    """把一条 JSON 消息编码为「长度前缀 + 载荷」。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(body) > MAX_MESSAGE_BYTES:
        raise LocalTransportError(
            f"message too large: {len(body)} > {MAX_MESSAGE_BYTES}"
        )
    return struct.pack("<I", len(body)) + body


def read_message(stream: BinaryIO) -> dict[str, Any] | None:
    """从二进制流读一条消息；流干净结束返回 ``None``（对端关闭）。"""
    header = _stream_read_exact(stream, 4)
    if header is None:
        return None
    size = struct.unpack("<I", header)[0]
    if size > MAX_MESSAGE_BYTES:
        raise LocalTransportError(f"declared message too large: {size}")
    body = _stream_read_exact(stream, size) if size else b""
    if body is None:
        raise LocalTransportError("truncated message body")
    return _decode_body(body)


def write_message(stream: BinaryIO, payload: dict[str, Any]) -> None:
    """向二进制流写一条消息并 flush。"""
    stream.write(encode_message(payload))
    stream.flush()


def _decode_body(body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LocalTransportError(f"invalid message payload: {exc}") from None
    if not isinstance(payload, dict):
        raise LocalTransportError("message payload must be a JSON object")
    return payload


def _stream_read_exact(stream: BinaryIO, size: int) -> bytes | None:
    """精确读取 size 字节；读满前遇 EOF：无任何字节返回 None，部分字节报错。"""
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            if not chunks:
                return None
            raise LocalTransportError("unexpected EOF inside message frame")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


# -- 端点命名与发现 ------------------------------------------------------------


def endpoint_prefix() -> str:
    """当前生效的端点前缀（环境变量可覆盖，测试隔离用）。"""
    return os.environ.get(_ENDPOINT_PREFIX_ENV) or _ENDPOINT_PREFIX


def instance_token(instance_id: str) -> str:
    """实例段 token：``sha256(instanceId)[:16]``（空 id 退化为 ``default``）。

    长度与内容都与 instanceId 无关，这是端点名长度可控的前提。哈希作用于**原始** id
    （先于任何截断/归一）——先 sanitize 再哈希会让两个超长 id 截断后撞名。

    代价是**不可逆**：端点名不再能反解出原始 instanceId。需要原始 id 时走 host 的
    ``status`` 握手（``identity.instanceId``），不要从端点名反解。
    """
    raw = str(instance_id or "")
    if not raw:
        return "default"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:_INSTANCE_TOKEN_CHARS]


def endpoint_name(
    browser: str, instance_id: str, prefix: str | None = None
) -> str:
    """端点名：``<prefix><browser>_<instanceToken>``（浏览器段非法字符归一为下划线）。"""
    effective = prefix if prefix is not None else endpoint_prefix()
    browser_segment = _sanitize(browser or "unknown")[:_BROWSER_SEGMENT_MAX] or "unknown"
    return f"{effective}{browser_segment}_{instance_token(instance_id)}"


def _sanitize(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in value)
    return cleaned[:64] or "default"


def _dbg(message: str) -> None:
    if os.environ.get("RPA_LT_DEBUG"):
        print(f"[lt] {message}", file=sys.stderr, flush=True)


def _posix_uid() -> int:
    getuid = getattr(os, "getuid", None)
    return int(getuid()) if getuid is not None else 0


def _runtime_root() -> Path:
    """端点目录的父目录。

    - ``$XDG_RUNTIME_DIR`` 优先（Linux/systemd：``/run/user/<uid>``，短，且浏览器与
      CLI 由同一套 systemd 用户会话注入，**两端天然同源**）；
    - 缺省 ``/tmp/rpa_core-<uid>``：macOS 没有 ``XDG_RUNTIME_DIR``（launchd GUI 会话
      只注入 ``SSH_AUTH_SOCK``，实测），而 ``tempfile.gettempdir()`` 在 macOS 上是
      61 字节的 per-user 私有临时区，会顶穿 ``sun_path`` 上限（M22 真机复现）。
      带 uid 是为了避免多用户共享 ``/tmp`` 时同名冲突。
    """
    base = os.environ.get("XDG_RUNTIME_DIR")
    if base:
        return Path(base)
    return Path("/tmp") / f"rpa_core-{_posix_uid()}"


def endpoint_dir() -> Path:
    """POSIX 端点目录（私有 0700；已存在时复核属主与类型）。

    ``/tmp`` 是 1777 全局可写：别的进程可以抢先在目标位置放符号链接或他人属主的目录。
    因此建完/复用前必须复核「非符号链接 + 目录 + 属主为本进程 euid」——不满足就报明确
    错误，而不是带着坏前提去 bind。

    注意：**不要对本函数结果调用 ``Path.resolve()``**——``/tmp`` 会被展开成
    ``/private/tmp``，白丢 8 字节预算，而 AF_UNIX 本来就不解析符号链接。
    """
    path = _runtime_root() / _ENDPOINT_DIR_NAME
    try:
        path.mkdir(mode=_ENDPOINT_DIR_MODE, parents=True, exist_ok=True)
    except OSError as exc:
        raise LocalTransportError(
            f"endpoint dir unavailable: {path} ({exc})"
        ) from None
    try:
        info = path.lstat()
    except OSError as exc:  # pragma: no cover - 建完即消失（极端竞态）
        raise LocalTransportError(
            f"endpoint dir unreadable: {path} ({exc})"
        ) from None
    if not stat.S_ISDIR(info.st_mode):
        raise LocalTransportError(f"endpoint dir is not a directory: {path}")
    if info.st_uid != _posix_uid():
        raise LocalTransportError(
            f"endpoint dir owned by uid {info.st_uid}, not {_posix_uid()}: {path}"
        )
    if info.st_mode & 0o077:
        # 自家目录权限偏松（正常路径不会发生：mkdir(0o700) 之后 umask 只能更严）。
        # 收紧失败不阻断（可用性优先），只留调试痕迹。
        try:
            path.chmod(_ENDPOINT_DIR_MODE)
        except OSError as exc:  # pragma: no cover
            _dbg(f"endpoint dir chmod failed: {path} ({exc})")
    return path


def endpoint_path(name: str) -> Path | None:
    """端点的落地路径；Windows 命名管道不落文件系统，返回 ``None``。

    POSIX 上同时做**长度守卫**：超限时抛 ``LocalTransportError``（带路径、实际字节数与
    上限），而不是让 ``bind()`` 抛一个语焉不详的 ``OSError``。
    """
    if _IS_WINDOWS:
        return None
    directory = endpoint_dir()
    path = directory / f"{name}{_SOCKET_SUFFIX}"
    size = len(os.fsencode(path))
    if size > _POSIX_PATH_MAX:
        raise LocalTransportError(
            f"endpoint path too long: {size} bytes > {_POSIX_PATH_MAX} "
            f"(POSIX sun_path 上限；AF_UNIX 不解析符号链接，路径不会自动变短) "
            f"dir={directory} endpoint={name!r}"
        )
    return path


def endpoint_diagnostics(name: str) -> dict[str, Any]:
    """端点的落地参数快照（host 启动自检 / 排障用，**不抛异常**）。

    长度类问题此前只有 ``bind()`` 的裸 traceback，而 host 的 stderr 由浏览器接管、
    用户侧只剩「插件离线」。启动时把这份快照写进日志，一眼可见实际字节数与上限。
    """
    if _IS_WINDOWS:
        size = len(name.encode("utf-8"))
        return {
            "transport": "pipe",
            "name": name,
            "address": "\\\\.\\pipe\\" + name,
            "nameChars": size,
            "limit": _PIPE_NAME_MAX,
            "ok": size <= _PIPE_NAME_MAX,
        }
    payload: dict[str, Any] = {"transport": "unix", "name": name}
    try:
        directory = endpoint_dir()
    except LocalTransportError as exc:
        return {**payload, "ok": False, "error": str(exc)}
    path = directory / f"{name}{_SOCKET_SUFFIX}"
    size = len(os.fsencode(path))
    return {
        **payload,
        "dir": str(directory),
        "path": str(path),
        "pathBytes": size,
        "limit": _POSIX_PATH_MAX,
        "ok": size <= _POSIX_PATH_MAX,
    }


def list_endpoints(prefix: str | None = None) -> list[str]:
    """列出当前存在的端点名（不含平台后缀），升序。"""
    effective = prefix if prefix is not None else endpoint_prefix()
    if _IS_WINDOWS:
        try:
            names = os.listdir("\\\\.\\pipe\\")
        except OSError:  # pragma: no cover - 极少数受限环境
            return []
        return sorted(name for name in names if name.startswith(effective))
    directory = endpoint_dir()
    return sorted(
        path.name[: -len(_SOCKET_SUFFIX)]
        for path in directory.glob(f"*{_SOCKET_SUFFIX}")
        if path.name.startswith(effective)
    )


def prune_stale_endpoints(
    prefix: str | None = None, *, max_age: float = _STALE_ENDPOINT_AGE_SECONDS
) -> list[str]:
    """回收同前缀下「超期且连不上」的残留端点，返回被删端点名。

    为什么需要：端点目录落在 ``/tmp``，而 macOS **不清理 /tmp**（无 systemd-tmpfiles，
    ``/etc/periodic/daily`` 在本机已不存在）。host 被浏览器终结时不会执行 unlink，
    而实例 id 持久在扩展的 ``chrome.storage.local`` —— 同一 profile 复用同名（走
    ``_UnixServer`` 的「同名重绑回收」即可），但换 profile / 重装 / 清 storage 后换成
    新名字，旧 socket 文件就再也没人复用，只能靠这里回收。

    判据两条同时满足才删：``mtime`` 早于 ``max_age`` **且** connect 失败。
    活端点被连上即保留（哪怕 mtime 很老——mtime 只在 bind 时更新一次）。
    """
    if _IS_WINDOWS:
        return []  # 命名管道随句柄关闭即消失，无残留
    effective = prefix if prefix is not None else endpoint_prefix()
    try:
        directory = endpoint_dir()
    except LocalTransportError:
        return []
    removed: list[str] = []
    now = time.time()
    # 不做 `<prefix>*.sock` 的 glob：前缀来自环境变量，可能含 glob 元字符
    candidates = [p for p in directory.glob(f"*{_SOCKET_SUFFIX}") if p.name.startswith(effective)]
    for path in sorted(candidates):
        try:
            if now - path.lstat().st_mtime < max_age:
                continue
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            probe.settimeout(0.2)
            try:
                probe.connect(str(path))
            except OSError:
                path.unlink()
                removed.append(path.name[: -len(_SOCKET_SUFFIX)])
            finally:
                probe.close()
        except OSError:  # pragma: no cover - 竞态：文件已被对端清掉
            continue
    if removed:
        _dbg(f"pruned stale endpoints: {', '.join(removed)}")
    return removed


def connect(name: str, timeout: float = 1.0) -> Channel:
    """连接端点；不存在/超时抛 ``LocalTransportError``。"""
    if _IS_WINDOWS:
        return _connect_pipe(name, timeout)
    return _connect_unix(name, timeout)


# -- 通道抽象 ------------------------------------------------------------------


class Channel:
    """双向消息通道：``send``/``recv`` 以完整 JSON 消息为单位，``recv`` 在 EOF 返回 None。"""

    def send(self, payload: dict[str, Any]) -> None:
        raise NotImplementedError

    def recv(self) -> dict[str, Any] | None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def __enter__(self) -> Channel:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


class StreamChannel(Channel):
    """stdio 流通道（host 子进程的 stdin/stdout）。"""

    def __init__(self, reader: BinaryIO, writer: BinaryIO):
        self._reader = reader
        self._writer = writer

    def send(self, payload: dict[str, Any]) -> None:
        write_message(self._writer, payload)

    def recv(self) -> dict[str, Any] | None:
        return read_message(self._reader)

    def close(self) -> None:
        for stream in (self._reader, self._writer):
            try:
                stream.close()
            except OSError:  # pragma: no cover - 已关闭
                pass


class _SocketChannel(Channel):
    def __init__(self, sock: socket.socket):
        self._sock = sock

    def send(self, payload: dict[str, Any]) -> None:
        try:
            self._sock.sendall(encode_message(payload))
        except OSError as exc:
            # 领域边界：send/recv 的裸 OSError 不得逃逸，否则调用方的
            # `except LocalTransportError` 形同虚设（同 bind 的历史缺陷）
            raise LocalTransportError(f"channel send failed: {exc}") from None

    def recv(self) -> dict[str, Any] | None:
        try:
            header = _socket_read_exact(self._sock, 4)
            if header is None:
                return None
            size = struct.unpack("<I", header)[0]
            if size > MAX_MESSAGE_BYTES:
                raise LocalTransportError(f"declared message too large: {size}")
            body = _socket_read_exact(self._sock, size) if size else b""
            if body is None:
                raise LocalTransportError("truncated message body")
        except OSError as exc:
            # 对已关闭通道的读取是 EBADF（Bad file descriptor）——读循环必须只看到
            # 领域异常，否则线程里会逃逸成未捕获异常（见 capture.extension._read_loop）
            raise LocalTransportError(f"channel recv failed: {exc}") from None
        return _decode_body(body)

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:  # pragma: no cover - 已关闭
            pass


def _socket_read_exact(sock: socket.socket, size: int) -> bytes | None:
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        try:
            chunk = sock.recv(remaining)
        except (ConnectionResetError, BrokenPipeError):
            chunk = b""
        if not chunk:
            if not chunks:
                return None
            raise LocalTransportError("unexpected EOF inside message frame")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


# -- 服务端 --------------------------------------------------------------------


class LocalEndpointServer:
    """绑定单个端点并接受多个客户端连接（执行器侧可并发连接）。"""

    def __init__(self, name: str):
        self._name = name
        if _IS_WINDOWS:
            self._impl: _ServerImpl = _PipeServer(name)
        else:
            self._impl = _UnixServer(name)

    @property
    def name(self) -> str:
        return self._name

    @property
    def address(self) -> str:
        return self._impl.address

    def accept(self, timeout: float | None = None) -> Channel | None:
        """等待一个客户端连接；超时返回 None，服务已关闭抛错。"""
        return self._impl.accept(timeout)

    def close(self) -> None:
        self._impl.close()

    def __enter__(self) -> LocalEndpointServer:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


class _ServerImpl:
    address: str

    def accept(self, timeout: float | None = None) -> Channel | None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


class _UnixServer(_ServerImpl):
    def __init__(self, name: str):
        prune_stale_endpoints()  # /tmp 不会被系统清理，host 启动时顺手回收残留
        path = endpoint_path(name)  # 长度守卫：超限在此抛领域异常，不让裸 OSError 逃逸
        assert path is not None  # POSIX 分支必然有路径
        if path.exists():
            # 只有连不上的残留端点才可回收；活端点撞名必须显式失败
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            probe.settimeout(0.2)
            try:
                probe.connect(str(path))
            except OSError:
                path.unlink()
            else:
                raise LocalTransportError(f"endpoint already in use: {name}")
            finally:
                probe.close()
        self._path = path
        self.address = str(path)
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self._sock.bind(str(path))
        except OSError as exc:
            # 兜底：把任何 bind 期 OSError（权限/长度/被占）转成领域异常。
            # 否则调用方的 `except LocalTransportError` 是死代码，host 带裸 traceback
            # 静默退出（退出码 1），浏览器侧只剩「插件离线」。
            self._sock.close()
            raise LocalTransportError(
                f"endpoint bind failed: {name} ({exc}); "
                f"path={path} bytes={len(os.fsencode(path))}"
            ) from None
        self._sock.listen(8)
        self._closed = False

    def accept(self, timeout: float | None = None) -> Channel | None:
        if self._closed:
            raise LocalTransportError("endpoint server closed")
        self._sock.settimeout(timeout)
        try:
            conn, _ = self._sock.accept()
        except TimeoutError:
            return None
        except OSError as exc:
            if self._closed:
                raise LocalTransportError("endpoint server closed") from None
            raise LocalTransportError(str(exc)) from None
        return _SocketChannel(conn)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._sock.close()
        except OSError:  # pragma: no cover
            pass
        try:
            self._path.unlink()
        except OSError:  # pragma: no cover
            pass


class _PipeServer(_ServerImpl):
    """Windows 命名管道服务端：overlapped 连接，单线程 accept 循环即可服务多客户端。"""

    def __init__(self, name: str):
        # 阈值按平台取：管道名走内核命名空间，上限 256 字符（不经路径解析）
        if len(name) > _PIPE_NAME_MAX:
            raise LocalTransportError(
                f"endpoint name too long: {len(name)} > {_PIPE_NAME_MAX} chars: {name!r}"
            )
        self.address = "\\\\.\\pipe\\" + name
        self._closed = False
        self._lock = threading.Lock()
        # 立即建实例：端点存在即「在线」，执行器靠枚举管道名发现宿主
        self._pending: tuple[Any, Any, bool] | None = self._begin_connect()

    def _create_instance(self) -> Any:
        import win32file
        import win32pipe

        return win32pipe.CreateNamedPipe(
            self.address,
            win32pipe.PIPE_ACCESS_DUPLEX | win32file.FILE_FLAG_OVERLAPPED,
            win32pipe.PIPE_TYPE_BYTE
            | win32pipe.PIPE_READMODE_BYTE
            | win32pipe.PIPE_WAIT,
            win32pipe.PIPE_UNLIMITED_INSTANCES,
            _PIPE_BUF,
            _PIPE_BUF,
            0,
            None,
        )

    def _begin_connect(self) -> tuple[Any, Any, bool]:
        """建一个管道实例并发起连接（返回见 ``_connect_instance``）。"""
        return self._connect_instance(self._create_instance())

    def _connect_instance(self, handle: Any) -> tuple[Any, Any, bool]:
        """在给定实例上发起连接。

        返回 ``(handle, overlapped, connected_now)``。三种情况：
        - 客户端在 ``ConnectNamedPipe`` 调用前已接入 → 抛 ``ERROR_PIPE_CONNECTED``
          （或 overlapped 报 pending 但 ``PeekNamedPipe`` 可见，见下）→ 直接交接；
        - 正常挂起 → ``ERROR_IO_PENDING``（或 pywin32 静默正常返回，需用
          ``GetOverlappedResult`` 复核，返回 996 即未完成）；
        - 同步完成 → ``GetOverlappedResult`` 直接成功。

        **竞态兜底**（实测）：客户端若在 ``CreateNamedPipe`` 与 ``ConnectNamedPipe``
        之间接入，overlapped 的 connect 会返回 pending 且**永不完成**，此时只有
        ``PeekNamedPipe`` 能识别「已连接」——漏掉会让该客户端永远不被 accept。
        """
        import pywintypes
        import win32event
        import win32file
        import win32pipe

        overlapped = pywintypes.OVERLAPPED()
        overlapped.hEvent = win32event.CreateEvent(None, 1, 0, None)
        _dbg(f"begin: instance handle={int(handle)}")
        try:
            win32pipe.ConnectNamedPipe(handle, overlapped)
            _dbg("begin: ConnectNamedPipe returned normally")
        except pywintypes.error as exc:
            code = exc.args[0] if exc.args else 0
            _dbg(f"begin: ConnectNamedPipe exc={code}")
            if code == _ERROR_PIPE_CONNECTED:
                return handle, overlapped, True
            if code != _ERROR_IO_PENDING:
                _close_handle(handle)
                raise LocalTransportError(f"connect failed: {exc}") from None
            return handle, overlapped, False
        try:
            win32file.GetOverlappedResult(handle, overlapped, False)
            _dbg("begin: overlapped completed immediately -> connected")
        except pywintypes.error as exc:
            code = exc.args[0] if exc.args else 0
            _dbg(f"begin: overlapped err={code}")
            if code == _ERROR_IO_INCOMPLETE:
                # 竞态兜底：客户端若在 ConnectNamedPipe 之前接入，overlapped 的
                # connect 返回 pending 且**永不完成**（实测），只能用 PeekNamedPipe
                # 识别「已连接」（成功=已连接；未连接报 ERROR_PIPE_NOT_CONNECTED）。
                if _pipe_has_client(handle):
                    _dbg("begin: peek says connected (race) -> connected")
                    return handle, overlapped, True
                return handle, overlapped, False
            _close_handle(handle)
            raise LocalTransportError(f"connect failed: {exc}") from None
        return handle, overlapped, True

    def accept(self, timeout: float | None = None) -> Channel | None:
        import pywintypes
        import win32event
        import win32file

        if self._closed:
            raise LocalTransportError("endpoint server closed")
        with self._lock:
            if self._pending is None:
                self._pending = self._begin_connect()
            handle, overlapped, connected = self._pending
            _dbg(f"accept: handle={int(handle)} connected={connected}")
            if not connected:
                wait_ms = (
                    win32event.INFINITE if timeout is None else max(1, int(timeout * 1000))
                )
                rc = win32event.WaitForSingleObject(overlapped.hEvent, wait_ms)
                _dbg(f"accept: wait rc={rc}")
                if rc == win32event.WAIT_TIMEOUT:
                    # 再兜一层：竞态下 connect 可能已建立但 overlapped 不完成
                    if not _pipe_has_client(handle):
                        return None
                    _dbg("accept: peek says connected (race)")
                else:
                    try:
                        win32file.GetOverlappedResult(handle, overlapped, False)
                    except pywintypes.error as exc:
                        code = exc.args[0] if exc.args else 0
                        _dbg(f"accept: overlapped err={code}")
                        if code == _ERROR_IO_INCOMPLETE:
                            return None  # 竞态：连接尚未落定，保持 pending 下次再试
                        self._pending = None
                        _close_handle(handle)
                        if self._closed:
                            raise LocalTransportError("endpoint server closed") from None
                        raise LocalTransportError(f"connect failed: {exc}") from None
            self._pending = None
            # 交接后立刻补建下一个实例，保证存活期间端点名始终可发现
            if not self._closed:
                try:
                    self._pending = self._begin_connect()
                except LocalTransportError:  # pragma: no cover - 极端资源耗尽
                    self._pending = None
        return _PipeChannel(handle)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            pending = self._pending
            self._pending = None
        if pending is not None:
            handle = pending[0]
            # 挂起的 ConnectNamedPipe 用 CloseHandle 解除不了，先 CancelIoEx
            _cancel_io(handle)
            _close_handle(handle)


class _PipeChannel(Channel):
    """Windows 管道通道：overlapped 读写，允许一读一写并发（中继模型必需）。"""

    def __init__(self, handle: Any):
        self._handle = handle

    def send(self, payload: dict[str, Any]) -> None:
        data = encode_message(payload)
        offset = 0
        while offset < len(data):
            written = self._write_once(data[offset:])
            if not written:
                raise LocalTransportError("pipe write returned 0 bytes")
            offset += written

    def recv(self) -> dict[str, Any] | None:
        header = self._read_exact(4)
        if header is None:
            return None
        size = struct.unpack("<I", header)[0]
        if size > MAX_MESSAGE_BYTES:
            raise LocalTransportError(f"declared message too large: {size}")
        body = self._read_exact(size) if size else b""
        if body is None:
            raise LocalTransportError("truncated message body")
        return _decode_body(body)

    def _write_once(self, data: bytes) -> int:
        import pywintypes
        import win32event
        import win32file

        overlapped = _new_overlapped()
        try:
            err, written = win32file.WriteFile(self._handle, data, overlapped)
        except pywintypes.error as exc:
            raise LocalTransportError(f"pipe write failed: {exc}") from None
        if err != _ERROR_IO_PENDING:
            return int(written or 0)
        win32event.WaitForSingleObject(overlapped.hEvent, win32event.INFINITE)
        try:
            return int(win32file.GetOverlappedResult(self._handle, overlapped, False))
        except pywintypes.error as exc:
            raise LocalTransportError(f"pipe write failed: {exc}") from None

    def _read_exact(self, size: int) -> bytes | None:
        chunks: list[bytes] = []
        remaining = size
        while remaining > 0:
            chunk = self._read_once(remaining)
            if not chunk:
                if not chunks:
                    return None
                raise LocalTransportError("unexpected EOF inside message frame")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _read_once(self, size: int) -> bytes:
        import pywintypes
        import win32event
        import win32file

        buffer = win32file.AllocateReadBuffer(size)
        overlapped = _new_overlapped()
        try:
            err, data = win32file.ReadFile(self._handle, buffer, overlapped)
        except pywintypes.error as exc:
            code = exc.args[0] if exc.args else 0
            if code in (_ERROR_BROKEN_PIPE, _ERROR_NO_DATA, _ERROR_OPERATION_ABORTED):
                return b""
            raise LocalTransportError(f"pipe read failed: {exc}") from None
        if err != _ERROR_IO_PENDING:
            # 同步完成：pywin32 直接返回数据
            return bytes(data) if data else b""
        win32event.WaitForSingleObject(overlapped.hEvent, win32event.INFINITE)
        try:
            count = int(win32file.GetOverlappedResult(self._handle, overlapped, False))
        except pywintypes.error as exc:
            code = exc.args[0] if exc.args else 0
            if code in (_ERROR_BROKEN_PIPE, _ERROR_NO_DATA, _ERROR_OPERATION_ABORTED):
                return b""
            raise LocalTransportError(f"pipe read failed: {exc}") from None
        return bytes(buffer[:count]) if count else b""

    def close(self) -> None:
        _close_handle(self._handle)


# -- 客户端 --------------------------------------------------------------------


def _connect_pipe(name: str, timeout: float) -> Channel:
    import pywintypes
    import win32file
    import win32pipe

    address = "\\\\.\\pipe\\" + name
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        try:
            handle = win32file.CreateFile(
                address,
                win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                0,
                None,
                win32file.OPEN_EXISTING,
                win32file.FILE_FLAG_OVERLAPPED,
                None,
            )
        except pywintypes.error as exc:
            code = exc.args[0] if exc.args else 0
            _dbg(f"client: CreateFile exc={code}")
            if code in (_ERROR_FILE_NOT_FOUND, _ERROR_PIPE_BUSY):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LocalTransportError(
                        f"endpoint not available: {name}"
                    ) from None
                try:
                    win32pipe.WaitNamedPipe(address, int(remaining * 1000) or 1)
                    _dbg("client: WaitNamedPipe returned")
                except pywintypes.error:
                    # 端点仍未出现（或名字不合法）：小睡后按剩余预算重试
                    time.sleep(min(0.02, remaining))
                continue
            raise LocalTransportError(f"endpoint connect failed: {exc}") from None
        _dbg(f"client: connected handle={int(handle)}")
        return _PipeChannel(handle)


def _connect_unix(name: str, timeout: float) -> Channel:
    path = endpoint_path(name)  # 长度守卫：超限时给明确错误，而不是笼统的 CHANNEL_OFFLINE
    assert path is not None
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(max(0.001, timeout))
    try:
        sock.connect(str(path))
    except (FileNotFoundError, ConnectionRefusedError, TimeoutError, OSError) as exc:
        sock.close()
        raise LocalTransportError(f"endpoint not available: {name} ({exc})") from None
    sock.settimeout(None)
    return _SocketChannel(sock)


# -- win32 辅助 -----------------------------------------------------------------


def _pipe_has_client(handle: Any) -> bool:
    """``PeekNamedPipe`` 成功 = 已有客户端接入。

    兜底 Windows 竞态：客户端若在 ``ConnectNamedPipe`` **之前**接入，overlapped 的
    connect 返回 pending 且**永不完成**（实测），此时这是唯一可靠的已连接判据
    （未连接时报 ``ERROR_PIPE_NOT_CONNECTED`` = 230）。
    """
    import pywintypes
    import win32pipe

    try:
        win32pipe.PeekNamedPipe(handle, 0)
        return True
    except pywintypes.error:
        return False


def _new_overlapped() -> Any:
    import pywintypes
    import win32event

    overlapped = pywintypes.OVERLAPPED()
    overlapped.hEvent = win32event.CreateEvent(None, 1, 0, None)
    return overlapped


def _close_handle(handle: Any) -> None:
    import win32file

    try:
        win32file.CloseHandle(handle)
    except Exception:  # noqa: BLE001 - 已关闭/竞态
        pass


def _cancel_io(handle: Any) -> None:
    """取消该句柄上未完成的 I/O。

    pywin32 只暴露 CancelIo（仅限调用线程），跨线程取消需 CancelIoEx，走 ctypes。
    """
    import ctypes
    from ctypes import wintypes

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
        kernel32.CancelIoEx.restype = wintypes.BOOL
        kernel32.CancelIoEx(wintypes.HANDLE(int(handle)), None)
    except Exception:  # noqa: BLE001 - 取消失败由调用方按超时兜底
        pass
