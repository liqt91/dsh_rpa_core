"""元素资产探针：捕获暂存 / 确认入库（中文名）/ 详情改名 / 删除护栏。

headless 直调 ppx_bridge，不碰仓库里的真实流程与资产，不弹任何桌面浮窗
（本进程里把 ``_new_float`` / ``_notify`` 打成静默，只记录「浮窗里会说什么」）。
跑法：./.venv/Scripts/python.exe .harness/spike/probe_element_library.py
"""

from __future__ import annotations

import json
import sys
import tempfile
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


class _Ctl:
    """假控制器：只回一个状态，验证轮询口径，不起子进程。"""

    def __init__(self, state: str) -> None:
        self._state = state

    def poll(self) -> dict[str, Any]:
        return {"state": self._state}

    def cancel(self) -> None:
        self._state = "cancelled"


def main() -> None:
    api = pb._load_api()
    notices: list[tuple[str, str]] = []
    api._new_float = lambda: None
    api._notify = lambda title, detail="", **kw: notices.append((title, detail))
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
        doc["root"]["children"] += [
            {
                "type": "action", "id": "openHome", "command": "browser.click",
                "with": {}, "elementRefs": {"selector": "userField"},
            },
            {
                "type": "if", "id": "ifNode", "condition": "true",
                "then": [{
                    "type": "action", "id": "inThen", "command": "browser.click",
                    "with": {}, "elementRefs": {"selector": "userField"},
                }],
                "else": [],
            },
        ]
        flow_file.write_bytes(
            (json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        )
        ed = flow_file.parent / "elements"
        ok: list[str] = []

        # 1) 捕获只暂存：ready 时盘上还没有文件，preview 里带默认名。
        entry = {"path": path, "name": "userField", "suggestName": "userField",
                 "nodeId": "openHome", "overwrite": False, "captured": None,
                 "preview": None, "notified": False}
        api._CAPTURES[api._capture_key(path, "c1")] = {
            **entry, "controller": _Ctl("pending"),
        }
        assert call("rpa.capturePoll", path=path, capture_id="c1")["state"] == "pending"
        api._CAPTURES[api._capture_key(path, "c1")]["controller"] = _Ctl("ready")
        api._CAPTURES[api._capture_key(path, "c1")]["captured"] = api._hold_captured(
            entry, {"kind": "browser", "selector": {"css": "#q"}, "verifyCount": 2}
        )
        api._CAPTURES[api._capture_key(path, "c1")]["preview"] = entry["preview"]
        ready = call("rpa.capturePoll", path=path, capture_id="c1")
        assert ready["state"] == "ready" and ready["preview"]["name"] == "userField", ready
        assert not ed.exists() or not list(ed.glob("*.json")), "还没确认就落盘了"
        ok.append("捕获只暂存：ready 时盘上为空，名字在 preview 里等确认")

        # 2) 确认入库（允许中文），写 UTF-8/LF，并就地绑定引用它的节点。
        saved = call("rpa.commitElement", path=path, capture_id="c1", name="搜索框")
        assert saved["ok"] is True and saved["name"] == "搜索框", saved
        target = ed / "搜索框.json"
        assert target.is_file() and target.read_bytes().count(b"\r\n") == 0, "落盘格式不对"
        ok.append("确认后才入库：中文名写成 UTF-8/LF 文件")

        body = json.loads(flow_file.read_text(encoding="utf-8"))
        refs = [body["root"]["children"][-2]["elementRefs"]["selector"],
                body["root"]["children"][-1]["then"][0]["elementRefs"]["selector"]]
        assert refs == ["搜索框", "搜索框"], refs
        ok.append("入库改名时流程引用一起改（ref-only 不悬空）")

        listed = call("rpa.listElements", path=path)
        assert "搜索框" in [e["name"] for e in listed["elements"]], listed
        ok.append("listElements 立刻列出新资产")

        detail = call("rpa.elementDetail", path=path, name="搜索框")
        assert detail["document"]["selector"]["css"] == "#q", detail
        # M72：引用清单结构化，网页端才说得出「哪一步的哪个参数」。
        assert [(r["nodeId"], r["param"]) for r in detail["referencedBy"]] == [
            ("inThen", "selector"), ("openHome", "selector"),
        ], detail
        ok.append("elementDetail 回完整描述符 + 结构化引用（真实 AST 分支）")

        # 3) 仍被引用的资产不能删；M71 起说明只在网页端，不借桌面浮窗。
        notices_before = len(notices)
        denied = call("rpa.deleteElement", path=path, name="搜索框")
        assert denied["ok"] is False and denied["code"] == "ELEMENT_IN_USE", denied
        assert denied["referencedBy"], "没把引用节点结构回给网页端"
        assert target.is_file(), "被拒绝的删除却把文件删了"
        assert len(notices) == notices_before, "删除失败又弹了桌面浮窗"
        ok.append("仍被引用：拒绝删除 + 说明留在网页端")

        # 4) 改名联动引用；重名拒绝。
        renamed = call("rpa.saveElement", path=path, name="搜索框", new_name="搜索输入框")
        assert renamed["ok"] is True and renamed["renamedRefs"] == ["inThen", "openHome"], renamed
        assert (ed / "搜索输入框.json").is_file() and not target.exists()
        ok.append("详情改名：文件与流程引用一起改")
        dup = call("rpa.saveElement", path=path, name="搜索输入框", new_name="搜索输入框")
        assert dup["ok"] is True and dup["unchanged"] is True, dup
        ok.append("同名保存是空操作（不会误报失败）")

        # 5) 未引用资产删掉后从列表消失；非法名一律 BAD_NAME。
        orphan = {"kind": "browser", "selector": {"css": "#x"}, "verifyCount": 1}
        (ed / "多余.json").write_bytes(
            (json.dumps(orphan, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        )
        removed = call("rpa.deleteElement", path=path, name="多余")
        assert removed["deleted"] is True and not (ed / "多余.json").exists(), removed
        ok.append("未引用资产删掉后从列表消失")
        for bad in ("../evil", "a.b", "", "  "):
            r = call("rpa.deleteElement", path=path, name=bad)
            assert r["ok"] is False and r["code"] == "BAD_NAME", (bad, r)
        ok.append("非法资产名（含路径穿越、点号）一律 BAD_NAME")

        for line in ok:
            print("PASS |", line)
        print(f"全部通过：元素资产 {len(ok)} 项")


if __name__ == "__main__":
    main()
