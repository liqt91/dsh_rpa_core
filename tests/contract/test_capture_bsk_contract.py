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
