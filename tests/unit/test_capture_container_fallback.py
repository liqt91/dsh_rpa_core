"""捕获粒度兜底：窗口作用域 DFS 只拿到整窗时，不许把整窗当成用户要的控件。

M96 真机：微信 4.x 的 UIA provider 只支持 TreeWalker 导航，pywinauto 的
``children()``（FindAll）返回空 ⇒ ``capture_in_window`` 从根窗口一步都迈不出去，
直接返回 1024x865 的渲染窗；而悬停红框走屏幕级 ``ElementFromPoint``，能给出
搜索框那一级。两条路必须收敛：先判「是不是只拿到整窗」，是就再问屏幕级，
**只要同一窗口内更细的元素**（别的窗口面积再小也不能要，那是 DFS 优先要免疫的）。
"""

from __future__ import annotations

import pytest

from rpa_core.capture import desktop_agent

WINDOW_HWND = 4242

WINDOW_DOC = {
    "kind": "desktop",
    "selector": {
        "locator": {
            "backend": "uia",
            "controlType": "Pane",
            "name": "MMUIRenderSubWindowHW",
        }
    },
    "verifyCount": 1,
    "metadata": {
        "windowHandle": WINDOW_HWND,
        "controlType": "Pane",
        "name": "MMUIRenderSubWindowHW",
        "rect": {"left": 232, "top": 82, "width": 1024, "height": 865},
    },
}

FINE_DOC = {
    "kind": "desktop",
    "selector": {
        "locator": {"backend": "uia", "controlType": "Button", "name": "搜索"}
    },
    "verifyCount": 1,
    "metadata": {
        "windowHandle": WINDOW_HWND,
        "controlType": "Button",
        "name": "搜索",
        "rect": {"left": 300, "top": 130, "width": 200, "height": 30},
    },
}


@pytest.fixture
def capture_env(monkeypatch):
    """把 capture_at 的窗口/唤醒依赖都换成替身，只留「粒度选择」这条逻辑。"""
    calls: dict[str, int] = {"wake": 0, "finer": 0}
    state: dict[str, object] = {"scoped": WINDOW_DOC, "finer": FINE_DOC}

    monkeypatch.setattr(desktop_agent, "_maybe_wake_accessibility",
                        lambda *a, **k: calls.__setitem__("wake", calls["wake"] + 1))
    monkeypatch.setattr(desktop_agent, "capture_in_window",
                        lambda hwnd, x, y: state["scoped"])

    def fake_finer(x, y, root_hwnd):
        calls["finer"] += 1
        assert root_hwnd == WINDOW_HWND, "兜底必须按同一个根窗口问"
        return state["finer"]

    monkeypatch.setattr(desktop_agent, "_finer_element_in_same_window", fake_finer)
    # 根窗口 1000x800；用真窗口 API 在 CI 上取不到，这里直接给尺寸。
    monkeypatch.setattr(desktop_agent, "_window_rect",
                        lambda hwnd: (1040, 873) if hwnd == WINDOW_HWND else None)
    return calls, state


def test_capture_at_switches_to_finer_screen_hit(capture_env) -> None:
    """DFS 只给整窗 ⇒ 必须换成屏幕级那个更细的元素。"""
    calls, _state = capture_env

    doc = desktop_agent.capture_at(320, 140, WINDOW_HWND)

    assert doc["metadata"]["controlType"] == "Button", "整窗容器被当成目标保存了"
    assert doc["metadata"]["rect"]["width"] == 200, "应采信屏幕级命中的搜索框矩形"
    assert calls["finer"] == 1, "只拿到整窗时必须再问一次屏幕级"


def test_capture_at_keeps_scoped_when_no_finer_hit(capture_env) -> None:
    """屏幕级也拿不到更细的 ⇒ 退回窗口作用域结果，不能返回空。"""
    calls, state = capture_env
    state["finer"] = None

    doc = desktop_agent.capture_at(320, 140, WINDOW_HWND)

    assert doc is WINDOW_DOC, "没有更细命中时不能丢窗口作用域结果"
    assert calls["finer"] == 1


def test_capture_at_skips_fallback_when_already_specific(capture_env) -> None:
    """DFS 已经钻到具体控件时，不许再问屏幕级（省一次跨进程命中，也不引入覆盖层风险）。"""
    calls, state = capture_env
    state["scoped"] = FINE_DOC

    doc = desktop_agent.capture_at(320, 140, WINDOW_HWND)

    assert doc is FINE_DOC
    assert calls["finer"] == 0, "已经拿到细元素就不该触发兜底"


