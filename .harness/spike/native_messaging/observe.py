"""S0 spike 观测器：把扩展侧连接错误与 host 生命周期落到一个日志文件。

两个职责：
1. **错误上报服务器**（127.0.0.1:8799）：spike 扩展在连接尝试/断开/异常时 POST 过来，
   免去人工从 SW 控制台抄录 `chrome.runtime.lastError`；
2. **端点观测**：轮询 `rpa_core_ext_*` 端点的出现/消失（= host 被拉起/被回收），
   并对每个出现的端点建立客户端连接、记录收到的每条消息（含扩展的 15s ping 广播）
   ——据此判断 MV3 SW 是否被回收。

用法：
    uv run python .harness/spike/native_messaging/observe.py --seconds 900
日志：同目录 `observe.log`（每行带毫秒时间戳）。
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from rpa_core import local_transport as lt

SPIKE_DIR = Path(__file__).resolve().parent
LOG_PATH = SPIKE_DIR / "observe.log"
PREFIX = "rpa_core_ext_"
REPORT_PORT = 8799


def log(message: str) -> None:
    stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    line = f"{stamp} {message}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


class _ReportHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - http.server 命名
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw.decode("utf-8"))
        except ValueError:
            payload = {"raw": raw.decode("utf-8", errors="replace")}
        log(f"EXT-REPORT {json.dumps(payload, ensure_ascii=False)}")
        self.send_response(204)
        self.end_headers()

    def log_message(self, *_args) -> None:  # 静音默认访问日志
        return


def _start_reporter() -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", REPORT_PORT), _ReportHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log(f"reporter listening on 127.0.0.1:{REPORT_PORT}")
    return server


def _watch_endpoint(name: str) -> None:
    """连接一个端点并持续记录其消息，直到断开。"""
    try:
        channel = lt.connect(name, timeout=1.0)
    except lt.LocalTransportError as exc:
        log(f"ENDPOINT {name} connect-failed: {exc}")
        return
    log(f"ENDPOINT {name} connected")
    try:
        while True:
            try:
                message = channel.recv()
            except lt.LocalTransportError as exc:
                log(f"ENDPOINT {name} read-error: {exc}")
                return
            if message is None:
                log(f"ENDPOINT {name} closed-by-host")
                return
            kind = message.get("type")
            if kind == "ping":
                log(f"PING seq={message.get('seq')} ext_at={message.get('at')}")
            else:
                log(f"MSG {json.dumps(message, ensure_ascii=False)[:300]}")
    finally:
        channel.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=900.0)
    args = parser.parse_args()

    LOG_PATH.write_text("", encoding="utf-8")
    server = _start_reporter()
    log("observing... 请在浏览器里操作（reload 扩展 / 关闭浏览器）")
    seen: set[str] = set()
    active: set[str] = set()
    deadline = time.monotonic() + args.seconds
    try:
        while time.monotonic() < deadline:
            current = {
                name
                for name in lt.list_endpoints()
                if name.startswith(PREFIX)
            }
            for name in sorted(current - seen):
                log(f"HOST-UP {name}")
                threading.Thread(
                    target=_watch_endpoint, args=(name,), daemon=True
                ).start()
                active.add(name)
            for name in sorted(seen - current):
                log(f"HOST-DOWN {name}")
                active.discard(name)
            seen = current
            time.sleep(1.0)
    except KeyboardInterrupt:
        log("interrupted")
    finally:
        server.shutdown()
    log("observer stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
