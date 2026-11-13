"""工作流源码层契约：七步采集链路的形状、顺序与「不装样子」。

`rpa-core validate` 证明「能被 catalog + compiler 接受」，证明不了流程真的按
用户要的七步跑、条件分支接的是探测结果、逐帖采集的数据真汇总回工作台。
这里补源码层判据；每条都要能被负向注入打红（见
``.harness/spike/negative_xhs_workflow.py``）。

★ walk() 必须下钻 then / else / catch：条件分支里的 action 也是真实执行的步骤，
漏下钻 = 整个分支不在判据里 = 典型假绿灯。
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
BRANCHES = ("children", "then", "else", "catch")

# 结构锚点：33 个 action，删掉任何一步都必须在这里变红。
EXPECTED_ACTIONS = frozenset(
    {
        "openHome", "waitHome", "typeKeyword", "probeSearch", "fallbackNav",
        "waitResults", "waitItems", "probeAiPanel", "closeAiPanel",
        "clickFilter", "sleepFilter", "clickLatest", "sleepLatest",
        "clickOneDay", "sleepReload", "waitReload", "shotResults",
        "listPosts", "initCollect",
        "openPost", "waitPost", "grabPost",
        "scrollComments1", "sleepComments1",
        "scrollComments2", "sleepComments2",
        "scrollComments3", "sleepComments3",
        "grabComments", "appendPost",
        "grabItems", "saveRaw", "close",
    }
)


def document() -> dict[str, Any]:
    return json.loads(WF.read_text(encoding="utf-8"))


def walk(node: dict[str, Any]):
    yield node
    for branch in BRANCHES:
        for child in node.get(branch, []):
            yield from walk(child)


def nodes() -> list[dict[str, Any]]:
    return list(walk(document()["root"]))


def actions() -> list[dict[str, Any]]:
    return [n for n in nodes() if n.get("type") == "action"]


def by_id() -> dict[str, dict[str, Any]]:
    return {n["id"]: n for n in actions()}


def order() -> list[str]:
    return [n["id"] for n in actions()]


def iter_strings(value: Any):
    """递归吐出所有字符串（args 是数组、condition 在 dict 里，只扫 with 会漏）。"""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from iter_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from iter_strings(item)


def own_strings(node: dict[str, Any]):
    """只扫节点自身字段，不下钻分支体——否则 root 会把整棵树的引用都算在它头上。"""
    for key, value in node.items():
        if key in BRANCHES:
            continue
        yield from iter_strings(value)


def _script_is_function_body(script: str) -> bool:
    """扩展侧 pageEval 把 script 塞进 `new Function` 的函数体，必须自带 return。

    写成 `(() => {...})()` 这种表达式语句时，函数体没有 return，注入结果恒为
    undefined → `outputs.result` 为 null → 下一步 data.writeText 报 INVALID_INPUT。
    真机 E2E 实跑踩到过，这里把形状钉死。
    """
    return "return " in script and not script.lstrip().startswith("(() =>")


def _controls() -> dict[str, dict[str, Any]]:
    return {n["id"]: n for n in nodes() if n["type"] != "action"}


def _scripts() -> dict[str, str]:
    return {
        n["id"]: n["with"]["script"]
        for n in actions()
        if n["command"] == "browser.executeScript"
    }


# --- 基础形状 -----------------------------------------------------------------
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
    """含 if / forEach 分支内的引用：分支体也是真实执行的步骤，不能漏检。"""
    seen: set[str] = set()
    for node in nodes():
        for value in own_strings(node):
            for step_id, _key in STEP_RE.findall(value):
                assert step_id in seen, f"{node['id']} 引用了尚未执行的 {step_id}"
        if node["type"] == "action":
            seen.add(node["id"])
    assert seen == EXPECTED_ACTIONS


def test_all_execute_scripts_are_function_bodies() -> None:
    """18 个脚本逐个验形状：任何一个写成 IIFE 都会静默返回 null。"""
    scripts = _scripts()
    assert len(scripts) >= 15
    for node_id, script in scripts.items():
        assert _script_is_function_body(script), f"{node_id} 的 script 不是函数体"


def test_iife_script_shape_is_rejected() -> None:
    """负向对照：旧写法必须被判据挡下，不是恒绿的形状检查。"""
    legacy = "(() => { const nodes = []; return JSON.stringify(nodes); })()"
    assert not _script_is_function_body(legacy)


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


def test_single_tab_session_is_used_and_closed() -> None:
    """★ 全程只能有一个 tab：browser.navigate 顶层 schema 不收 sessionId，

    在流程中途再调一次 navigate(goto) 会**新开标签页**，之后所有 sessionId
    就指错 tab（数据抓的是旧页、关的也是旧页）。所以兜底导航与逐帖打开
    必须走 executeScript 的 location 导航，不能回到 navigate。
    """
    acts = actions()
    sessions = {
        node["with"]["sessionId"]
        for node in acts
        if "sessionId" in (node.get("with") or {})
    }
    assert sessions == {"${steps.openHome.outputs.sessionId}"}
    assert by_id()["close"]["with"]["sessionId"] == "${steps.openHome.outputs.sessionId}"
    navigates = [n["id"] for n in acts if n["command"] == "browser.navigate"]
    assert navigates == ["openHome"], f"中途新开标签页：{navigates}"
    for node_id in ("fallbackNav", "openPost"):
        assert by_id()[node_id]["command"] == "browser.executeScript"


# --- 用户要的七步，逐步钉死 ----------------------------------------------------
def test_seven_step_pipeline_order() -> None:
    ids = order()
    assert ids[0] == "openHome"
    milestones = [
        "typeKeyword",     # 2 输入关键词并回车
        "waitItems",       # 3 等搜索结果
        "probeAiPanel",    # 4 条件性关 AI 框
        "clickFilter",     # 5 点筛选
        "clickLatest",
        "clickOneDay",
        "waitReload",      # 6 等重新载入
        "listPosts",       # 7 逐帖
        "eachPost",        # forEach 本身不是 action，见下面单独判据
        "grabItems",
        "saveRaw",
    ]
    # eachPost 是控制流节点，其余都是 action；单独把容器按 DFS 位置插进来
    seq = [n["id"] for n in nodes()]
    positions = [seq.index(m) for m in milestones]
    assert positions == sorted(positions), (
        f"步骤顺序乱了：{list(zip(milestones, positions, strict=True))}"
    )
    assert ids[-1] == "close"


def test_enter_search_has_fallback_when_url_does_not_change() -> None:
    """真机踩到过「合成 Enter 不触发小红书搜索，URL 停在 /explore」。

    用户要的是按回车，所以主路径必须 pressEnter=True；同时留一条**条件兜底**：
    只有探测到 URL 没进搜索结果页才同 tab 导航，关键词仍来自 inputs。
    """
    acts = by_id()
    assert acts["typeKeyword"]["with"]["pressEnter"] is True
    assert acts["typeKeyword"]["with"]["text"] == "${inputs.keyword}"
    probe = _scripts()["probeSearch"]
    assert "search_result" in probe and "location.href" in probe
    node = _controls()["needFallback"]
    assert node["type"] == "if" and node["condition"]["op"] == "truthy"
    assert node["condition"]["left"] == "${steps.probeSearch.outputs.result}"
    assert [c["id"] for c in node["then"]] == ["fallbackNav"]
    assert node["else"] == []
    fallback = acts["fallbackNav"]
    assert fallback["command"] == "browser.executeScript"
    assert fallback["with"]["args"] == ["${inputs.keyword}"]
    script = fallback["with"]["script"]
    assert "search_result" in script and "encodeURIComponent" in script
    assert "location.href" in script, "兜底必须同 tab 导航"


def test_ai_panel_close_is_conditional() -> None:
    """「如果页面出现了…则点击关闭」：探测 → 条件 → 只点关闭，不恒真不硬点。"""
    probe = _scripts()["probeAiPanel"]
    assert "点点" in probe and "close" in probe
    node = _controls()["hasAiPanel"]
    assert node["type"] == "if" and node["condition"]["op"] == "truthy"
    assert node["condition"]["left"] == "${steps.probeAiPanel.outputs.result}"
    assert [c["id"] for c in node["then"]] == ["closeAiPanel"]
    assert node["else"] == []
    close = _scripts()["closeAiPanel"]
    assert "点点" in close and ".click()" in close


def test_filter_clicks_latest_then_one_day() -> None:
    acts = by_id()
    expected = [("clickFilter", "筛选"), ("clickLatest", "最新"), ("clickOneDay", "一天内")]
    for node_id, label in expected:
        node = acts[node_id]
        assert node["command"] == "browser.executeScript"
        assert node["with"]["args"] == [label], f"{node_id} 点错了：{node['with']['args']}"
    ids = order()
    assert ids.index("clickFilter") < ids.index("clickLatest") < ids.index("clickOneDay")
    # 每个弹层动作后要留加载时间：同步脚本点完立刻点下一个会打在未渲染的 DOM 上
    assert ids.index("clickFilter") < ids.index("sleepFilter") < ids.index("clickLatest")
    assert ids.index("clickLatest") < ids.index("sleepLatest") < ids.index("clickOneDay")
    assert ids.index("clickOneDay") < ids.index("sleepReload") < ids.index("waitReload")


def test_items_are_waited_for_before_scraping() -> None:
    """真机踩到：`browser.waitLoad` 只认 tab.status=complete，SPA 结果列表还没渲染

    就取数 ⇒ 每次「采集完成」但 items 恒为 0（三份历史 run 里两份是 0 条）。
    这里钉死「取数前必须显式等结果卡片出现」，否则同一 bug 会静默复活。
    """
    ids = order()
    acts = by_id()
    wait = acts["waitItems"]
    assert wait["command"] == "browser.waitFor"
    assert wait["with"]["state"] == "attached"
    assert "note-item" in wait["with"]["selector"]
    assert ids.index("waitItems") < ids.index("listPosts")
    assert ids.index("waitResults") < ids.index("waitItems")
    reload_node = acts["waitReload"]
    assert reload_node["command"] == "browser.waitFor"
    assert "note-item" in reload_node["with"]["selector"]
    assert ids.index("waitReload") < ids.index("shotResults")
    # 截图落在取数之前：既是「当时页面长什么样」的证据，也不受取数结果影响
    assert ids.index("shotResults") < ids.index("grabItems")
    assert acts["shotResults"]["command"] == "browser.screenshot"
    # 必须是**整串**引用：`${}` 只认整串，`${inputs.workspace}/results.png` 会原样
    # 落成一个叫 `${inputs.workspace}` 的目录（真机踩到过）
    assert acts["shotResults"]["with"]["savePath"] == "${inputs.shotPath}"


def test_each_post_opens_captures_and_scrolls_comments() -> None:
    """逐帖：打开 → 抓详情 → 滚动评论区三次（每次等加载）→ 逐条抓 → 汇总。"""
    loop = _controls()["eachPost"]
    assert loop["type"] == "forEach"
    assert loop["items"] == "${steps.listPosts.outputs.result}"
    assert loop["item_var"] == "post"
    child_ids = [c["id"] for c in loop["children"] if c["type"] == "action"]
    assert "openPost" in child_ids and "grabPost" in child_ids
    assert "grabComments" in child_ids and "appendPost" in child_ids
    scripts = _scripts()
    assert "${loop.post.href}" in by_id()["openPost"]["with"]["args"]
    assert ".click()" in scripts["openPost"] and "location.href" in scripts["openPost"]
    for key in ("likes", "collects", "comments", "commentList"):
        assert key in scripts["grabPost"], f"帖子详情漏抓 {key}"
    # 评论区要滚 + 等 + 再滚，只有一次滚动抓不到懒加载的评论
    scrolls = [i for i in child_ids if i.startswith("scrollComments")]
    sleeps = [i for i in child_ids if i.startswith("sleepComments")]
    assert len(scrolls) == 3 and len(sleeps) == 3
    for i in range(3):
        assert child_ids.index(f"scrollComments{i + 1}") < child_ids.index(
            f"sleepComments{i + 1}"
        )
    assert "comment-item" in scripts["grabComments"]
    append = by_id()["appendPost"]["with"]["args"]
    assert append == [
        "${steps.grabPost.outputs.result}",
        "${steps.grabComments.outputs.result}",
        "${loop.post.href}",
    ]
    assert "sessionStorage.setItem" in scripts["appendPost"]


def test_extracted_result_is_actually_persisted() -> None:
    """工作台硬编码读 grabItems.outputs.result，它必须是**整轮逐帖汇总**。"""
    acts = by_id()
    assert acts["grabItems"]["command"] == "browser.executeScript"
    scripts = _scripts()
    assert "sessionStorage" in scripts["grabItems"]
    assert "JSON.stringify" in scripts["grabItems"]
    assert _script_is_function_body(scripts["grabItems"])
    assert order().index("appendPost") < order().index("grabItems")
    assert acts["saveRaw"]["command"] == "data.writeText"
    assert acts["saveRaw"]["with"]["text"] == "${steps.grabItems.outputs.result}"
