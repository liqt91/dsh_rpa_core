import json

import pytest

from rpa_core.capture.browser_bsk import BrowserBskCaptureSession

FLOW = "demo"

_BSK_SESSION_START = {"agent_window_id": 1001, "browser_instance_id": "edge1", "session_id": "abcd"}

_DESCRIPTOR = {
    "kind": "browser",
    "selector": {"css": "#search-form > button"},
    "verifyCount": 1,
    "metadata": {"tag": "button", "id": None, "classes": [], "text": "Search",
                 "rect": {"x": 0, "y": 0, "width": 10, "height": 10}},
}


def _make_runner(responses: dict[str, list[dict]] | None = None,
                 script: list[tuple[list[str], dict]] | None = None):
    """可注入的 bsk runner：按命令前缀匹配返回，或顺序脚本。"""
    calls: list[list[str]] = []

    def runner(*args: str) -> dict:
        calls.append(list(args))
        if script is not None:
            if not script:
                return {"code": "unexpected_call", "message": f"no scripted response for {args}"}
            expected, response = script.pop(0)
            assert list(args[: len(expected)]) == expected, (
                f"expected {expected}, got {list(args)}"
            )
            return response
        key = " ".join(args[:2])
        queue = (responses or {}).get(key)
        if queue:
            return queue.pop(0)
        return {"code": "unexpected_call", "message": f"no response for {key}"}

    runner.calls = calls
    return runner


def test_bsk_capture_start_returns_session_and_navigates():
    runner = _make_runner(script=[
        (["session", "start"], dict(_BSK_SESSION_START)),
        (["navigate", "--session", "abcd", "https://example.com"], {"reached": "load"}),
        (["wait-for-navigation", "--session", "abcd"], {"reached": "load"}),
    ])
    session = BrowserBskCaptureSession(
        transport="bsk",
        browser_instance_id="edge1",
        start_url="https://example.com",
        runner=runner,
    )
    pages = session.start()
    assert pages == ["abcd"]
    assert session.session_id == "abcd"
    # start 带 instance id 时传给 --browser
    runner2 = _make_runner(script=[
        (["session", "start", "--browser", "edge1"], dict(_BSK_SESSION_START)),
    ])
    session2 = BrowserBskCaptureSession(
        transport="bsk", browser_instance_id="edge1", runner=runner2
    )
    session2.start()
    assert session2.session_id == "abcd"


def test_bsk_capture_pick_polls_until_descriptor():
    descriptor_json = json.dumps(_DESCRIPTOR)
    runner = _make_runner(script=[
        (["session", "start"], dict(_BSK_SESSION_START)),
        (["evaluate", "--session", "abcd"], {"ok": True, "value": "installed"}),
        # 前两次轮询未命中（picker 等用户点选），第三次返回描述符
        (["evaluate", "--session", "abcd"], {"ok": True, "value": "null"}),
        (["evaluate", "--session", "abcd"], {"ok": True, "value": "null"}),
        (["evaluate", "--session", "abcd"], {"ok": True, "value": descriptor_json}),
        (["evaluate", "--session", "abcd"], {"ok": True, "value": None}),  # cleanup
    ])
    session = BrowserBskCaptureSession(transport="bsk", runner=runner)
    session.start()
    result = session.pick(timeout_seconds=10)
    assert result["kind"] == "browser"
    assert result["selector"] == {"css": "#search-form > button"}
    assert result["verifyCount"] == 1


def test_bsk_capture_pick_timeout():
    runner = _make_runner(script=[
        (["session", "start"], dict(_BSK_SESSION_START)),
        (["evaluate", "--session", "abcd"], {"ok": True, "value": "installed"}),
        (["evaluate", "--session", "abcd"], {"ok": True, "value": "null"}),
        (["evaluate", "--session", "abcd"], {"ok": True, "value": None}),
    ])
    session = BrowserBskCaptureSession(transport="bsk", runner=runner)
    session.start()
    result = session.pick(timeout_seconds=0.01)
    assert result == {"timeout": True}


def test_bsk_capture_pick_cancelled_by_esc():
    runner = _make_runner(script=[
        (["session", "start"], dict(_BSK_SESSION_START)),
        (["evaluate", "--session", "abcd"], {"ok": True, "value": "installed"}),
        (["evaluate", "--session", "abcd"], {"ok": True, "value": '{"cancelled": true}'}),
        (["evaluate", "--session", "abcd"], {"ok": True, "value": None}),
    ])
    session = BrowserBskCaptureSession(transport="bsk", runner=runner)
    session.start()
    result = session.pick(timeout_seconds=5)
    assert result == {"cancelled": True}


def test_bsk_capture_pick_cancelled_by_close():
    runner = _make_runner(script=[
        (["session", "start"], dict(_BSK_SESSION_START)),
        (["evaluate", "--session", "abcd"], {"ok": True, "value": "installed"}),
        (["evaluate", "--session", "abcd"], {"ok": True, "value": "null"}),
        (["evaluate", "--session", "abcd"], {"ok": True, "value": None}),
        (["session", "stop", "abcd"], {"stopped": ["abcd"]}),
    ])
    session = BrowserBskCaptureSession(transport="bsk", runner=runner)
    session.start()
    session.cancel()
    result = session.pick(timeout_seconds=5)
    assert result == {"cancelled": True}
    session.close()
    assert session.session_id is None