def test_rect_fills_window_threshold(monkeypatch) -> None:
    """整窗判定：铺满窗口算容器，明显更小才算具体控件；矩形缺失按容器处理。"""
    monkeypatch.setattr(desktop_agent, "_window_rect", lambda hwnd: (1000, 800))

    def rect(w: int, h: int) -> dict:
        return {"left": 0, "top": 0, "width": w, "height": h}

    assert desktop_agent._rect_fills_window(rect(1000, 800), 1) is True
    assert desktop_agent._rect_fills_window(rect(950, 760), 1) is True   # 90% 以上
    assert desktop_agent._rect_fills_window(rect(400, 40), 1) is False
    assert desktop_agent._rect_fills_window(None, 1) is True, "拿不到矩形要按容器再问一次"
    assert desktop_agent._rect_fills_window(rect(1000, 800), 0) is False


# ---------------------------------------------------------------------------
# M97 真机复现：hover 框着搜索框、按 F9 保存下来是整个微信窗口。
# 根因不是「兜底没写」，而是**兜底写在了另一条路上**：
#   · hover 快路径（ElementFromPoint 成功）压根不产 DFS 的 leaf；
#   · 捕获瞬间只复用 last_leaf ⇒ 微信这种 FindAll 失效的 provider 永远不复用，
#     一路退到 capture_at 再走一遍 DFS，最后拿到整窗。
# 下面三条把「画红框的元素必须就是保存的元素」钉死。
# ---------------------------------------------------------------------------


class _FakeElement:
    """最小 UIA 元素替身：够 ``_drill_to_leaf`` / ``_describe_info`` 走完。"""

    def __init__(self, *, handle=0, left=300, top=130, width=200, height=30,
                 control_type="Edit", name="搜索"):
        self.handle = handle
        self.control_type = control_type
        self.name = name
        self.automation_id = None
        self.class_name = "Qt51514QWindowIcon"
        self.parent = None
        self._box = (left, top, left + width, top + height)

    @property
    def rectangle(self):
        class _Rect:
            pass

        rect = _Rect()
        rect.left, rect.top, rect.right, rect.bottom = self._box
        return rect

    def children(self):
        return []


@pytest.fixture
def finer_env(monkeypatch):
    """把 ``_finer_element_in_same_window`` 的外部依赖换成替身（只留粒度判据）。"""
    state = {"info": _FakeElement(), "root_at": WINDOW_HWND, "traces": []}
    monkeypatch.setattr(desktop_agent, "_element_from_point",
                        lambda x, y: state["info"])
    monkeypatch.setattr(desktop_agent, "_win32_root_at",
                        lambda x, y: state["root_at"])
    monkeypatch.setattr(desktop_agent, "_window_rect", lambda hwnd: (1040, 873))
    monkeypatch.setattr(desktop_agent, "_verify_in_root", lambda *a, **k: 1)
    monkeypatch.setattr(desktop_agent, "_trace",
                        lambda side, mark, **kw: state["traces"].append(mark))
    return state


def test_finer_accepts_virtual_element_without_handle(finer_env) -> None:
    """★ handle=0 的 Qt 自绘控件：按光标处 win32 根窗口定身份，不能一票否决。

    微信搜索框这类虚拟元素的 ``handle`` 就是 0；拿 0 去和根窗口比永远不等，
    于是「唯一能用的屏幕级命中」被挡掉，只剩整窗（M97 真机）。
    """
    finer_env["info"] = _FakeElement(handle=0)

    captured = desktop_agent._finer_element_in_same_window(320, 140, WINDOW_HWND)

    assert captured is not None, "同一窗口内的虚拟元素必须被采信"
    assert captured["metadata"]["rect"]["width"] == 200


def test_finer_rejects_element_of_another_window(finer_env) -> None:
    """反向：别的窗口（浮层/覆盖层）面积再小也不能要——那是 DFS 优先要免疫的。"""
    finer_env["info"] = _FakeElement(handle=0)
    finer_env["root_at"] = 9999  # 光标下是另一个根窗口

    assert desktop_agent._finer_element_in_same_window(320, 140, WINDOW_HWND) is None
    assert "finer_other_window" in finer_env["traces"]


