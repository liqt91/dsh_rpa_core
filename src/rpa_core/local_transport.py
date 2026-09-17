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

Windows 实现要点：管道句柄一律以 ``FILE_FLAG_OVERLAPPED`` 打开。同步（非 overlapped）
句柄上，一端挂起的阻塞 ``ReadFile`` 会阻塞另一线程的 ``WriteFile``（同一文件对象串行化），
而中继模型天然是「客户端线程阻塞读 + 扩展线程写回」并发，故必须 overlapped。
"""

from __future__ import annotations

import json
import os
import socket
import struct
import sys
import tempfile
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


def endpoint_name(
    browser: str, instance_id: str, prefix: str | None = None
) -> str:
    """端点名：``<prefix><browser>_<instanceId>``（非法字符归一为下划线）。"""
    effective = prefix if prefix is not None else endpoint_prefix()
    return (
        f"{effective}{_sanitize(browser or 'unknown')}"
        f"_{_sanitize(instance_id or 'default')}"
    )


def _sanitize(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in value)
    return cleaned[:64] or "default"


def _dbg(message: str) -> None:
    if os.environ.get("RPA_LT_DEBUG"):
        print(f"[lt] {message}", file=sys.stderr, flush=True)


def endpoint_dir() -> Path:
    """POSIX 端点目录（``$XDG_RUNTIME_DIR`` 优先，否则系统临时区）。"""
    base = os.environ.get("XDG_RUNTIME_DIR")
    root = Path(base) if base else Path(tempfile.gettempdir())
    path = root / "rpa_core_ext"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


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
        path.name[: -len(".sock")]
        for path in directory.glob("*.sock")
        if path.name.startswith(effective)
    )


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
        self._sock.sendall(encode_message(payload))

    def recv(self) -> dict[str, Any] | None:
        header = _socket_read_exact(self._sock, 4)
        if header is None:
            return None
        size = struct.unpack("<I", header)[0]
        if size > MAX_MESSAGE_BYTES:
            raise LocalTransportError(f"declared message too large: {size}")
        body = _socket_read_exact(self._sock, size) if size else b""
        if body is None:
            raise LocalTransportError("truncated message body")
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
        path = endpoint_dir() / f"{name}.sock"
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
        self._sock.bind(str(path))
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
    path = endpoint_dir() / f"{name}.sock"
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