def test_bsk_capture_pick_click_css_synthesizes_ctrl_click():
    descriptor_json = json.dumps(_DESCRIPTOR)
    runner = _make_runner(script=[
        (["session", "start"], dict(_BSK_SESSION_START)),
        (["evaluate", "--session", "abcd"], {"ok": True, "value": "installed"}),
        # click_css 触发 CDP 合成 Ctrl+Click
        (["click", "--session", "abcd", "--selector", "#go", "--modifiers", "ctrl"],
         {"tab_id": 1, "used_selector": "#go"}),
        (["evaluate", "--session", "abcd"], {"ok": True, "value": descriptor_json}),
        (["evaluate", "--session", "abcd"], {"ok": True, "value": None}),
    ])
    session = BrowserBskCaptureSession(transport="bsk", runner=runner)
    session.start()
    result = session.pick(timeout_seconds=10, click_css="#go")
    assert result["selector"]["css"] == "#search-form > button"


def test_bsk_capture_close_forces_session_stop():
    runner = _make_runner(script=[
        (["session", "start"], dict(_BSK_SESSION_START)),
        (["session", "stop", "abcd"], {"stopped": ["abcd"]}),
    ])
    session = BrowserBskCaptureSession(transport="bsk", runner=runner)
    session.start()
    assert session.session_id == "abcd"
    session.close()
    assert session.session_id is None


def test_bsk_capture_start_failure_raises():
    runner = _make_runner(script=[
        (["session", "start"], {"code": "not_found", "message": "no browsers online"}),
    ])
    session = BrowserBskCaptureSession(transport="bsk", runner=runner)
    with pytest.raises(RuntimeError, match="no browsers online"):
        session.start()


# -- 借用模式：捕获用户已打开的标签页（page_url 子串匹配） -------------------

_USER_TABS = [
    {"tab_id": 101, "title": "小红书 - 探索", "url": "https://www.xiaohongshu.com/explore"},
    {"tab_id": 102, "title": "DeepSeek", "url": "https://www.deepseek.com/"},
]


def _borrow_runner(tab_url_keyword: str):
    descriptor_json = json.dumps(_DESCRIPTOR)
    calls: list[list[str]] = []

    def runner(*args: str) -> dict:
        args = list(args)
        calls.append(list(args))
        if args[:2] == ["session", "start"]:
            return dict(_BSK_SESSION_START)
        if args[:3] == ["tab", "list", "--session"]:
            return list(_USER_TABS)
        if args[:3] == ["tab", "borrow", "--session"]:
            return {"tab_id": 101, "original_window_id": 50, "agent_window_id": 60}
        if args[:3] == ["tab", "return", "--session"]:
            return {"tab_id": 101, "returned_to_window_id": 50}
        if args[0] == "evaluate":
            expr = args[-1]
            assert "--tab-id" in args, "borrowed mode must target the borrowed tab"
            assert "101" in args
            if "__rpaCaptureResult" in expr:
                return {"ok": True, "value": descriptor_json}
            return {"ok": True, "value": "installed"}
        if args[:2] == ["session", "stop"]:
            return {"stopped": ["abcd"]}
        return {"ok": True}

    runner.calls = calls
    return runner


def test_bsk_capture_borrows_user_tab_by_page_url():
    runner = _borrow_runner("xiaohongshu")
    session = BrowserBskCaptureSession(
        transport="bsk",
        page_url="xiaohongshu.com/explore",
        runner=runner,
    )
    pages = session.start()
    assert pages == ["abcd"]
    assert session._borrowed_tab_id == "101"
    result = session.pick(timeout_seconds=10)
    assert result["selector"]["css"] == "#search-form > button"
    session.close()
    verbs = [c[1] for c in runner.calls if c[0] == "tab"]
    assert verbs == ["list", "borrow", "return"]
    assert ["session", "stop", "abcd"] in runner.calls


def test_bsk_capture_borrow_no_match_reports_available():
    runner = _borrow_runner("not-found")
    session = BrowserBskCaptureSession(
        transport="bsk", page_url="nonexistent.example", runner=runner,
    )
    with pytest.raises(RuntimeError, match="no user tab matches"):
        session.start()


def test_bsk_capture_start_url_still_uses_agent_window():
    """不带 page_url 时维持 Agent Window 导航路径（不借用）。"""
    runner = _make_runner(script=[
        (["session", "start"], dict(_BSK_SESSION_START)),
        (["navigate", "--session", "abcd"], {"reached": "load"}),
        (["wait-for-navigation", "--session", "abcd"], {"reached": "load"}),
        (["session", "stop", "abcd"], {"stopped": ["abcd"]}),
    ])
    session = BrowserBskCaptureSession(
        transport="bsk", start_url="https://example.com", runner=runner,
    )
    session.start()
    assert session._borrowed_tab_id is None
    session.close()
    assert not [c for c in runner.calls if c[0] == "tab"]