def test_capture_rank_tolerates_missing_rect(monkeypatch) -> None:
    """没有 rect 时不能假装「已钻到具体控件」（否则整窗会被当成好结果）。"""
    monkeypatch.setattr(desktop_agent, "_window_rect", lambda hwnd: (1040, 873))

    assert desktop_agent._capture_rank({"verifyCount": 1, "metadata": {}}, WINDOW_HWND) == (0, 1)


def test_capture_at_records_rect_in_semantic_mode(monkeypatch) -> None:
    """★ 语义捕获也必须落 rect。

    预览红框读它，``_capture_rank`` 判「这一枪是不是只拿到整窗」也读它。此前只有
    图像模式写 ⇒ 语义捕获的 rect 恒缺 ⇒ 粒度无从判断（M97）。
    """
    monkeypatch.setattr(desktop_agent, "_element_from_point",
                        lambda x, y: _FakeElement())

    doc = desktop_agent.capture_at(320, 140)

    assert doc["metadata"]["rect"] == {
        "left": 300, "top": 130, "width": 200, "height": 30,
    }


CONTAINER_DOC = {
    "kind": "desktop",
    "selector": {"locator": {"backend": "uia", "controlType": "Pane"}},
    "verifyCount": 3,
    "metadata": {
        "controlType": "Pane",
        "rect": {"left": 232, "top": 82, "width": 1024, "height": 865},
    },
}

FINE_DOC_ZERO = {
    "kind": "desktop",
    "selector": {"locator": {"backend": "uia", "controlType": "Edit"}},
    "verifyCount": 0,  # 微信 provider 不支持 FindAll ⇒ 实测不出
    "metadata": {
        "controlType": "Edit",
        "rect": {"left": 300, "top": 130, "width": 200, "height": 30},
    },
}


class _NoSleep:
    @staticmethod
    def sleep(_seconds):
        return None


def test_capture_with_retry_prefers_finer_granularity(monkeypatch) -> None:
    """★ 整窗命中 3 次 vs 搜索框命中 0 次 ⇒ 必须取搜索框（粒度压过命中数）。"""
    sequence = [FINE_DOC_ZERO, CONTAINER_DOC]
    monkeypatch.setattr(desktop_agent, "_window_rect", lambda hwnd: (1040, 873))
    monkeypatch.setattr(desktop_agent, "time", _NoSleep)
    monkeypatch.setattr(desktop_agent, "capture_at",
                        lambda *a, **k: sequence.pop(0))

    doc = desktop_agent.capture_with_retry(320, 140, WINDOW_HWND, attempts=2)

    assert doc is FINE_DOC_ZERO, "命中数多的整窗容器不许盖掉更细的元素"


def test_capture_with_retry_does_not_stop_on_container_hit(monkeypatch) -> None:
    """整窗容器即使「唯一命中」也不算够好——不能第一轮就带着整窗返回。"""
    calls: list[int] = []
    container_unique = {**CONTAINER_DOC, "verifyCount": 1}
    monkeypatch.setattr(desktop_agent, "_window_rect", lambda hwnd: (1040, 873))
    monkeypatch.setattr(desktop_agent, "time", _NoSleep)

    def fake_capture(*a, **k):
        calls.append(1)
        return container_unique

    monkeypatch.setattr(desktop_agent, "capture_at", fake_capture)

    doc = desktop_agent.capture_with_retry(320, 140, WINDOW_HWND)

    assert len(calls) == 4, "整窗就是没钻下去，必须继续重试"
    assert doc is container_unique


# ---------------------------------------------------------------------------
# hover 捕获瞬间：复用的必须是**画红框的那个元素**（hit_info），不是 DFS leaf。
# ---------------------------------------------------------------------------


class _FakeClock:
    """每读一次时间就前进 0.12s：让 hover 的 100ms 命中节流每帧都放行。

    真机上一帧只有十几毫秒，这里要的是「每帧都重新命中」——否则第二帧的
    「这一帧没命中元素」永远测不到（缓存只在跑过命中的帧里更新）。
    """

    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        self.now += 0.12
        return self.now

    def perf_counter(self) -> float:
        return self.now

    @staticmethod
    def sleep(_seconds) -> None:
        return None


