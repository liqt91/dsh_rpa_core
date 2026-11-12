"""临时探针：确认工作台 RPC 真能插入四种控制流节点，并看编译/卡片实况。

跑法：./.venv/Scripts/python.exe .harness/spike/probe_control_nodes.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "apps" / "xhs-workbench"
sys.path.insert(0, str(APP))

from src import ppx_bridge as pb  # noqa: E402


def main() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        (root / "workflows").mkdir()
        pb.XHS_WORKFLOWS = root / "workflows"
        api = pb._load_api()
        api.WORKSPACE = root  # _flow_path 按 WORKSPACE 解析 "workflows/xxx.json"
        created = pb._create_flow("控制流探针")
        path = created["path"]
        fp = root / "workflows" / "控制流探针.json"
        print("created:", created["path"])
        for kind in ("if", "forEach", "try", "return"):
            env = pb.dispatch("rpa.insertControlNode", {"path": path, "kind": kind})
            data = env.get("data") or {}
            print(f"\n--- {kind}: ok={env['ok']} error={env.get('error')}")
            if not env["ok"]:
                continue
            print("  data keys:", sorted(data.keys()))
            print("  data:", json.dumps(data, ensure_ascii=False)[:700])
            print("  placed:", data.get("placed"))
            print(
                "  cards:",
                [(c["kind"], c.get("branch"), c.get("label")) for c in data["flow"]["cards"]],
            )
        doc = json.loads(fp.read_text(encoding="utf-8"))
        print("\nroot children:", json.dumps(doc["root"]["children"], ensure_ascii=False))

        # 往「否则」分支插一条命令：模拟前端选中虚拟组行后发的参数
        if_node = doc["root"]["children"][0]
        env = pb.dispatch(
            "rpa.insertNode",
            {
                "path": path,
                "command": "data.log",
                "anchor_id": if_node["id"],
                "branch": "else",
            },
        )
        print("\ninsert into else:", env["ok"], env.get("error"))
        if env["ok"]:
            print("  placed:", env["data"]["placed"])
            doc2 = json.loads(fp.read_text(encoding="utf-8"))
            print("  else now:", json.dumps(doc2["root"]["children"][0]["else"], ensure_ascii=False))


if __name__ == "__main__":
    main()
