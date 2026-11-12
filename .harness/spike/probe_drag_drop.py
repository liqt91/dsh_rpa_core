"""临时探针：拖拽落点 / 移动语义先在真 RPC 上实测，再把期望写进单测。

跑法：./.venv/Scripts/python.exe .harness/spike/probe_drag_drop.py
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


def call(method: str, **params: object) -> dict:
    env = pb.dispatch(method, params)
    if not env["ok"]:
        raise RuntimeError(f"{method} 信封失败: {env['error']}")
    return env["data"]


def ids(nodes: list[dict]) -> list[str]:
    return [n.get("id", "?") for n in nodes]


def main() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        (root / "workflows").mkdir()
        pb.XHS_WORKFLOWS = root / "workflows"
        api = pb._load_api()
        api.WORKSPACE = root
        created = pb._create_flow("拖拽探针")
        path = created["path"]
        fp = root / "workflows" / "拖拽探针.json"
        api._flows.pop(path, None)
        doc0 = json.loads(fp.read_text(encoding="utf-8"))
        print("root:", doc0["root"].get("id"), doc0["root"].get("type"))

        for command in ("data.log", "browser.navigate", "browser.close"):
            data = call("rpa.insertNode", path=path, command=command)
            print(f"click insert {command}: placed={data['placed']}")
        doc = json.loads(fp.read_text(encoding="utf-8"))
        print("A children:", ids(doc["root"]["children"]))

        # B 拖一条指令到第二行上半区：index=1
        data = call(
            "rpa.insertNode",
            path=path,
            command="data.log",
            parent_id="",
            branch="children",
            index=1,
            explicit=True,
        )
        print("B drop insert:", data["placed"])
        print("B children:", ids(json.loads(fp.read_text(encoding="utf-8"))["root"]["children"]))

        # C 拖一个 if 到最前面
        data = call(
            "rpa.insertControlNode",
            path=path,
            kind="if",
            parent_id="",
            branch="children",
            index=0,
            explicit=True,
        )
        if_id = data["newNodeId"]
        print("C drop if:", data["placed"], "newId=", if_id)
        print("C compile:", json.dumps(data["compile"], ensure_ascii=False)[:600])
        doc = json.loads(fp.read_text(encoding="utf-8"))
        print("C children:", ids(doc["root"]["children"]))

        # D 卡片定位字段
        flow = call("rpa.openFlow", path=path)
        print(
            "D cards:",
            [
                (c["kind"], c.get("id"), c.get("parentId"), c.get("branch"), c.get("index"))
                for c in flow["cards"]
            ],
        )

        # E 拖到「否则」组行（分支末尾）
        data = call(
            "rpa.insertNode",
            path=path,
            command="data.log",
            parent_id=if_id,
            branch="else",
            index=0,
            explicit=True,
        )
        print("E drop into else:", data["placed"])

        # F 同分支后移：把第 0 个节点拖到第 3 行下半区（index=3，源在前要 -1）
        doc = json.loads(fp.read_text(encoding="utf-8"))
        before = ids(doc["root"]["children"])
        src = before[0]
        data = call("rpa.moveNode", path=path, node_id=src, parent_id="", branch="children", index=3)
        doc = json.loads(fp.read_text(encoding="utf-8"))
        print("F move 0->3:", data["moved"], "before=", before, "after=", ids(doc["root"]["children"]))

        # G 同分支前移：末位 -> index=0
        before = ids(doc["root"]["children"])
        src = before[-1]
        data = call("rpa.moveNode", path=path, node_id=src, parent_id="", branch="children", index=0)
        doc = json.loads(fp.read_text(encoding="utf-8"))
        print("G move last->0:", data["moved"], "after=", ids(doc["root"]["children"]))

        # H 跨分支：root.children 里的 action 移到 if 的 else 末尾
        before = ids(doc["root"]["children"])
        src = before[-1]
        data = call("rpa.moveNode", path=path, node_id=src, parent_id=if_id, branch="else", index=-1)
        doc = json.loads(fp.read_text(encoding="utf-8"))
        if_node = next(n for n in doc["root"]["children"] if n["id"] == if_id)
        print(
            "H cross-branch:",
            data["moved"],
            "children=",
            ids(doc["root"]["children"]),
            "else=",
            ids(if_node["else"]),
        )

        # I 失败路径：防环 / 非法目标 / root / 不存在 / 越界 / 乐观锁
        doc = json.loads(fp.read_text(encoding="utf-8"))
        if_node = next(n for n in doc["root"]["children"] if n["id"] == if_id)
        cases = [
            ("cycle", {"node_id": if_id, "parent_id": if_id, "branch": "then"}),
            ("bad-parent", {"node_id": src, "parent_id": "nope", "branch": "children"}),
            ("bad-branch", {"node_id": src, "parent_id": if_id, "branch": "children"}),
            ("root", {"node_id": doc["root"]["id"]}),
            ("not-found", {"node_id": "missing"}),
            ("overflow", {"node_id": src, "parent_id": "", "branch": "children", "index": 999}),
        ]
        for label, params in cases:
            env = pb.dispatch("rpa.moveNode", {"path": path, **params})
            data = env.get("data") or {}
            print(f"I {label}: ok={data.get('ok')} code={data.get('code')} moved={data.get('moved')}")

        # J FLOW_CHANGED：外部改盘后必须拒绝且不写盘
        raw_bytes = fp.read_bytes()
        fp.write_bytes(raw_bytes.replace(b'"name":', b'"name2":', 1))
        before = fp.read_bytes()
        env = pb.dispatch("rpa.moveNode", {"path": path, "node_id": src, "index": 0})
        print("J stale:", (env.get("data") or {}).get("code"), "unchanged=", fp.read_bytes() == before)


if __name__ == "__main__":
    main()
