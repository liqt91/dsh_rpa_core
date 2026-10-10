"""M52 桌面定位器优选判据：唯一性硬门 + penalty 最小化 + 稳定序。

口径对齐 2026-10-10 开源调研（@medv/finder 的 penalty 表 / Robula+ 的属性白黑名单 /
Playwright codegen 的语义阶梯）：

- **唯一命中是硬门**（不是加权因子）——先筛唯一解集，再在解集内比 penalty；
- 无唯一候选时退化为「命中数最小」；
- 同 count 同 penalty 保**输入序**（不依赖集合遍历顺序，防 flaky）。

penalty 表是**先验**，判据只钉「表被读到、合成生效」，不钉具体数值。
"""

from rpa_core.model.desktop import (
    PATH_STEP_PENALTY,
    choose_best_locator,
    locator_penalty,
    looks_generated_id,
)


def _entry(locator: dict, count: int) -> dict:
    return {"locator": locator, "count": count}


def test_unique_match_wins_even_with_higher_penalty():
    """唯一性是硬门：更脆但唯一的那条，必须胜过更稳但不唯一的。"""
    stable_but_ambiguous = _entry({"backend": "uia", "automationId": "fixedId"}, 3)
    fragile_but_unique = _entry({"backend": "uia", "controlType": "Button"}, 1)
    assert locator_penalty(stable_but_ambiguous["locator"]) < locator_penalty(
        fragile_but_unique["locator"]
    )
    assert choose_best_locator([stable_but_ambiguous, fragile_but_unique]) is fragile_but_unique


def test_all_non_unique_falls_back_to_smallest_count():
    wide = _entry({"backend": "uia", "controlType": "Button"}, 5)
    narrow = _entry({"backend": "uia", "controlType": "Button", "name": "OK"}, 2)
    assert choose_best_locator([wide, narrow]) is narrow


def test_tie_on_count_prefers_lower_penalty():
    fragile = _entry({"backend": "uia", "controlType": "Button"}, 4)
    stable = _entry({"backend": "uia", "automationId": "fixedId"}, 4)
    assert choose_best_locator([fragile, stable]) is stable


def test_stable_order_on_full_tie():
    """完全并列时必须取**先出现**者（min 保序），不得依赖集合遍历顺序。"""
    first = _entry({"backend": "uia", "controlType": "Button"}, 1)
    second = _entry({"backend": "uia", "controlType": "Button"}, 1)
    assert choose_best_locator([first, second]) is first
    assert choose_best_locator([second, first]) is second


def test_empty_or_invalid_entries_return_none():
    assert choose_best_locator([]) is None
    assert choose_best_locator([{"locator": {"backend": "uia"}, "count": None}]) is None
    assert choose_best_locator([{"locator": {}}]) is None
    assert choose_best_locator([{"count": 1}]) is None


def test_path_depth_increases_penalty():
    shallow = {"backend": "uia", "controlType": "Button", "path": [{"name": "a"}]}
    deep = {
        "backend": "uia",
        "controlType": "Button",
        "path": [{"name": "a"}, {"name": "b"}, {"name": "c"}],
    }
    assert locator_penalty(deep) - locator_penalty(shallow) == 2 * PATH_STEP_PENALTY


def test_generated_id_is_penalized_more_than_fixed_id():
    """「比 id 最稳更细」的那一层：框架生成的哈希 id 要降权。"""
    fixed = {"backend": "uia", "automationId": "submitButton"}
    generated = {"backend": "uia", "automationId": "css-1x2y3z"}
    assert locator_penalty(fixed) < locator_penalty(generated)
    assert looks_generated_id("css-1x2y3z") is True
    assert looks_generated_id("ember1234") is True
    assert looks_generated_id("a1b2c3d4e5f60718") is True
    assert looks_generated_id("submitButton") is False


def test_penalty_table_is_read(monkeypatch):
    """表被读到才生效——改表即改结果（证明没有把数值硬编码在别处）。"""
    from rpa_core.model import desktop as desktop_module

    locator = {"backend": "uia", "controlType": "Button"}
    base = locator_penalty(locator)
    monkeypatch.setitem(desktop_module.LOCATOR_FIELD_PENALTIES, "controlType", base + 7)
    assert locator_penalty(locator) == base + 7


