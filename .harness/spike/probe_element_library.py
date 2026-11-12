"""元素资产库探针：预置资产入库 / 详情 / 删除护栏 / 重捕 mtime 判据。

headless 直调 ppx_bridge，不碰仓库里的真实流程与资产。
跑法：./.venv/Scripts/python.exe .harness/spike/probe_element_library.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "apps" / "xhs-workbench"
sys.path.insert(0, str(APP))

from src import ppx_bridge as pb  # noqa: E402

def call(method: str, **params: Any) -> dict[str, Any]:
    env = pb.dispatch(method, params)
    assert env["ok"] is True, (method, env)
    return env["data"]

def main() -> None:
    api = pb._load_api()
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        (tmp / "workflows").mkdir()
        pb.XHS_WORKFLOWS = tmp / "workflows"
        api.WORKSPACE = tmp
        created = pb._create_flow("元素库探针")
        api._flows.pop(created["path"], None)
        path = created["path"]
        flow_file = tmp / path
        doc = json.loads(flow_file.read_text(encoding="utf-8"))
        doc["root"]["children"].append(
            {
                "type": "action",
                "id": "usesInUse",
                "command": "browser.click",
                "with": {},
                "elementRefs": {"selector": "in_use"},
            }
        )
        flow_file.write_text(
            json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        ed = flow_file.parent / "elements"
        ok: list[str] = []

        wrote = call("rpa.writeElement", path=path, name="preset_one", selector="#preset")
        assert wrote["ok"] is True and wrote["boundNodeId"] is None, wrote
        ok.append("writeElement 建了不在缺口里的预置资产，不绑节点")

        listed = call("rpa.listElements", path=path)
        assert "preset_one" in [e["name"] for e in listed["elements"]], listed
        ok.append("listElements 立刻列出新入库资产")

        call("rpa.writeElement", path=path, name="in_use", selector="#used")
        detail = call("rpa.elementDetail", path=path, name="in_use")
        assert detail["document"]["selector"]["css"] == "#used", detail
        assert detail["referencedBy"] == ["usesInUse"], detail
        ok.append("elementDetail 回完整描述符 + 按真实 AST 找到引用节点")

        denied = call("rpa.deleteElement", path=path, name="in_use")
        assert denied["ok"] is False and denied["code"] == "ELEMENT_IN_USE", denied
        assert (ed / "in_use.json").is_file(), "被拒绝的删除却把文件删了"
        ok.append("仍被引用的资产拒绝删除且不落删")

        removed = call("rpa.deleteElement", path=path, name="preset_one")
        assert removed["deleted"] is True and "preset_one" not in removed["remaining"], removed
        assert not (ed / "preset_one.json").exists()
        ok.append("未引用资产删掉后从列表消失")

        for bad in ("../evil", "a.b", ""):
            r = call("rpa.deleteElement", path=path, name=bad)
            assert r["ok"] is False and r["code"] == "BAD_NAME", (bad, r)
        ok.append("非法资产名（含路径穿越）一律 BAD_NAME")

        # 重捕判据：新模型只看后台 pick 线程是否真正产出结果，
        # 旧文件在盘上不会假报 ready —— 链路里没有「文件在不在」这种超时补丁。
        key = api._capture_key(path, "in_use")
        api._CAPTURES[key] = {
            "name": "in_use", "path": path, "decision": {"target": "browser"},
            "nodeId": None, "session": None, "cancel_event": threading.Event(),
            "result": None, "cancelled": False, "offline": False, "armed": False,
            "instructions": "", "startedAt": time.time(),
        }
        polled = call("rpa.capturePoll", path=path, name="in_use")
        assert polled["state"] == "pending", polled
        ok.append("重捕进行中（pending）不假报 ready，即便旧文件还在盘上")

        # 只有后台 pick 真正落地的结果才让 poll 报 ready，且回显新选择器。
        api._CAPTURES[key]["result"] = {
            "kind": "browser", "selector": {"css": "#new"}, "verifyCount": 2,
            "metadata": {}, "savedAs": "in_use",
        }
        polled2 = call("rpa.capturePoll", path=path, name="in_use")
        assert polled2["state"] == "ready", polled2
        assert polled2["result"]["selector"]["css"] == "#new", polled2
        ok.append("只有 pick 真正产出才报 ready，且回显新选择器")

        for line in ok:
            print("PASS |", line)
        print(f"全部通过：元素资产库 {len(ok)} 项")

if __name__ == "__main__":
    main()
