"""编辑器拖拽：显式落点插入、移动排序、卡片定位，以及前端接线两层判据。

语义层走真 RPC（工作台 → 原型 api → 磁盘 workflow → openFlow 卡片）；
接线层是源码静态断言 + 构建产物同步断言（前端没有 headless 宿主）。
负向验证见 `.harness/spike/negative_drag_drop.py`。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from src import ppx_bridge as pb

APP = Path(__file__).resolve().parents[1]
ROOT = APP.parents[1]
MAIN_JS = ROOT / ".harness" / "demo" / "ppx_probe" / "app" / "gui" / "src" / "main.js"
DIST_DIR = ROOT / ".harness" / "demo" / "ppx_probe" / "app" / "gui" / "dist" / "assets"


def _flow_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, Path]:
    """把新建流程落到临时工作区：_flow_path 认 api.WORKSPACE，_create_flow 认 XHS_WORKFLOWS。"""
    (tmp_path / "workflows").mkdir()
    monkeypatch.setattr(pb, "XHS_WORKFLOWS", tmp_path / "workflows")
    api = pb._load_api()
    monkeypatch.setattr(api, "WORKSPACE", tmp_path)
    created = pb._create_flow("拖拽探针")
    # 桥层是单例：上一条用例可能在 _flows 里留下同名 path 的 sha，乐观锁会误判
    api._flows.pop(created["path"], None)
    return created["path"], tmp_path / "workflows" / "拖拽探针.json"


def _call(method: str, **params: Any) -> dict[str, Any]:
    env = pb.dispatch(method, params)
    assert env["ok"] is True, env
    return env["data"]


def _doc(fp: Path) -> dict[str, Any]:
    return json.loads(fp.read_text(encoding="utf-8"))


def _children(fp: Path) -> list[dict[str, Any]]:
    return _doc(fp)["root"]["children"]


def _ids(nodes: list[dict[str, Any]]) -> list[str]:
    return [n["id"] for n in nodes]


def _insert(path: str, command: str = "data.log", **extra: Any) -> dict[str, Any]:
    return _call("rpa.insertNode", path=path, command=command, **extra)


def test_drop_insert_uses_explicit_index_and_preserves_click_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """点击仍追加到分支末尾；拖入按 (parent_id, branch, index) 精确落位。"""
    path, fp = _flow_fixture(tmp_path, monkeypatch)
    for _ in range(3):
        assert _insert(path)["placed"]["index"] >= 0
    before = _ids(_children(fp))

    dropped = _insert(
        path,
        parent_id="",
        branch="children",
        index=1,
        explicit=True,
    )
    assert dropped["placed"]["index"] == 1
    assert _ids(_children(fp)) == [before[0], dropped["newNodeId"], *before[1:]]

    control = _call(
        "rpa.insertControlNode",
        path=path,
        kind="if",
        parent_id="",
        branch="children",
        index=0,
        explicit=True,
    )
    # 空 action 的 with 没填时编译会报「缺参数」，那是参数未设置、不是 AST 写坏；
    # 这里改用 openFlow 的卡片钉住「拖进去之后画布真的有这一行」。
    assert control["flow"]["cards"][0]["id"] == control["newNodeId"]
    first = _children(fp)[0]
    assert first["id"] == control["newNodeId"]
    assert first["type"] == "if" and first["then"] == [] and first["else"] == []

    into_else = _insert(
        path, parent_id=first["id"], branch="else", index=0, explicit=True
    )
    assert into_else["placed"]["branch"] == "else"
    assert _doc(fp)["root"]["children"][0]["else"][0]["id"] == into_else["newNodeId"]


def test_open_flow_cards_carry_ast_drop_locations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """每张卡片的 (parentId, branch, index) 必须与真实 AST 一致——前端靠它算落点。"""
    path, fp = _flow_fixture(tmp_path, monkeypatch)
    control = _call("rpa.insertControlNode", path=path, kind="if")
    if_id = control["newNodeId"]
    _insert(path, anchor_id=if_id, branch="then")
    _insert(path, anchor_id=if_id, branch="else")
    _insert(path)  # root 末尾再挂一条

    doc = _doc(fp)
    expected: dict[str, tuple[str, str, int]] = {}
    by_id: dict[str, dict[str, Any]] = {}

    def walk(node: dict[str, Any], parent: str, branch: str, index: int, root: bool) -> None:
        if not root:
            expected[node["id"]] = (parent, branch, index)
        by_id[node["id"]] = node
        for key in ("children", "then", "else", "catch"):
            for i, child in enumerate(node.get(key) or []):
                walk(child, node["id"], key, i, False)

    walk(doc["root"], "", "", 0, True)
    flow = _call("rpa.openFlow", path=path)
    seen = 0
    for card in flow["cards"]:
        if card["kind"] == "group":
            parent = by_id[card["parentId"]]
            assert card["index"] == len(parent[card["branch"]])
            continue
        seen += 1
        assert (card["parentId"], card["branch"], card["index"]) == expected[card["id"]]
    assert seen == len(expected)


def test_move_node_reorders_within_branch_and_across_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同分支前移 / 后移（含 pop 后下标修正）与跨分支移动，都落到真实文件。"""
    path, fp = _flow_fixture(tmp_path, monkeypatch)
    for _ in range(5):
        _insert(path)
    control = _call("rpa.insertControlNode", path=path, kind="if")
    if_id = control["newNodeId"]
    _insert(path, anchor_id=if_id, branch="else")
    base = _ids(_children(fp))
    assert len(base) == 6

    # 后移：源在目标之前，pop 之后目标左移一格 ⇒ 落点应是 index=2
    moved = _call(
        "rpa.moveNode", path=path, node_id=base[0], parent_id="", branch="children", index=3
    )
    assert moved["moved"]["index"] == 2
    assert _ids(_children(fp)) == [base[1], base[2], base[0], *base[3:]]

    # 前移：源在目标之后，不需要修正
    order = _ids(_children(fp))
    moved = _call(
        "rpa.moveNode", path=path, node_id=order[-2], parent_id="", branch="children", index=0
    )
    assert moved["moved"]["index"] == 0
    assert _ids(_children(fp))[0] == order[-2]

    # 跨分支：把 root 里的一条 action 拖进「否则」末尾
    order = _ids(_children(fp))
    source = next(n for n in order if n != if_id)
    else_before = _ids(_doc(fp)["root"]["children"][-1]["else"])
    moved = _call(
        "rpa.moveNode", path=path, node_id=source, parent_id=if_id, branch="else", index=-1
    )
    assert moved["moved"]["branch"] == "else"
    children = _children(fp)
    assert source not in _ids(children)
    if_node = next(n for n in children if n["id"] == if_id)
    assert _ids(if_node["else"]) == [*else_before, source]

    # 移动完仍是能被编译器接受的 workflow，不是只在 Python dict 里成立
    repo_src = ROOT / "src"
    if str(repo_src) not in sys.path:
        sys.path.insert(0, str(repo_src))
    from rpa_core.model.workflow import Workflow

    assert Workflow.model_validate(_doc(fp)).root.type == "sequence"


