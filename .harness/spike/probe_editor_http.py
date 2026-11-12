"""HTTP 冒烟：工作台托管的编辑器确实是刚重建的那份，且新 RPC 经 /editor-ppx-rpc 可达。

不写任何流程文件：控制流 RPC 故意传一个未知 kind，只验证链路，不验证落盘。
跑法：./.venv/Scripts/python.exe .harness/spike/probe_editor_http.py
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "apps" / "xhs-workbench"
sys.path.insert(0, str(APP))

from src import server as srv  # noqa: E402


def main() -> None:
    with tempfile.TemporaryDirectory() as raw:
        ctx = srv.make_context(root=Path(raw))
        httpd = srv.make_server(ctx, host="127.0.0.1", port=0)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{port}"
        try:
            with urllib.request.urlopen(base + "/editor-ppx/", timeout=10) as resp:
                html = resp.read().decode("utf-8")
            assert "/editor-ppx/bridge.js" in html
            assert "小红书工作台 · 流程编辑器" in html
            hit = re.search(r'/editor-ppx/(assets/[^"]+\.js)', html)
            assert hit, "index.html 没被改写成 /editor-ppx/assets/"
            with urllib.request.urlopen(base + "/editor-ppx/" + hit.group(1), timeout=10) as resp:
                bundle = resp.read().decode("utf-8")
            assert "流程控制" in bundle, "托管的 JS 不是含新分组的那份"
            assert "insertControlNode" in bundle, "托管的 JS 里没有新 RPC"
            print("PASS | /editor-ppx/ 托管的是重建产物（含流程控制分组 + 新 RPC）")

            def rpc(payload: dict) -> dict:
                req = urllib.request.Request(
                    base + "/editor-ppx-rpc",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=20) as resp:
                    return json.loads(resp.read().decode("utf-8"))

            boot = rpc({"method": "rpa.bootstrap", "params": {}, "requestId": "probe-1"})
            assert boot["ok"] is True
            print(f"PASS | rpa.bootstrap commands={len(boot['data']['commands'])}")
            denied = rpc(
                {
                    "method": "rpa.insertControlNode",
                    "params": {"path": "workflows/xhs_product_search.json", "kind": "__probe__"},
                    "requestId": "probe-2",
                }
            )
            assert denied["ok"] is True
            assert denied["data"]["code"] == "UNKNOWN_CONTROL_KIND", denied
            print("PASS | /editor-ppx-rpc → rpa.insertControlNode 可达（未知 kind 被明确拒绝）")
        finally:
            httpd.shutdown()
            thread.join(timeout=5)


if __name__ == "__main__":
    main()
