"""编辑器「元素资产库」：预置资产入库、详情、删除护栏、重捕判据与三端接线。

用户反馈「界面上当前没有捕获资产的入库」：原先只有「补 elementRefs 缺口」一条路，
缺口为空时界面上一个捕获入口都没有。本轮把工具条做成常驻，新建的是**预置资产**
（ref-only 契约下不改写工作流、不自动绑节点），已就绪行补详情 / 重捕 / 删除。

后端语义探针 ``.harness/spike/probe_element_library.py``；
UI 动态判据 ``scripts/check_element_library.mjs``；
负向验证 ``.harness/spike/negative_element_library.py``。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from src import ppx_bridge as pb

APP = Path(__file__).resolve().parents[1]
ROOT = APP.parents[1]
MAIN_JS = ROOT / ".harness" / "demo" / "ppx_probe" / "app" / "gui" / "src" / "main.js"
INDEX_HTML = ROOT / ".harness" / "demo" / "ppx_probe" / "app" / "gui" / "index.html"
STYLE_CSS = ROOT / ".harness" / "demo" / "ppx_probe" / "app" / "gui" / "src" / "style.css"
API_PY = ROOT / ".harness" / "demo" / "ppx_probe" / "app" / "api" / "api.py"
DIST_DIR = ROOT / ".harness" / "demo" / "ppx_probe" / "app" / "gui" / "dist" / "assets"
CHECK_ALL = ROOT / ".harness" / "scripts" / "check_all.py"
UI_SCRIPT = ROOT / "scripts" / "check_element_library.mjs"

def _call(method: str, **params: Any) -> dict[str, Any]:
    env = pb.dispatch(method, params)
    assert env["ok"] is True, env
    return env["data"]

@pytest.fixture
def flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, Path, Any]:
    (tmp_path / "workflows").mkdir()
    monkeypatch.setattr(pb, "XHS_WORKFLOWS", tmp_path / "workflows")
    api = pb._load_api()
    monkeypatch.setattr(api, "WORKSPACE", tmp_path)
    created = pb._create_flow("元素资产库夹具")
    api._flows.pop(created["path"], None)
    target = tmp_path / created["path"]
    doc = json.loads(target.read_text(encoding="utf-8"))
    # 真实 AST 形状：引用散在 children / if.then / if.else / try.catch 四处，
    # 只递归 children 的删除护栏会漏掉控制流里的引用，删完等运行才炸。
    doc["root"]["children"] = [
        {
            "type": "action", "id": "inRoot", "command": "browser.click",
            "with": {}, "elementRefs": {"selector": "in_use"},
        },
        {
            "type": "if", "id": "ifNode", "condition": "true",
            "then": [{
                "type": "action", "id": "inThen", "command": "browser.click",
                "with": {}, "elementRefs": {"selector": "branch_ref"},
            }],
            "else": [{
                "type": "action", "id": "inElse", "command": "browser.click",
                "with": {}, "elementRefs": {"selector": "branch_ref"},
            }],
        },
        {
            "type": "try", "id": "tryNode", "errorVar": "err", "children": [],
            "catch": [{
                "type": "action", "id": "inCatch", "command": "desktop.click",
                "with": {}, "elementRefs": {"selector": "branch_ref"},
            }],
        },
    ]
    target.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return created["path"], target, api

def test_bridge_registers_asset_library_rpcs() -> None:
    registry = pb._registry()
    for name in ("rpa.elementDetail", "rpa.deleteElement"):
        assert name in registry, f"{name} 没注册进桥层"
        assert callable(registry[name]), f"{name} 不是可调用的处理函数"

def test_write_element_creates_unbound_preset_asset(flow: tuple[str, Path, Any]) -> None:
    path, target, _ = flow
    wrote = _call("rpa.writeElement", path=path, name="preset_one", selector="#preset")
    assert wrote["ok"] is True
    assert wrote["boundNodeId"] is None, "预置资产不该凭空绑到节点"
    listed = _call("rpa.listElements", path=path)
    assert "preset_one" in [e["name"] for e in listed["elements"]]
    doc = json.loads(target.read_text(encoding="utf-8"))
    assert "preset_one" not in json.dumps(doc), "新建资产不该改写工作流"

def test_element_detail_returns_full_descriptor(flow: tuple[str, Path, Any]) -> None:
    path, _, _ = flow
    elem = _call("rpa.writeElement", path=path, name="in_use", selector="#used")
    assert elem["ok"] is True
    detail = _call("rpa.elementDetail", path=path, name="in_use")
    assert detail["ok"] is True
    assert detail["document"]["selector"]["css"] == "#used"
    assert detail["document"]["metadata"]["source"] == "manual"
    assert detail["size"] > 0 and detail["file"].endswith("in_use.json")

def test_element_detail_walks_all_real_ast_branches(flow: tuple[str, Path, Any]) -> None:
    path, _, _ = flow
    _call("rpa.writeElement", path=path, name="branch_ref", selector="#branch")
    detail = _call("rpa.elementDetail", path=path, name="branch_ref")
    assert detail["referencedBy"] == ["inCatch", "inElse", "inThen"], detail["referencedBy"]

@pytest.mark.parametrize("name", ["../evil", "a.b", "", "空格"])
def test_detail_rejects_bad_names(flow: tuple[str, Path, Any], name: str) -> None:
    path, _, _ = flow
    res = _call("rpa.elementDetail", path=path, name=name)
    assert res["ok"] is False and res["code"] == "BAD_NAME", (name, res)

def test_detail_reports_missing_and_broken_json(flow: tuple[str, Path, Any]) -> None:
    path, target, _ = flow
    assert _call("rpa.elementDetail", path=path, name="nope")["code"] == "NOT_FOUND"
    ed = target.parent / "elements"
    ed.mkdir(parents=True, exist_ok=True)
    (ed / "broken.json").write_text("{bad json", encoding="utf-8")
    assert _call("rpa.elementDetail", path=path, name="broken")["code"] == "BAD_JSON"

def test_delete_refuses_referenced_asset_without_unlink(flow: tuple[str, Path, Any]) -> None:
    path, target, _ = flow
    _call("rpa.writeElement", path=path, name="branch_ref", selector="#branch")
    denied = _call("rpa.deleteElement", path=path, name="branch_ref")
    assert denied["ok"] is False and denied["code"] == "ELEMENT_IN_USE"
    assert denied["referencedBy"] == ["inCatch", "inElse", "inThen"]
    assert (target.parent / "elements" / "branch_ref.json").is_file()

def test_delete_removes_unreferenced_asset(flow: tuple[str, Path, Any]) -> None:
    path, target, _ = flow
    _call("rpa.writeElement", path=path, name="preset_one", selector="#preset")
    removed = _call("rpa.deleteElement", path=path, name="preset_one")
    assert removed["deleted"] is True and "preset_one" not in removed["remaining"]
    assert not (target.parent / "elements" / "preset_one.json").exists()
    assert "preset_one" not in [
        e["name"] for e in _call("rpa.listElements", path=path)["elements"]
    ]

@pytest.mark.parametrize("name", ["../evil", "a.b", "", "空格"])
def test_delete_rejects_bad_names(flow: tuple[str, Path, Any], name: str) -> None:
    path, _, _ = flow
    res = _call("rpa.deleteElement", path=path, name=name)
    assert res["ok"] is False and res["code"] == "BAD_NAME", (name, res)

def test_recapture_needs_a_fresh_write_not_just_a_file(flow: tuple[str, Path, Any]) -> None:
    """重捕一个已存在的资产：新模型只看后台 pick 线程是否真正产出结果，
    旧文件在盘上不会假报 ready —— 链路里没有「文件在不在」这种超时补丁。"""
    path, _, api = flow
    _call("rpa.writeElement", path=path, name="in_use", selector="#used")
    key = api._capture_key(path, "in_use")

    # 进行中（pending）：还没真正点选，即便文件已存在也绝不能报 ready。
    api._CAPTURES[key] = {
        "name": "in_use", "path": path, "decision": {"target": "browser"},
        "nodeId": None, "session": None, "cancel_event": threading.Event(),
        "result": None, "cancelled": False, "offline": False, "armed": False,
        "instructions": "", "startedAt": time.time(),
    }
    assert _call("rpa.capturePoll", path=path, name="in_use")["state"] == "pending"

    # 取消优先：cancel_event 置位后，即便后面真有结果也只报 cancelled。
    api._CAPTURES[key]["cancel_event"].set()
    assert _call("rpa.capturePoll", path=path, name="in_use")["state"] == "cancelled"

    # 真正产出（ready）：只有后台 pick 落盘的结果才让 poll 报 ready，且回显选择器。
    ev = api._CAPTURES[key]
    ev["cancel_event"] = threading.Event()
    ev["cancelled"] = False
    ev["result"] = {
        "kind": "browser", "selector": {"css": "#new"}, "verifyCount": 2,
        "metadata": {}, "savedAs": "in_use",
    }
    ready = _call("rpa.capturePoll", path=path, name="in_use")
    assert ready["state"] == "ready" and ready["result"]["selector"]["css"] == "#new"

def test_frontend_wires_constant_entry_and_asset_actions() -> None:
    main = MAIN_JS.read_text(encoding="utf-8")
    html = INDEX_HTML.read_text(encoding="utf-8")
    css = STYLE_CSS.read_text(encoding="utf-8")
    for needle in (
        "bindElementTools()",
        "ppx.call('rpa.writeElement'",
        "ppx.call('rpa.elementDetail'",
        "ppx.call('rpa.deleteElement'",
        "if (!pickMissing.length || !pickCapabilities",
    ):
        assert needle in main, f"main.js 缺 {needle}"
    for node_id in (
        "elemNewName", "elemNewWhere", "elemNewCapture",
        "elemNewSelector", "elemNewWrite", "elemNewNote",
    ):
        assert f'id="{node_id}"' in html, f"index.html 缺 {node_id}"
    # 带花括号的等值形状：只写 ".elemTools" 会被 .elemToolsRow 这个子串糊弄成恒绿。
    for selector in (".elemTools {", ".elemDetail {", ".elemAct.danger", ".elemNewNote.error"):
        assert selector in css, f"style.css 缺 {selector}"

def test_ui_check_is_wired_into_full_gate() -> None:
    hits = CHECK_ALL.read_text(encoding="utf-8").count('"check_element_library.mjs"')
    assert hits == 1, f"check_all.py 里接线命中 {hits} 次（须恰好 1）"

def test_ui_check_really_runs() -> None:
    node = shutil.which("node")
    if not node or not UI_SCRIPT.is_file():
        pytest.skip("本机没有 node；check_all.py 同样会跳过 JS 判据")
    result = subprocess.run(
        [node, str(UI_SCRIPT)], capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ELEMENT LIBRARY CHECK PASSED" in result.stdout

def test_dist_bundle_is_rebuilt_from_source() -> None:
    if not DIST_DIR.is_dir():
        pytest.skip("dist 未构建（.gitignore 忽略产物）")
    bundles = list(DIST_DIR.glob("*.js")) + list(DIST_DIR.glob("*.css"))
    joined = "\n".join(p.read_text(encoding="utf-8") for p in bundles)
    for token in ("rpa.elementDetail", "rpa.deleteElement", ".elemTools", "elemNewCapture"):
        assert token in joined, f"dist 产物里没有 {token}（改了源码忘了 npm run build）"
