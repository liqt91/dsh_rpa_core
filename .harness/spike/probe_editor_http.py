"""HTTP 冒烟：工作台托管的编辑器确实是刚重建的那份，且新 RPC 经 /editor-ppx-rpc 可达。

不写任何流程文件：控制流 RPC 故意传未知 kind、移动 RPC 传不存在的节点、
全局参数 RPC 传非法参数名，全部只验证链路与明确拒绝，不验证落盘。
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
            for token in ("application/x-ppx-node", "rpa.moveNode"):
                assert token in bundle, f"托管的 JS 里没有拖拽实现：{token}"
            for token in ("rpa.saveFlowInputs", "bottomDock", "globalsCount"):
                assert token in bundle, f"托管的 JS 里没有底栏/全局参数：{token}"
            for token in ("rpa.elementDetail", "rpa.deleteElement", "elemNewCapture"):
                assert token in bundle, f"托管的 JS 里没有元素资产库：{token}"
            css_hit = re.search(r'/editor-ppx/(assets/[^"]+\.css)', html)
            assert css_hit, "index.html 没引用 CSS 产物"
            with urllib.request.urlopen(base + "/editor-ppx/" + css_hit.group(1), timeout=10) as resp:
                built_css = resp.read().decode("utf-8")
            for token in ("#bottomDock", ".dockTab.active", ".globalRow", ".card.drop-before", ".elemTools"):
                assert token in built_css, f"托管的 CSS 缺 {token}"
            print("PASS | /editor-ppx/ 托管的是重建产物（流程控制 + 拖拽 + 底栏三模块 + 资产库 + 版式）")

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
            moved = rpc(
                {
                    "method": "rpa.moveNode",
                    "params": {
                        "path": "workflows/xhs_product_search.json",
                        "node_id": "__probe_missing__",
                    },
                    "requestId": "probe-3",
                }
            )
            assert moved["ok"] is True
            assert moved["data"]["code"] == "NODE_NOT_FOUND", moved
            print("PASS | /editor-ppx-rpc → rpa.moveNode 可达（不存在的节点被明确拒绝）")
            globals_denied = rpc(
                {
                    "method": "rpa.saveFlowInputs",
                    "params": {
                        "path": "workflows/xhs_product_search.json",
                        "values": {"a.b": "1"},
                    },
                    "requestId": "probe-4",
                }
            )
            assert globals_denied["ok"] is True
            assert globals_denied["data"]["code"] == "INVALID_DECLARATION", globals_denied
            print("PASS | /editor-ppx-rpc → rpa.saveFlowInputs 可达（非法名被明确拒绝、未写盘）")
            for method in ("rpa.elementDetail", "rpa.deleteElement"):
                lib = rpc(
                    {
                        "method": method,
                        "params": {
                            "path": "workflows/xhs_product_search.json",
                            "name": "__probe_missing__",
                        },
                        "requestId": f"probe-lib-{method}",
                    }
                )
                assert lib["ok"] is True, lib
                assert lib["data"]["code"] == "NOT_FOUND", lib
            print("PASS | /editor-ppx-rpc → 资产库详情/删除可达（不存在的资产被明确拒绝、未写盘）")
        finally:
            httpd.shutdown()
            thread.join(timeout=5)


if __name__ == "__main__":
    main()
