#!/usr/bin/env python3
"""启动小红书工作台本地服务（默认 127.0.0.1:8790）。

用法：
    python -m src.run_server                 # 默认 8790
    python -m src.run_server --port 9000     # 换端口
    python -m src.run_server --data-root /tmp/xhs-data

数据落在 ``--data-root``（默认 apps/xhs-workbench/data），与单测用的临时目录互不干扰。
采集触发走后台线程，触发后轮询 GET /api/monitor 看进度。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from src.server import DEFAULT_PORT, make_context, make_server  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="小红书工作台本地服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--data-root", default=None, help="数据目录，默认 apps/xhs-workbench/data")
    parser.add_argument("--browser", default="chrome", choices=("chrome", "msedge"))
    args = parser.parse_args(argv)

    ctx = make_context(root=args.data_root, default_browser=args.browser)
    server = make_server(ctx, host=args.host, port=args.port)
    url = f"http://{args.host}:{args.port}"
    print(f"小红书工作台已启动：{url}  （数据目录 {ctx.root}）")
    print("  GET  /api/health | /api/snapshots | /api/products | /api/alerts | /api/monitor")
    print(
        "  POST /api/collect {keyword,browserType?} | /api/pause | /api/resume"
        " | /api/pause-all | /api/resume-all"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在关闭…")
    finally:
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