def test_pick_best_candidate_selects_unique_over_lower_penalty(monkeypatch):
    """接线：capture_at 的候选择优确实走共享引擎（不是残留的朴素循环）。"""
    from rpa_core.capture import desktop_agent

    def fake_verify(window, criteria, automation_id):
        return 1 if criteria.get("title") == "OK" else 5

    monkeypatch.setattr(desktop_agent, "_verify", fake_verify)
    candidates = [
        ({"control_type": "Button"}, {"backend": "uia", "controlType": "Button"}, None),
        (
            {"control_type": "Button", "title": "OK"},
            {"backend": "uia", "controlType": "Button", "name": "OK"},
            None,
        ),
    ]
    locator, count = desktop_agent._pick_best_candidate(candidates, window=object())
    assert count == 1
    assert locator.get("name") == "OK"


def test_pick_best_candidate_falls_back_to_smallest_count(monkeypatch):
    from rpa_core.capture import desktop_agent

    def fake_verify(window, criteria, automation_id):
        return 7 if criteria.get("title") == "A" else 2

    monkeypatch.setattr(desktop_agent, "_verify", fake_verify)
    candidates = [
        ({"title": "A"}, {"backend": "uia", "controlType": "Button", "name": "A"}, None),
        ({"title": "B"}, {"backend": "uia", "controlType": "Button", "name": "B"}, None),
    ]
    locator, count = desktop_agent._pick_best_candidate(candidates, window=object())
    assert count == 2
    assert locator.get("name") == "B"


def test_pick_best_candidate_without_window_returns_empty():
    from rpa_core.capture import desktop_agent

    assert desktop_agent._pick_best_candidate([], window=None) == ({}, 0)


# ---- M52 S3：捕获期把评估过的候选（含分数）随元素落盘 --------------------------

def test_evaluate_candidates_keeps_all_with_scores(monkeypatch):
    """``_evaluate_candidates`` 保留**全部**评估过的方案（含被选中那条）并打分。"""
    from rpa_core.capture import desktop_agent

    def fake_verify(window, criteria, automation_id):
        return 1 if criteria.get("title") == "OK" else 5

    monkeypatch.setattr(desktop_agent, "_verify", fake_verify)
    candidates = [
        ({"control_type": "Button"}, {"backend": "uia", "controlType": "Button"}, None),
        (
            {"control_type": "Button", "title": "OK"},
            {"backend": "uia", "controlType": "Button", "name": "OK"},
            None,
        ),
    ]
    scored = desktop_agent._evaluate_candidates(candidates, desktop_agent._window_verify(object()))
    assert [item["matchedCount"] for item in scored] == [5, 1]  # 全部保留，不丢
    assert all(item["kind"] == "uia" for item in scored)
    assert all(item["penalty"] == locator_penalty(item["locator"]) for item in scored)


def test_evaluate_candidates_injects_path_into_scores(monkeypatch):
    """祖先链 path 注入每条候选并计入分数——候选之一与主定位逐字节一致。"""
    from rpa_core.capture import desktop_agent

    monkeypatch.setattr(desktop_agent, "_verify", lambda w, c, a: 1)
    candidates = [
        ({"control_type": "Button"}, {"backend": "uia", "controlType": "Button"}, None)
    ]
    steps = [{"name": "Pane"}]
    scored = desktop_agent._evaluate_candidates(
        candidates, desktop_agent._window_verify(object()), path_steps=steps
    )
    expected = {"backend": "uia", "controlType": "Button", "path": steps}
    assert scored[0]["locator"] == expected
    assert scored[0]["penalty"] == locator_penalty(expected)


def test_evaluate_candidates_without_verifier_is_empty():
    """无法实测（无 window / 无根句柄）⇒ 不产假数据，返回空表。"""
    from rpa_core.capture import desktop_agent

    assert desktop_agent._evaluate_candidates([], None) == []
    assert desktop_agent._window_verify(None) is None
    assert desktop_agent._root_verify(0) is None


