"""S4 端到端验收探针：经本地端点驱动真实扩展做一次页面捕获。

流程：连 `rpa_core_ext_<browser>_<iid>` 端点 → 发 `capture_arm` → 扩展广播到全部标签页
→ 用户 Ctrl+Click 捕获 → 扩展回 `capture_result` → host 广播给客户端 → 本脚本打印描述符。

用法（先加载 extension/ 到浏览器并确认 bridge 已连接）：
    uv run python .harness/spike/native_messaging/arm_capture_probe.py --seconds 300
日志：同目录 `arm_capture.log`。
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

from rpa_core import local_transport as lt

SPIKE_DIR = Path(__file__).resolve().parent
LOG_PATH = SPIKE_DIR / "arm_capture.log"
PREFIX = "rpa_core_ext_"


def log(message: str) -> None:
    stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    line = f"{stamp} {message}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=300.0)
    parser.add_argument("--browser", default="", help="只连该浏览器的端点（msedge/chrome）")
    args = parser.parse_args()

    LOG_PATH.write_text("", encoding="utf-8")
    log("waiting for an endpoint (load the extension first)...")
    deadline = time.monotonic() + args.seconds
    while time.monotonic() < deadline:
        channel = None
        name = ""
        while time.monotonic() < deadline and channel is None:
            for candidate in lt.list_endpoints(PREFIX):
                if args.browser and f"_{args.browser}_" not in candidate:
                    continue
                try:
                    channel = lt.connect(candidate, timeout=1.0)
                    name = candidate
                    break
                except lt.LocalTransportError:
                    continue
            if channel is None:
                time.sleep(1.0)
        if channel is None:
            log("no endpoint appeared; is the host registered and the extension loaded?")
            return 2

        log(f"connected to {name}")
        channel.send({"type": "capture_arm", "sessionId": "s4-probe"})
        log("capture_arm sent; Ctrl+Click an element in the browser now")
        try:
            while time.monotonic() < deadline:
                try:
                    message = channel.recv()
                except lt.LocalTransportError as exc:
                    log(f"read error: {exc}")
                    break
                if message is None:
                    log("host closed the connection (extension reloaded?) -> reconnect")
                    break
                kind = message.get("type")
                if kind == "capture_result":
                    log("CAPTURE_RESULT " + json.dumps(message, ensure_ascii=False))
                    channel.send({"type": "capture_disarm"})
                    log("disarmed; done")
                    return 0
                if kind in ("capture_armed", "capture_disarmed"):
                    log(f"{kind} (extension ack)")
                    continue
                log(f"msg {kind}")
        finally:
            channel.close()
    log("timed out without a capture result")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
