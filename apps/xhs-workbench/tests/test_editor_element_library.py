"""编辑器「元素资产」：单按钮捕获、确认入库（中文名）、详情改名、删除护栏。

用户反馈「元素捕获界面简化为单个按钮」：不预填资产名、不选网页/桌面、不手写选择器；
抓完弹编辑框确认名字（可中文），详情按钮复用同一个框改名。提示一律走桌面浮窗，
元素清单里不出现任何提示条。

后端语义探针 ``.harness/spike/probe_element_library.py``；
动态契约 ``.harness/scripts/check_element_pick_contract.py``；
UI 判据 ``scripts/check_element_library.mjs`` / ``scripts/check_element_pick_ui.mjs``；
负向验证 ``.harness/spike/negative_element_library.py``。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from src import ppx_bridge as pb

APP = Path(__file__).resolve().parents[1]
ROOT = APP.parents[1]
MAIN_JS = ROOT / ".harness" / "demo" / "ppx_probe" / "app" / "gui" / "src" / "main.js"
INDEX_HTML = ROOT / ".harness" / "demo" / "ppx_probe" / "app" / "gui" / "index.html"
STYLE_CSS = ROOT / ".harness" / "demo" / "ppx_probe" / "app" / "gui" / "src" / "style.css"
DIST_DIR = ROOT / ".harness" / "demo" / "ppx_probe" / "app" / "gui" / "dist" / "assets"
CHECK_ALL = ROOT / ".harness" / "scripts" / "check_all.py"
UI_SCRIPT = ROOT / "scripts" / "check_element_library.mjs"


def _call(method: str, **params: Any) -> dict[str, Any]:
    env = pb.dispatch(method, params)
    assert env["ok"] is True, env
    return env["data"]


def _put_element(target: Path, name: str, selector: str = "#used") -> Path:
    """直接落一个元素文件：本轮删掉了「手写选择器入库」的 RPC，夹具只能自己写盘。"""
    folder = target.parent / "elements"
    folder.mkdir(parents=True, exist_ok=True)
    doc = {
        "kind": "browser",
        "selector": {"css": selector},
        "verifyCount": 1,
        "name": name,
        "metadata": {},
        "capturedAt": "2026-11-12T00:00:00Z",
    }
    path = folder / f"{name}.json"
    path.write_bytes((json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return path


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


def test_bridge_registers_asset_rpcs_and_drops_manual_entry() -> None:
    registry = pb._registry()
    for name in (
        "rpa.elementDetail",
        "rpa.deleteElement",
        "rpa.commitElement",
        "rpa.saveElement",
        "rpa.discardCapture",
        "rpa.editElement",
    ):
        assert name in registry, f"{name} 没注册进桥层"
        assert callable(registry[name]), f"{name} 不是可调用的处理函数"
    # 手写选择器入库与本轮的产品口径冲突：留着入口就是留着一条能绕过捕获的路。
    for gone in ("rpa.writeElement", "rpa.pickPlan"):
        assert gone not in registry, f"{gone} 还在（本轮已删）"


def test_element_detail_returns_full_descriptor(flow: tuple[str, Path, Any]) -> None:
    path, target, _ = flow
    _put_element(target, "in_use", "#used")
    detail = _call("rpa.elementDetail", path=path, name="in_use")
    assert detail["ok"] is True
    assert detail["document"]["selector"]["css"] == "#used"
    assert detail["selectorText"] == "#used"
    assert detail["size"] > 0 and detail["file"].endswith("in_use.json")


def test_element_detail_walks_all_real_ast_branches(flow: tuple[str, Path, Any]) -> None:
    path, target, _ = flow
    _put_element(target, "branch_ref", "#branch")
    detail = _call("rpa.elementDetail", path=path, name="branch_ref")
    assert detail["referencedBy"] == ["inCatch", "inElse", "inThen"], detail["referencedBy"]


@pytest.mark.parametrize("name", ["../evil", "a/b", "a.b", "", "  ", "con"])
def test_detail_rejects_bad_names(flow: tuple[str, Path, Any], name: str) -> None:
    path, _, _ = flow
    res = _call("rpa.elementDetail", path=path, name=name)
    assert res["ok"] is False and res["code"] == "BAD_NAME", (name, res)


def test_detail_accepts_chinese_names(flow: tuple[str, Path, Any]) -> None:
    """用户要求「资产名称支持中文输入」：中文名不是特例，而是正常路径。"""
    path, target, _ = flow
    _put_element(target, "登录按钮", "#submit")
    detail = _call("rpa.elementDetail", path=path, name="登录按钮")
    assert detail["ok"] is True, detail
    assert detail["document"]["selector"]["css"] == "#submit"


def test_detail_reports_missing_and_broken_json(flow: tuple[str, Path, Any]) -> None:
    path, target, _ = flow
    assert _call("rpa.elementDetail", path=path, name="nope")["code"] == "NOT_FOUND"
    ed = target.parent / "elements"
    ed.mkdir(parents=True, exist_ok=True)
    (ed / "broken.json").write_text("{bad json", encoding="utf-8")
    assert _call("rpa.elementDetail", path=path, name="broken")["code"] == "BAD_JSON"


def test_rename_updates_asset_and_every_reference(flow: tuple[str, Path, Any]) -> None:
    path, target, _ = flow
    _put_element(target, "branch_ref", "#branch")
    res = _call("rpa.saveElement", path=path, name="branch_ref", new_name="搜索结果")
    assert res["ok"] is True and res["name"] == "搜索结果", res
    assert res["renamedRefs"] == ["inCatch", "inElse", "inThen"], res["renamedRefs"]
    ed = target.parent / "elements"
    assert (ed / "搜索结果.json").is_file() and not (ed / "branch_ref.json").exists()
    doc = json.loads(target.read_text(encoding="utf-8"))
    assert "搜索结果" in json.dumps(doc, ensure_ascii=False), "流程引用没跟着改名"
    assert "branch_ref" not in json.dumps(doc, ensure_ascii=False), "还有引用指着旧名"
    assert target.read_bytes().count(b"\r\n") == 0, "流程文件被写成 CRLF"


def test_rename_rejects_duplicate_and_unknown(flow: tuple[str, Path, Any]) -> None:
    path, target, _ = flow
    _put_element(target, "a", "#a")
    _put_element(target, "b", "#b")
    assert _call("rpa.saveElement", path=path, name="a", new_name="b")["code"] == "NAME_TAKEN"
    assert _call("rpa.saveElement", path=path, name="ghost", new_name="c")["code"] == "NOT_FOUND"
    same = _call("rpa.saveElement", path=path, name="a", new_name="a")
    assert same["ok"] is True and same["unchanged"] is True


def test_delete_refuses_referenced_asset_without_unlink(flow: tuple[str, Path, Any]) -> None:
    path, target, _ = flow
    _put_element(target, "branch_ref", "#branch")
    denied = _call("rpa.deleteElement", path=path, name="branch_ref")
    assert denied["ok"] is False and denied["code"] == "ELEMENT_IN_USE"
    assert denied["referencedBy"] == ["inCatch", "inElse", "inThen"]
    assert (target.parent / "elements" / "branch_ref.json").is_file()


def test_delete_removes_unreferenced_asset(flow: tuple[str, Path, Any]) -> None:
    path, target, _ = flow
    _put_element(target, "preset_one", "#preset")
    removed = _call("rpa.deleteElement", path=path, name="preset_one")
    assert removed["deleted"] is True and "preset_one" not in removed["remaining"]
    assert not (target.parent / "elements" / "preset_one.json").exists()
    assert "preset_one" not in [
        e["name"] for e in _call("rpa.listElements", path=path)["elements"]
    ]


@pytest.mark.parametrize("name", ["../evil", "a/b", "", "  "])
def test_delete_rejects_bad_names(flow: tuple[str, Path, Any], name: str) -> None:
    path, _, _ = flow
    res = _call("rpa.deleteElement", path=path, name=name)
    assert res["ok"] is False and res["code"] == "BAD_NAME", (name, res)


def test_capture_is_held_until_the_user_confirms(flow: tuple[str, Path, Any]) -> None:
    """捕获完盘上不能有文件：名字要等用户在编辑框里确认才落盘。"""
    path, target, api = flow
    key = (path, "c1")
    entry = {
        "path": path, "name": None, "suggestName": "元素1",
        "nodeId": "inRoot", "overwrite": False,
    }
    api._CAPTURES[key] = entry
    try:
        doc = api._hold_captured(
            entry, {"kind": "browser", "selector": {"css": "#new"}, "verifyCount": 2}
        )
        assert doc["selector"]["css"] == "#new"
        assert entry["preview"]["name"] == "元素1"
        assert not list((target.parent / "elements").glob("*.json")), "还没确认就落盘了"

        saved = api.commit_element(path, "c1", "搜索框")
        assert saved["ok"] is True and saved["name"] == "搜索框", saved
        assert (target.parent / "elements" / "搜索框.json").is_file()
        assert api._CAPTURES.get(key) is None, "入库后没释放捕获会话"
    finally:
        api._CAPTURES.pop(key, None)


def test_commit_refuses_duplicate_name(flow: tuple[str, Path, Any]) -> None:
    path, target, api = flow
    _put_element(target, "占用", "#taken")
    key = (path, "c2")
    entry = {"path": path, "name": None, "suggestName": "元素1", "nodeId": None, "overwrite": False}
    api._CAPTURES[key] = entry
    try:
        api._hold_captured(entry, {"kind": "browser", "selector": {"css": "#x"}})
        res = api.commit_element(path, "c2", "占用")
        assert res["ok"] is False and res["code"] == "NAME_TAKEN", res
    finally:
        api._CAPTURES.pop(key, None)


def test_frontend_wires_single_button_and_dialog() -> None:
    main = MAIN_JS.read_text(encoding="utf-8")
    html = INDEX_HTML.read_text(encoding="utf-8")
    css = STYLE_CSS.read_text(encoding="utf-8")
    for needle in (
        "bindElementTools()",
        "ppx.call('rpa.startCapture'",
        "ppx.call('rpa.commitElement'",
        "ppx.call('rpa.discardCapture'",
        "ppx.call('rpa.elementDetail'",
        "ppx.call('rpa.saveElement'",
        "ppx.call('rpa.deleteElement'",
        "ppx.call('rpa.editElement'",
    ):
        assert needle in main, f"main.js 缺 {needle}"
    for node_id in (
        "elemCapture", "elemDialog", "elemDialogName",
        "elemDialogMeta", "elemDialogNote", "elemDialogSave",
    ):
        assert f'id="{node_id}"' in html, f"index.html 缺 {node_id}"
    for gone in ("elemNewName", "elemNewWhere", "elemNewSelector", "elemNewWrite"):
        assert f'id="{gone}"' not in html, f"index.html 还留着旧控件 {gone}"
    assert 'id="elemNewNote"' not in html, "元素面板还留着提示位（提示应只在桌面浮窗）"
    for selector in (".elemTools {", ".elemCapture {", ".elemAct.danger {", ".modal {"):
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
    for token in (
        "rpa.commitElement", "rpa.saveElement", "rpa.editElement", ".elemTools", "elemCapture"
    ):
        assert token in joined, f"dist 产物里没有 {token}（改了源码忘了 npm run build）"
