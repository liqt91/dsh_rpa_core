"""M52 S2 浏览器候选择优判据：唯一性硬门 + penalty 最小化 + 稳定序。

与 S1 桌面版（``test_desktop_locator_ranking.py``）**同源**：两端共用
``model.selector_ranking.rank_candidates`` 这**一个**排序引擎，只是 penalty 分派不同
（桌面 ``locator_penalty`` / 浏览器 ``web_candidate_penalty``）。

本文件除引擎语义外，另钉三组：
① 同源（两端确实是同一个函数对象，且偏好方向一致）；
② 浏览器特有先验（kind 表、attribute 细分、哈希 id 降权、未实测排后）；
③ 接线（``_element_candidates`` 返回排序后的候选 → 运行期自愈按此序 try）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from rpa_core.executors.browser import PlaywrightExecutor
from rpa_core.model.selector_ranking import (
    WEB_HASHY_ID_PENALTY,
    rank_candidates,
    rank_web_candidates,
    web_candidate_penalty,
)


def _candidate(kind: str, selector: str, matched: int | None = None) -> dict:
    item: dict = {"kind": kind, "selector": selector}
    if matched is not None:
        item["matchedCount"] = matched
    return item


def _selectors(ranked: list[dict]) -> list[str]:
    return [item["selector"] for item in ranked]


def test_unique_candidate_wins_even_with_higher_penalty():
    """唯一性是硬门：更脆但唯一的那条，必须排到更稳但不唯一的前面。"""
    stable_but_ambiguous = _candidate("name", "input[name=q]", 3)
    fragile_but_unique = _candidate("role", "div[role=button]", 1)
    assert web_candidate_penalty(stable_but_ambiguous) < web_candidate_penalty(fragile_but_unique)
    assert rank_web_candidates([stable_but_ambiguous, fragile_but_unique])[0] is fragile_but_unique


def test_all_non_unique_falls_back_to_smallest_count():
    wide = _candidate("name", "input[name=q]", 5)
    narrow = _candidate("placeholder", "input[placeholder=x]", 2)
    assert _selectors(rank_web_candidates([wide, narrow])) == [
        "input[placeholder=x]",
        "input[name=q]",
    ]


def test_tie_on_count_prefers_lower_penalty():
    fragile = _candidate("role", "div[role=button]", 4)
    stable = _candidate("name", "input[name=q]", 4)
    assert _selectors(rank_web_candidates([fragile, stable])) == [
        "input[name=q]",
        "div[role=button]",
    ]


def test_stable_order_on_full_tie():
    """完全并列时保留输入序（不得依赖集合遍历顺序，防 flaky）。"""
    first = _candidate("name", "input[name=a]", 1)
    second = _candidate("name", "input[name=b]", 1)
    assert _selectors(rank_web_candidates([first, second])) == ["input[name=a]", "input[name=b]"]
    assert _selectors(rank_web_candidates([second, first])) == ["input[name=b]", "input[name=a]"]


def test_unmeasured_count_sorts_after_measured():
    """matchedCount 缺席 = 未实测 → 排到所有实测候选之后（信息不足者不抢先试）。"""
    measured = _candidate("role", "div[role=button]", 2)
    unmeasured = _candidate("name", "input[name=q]")
    assert _selectors(rank_web_candidates([unmeasured, measured])) == [
        "div[role=button]",
        "input[name=q]",
    ]


def test_generated_id_candidate_is_penalized():
    """kind=="id" 但值像框架生成哈希 → 降权（比语义属性还脆）；固定 id 则最稳。"""
    fixed = _candidate("id", "#submitButton")
    generated = _candidate("id", "#css-1x2y3z")
    named = _candidate("name", "input[name=q]")
    assert web_candidate_penalty(fixed) < web_candidate_penalty(named)
    assert web_candidate_penalty(named) < web_candidate_penalty(generated)
    assert web_candidate_penalty(generated) == WEB_HASHY_ID_PENALTY
    assert _selectors(rank_web_candidates([generated, named])) == ["input[name=q]", "#css-1x2y3z"]


def test_attribute_kind_looks_up_attribute_penalty():
    """kind=="attribute" 时按 selector 里的属性名细分（content.js 收的 7 属性）。"""
    testid = _candidate("attribute", 'div[data-testid="x"]')
    placeholder = _candidate("attribute", "input[placeholder='q']")
    assert web_candidate_penalty(testid) < web_candidate_penalty(placeholder)


def test_path_kind_is_registered_below_unknown():
    """M51-B 祖先链收缩候选（kind="path"）必须**登记在表里**，不能落兜底值。

    落兜底（``WEB_UNKNOWN_KIND_PENALTY``）不是崩，是**静默排到最后**——排序照常返回、
    界面上照常有候选，只是新来源永远轮不上。这种失配没有任何症状，所以要用判据钉住。
    """
    from rpa_core.model.selector_ranking import WEB_UNKNOWN_KIND_PENALTY

    path_kind = web_candidate_penalty(_candidate("path", "a.cover"))
    unknown = web_candidate_penalty(_candidate("weird", "div.foo"))
    assert path_kind < unknown
    assert path_kind < WEB_UNKNOWN_KIND_PENALTY


def test_capture_script_kinds_are_all_registered_in_table():
    """成对判据：``content.js`` 实际会产出的 kind **全部**须在宿主 penalty 表里登记。

    这是「新增候选来源忘了同步宿主表」的拦网（M52 S3 的教训是同类问题换了个面：
    数据与展示两侧都"各自绿"，缺的是**跨端一致**的那条判据）。
    """
    source = Path(__file__).resolve().parents[2] / "extension" / "content.js"
    if not source.is_file():
        pytest.skip("源码检出形态才可定位 extension/content.js")
    kinds = set(re.findall(r'push\(\s*"([a-z-]+)"', source.read_text(encoding="utf-8")))
    assert kinds, 'content.js 里应能找到 push("<kind>", …) 形式的候选产出'
    from rpa_core.model.selector_ranking import WEB_KIND_PENALTIES

    missing = kinds - set(WEB_KIND_PENALTIES)
    assert not missing, f"content.js 产出的 kind 未在 WEB_KIND_PENALTIES 登记：{sorted(missing)}"


def test_unknown_kind_sorts_after_known_semantics():
    """表里没有的 kind 走兜底值，排在已知语义 kind 之后。"""
    known = _candidate("name", "input[name=q]")
    unknown = _candidate("weird", "div.foo")
    assert web_candidate_penalty(known) < web_candidate_penalty(unknown)


def test_kind_penalty_table_is_read(monkeypatch):
    """表被读到才生效——改表即改结果（证明没把数值硬编码在别处）。"""
    from rpa_core.model import selector_ranking

    candidate = _candidate("name", "input[name=q]")
    base = web_candidate_penalty(candidate)
    monkeypatch.setitem(selector_ranking.WEB_KIND_PENALTIES, "name", base + 7)
    assert web_candidate_penalty(candidate) == base + 7


def test_attribute_penalty_table_is_read(monkeypatch):
    from rpa_core.model import selector_ranking

    candidate = _candidate("attribute", 'div[data-testid="x"]')
    base = web_candidate_penalty(candidate)
    monkeypatch.setitem(selector_ranking.WEB_ATTRIBUTE_PENALTIES, "data-testid", base + 9)
    assert web_candidate_penalty(candidate) == base + 9


def test_ranking_engine_is_shared_across_backends():
    """同源（最强形式）：两端用的是**同一个**排序引擎对象，不是各自实现的两份。"""
    from rpa_core.model import desktop, selector_ranking

    assert desktop.rank_candidates is selector_ranking.rank_candidates
    assert desktop.rank_candidates is rank_candidates


def test_both_backends_agree_on_order_for_same_shape():
    """同构输入下两端偏好方向一致：唯一命中都压过「更稳但命中更多」的那条。"""
    from rpa_core.model.desktop import choose_best_locator

    fragile_unique = {"locator": {"backend": "uia", "controlType": "Button"}, "count": 1}
    stable_ambiguous = {"locator": {"backend": "uia", "automationId": "fixed"}, "count": 2}
    assert choose_best_locator([stable_ambiguous, fragile_unique]) is fragile_unique

    web_fragile_unique = _candidate("role", "div[role=button]", 1)
    web_stable_ambiguous = _candidate("name", "input[name=q]", 2)
    assert rank_web_candidates([web_stable_ambiguous, web_fragile_unique])[0] is web_fragile_unique


def _write_element(flow_dir: Path, name: str, css: str, candidates: list[dict]) -> None:
    elements = flow_dir / "elements"
    elements.mkdir(parents=True, exist_ok=True)
    (elements / f"{name}.json").write_text(
        json.dumps(
            {"name": name, "kind": "browser", "selector": {"css": css, "candidates": candidates}}
        ),
        encoding="utf-8",
    )


def test_element_candidates_sorted_by_stability(tmp_path):
    """接线：资产里候选按原序写入，``_element_candidates`` 必须返回**排序后**的顺序。"""
    flow_dir = tmp_path / "flows" / "demo"
    _write_element(
        flow_dir, "e", "#kw",
        [
            _candidate("placeholder", "input[placeholder='q']", 1),
            _candidate("name", "input[name=q]", 1),
        ],
    )
    executor = PlaywrightExecutor(flow_dir=flow_dir)
    got = executor._element_candidates("#kw")
    assert _selectors(got) == ["input[name=q]", "input[placeholder='q']"]


def test_element_candidates_unique_measured_first(tmp_path):
    """接线：唯一命中的候选排最前（哪怕它的 kind 更脆）。"""
    flow_dir = tmp_path / "flows" / "demo"
    _write_element(
        flow_dir, "e", "#kw",
        [
            _candidate("name", "input[name=q]", 3),
            _candidate("role", "div[role=button]", 1),
        ],
    )
    executor = PlaywrightExecutor(flow_dir=flow_dir)
    got = executor._element_candidates("#kw")
    assert _selectors(got) == ["div[role=button]", "input[name=q]"]
