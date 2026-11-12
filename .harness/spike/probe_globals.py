"""探针：rpa.saveFlowInputs 的真实行为（期望值先实测，不照抄实现）。

跑法：./.venv/Scripts/python.exe .harness/spike/probe_globals.py
只读仓库，流程落在系统临时目录，跑完自动清理。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "xhs-workbench"))

from src import ppx_bridge as pb  # noqa: E402


def main() -> int:
    api = pb._load_api()
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        (root / "workflows").mkdir()
        old_wf = pb.XHS_WORKFLOWS
        old_ws = api.WORKSPACE
        pb.XHS_WORKFLOWS = root / "workflows"
        api.WORKSPACE = root
        try:
            created = pb._create_flow("全局参数探针")
            path = created["path"]
            fp = root / "workflows" / "全局参数探针.json"
            api._flows.pop(path, None)

            opened = pb.dispatch("rpa.openFlow", {"path": path})
            print("openFlow envelope=", opened["ok"], "inputs=", opened.get("data", {}).get("inputs"))

            values = {
                "keyword": '"AI效率工具"',
                "limit": "20",
                "enabled": "true",
                "rows": "[1, 2]",
                "meta": '{"a": 1}',
                "empty": '""',
            }
            # 信封层的 ok 只说明「RPC 调通了」；业务结论在 data.ok / data.code 里。
            env = pb.dispatch("rpa.saveFlowInputs", {"path": path, "values": values})
            data = env.get("data") or {}
            print("save business ok=", data.get("ok"), "code=", data.get("code"))
            print("inputs=", json.dumps(data.get("inputs"), ensure_ascii=False))
            print("types=", {k: type(v).__name__ for k, v in (data.get("inputs") or {}).items()})
            print("compile=", json.dumps(data.get("compile"), ensure_ascii=False)[:300])
            print("added=", data.get("added"), "removed=", data.get("removed"))
            print("flow.inputs=", json.dumps((data.get("flow") or {}).get("inputs"), ensure_ascii=False))
            print("disk=", json.dumps(json.loads(fp.read_text(encoding="utf-8"))["inputs"], ensure_ascii=False))

            before = fp.read_bytes()
            again = pb.dispatch("rpa.saveFlowInputs", {"path": path, "values": values})
            print("idempotent saved=", (again.get("data") or {}).get("saved"), "bytes same=", fp.read_bytes() == before)

            def business(values: dict) -> dict:
                return pb.dispatch("rpa.saveFlowInputs", {"path": path, "values": values}).get("data") or {}

            bad_name = business({"a.b": "1"})
            print("bad name ok=", bad_name.get("ok"), "code=", bad_name.get("code"), "msg=", bad_name.get("error"))
            print("bad name disk unchanged=", fp.read_bytes() == before)

            reserved = business({"loop": "1"})
            print("reserved ok=", reserved.get("ok"), "code=", reserved.get("code"))

            bad_json = business({"x": "[1,"})
            print("bad json ok=", bad_json.get("ok"), "code=", bad_json.get("code"))

            # 乐观锁：外部改盘后，_flows 里的 sha 过期
            doc = json.loads(fp.read_text(encoding="utf-8"))
            doc["name"] = "外部改过"
            external = (json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            fp.write_bytes(external)
            stale = business({"only": '"x"'})
            print("stale ok=", stale.get("ok"), "code=", stale.get("code"))
            print("stale disk unchanged=", fp.read_bytes() == external)
        finally:
            pb.XHS_WORKFLOWS = old_wf
            api.WORKSPACE = old_ws
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
