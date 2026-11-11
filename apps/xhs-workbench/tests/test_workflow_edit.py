"""工作流编辑单测：改参数 / 插节点 / 删节点（引用护栏）/ 上下移 / 原子保存。"""

from __future__ import annotations

from pathlib import Path

import pytest

from src import workflow_edit as we


def _doc() -> dict:
    return {
        "root": {
            "type": "sequence",
            "id": "root",
            "children": [
                {"type": "action", "id": "a", "command": "browser.navigate", "with": {"url": "u"}},
                {"type": "action", "id": "b", "command": "browser.getText",
                 "with": {"selector": "s"}, "elementRefs": {"selector": "some_element"},
                 "_exprModes": {"selector": "fx"}},
                {"type": "action", "id": "c", "command": "data.log",
                 "with": {"message": "${steps.b.outputs.result}"}},
            ],
        }
    }


def test_update_params_replaces_with_and_tracks_fx_modes() -> None:
    doc = _doc()
    we.update_params(doc, "b", {"selector": "[inputs.kw]", "timeoutMs": 5000})
    node = we.find_node(doc, "b")[2]
    assert node["with"] == {"selector": "[inputs.kw]", "timeoutMs": 5000}
    assert node["_exprModes"] == {"selector": "fx"}
    assert node["elementRefs"] == {"selector": "some_element"}  # 引用不被参数编辑弄丢


def test_update_params_drops_empty_strings_and_stale_fx() -> None:
    doc = _doc()
    we.update_params(doc, "b", {"selector": ""})
    node = we.find_node(doc, "b")[2]
    assert node["with"] == {}
    assert "_exprModes" not in node  # fx 标记随内容清空


def test_update_params_whole_ref_needs_no_fx() -> None:
    """整串 ${...} 引用是编译器默认认的形状，挂 fx 反而是错信号。"""
    doc = _doc()
    we.update_params(doc, "b", {"selector": "${inputs.selector}"})
    node = we.find_node(doc, "b")[2]
    assert node["with"]["selector"] == "${inputs.selector}"
    assert "_exprModes" not in node


def test_insert_appends_or_after_anchor_with_unique_id() -> None:
    doc = _doc()
    n1 = we.insert_node(doc, "browser.waitFor", None, {"selector": "", "timeoutMs": 15000})
    assert n1["id"] == "waitFor"
    assert we.list_node_ids(doc) == ["a", "b", "c", "waitFor"]
    n2 = we.insert_node(doc, "browser.waitFor", "a", {})
    assert n2["id"] == "waitFor2"
    assert we.list_node_ids(doc) == ["a", "waitFor2", "b", "c", "waitFor"]


def test_delete_refuses_when_referenced_by_later_step() -> None:
    doc = _doc()
    with pytest.raises(ValueError, match="被"):
        we.delete_node(doc, "b")
    we.delete_node(doc, "c")  # c 没人引用，能删
    assert we.list_node_ids(doc) == ["a", "b"]


def test_move_node_up_down_and_boundaries() -> None:
    doc = _doc()
    we.move_node(doc, "b", -1)
    assert we.list_node_ids(doc)[:3] == ["b", "a", "c"]
    with pytest.raises(ValueError):
        we.move_node(doc, "b", -1)  # 已到顶
    we.move_node(doc, "b", 1)
    assert we.list_node_ids(doc) == ["a", "b", "c"]


def test_save_then_load_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "wf.json"
    we.save(path, _doc())
    doc = we.load(path)
    assert we.list_node_ids(doc) == ["a", "b", "c"]
    raw = path.read_bytes()
    assert raw.endswith(b"\n") and b"\r\n" not in raw  # LF，门禁要查


def test_save_is_atomic_no_tmp_left(tmp_path: Path) -> None:
    path = tmp_path / "wf.json"
    we.save(path, _doc())
    assert not list(tmp_path.glob("*.tmp"))
    leftovers = [p for p in tmp_path.iterdir() if p.name != "wf.json"]
    assert leftovers == []


def test_find_node_returns_none_for_unknown() -> None:
    assert we.find_node(_doc(), "nope") is None
