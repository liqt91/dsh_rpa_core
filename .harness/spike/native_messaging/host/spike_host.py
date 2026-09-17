"""S0 spike host：最小 native messaging host（不依赖 rpa_core，独立可跑）。

由浏览器按需拉起，读 stdin 的 Native Messaging 帧（4 字节小端长度前缀 + JSON），
把每个事件带时间戳追加到日志文件；对 ping 回 pong、对 hello 回 ready。

观测要点：
- START/EOF 行成对出现 = host 生命周期被浏览器正确接管（扩展 reload / 浏览器退出）；
- PING 行的时间间隔 = 心跳连续性（间隔稳定 15s 即 SW 未被回收）；
- 出现 EOF 后又出现新 START = 断线重连拉起了新 host 进程。
"""

from __future__ import annotations

import json
import os
import struct
import sys
import tempfile
import time
from datetime import datetime

DEFAULT_LOG = os.path.join(tempfile.gettempdir(), "rpa_nm_spike_host.log")


def _log_path() -> str:
    return os.environ.get("RPA_SPIKE_LOG") or DEFAULT_LOG


def log(message: str) -> None:
    stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    with open(_log_path(), "a", encoding="utf-8") as handle:
        handle.write(f"{stamp} [{os.getpid()}] {message}\n")


def _read_exact(stream, size: int) -> bytes | None:
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            return None if not chunks else b"".join(chunks)
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_message(stream) -> dict | None:
    header = _read_exact(stream, 4)
    if header is None or len(header) < 4:
        return None
    size = struct.unpack("<I", header)[0]
    body = _read_exact(stream, size) if size else b""
    if body is None:
        return None
    return json.loads(body.decode("utf-8"))


def write_message(stream, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    stream.write(struct.pack("<I", len(body)) + body)
    stream.flush()


def main() -> int:
    if sys.platform == "win32":
        import msvcrt

        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    log(f"START pid={os.getpid()} log={_log_path()}")
    reader = sys.stdin.buffer
    writer = sys.stdout.buffer
    while True:
        try:
            message = read_message(reader)
        except Exception as exc:  # noqa: BLE001 - spike 记录原始错误即可
            log(f"READ_ERROR {exc!r}")
            break
        if message is None:
            log("EOF stdin -> exit")
            return 0
        kind = str(message.get("type") or "")
        if kind == "hello":
            log(f"HELLO {json.dumps(message, ensure_ascii=False)}")
            write_message(
                writer,
                {"type": "ready", "pid": os.getpid(), "at": time.time()},
            )
        elif kind == "ping":
            log(f"PING seq={message.get('seq')} at={message.get('at')}")
            write_message(
                writer,
                {"type": "pong", "seq": message.get("seq"), "at": time.time()},
            )
        else:
            log(f"MSG {json.dumps(message, ensure_ascii=False)}")
            write_message(writer, {"type": "ack", "echo": kind})
    log("EOF stdin -> exit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