@pytest.fixture
def hover_env(monkeypatch):
    from ctypes import wintypes

    counts = {"describe": 0, "retry": 0, "describe_args": []}
    # ``sequence`` 是每帧的命中元素（耗尽后沿用最后一个）；``leaf`` 是 DFS 的
    # leaf——快路径下恒为 None，这正是 M97 的分叉点。
    state = {"f9": 0, "n": 0, "sequence": [], "moved_leaf": None}
    win_rect = wintypes.RECT(0, 0, 1920, 1080)

    class _Overlay:
        hwnd = 1

        def show_rect(self, *a):  # noqa: ANN002
            pass

        def hide(self):
            pass

        def pump(self):
            pass

        def destroy(self):
            pass

    def cursor():
        state["n"] += 1
        return (100 + state["n"] * 10, 200)  # 每帧都移动，逼迫重新命中

    def hotkey(vk):
        if vk != desktop_agent.VK_F9:
            return False
        # 按「已经跑过几帧」触发，而不是数 F9 被问了几次（入口处还会多问一次）。
        return state["n"] >= 2  # 第二帧按下（第一帧先把缓存填上）

    def fake_hit(x, y, exclude, allow_scoped=True):  # noqa: ANN001, ARG001
        # 每个元素表示「这一帧命中了谁」；序列耗尽后沿用最后一项（模拟鼠标停在原处）。
        if state["sequence"]:
            state["last_hit"] = state["sequence"].pop(0)
        return (win_rect, WINDOW_HWND, state["moved_leaf"], False, [],
                state.get("last_hit"))

    monkeypatch.setattr(desktop_agent, "_HoverOverlay", lambda: _Overlay())
    monkeypatch.setattr(desktop_agent, "_cursor_pos", cursor)
    monkeypatch.setattr(desktop_agent, "_hotkey_pressed", hotkey)
    monkeypatch.setattr(desktop_agent, "_poll_click_hook_event", lambda: False)
    monkeypatch.setattr(desktop_agent, "_start_click_hook", lambda *a, **k: None)
    monkeypatch.setattr(desktop_agent, "_stop_click_hook", lambda: None)
    monkeypatch.setattr(desktop_agent, "_in_browser_content", lambda x, y: False)
    monkeypatch.setattr(desktop_agent, "_win32_root_at", lambda x, y: WINDOW_HWND)
    monkeypatch.setattr(desktop_agent, "_hover_hit", fake_hit)
    monkeypatch.setattr(desktop_agent, "_WAKE_INTERVAL", 999.0)
    monkeypatch.setattr(desktop_agent, "time", _FakeClock())
    monkeypatch.setattr(desktop_agent, "_maybe_wake_accessibility",
                        lambda *a, **k: False)
    monkeypatch.setattr(
        desktop_agent, "_describe_info",
        lambda element, root, path, **kw: (
            counts.__setitem__("describe", counts["describe"] + 1),
            counts["describe_args"].append(element),
            {"fast": 1},
        )[-1],
    )
    monkeypatch.setattr(
        desktop_agent, "capture_with_retry",
        lambda *a, **k: (counts.__setitem__("retry", counts["retry"] + 1), {"full": 1})[-1],
    )
    return counts, state


def test_hover_capture_reuses_element_that_drew_the_frame(hover_env) -> None:
    """★ 快路径不产 leaf，但 hit_info 就是画红框的那个元素 ⇒ 必须复用它。

    这是 M97 真机的直接判据：微信上 DFS 迈不出去，leaf 永远 None，只认 leaf
    就等于「永远不复用」，最后保存成整窗。
    """
    counts, state = hover_env
    drawn = _FakeElement()
    state["sequence"] = [drawn]
    state["moved_leaf"] = None  # 快路径形态：只有 hit_info，没有 DFS leaf

    result = desktop_agent._hover_capture(desktop_agent.VK_F9, 2.0)

    assert counts["describe"] == 1, "画红框的元素必须被复用，不许退回去重跑 DFS"
    assert counts["retry"] == 0
    assert counts["describe_args"][0] is drawn
    assert result == {"fast": 1}


def test_hover_capture_does_not_reuse_stale_element(hover_env) -> None:
    """反向：这一帧没命中元素 ⇒ 不许复用上一帧那一处的旧元素。

    旧实现在 ``hit_info is None`` 时**保留**上一次的元素，于是「矩形换到了新位置、
    元素还是旧那个」——按 F9 就把 A 处的东西存成 B 处。
    """
    counts, state = hover_env
    state["sequence"] = [_FakeElement(name="旧元素"), None]
    state["moved_leaf"] = None

    result = desktop_agent._hover_capture(desktop_agent.VK_F9, 2.0)

    assert counts["describe"] == 0, "复用旧元素会把 A 处的东西存成 B 处"
    assert counts["retry"] == 1
    assert result == {"full": 1}