def test_capture_at_persists_scored_candidates(monkeypatch):
    """接线：``capture_at`` 把评估过的候选（含分数）落进 ``selector['candidates']``。

    desktop 的 selector 是自由字典（``_candidate_errors`` 只校验 browser 候选），
    故这里的 ``{kind, locator, matchedCount, penalty}`` 形状不会被拦。
    """
    import sys
    import types
    from types import SimpleNamespace

    from rpa_core.capture import desktop_agent

    fake_pywinauto = types.ModuleType("pywinauto")
    fake_pywinauto.Desktop = lambda backend=None: SimpleNamespace(
        window=lambda handle=None: object()
    )
    monkeypatch.setitem(sys.modules, "pywinauto", fake_pywinauto)
    monkeypatch.setattr(
        desktop_agent,
        "ctypes",
        SimpleNamespace(
            create_unicode_buffer=lambda size: SimpleNamespace(value=""),
            windll=SimpleNamespace(user32=SimpleNamespace(GetWindowTextW=lambda *a: 0)),
        ),
    )
    monkeypatch.setattr(
        desktop_agent,
        "_element_from_point",
        lambda x, y: SimpleNamespace(
            handle=4321, control_type="Button", automation_id="submit",
            name="提交", class_name="Button",
        ),
    )
    monkeypatch.setattr(desktop_agent, "_root_window_handle", lambda hwnd: 4321)
    monkeypatch.setattr(desktop_agent, "_ancestor_chain", lambda info, root: [])
    monkeypatch.setattr(desktop_agent, "_path_steps_from", lambda chain, root: [])
    monkeypatch.setattr(
        desktop_agent,
        "_verify",
        lambda w, c, a: 1 if c.get("title") == "提交" else 4,
    )

    result = desktop_agent.capture_at(10, 20)
    candidates = result["selector"]["candidates"]
    assert len(candidates) == 3  # 三条定位方案全部落盘（含被选中那条）
    assert [c["matchedCount"] for c in candidates] == [1, 1, 4]
    assert all(c["kind"] == "uia" for c in candidates)
    # 被选中的主定位就是候选之一（内容一致）——debug 时不会「主定位不在候选里」
    assert result["selector"]["locator"] in [c["locator"] for c in candidates]
    assert result["selector"]["locator"]["name"] == "提交"
    # 候选形状出自唯一权威 `_locator_candidates`（不是本函数里的第二套就地构造）
    assert [c["locator"] for c in candidates] == [
        locator for _criteria, locator, _aid in
        desktop_agent._locator_candidates("Button", "submit", "提交")
    ]


# ---- M52 S3 回归：hover 快路径也必须落盘候选（2026-10-10 维护者实测的根因）------

def test_describe_info_persists_scored_candidates(monkeypatch):
    """回归：``_describe_info`` 是 GUI **日常捕获**的出口，候选与分数必须在这里也落盘。

    2026-10-10 维护者「重启了，捕获确认框上还是没看到候选和分数」——根因是 GUI 的
    桌面捕获走 ``HybridCaptureSession(hover=True)``，``_hover_capture`` 复用缓存元素时
    直接 ``return _describe_info(...)``，**压根不经 ``capture_at``**；而 S3 首版只把候选
    加在 ``capture_at`` 里。本判据钉住这条路径不再回退。
    """
    from types import SimpleNamespace

    from rpa_core.capture import desktop_agent

    monkeypatch.setattr(
        desktop_agent,
        "_verify_in_root",
        lambda root, criteria, automation_id: 1 if criteria.get("title") == "提交" else 4,
    )
    info = SimpleNamespace(
        control_type="Button", automation_id="submit", name="提交", class_name="Button",
    )
    result = desktop_agent._describe_info(info, root_hwnd=4321)
    candidates = result["selector"]["candidates"]
    assert len(candidates) == 3
    assert [c["matchedCount"] for c in candidates] == [1, 1, 4]
    assert all(c["kind"] == "uia" for c in candidates)
    assert all(c["penalty"] == locator_penalty(c["locator"]) for c in candidates)
    # 主定位恒是候选之一（candidates[0] 即本函数选定的最具体那层）
    assert result["selector"]["locator"] in [c["locator"] for c in candidates]
    assert result["selector"]["locator"]["name"] == "提交"


def test_candidates_are_shared_by_both_capture_paths(monkeypatch):
    """候选形状只有一份权威：点捕获与 hover 描述产出**逐字节相同**的候选集。

    光有「两条路各自绿」不够——要有**「两端相等」**的判据，否则两套就地构造各自漂移
    也仍然是全绿（这正是本次事故的形状：``capture_at`` 有候选、``_describe_info`` 没有）。
    """
    from types import SimpleNamespace

    from rpa_core.capture import desktop_agent

    monkeypatch.setattr(desktop_agent, "_verify_in_root", lambda r, c, a: 1)
    info = SimpleNamespace(
        control_type="Button", automation_id="submit", name="提交", class_name="Button",
    )
    described = desktop_agent._describe_info(info, root_hwnd=4321)
    shared = [
        locator
        for _criteria, locator, _aid in desktop_agent._locator_candidates(
            "Button", "submit", "提交"
        )
    ]
    assert [c["locator"] for c in described["selector"]["candidates"]] == shared

