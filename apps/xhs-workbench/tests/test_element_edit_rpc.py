"""元素编辑服务化的后端 RPC 契约：语义模型、校验、保存、候选。

前端只认这些 RPC，不感知 UIA/CDP/对话框；判据同时钉住「权威来自内核」与
「非法编辑不落盘、不吞会话」两件事。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src import ppx_bridge as pb


def _call(method: str, **params: Any) -> dict[str, Any]:
    env = pb.dispatch(method, params)
    assert env["ok"] is True, env
    return env["data"]


@pytest.fixture
def ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, str, Path]:
    (tmp_path / "workflows").mkdir()
    monkeypatch.setattr(pb, "XHS_WORKFLOWS", tmp_path / "workflows")
    api = pb._load_api()
    monkeypatch.setattr(api, "WORKSPACE", tmp_path)
    created = pb._create_flow("元素编辑 RPC 夹具")
    api._flows.pop(created["path"], None)
    path = created["path"]
    return api, path, tmp_path / path


def _write_element(target: Path, name: str, kind: str = "browser", css: str = "#q") -> None:
    folder = target.parent / "elements"
    folder.mkdir(parents=True, exist_ok=True)
    doc = {
        "kind": kind,
        "selector": {"css": css} if kind == "browser" else {"locator": {"kind": "uia"}},
        "verifyCount": 1,
        "name": name,
        "metadata": {},
    }
    (folder / f"{name}.json").write_bytes(
        (json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )


def test_compose_rpc_uses_kernel_result(ctx: tuple[Any, str, Path]) -> None:
    _api, _path, _target = ctx
    levels = [
        {"tag": "html", "included": False, "id": {"on": False, "value": ""},
         "classes": [], "nth": {"on": False, "value": ""}},
        {"tag": "div", "included": True, "id": {"on": True, "value": "root"},
         "classes": [], "nth": {"on": False, "value": ""}},
    ]
    res = _call("rpa.elementCompose", levels=levels)
    assert res["selector"] == "#root"
    levels[0]["included"] = True
    assert _call("rpa.elementCompose", levels=levels)["selector"] == "html > #root"


def test_validate_rpc_rejects_bad_name_bad_selector_and_duplicate(
    ctx: tuple[Any, str, Path],
) -> None:
    api, path, target = ctx
    _write_element(target, "已存在")
    api._CAPTURES.clear()
    doc = {"kind": "browser", "selector": {"css": "#q"}}
    api._CAPTURES[api._capture_key(path, "c1")] = {
        "id": "c1", "path": path, "name": None, "overwrite": False,
        "captured": doc, "preview": {}, "controller": None,
    }
    bad_name = pb.dispatch("rpa.elementValidate", {
        "path": path, "name": "a/b", "selector": "#q", "capture_id": "c1",
    })
    assert bad_name["ok"] is True and bad_name["data"]["ok"] is False
    assert bad_name["data"]["code"] == "BAD_NAME"

    bad_selector = pb.dispatch("rpa.elementValidate", {
        "path": path, "name": "新元素", "selector": "", "capture_id": "c1",
    })
    assert bad_selector["data"]["code"] == "BAD_SELECTOR"

    duplicate = pb.dispatch("rpa.elementValidate", {
        "path": path, "name": "已存在", "selector": "#q", "capture_id": "c1",
    })
    assert duplicate["data"]["code"] == "NAME_TAKEN"

    good = _call("rpa.elementValidate", path=path, name="新元素", selector="#new",
                 capture_id="c1")
    assert good["valid"] is True


def test_verify_all_rpc_reports_each_element(ctx: tuple[Any, str, Path]) -> None:
    _api, path, target = ctx
    _write_element(target, "搜索框", css="#q")
    _write_element(target, "登录框", css="#login")
    res = _call("rpa.verifyAllElements", path=path)
    assert [r["name"] for r in res["results"]] == ["搜索框", "登录框"]
    assert all("count" in r and "error" in r for r in res["results"])


def test_candidates_rpc_filters_by_param_key(ctx: tuple[Any, str, Path]) -> None:
    _api, path, target = ctx
    _write_element(target, "网页元素", kind="browser")
    _write_element(target, "桌面元素", kind="desktop")
    all_candidates = _call("rpa.elementCandidates", path=path)
    assert set(all_candidates["capableKeys"]) == {"selector", "locator"}
    assert len(all_candidates["elements"]) == 2

    browser = _call("rpa.elementCandidates", path=path, param_key="selector")
    assert browser["kind"] == "browser"
    assert [e["name"] for e in browser["elements"]] == ["网页元素"]
    desktop = _call("rpa.elementCandidates", path=path, param_key="locator")
    assert [e["name"] for e in desktop["elements"]] == ["桌面元素"]


def test_commit_with_selector_writes_edited_selector(ctx: tuple[Any, str, Path]) -> None:
    api, path, target = ctx
    _write_element(target, "原元素")
    api._CAPTURES.clear()
    api._CAPTURES[api._capture_key(path, "c1")] = {
        "id": "c1", "path": path, "name": "原元素", "overwrite": True,
        "captured": {"kind": "browser", "selector": {"css": "#old"}},
        "preview": {}, "controller": None,
    }
    res = _call("rpa.commitElement", path=path, capture_id="c1", name="原元素",
                selector="#edited")
    assert res["ok"] is True
    saved = json.loads((target.parent / "elements" / "原元素.json").read_text(encoding="utf-8"))
    assert saved["selector"]["css"] == "#edited"


def test_element_edit_model_desktop_carries_nodes(ctx: tuple[Any, str, Path]) -> None:
    api, path, _target = ctx
    api._CAPTURES.clear()
    api._CAPTURES[api._capture_key(path, "d1")] = {
        "id": "d1", "path": path, "name": None, "overwrite": False,
        "captured": {
            "kind": "desktop",
            "selector": {"locator": {
                "backend": "uia", "controlType": "Button", "name": "发送",
                "path": [{"controlType": "Pane", "name": "聊天区"}],
            }},
            "metadata": {"windowTitle": "微信", "className": "Qt51514QWindowIcon"},
        },
        "preview": {"name": "发送按钮", "kind": "desktop", "matches": 1},
        "controller": None,
    }
    res = _call("rpa.elementEditModel", path=path, capture_id="d1")
    assert res["kind"] == "desktop"
    assert [n["type"] for n in res["nodes"]] == ["wnd", "Pane", "Button"]
    assert res["nodes"][0]["readonly"] is True
    assert json.loads(res["selector"])["locator"]["name"] == "发送"


def test_edit_element_desktop_uses_node_dialog_and_saves_full_selector(
    ctx: tuple[Any, str, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """点「元素详情」也必须开桌面节点/属性框，保存不能抹掉捕获候选。"""
    api, path, target = ctx
    folder = target.parent / "elements"
    folder.mkdir(parents=True, exist_ok=True)
    doc = {
        "kind": "desktop",
        "name": "微信按钮",
        "verifyCount": 1,
        "metadata": {"windowTitle": "微信"},
        "selector": {
            "locator": {
                "backend": "uia", "controlType": "Button", "name": "发送",
                "path": [{"controlType": "Pane", "name": "聊天区"}],
            },
            "candidates": [{"kind": "uia", "matchedCount": 2}],
        },
    }
    (folder / "微信按钮.json").write_bytes(
        (json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )

    class FakeDialog:
        available = True

        def show(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    dialog = FakeDialog()
    monkeypatch.setattr(api, "_new_edit_dialog", lambda: dialog)
    res = api.edit_element(path, "微信按钮")
    assert res["ok"] is True and res["state"] == "editing"

    nodes = dialog.kwargs.get("nodes")
    assert nodes and [n["type"] for n in nodes] == ["wnd", "Pane", "Button"]
    assert dialog.kwargs.get("descriptor") == doc

    from rpa_core.capture_kit import desktop_editing

    for row in nodes[-1]["attributes"]:
        if row["field"] == "name":
            row["value"] = "发送2"
    composed = desktop_editing.compose_selector(doc, nodes)
    error = dialog.kwargs["on_save"](
        "微信按钮", json.dumps(composed, ensure_ascii=False, indent=2)
    )
    assert error is None
    saved = json.loads((folder / "微信按钮.json").read_text(encoding="utf-8"))
    assert saved["selector"]["locator"]["name"] == "发送2"
    assert saved["selector"]["candidates"] == doc["selector"]["candidates"]


def test_compose_desktop_rpc_keeps_candidates_and_honours_edits(
    ctx: tuple[Any, str, Path],
) -> None:
    api, path, _target = ctx
    api._CAPTURES.clear()
    api._CAPTURES[api._capture_key(path, "d2")] = {
        "id": "d2", "path": path, "name": None, "overwrite": False,
        "captured": {
            "kind": "desktop",
            "selector": {
                "locator": {"backend": "uia", "controlType": "Button", "name": "发送"},
                "candidates": [{"kind": "uia", "matchedCount": 3, "penalty": 10}],
            },
            "metadata": {},
        },
        "preview": {}, "controller": None,
    }
    res = _call("rpa.elementCompose", nodes=[{
        "type": "Button", "name": "发送", "selected": True,
        "attributes": [
            {"field": "controlType", "value": "Button", "operator": "精确", "required": True},
            {"field": "name", "value": "发送", "operator": "包含", "required": True},
        ],
    }], path=path, capture_id="d2")
    selector = json.loads(res["selector"])
    assert selector["locator"]["matchMode"] == "contains"
    assert selector["candidates"][0]["matchedCount"] == 3


def test_commit_desktop_edited_selector_writes_back(ctx: tuple[Any, str, Path]) -> None:
    api, path, target = ctx
    api._CAPTURES.clear()
    api._CAPTURES[api._capture_key(path, "d3")] = {
        "id": "d3", "path": path, "name": None, "overwrite": False,
        "captured": {
            "kind": "desktop", "selector": {"locator": {"backend": "uia", "name": "旧"}},
            "metadata": {},
        },
        "preview": {}, "controller": None,
    }
    edited = json.dumps({"locator": {"backend": "uia", "name": "新"}}, ensure_ascii=False)
    res = _call("rpa.commitElement", path=path, capture_id="d3", name="桌面按钮",
                selector=edited)
    assert res["ok"] is True
    saved = json.loads((target.parent / "elements" / "桌面按钮.json").read_text(encoding="utf-8"))
    assert saved["selector"]["locator"]["name"] == "新"


def test_bad_selector_stays_in_dialog_and_keeps_capture(ctx: tuple[Any, str, Path]) -> None:
    api, path, _target = ctx
    api._CAPTURES.clear()
    key = api._capture_key(path, "c2")
    api._CAPTURES[key] = {
        "id": "c2", "path": path, "name": None, "overwrite": False,
        "captured": {"kind": "browser", "selector": {"css": "#old"}},
        "preview": {}, "controller": None,
    }
    res = pb.dispatch("rpa.commitElement", {
        "path": path, "capture_id": "c2", "name": "新元素", "selector": "#a\n#b",
    })
    assert res["data"]["code"] == "BAD_SELECTOR"
    assert key in api._CAPTURES, "非法定位把捕获会话吞了，用户没法在框里改"
