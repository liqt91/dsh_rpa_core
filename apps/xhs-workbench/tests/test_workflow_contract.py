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
    assert used == {"keyword", "workspace", "shotPath", "browserType"}
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
        "gotoResults",
        "closeExplore",
        "waitResults",
        "waitItems",
        "shotResults",
        "grabItems",
        "saveRaw",
        "close",
    }


def test_items_are_waited_for_before_scraping() -> None:
    """真机踩到：`browser.waitLoad` 只认 tab.status=complete，SPA 结果列表还没渲染

    就取数 ⇒ 每次「采集完成」但 items 恒为 0（三份历史 run 里两份是 0 条）。
    这里钉死「取数前必须显式等结果卡片出现」，否则同一 bug 会静默复活。
    """
    order = [n["id"] for n in actions()]
    by_id = {n["id"]: n for n in actions()}
    wait = by_id["waitItems"]
    assert wait["command"] == "browser.waitFor"
    assert wait["with"]["state"] == "attached"
    assert "note-item" in wait["with"]["selector"]
    assert order.index("waitItems") < order.index("grabItems")
    assert order.index("waitResults") < order.index("waitItems")
    # 截图落在取数之前：既是「当时页面长什么样」的证据，也不受取数结果影响
    assert order.index("shotResults") < order.index("grabItems")
    assert by_id["shotResults"]["command"] == "browser.screenshot"
    # 必须是**整串**引用：`${}` 只认整串，`${inputs.workspace}/results.png` 会原样
    # 落成一个叫 `${inputs.workspace}` 的目录（真机踩到过）
    assert by_id["shotResults"]["with"]["savePath"] == "${inputs.shotPath}"


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


def _script_is_function_body(script: str) -> bool:
    """扩展侧 pageEval 把 script 塞进 `new Function` 的函数体，必须自带 return。

    写成 `(() => {...})()` 这种表达式语句时，函数体没有 return，注入结果恒为
    undefined → `outputs.result` 为 null → 下一步 data.writeText 报 INVALID_INPUT。
    真机 E2E 实跑踩到过，这里把形状钉死。
    """
    return "return " in script and not script.lstrip().startswith("(() =>")


def test_extracted_result_is_actually_persisted() -> None:
    by_id = {node["id"]: node for node in actions()}
    assert by_id["grabItems"]["command"] == "browser.executeScript"
    script = by_id["grabItems"]["with"]["script"]
    assert "JSON.stringify" in script
    assert _script_is_function_body(script)
    assert by_id["saveRaw"]["command"] == "data.writeText"
    assert by_id["saveRaw"]["with"]["text"] == "${steps.grabItems.outputs.result}"


def test_search_navigation_uses_the_input_keyword() -> None:
    """真机踩到：合成 Enter 不触发小红书搜索，URL 一直停在 /explore。

    流程改为显式导航搜索结果页；这里钉死「关键词来自输入」和「pressEnter 不装样子」，
    避免以后有人把 URL 写死或把假开关打开。
    """
    node = {n["id"]: n for n in actions()}["gotoResults"]
    assert node["command"] == "browser.navigate"
    assert "[inputs.keyword]" in node["with"]["url"]
    assert node["_exprModes"]["url"] == "fx"
    assert "sessionId" not in node["with"]  # navigate 的顶层 schema 不收 sessionId
    assert {n["id"]: n for n in actions()}["typeKeyword"]["with"]["pressEnter"] is False


def test_iife_script_shape_is_rejected() -> None:
    """负向对照：旧写法必须被判据挡下，不是恒绿的形状检查。"""
    legacy = (
        "(() => { const nodes = []; return JSON.stringify(nodes); })()"
    )
    assert not _script_is_function_body(legacy)


def test_sessions_follow_the_two_tabs_we_actually_open() -> None:
    """首页标签只用来验元素引用输入，采集跟搜索结果页那个新标签。

    两个 session 都必须指向真实更早步骤，且旧标签要有显式 close，不能残留。
    """
    by_id = {node["id"]: node for node in actions()}
    sessions = {
        node["with"]["sessionId"]
        for node in actions()
        if "sessionId" in (node.get("with") or {})
    }
    assert sessions == {
        "${steps.openHome.outputs.sessionId}",
        "${steps.gotoResults.outputs.sessionId}",
    }
    assert by_id["closeExplore"]["with"]["sessionId"] == "${steps.openHome.outputs.sessionId}"
    assert by_id["close"]["with"]["sessionId"] == "${steps.gotoResults.outputs.sessionId}"
