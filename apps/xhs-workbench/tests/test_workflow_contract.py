"""工作流源码层契约。

`rpa-core validate` 证明「能被 catalog + compiler 接受」，但证明不了运行时
取得到元素引用。这里补上源码层判据：inputs 键、步骤引用顺序、元素定位值、
以及提取结果确实被写盘。运行期解析函数直接调能力层，测试依赖不等于应用层依赖。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

WF_DIR = Path(__file__).resolve().parents[1] / "workflows"
WF = WF_DIR / "xhs_product_search.json"
INPUT_RE = re.compile(r"\$\{inputs\.([A-Za-z0-9_]+)\}")
STEP_RE = re.compile(r"\$\{steps\.([A-Za-z0-9_]+)\.outputs\.([A-Za-z0-9_]+)\}")


def document() -> dict[str, Any]:
    return json.loads(WF.read_text(encoding="utf-8"))


def walk(node: dict[str, Any]):
    yield node
    for child in node.get("children", []):
        yield from walk(child)


def actions() -> list[dict[str, Any]]:
    return [n for n in walk(document()["root"]) if n.get("type") == "action"]


def test_inputs_referenced_are_exactly_the_declared_ones() -> None:
    doc = document()
    used: set[str] = set()
    for node in actions():
        for value in (node.get("with") or {}).values():
            if isinstance(value, str):
                used.update(INPUT_RE.findall(value))
    assert used == {"keyword", "workspace"}
    assert used == set(doc["inputs"])


def test_step_refs_only_point_to_earlier_actions() -> None:
    seen: set[str] = set()
    for node in actions():
        for value in (node.get("with") or {}).values():
            if isinstance(value, str):
                for step_id, _key in STEP_RE.findall(value):
                    assert step_id in seen, f"{node['id']} 引用了尚未执行的 {step_id}"
        seen.add(node["id"])
    assert seen == {
        "openHome",
        "waitHome",
        "typeKeyword",
        "waitResults",
        "grabItems",
        "saveRaw",
        "close",
    }


def test_element_ref_resolves_to_a_usable_css_selector() -> None:
    from rpa_core.runtime.element_refs import element_value_for_key, make_element_reader

    names = [
        name
        for node in actions()
        for name in (node.get("elementRefs") or {}).values()
    ]
    assert names == ["xhs_search_box"]
    reader = make_element_reader(WF_DIR)
    for name in names:
        assert reader(name) is not None
        assert element_value_for_key(reader(name), "selector") == "#search-input-in-feeds"


def test_extracted_result_is_actually_persisted() -> None:
    by_id = {node["id"]: node for node in actions()}
    assert by_id["grabItems"]["command"] == "browser.executeScript"
    assert "JSON.stringify" in by_id["grabItems"]["with"]["script"]
    assert by_id["saveRaw"]["command"] == "data.writeText"
    assert by_id["saveRaw"]["with"]["text"] == "${steps.grabItems.outputs.result}"


def test_workflow_uses_a_single_browser_session() -> None:
    sessions = {
        node["with"]["sessionId"]
        for node in actions()
        if "sessionId" in (node.get("with") or {})
    }
    assert sessions == {"${steps.openHome.outputs.sessionId}"}