def test_move_node_rejects_cycles_and_invalid_targets_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """失败路径必须一个字节都不写：环、非法容器 / 分支、root、不存在的节点。"""
    path, fp = _flow_fixture(tmp_path, monkeypatch)
    control = _call("rpa.insertControlNode", path=path, kind="if")
    if_id = control["newNodeId"]
    _insert(path, anchor_id=if_id, branch="then")
    leaf = _doc(fp)["root"]["children"][0]["then"][0]["id"]
    root_id = _doc(fp)["root"]["id"]
    before = fp.read_bytes()

    cases = [
        ("CYCLE", {"node_id": if_id, "parent_id": if_id, "branch": "then"}),
        ("INVALID_TARGET", {"node_id": leaf, "parent_id": "nope"}),
        ("INVALID_TARGET", {"node_id": leaf, "parent_id": if_id, "branch": "children"}),
        ("ROOT_NOT_MOVABLE", {"node_id": root_id}),
        ("NODE_NOT_FOUND", {"node_id": "missing"}),
    ]
    for code, params in cases:
        data = _call("rpa.moveNode", path=path, **params)
        assert data["ok"] is False and data["code"] == code, (code, data)
        assert fp.read_bytes() == before


def test_move_node_index_overflow_appends(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """前端给的 index 越界时按「末尾」处理，不能炸也不能插丢。"""
    path, fp = _flow_fixture(tmp_path, monkeypatch)
    for _ in range(3):
        _insert(path)
    before = _ids(_children(fp))
    moved = _call(
        "rpa.moveNode", path=path, node_id=before[0], parent_id="", branch="children", index=999
    )
    assert moved["moved"]["index"] == len(before) - 1
    assert _ids(_children(fp)) == [*before[1:], before[0]]


def test_move_node_obeys_flow_changed_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """别处改过盘就拒绝覆盖——拖拽写盘和参数保存必须是同一把锁。"""
    path, fp = _flow_fixture(tmp_path, monkeypatch)
    for _ in range(2):
        _insert(path)
    node_id = _children(fp)[0]["id"]
    doc = _doc(fp)
    doc["name"] = "外部改过"
    fp.write_bytes((json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    stale = fp.read_bytes()

    data = _call("rpa.moveNode", path=path, node_id=node_id, index=0)
    assert data["ok"] is False and data["code"] == "FLOW_CHANGED"
    assert fp.read_bytes() == stale


def test_editor_wires_html5_drag_and_drop() -> None:
    """接线层：左栏可拖、grip 可拖、落点换算、drop 走新 RPC、样式随产物下发。

    源码层钉住事件与 RPC 名；产物层（dist 存在时）钉住「改了 src 忘了 build」。
    dist 按 .gitignore 不入库，没有产物时只跑源码层，换机器需 npm run build。
    """
    src = MAIN_JS.read_text(encoding="utf-8")
    assert "const DND_MIME = 'application/x-ppx-node'" in src
    assert src.count("row.draggable = true") == 2  # catalog 分组 + 流程控制分组
    assert src.count("addEventListener('dragstart'") == 3  # 两处指令行 + grip
    assert "setDragPayload(event, { mode: 'move', nodeId: card.id }, 'move')" in src
    assert "grip.draggable = true" in src
    assert src.count("dataset.cardId = card.id || ''") == 2  # 组行 + 普通卡片
    assert "(card.index ?? 0) + (before ? 0 : 1)" in src
    assert "mode: 'into'" in src
    assert "el.cards.addEventListener('dragover'" in src
    assert "el.cards.addEventListener('drop'" in src
    assert "el.cards.addEventListener('dragleave'" in src
    assert "document.addEventListener('dragend', clearDropHint)" in src
    assert "'rpa.moveNode'" in src
    assert "args.explicit = true" in src
    assert "await moveCard(payload.nodeId, target)" in src
    # 样式不能写进 ppx-only 独占的 style.css，只能随共享编辑面注入
    assert "const DND_CSS = `" in src and "card.drop-before" in src

    bundles = sorted(DIST_DIR.glob("*.js")) if DIST_DIR.is_dir() else []
    if bundles:
        built = "\n".join(p.read_text(encoding="utf-8") for p in bundles)
        assert "application/x-ppx-node" in built
        assert "rpa.moveNode" in built
        assert "dnd-styles" in built
