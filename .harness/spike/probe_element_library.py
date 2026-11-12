"""元素资产库探针：预置资产入库 / 详情 / 删除护栏 / 重捕 mtime 判据。

headless 直调 ppx_bridge，不碰仓库里的真实流程与资产。
跑法：./.venv/Scripts/python.exe .harness/spike/probe_element_library.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "apps" / "xhs-workbench"
sys.path.insert(0, str(APP))

from src import ppx_bridge as pb  # noqa: E402


class _FakeProc:
    returncode = None

    def poll(self) -> None:
        return None


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

        elem = ed / "in_use.json"
        baseline = elem.stat().st_mtime_ns
        entry = {
            "path": path,
            "name": "in_use",
            "proc": _FakeProc(),
            "baselineMtimeNs": baseline,
            "deadlineAt": time.time() + 60,
            "cancelled": False,
            "nodeId": None,
            "stdout": [],
            "stderr": [],
            "decision": {"target": "browser"},
        }
        api._CAPTURES[api._capture_key(path, "in_use")] = entry
        polled = call("rpa.capturePoll", path=path, name="in_use")
        assert polled["state"] == "waiting", polled
        ok.append("重捕时旧文件还在也不假报 bound（等这一轮真重写）")

        elem.write_text(
            json.dumps({"kind": "browser", "selector": {"css": "#new"}}) + "\n",
            encoding="utf-8",
        )
        future = baseline + 2_000_000_000
        os.utime(elem, ns=(future, future))
        polled2 = call("rpa.capturePoll", path=path, name="in_use")
        assert polled2["state"] == "bound", polled2
        assert polled2["result"]["selector"] == "#new", polled2
        ok.append("这轮真重写过盘才报 bound，且回显新选择器")

        for line in ok:
            print("PASS |", line)
        print(f"全部通过：元素资产库 {len(ok)} 项")


if __name__ == "__main__":
    main()
