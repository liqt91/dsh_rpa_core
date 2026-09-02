import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from rpa_core.devserver import DevServer

ROOT = Path(__file__).resolve().parents[2]

BROWSER_DESCRIPTOR = {
    "kind": "browser",
    "selector": {"css": "#go"},
    "verifyCount": 1,
    "metadata": {"tag": "button", "text": "OK"},
}
DESKTOP_DESCRIPTOR = {
    "kind": "desktop",
    "selector": {
        "locator": {
            "backend": "uia",
            "controlType": "Button",
            "automationId": "submitButton",
        }
    },
    "verifyCount": 1,
    "metadata": {"windowTitle": "RPA Core Desktop Demo"},
}


class FakeBrowserSession:
    def __init__(self, transport, user_data_dir=None, headless=False, user_agent=None,
                 browser_type="edge", page_url=None, start_url=None):
        self.config = {
            "transport": transport, "user_data_dir": user_data_dir,
            "headless": headless, "user_agent": user_agent,
            "browser_type": browser_type, "page_url": page_url, "start_url": start_url,
        }
        self.started = False
        self.closed = False
        self.cancelled = False

    def start(self):
        self.started = True
        return ["https://example.com/"]

    def pick(self, timeout_seconds=60, click_css=None):
        return dict(BROWSER_DESCRIPTOR)

    def cancel(self):
        self.cancelled = True

    def close(self):
        self.closed = True


class FakeDesktopSession:
    def __init__(self, hotkey="F9", timeout_seconds=60.0, point=None):
        self.config = {"hotkey": hotkey, "timeout_seconds": timeout_seconds, "point": point}
        self.cancelled = False

    def pick(self, timeout_seconds=90):
        return dict(DESKTOP_DESCRIPTOR)

    def cancel(self):
        self.cancelled = True

    def close(self):
        pass


@pytest.fixture()
def capture_server(tmp_path):
    dev = DevServer(
        commands_root=ROOT / "commands",
        workflows_root=tmp_path / "workflows",
        port=0,
        browser_capture_factory=FakeBrowserSession,
        desktop_capture_factory=FakeDesktopSession,
    )
    dev.start()
    try:
        yield dev
    finally:
        dev.stop()


def _request(method: str, path: str, payload=None, base: str = ""):
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{base}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_browser_capture_flow_saves_and_reads_element(capture_server):
    base = f"http://127.0.0.1:{capture_server.port}"
    status, payload = _request(
        "POST",
        "/api/capture/browser/start",
        {"transport": "persistent", "headless": True},
        base=base,
    )
    assert status == 200
    assert payload["pages"] == ["https://example.com/"]
    session_id = payload["sessionId"]
    assert capture_server.app._browser_sessions[session_id].started

    status, payload = _request(
        "POST",
        "/api/capture/browser/pick",
        {"sessionId": session_id, "saveAs": "goButton"},
        base=base,
    )
    assert status == 200
    assert payload["selector"] == {"css": "#go"}
    assert payload["verifyCount"] == 1
    assert payload["savedAs"] == "goButton"

    status, element = _request("GET", "/api/elements/goButton", base=base)
    assert status == 200
    assert element["kind"] == "browser"
    assert element["selector"] == {"css": "#go"}

    status, listing = _request("GET", "/api/elements", base=base)
    assert status == 200
    assert listing == {"elements": ["goButton"]}

    status, payload = _request(
        "POST", "/api/capture/browser/cancel", {"sessionId": session_id}, base=base
    )
    assert status == 200
    assert payload == {"cancelled": True, "sessionId": session_id}
    assert capture_server.app._browser_sessions == {}


def test_desktop_capture_flow_and_session_removal(capture_server):
    base = f"http://127.0.0.1:{capture_server.port}"
    status, payload = _request(
        "POST", "/api/capture/desktop/start", {"point": {"x": 10, "y": 20}}, base=base
    )
    assert status == 200
    assert payload["mode"] == "point"
    session_id = payload["sessionId"]

    status, payload = _request(
        "POST",
        "/api/capture/desktop/pick",
        {"sessionId": session_id, "saveAs": "submitButton"},
        base=base,
    )
    assert status == 200
    locator = payload["selector"]["locator"]
    assert locator["automationId"] == "submitButton"
    assert payload["savedAs"] == "submitButton"

    status, element = _request("GET", "/api/elements/submitButton", base=base)
    assert status == 200
    assert element["kind"] == "desktop"

    status, payload = _request(
        "POST", "/api/capture/desktop/cancel", {"sessionId": session_id}, base=base
    )
    assert status == 200
    assert capture_server.app._desktop_sessions == {}


def test_capture_pick_unknown_session_is_404(capture_server):
    base = f"http://127.0.0.1:{capture_server.port}"
    status, payload = _request(
        "POST", "/api/capture/browser/pick", {"sessionId": "missing"}, base=base
    )
    assert status == 404
    assert payload["error"] == "NOT_FOUND"


def test_element_store_rejects_bad_names(capture_server):
    base = f"http://127.0.0.1:{capture_server.port}"
    status, payload = _request("GET", "/api/elements/..", base=base)
    assert status == 403
    assert payload["error"] == "FORBIDDEN"


def test_element_put_verify_delete_roundtrip(capture_server):
    base = f"http://127.0.0.1:{capture_server.port}"

    browser_doc = {
        "kind": "browser",
        "selector": {"css": "#result"},
        "verifyCount": 1,
        "name": None,
        "metadata": {"tag": "div"},
    }
    status, payload = _request("POST", "/api/elements/homeBtn", browser_doc, base=base)
    assert status == 200
    assert payload == {"name": "homeBtn"}

    status, payload = _request("POST", "/api/elements/homeBtn/verify", {}, base=base)
    assert status == 200
    assert payload["valid"] is True
    assert payload["verifyCount"] is None

    status, element = _request("GET", "/api/elements/homeBtn", base=base)
    assert status == 200
    assert element["selector"] == {"css": "#result"}

    status, payload = _request("DELETE", "/api/elements/homeBtn", base=base)
    assert status == 200
    assert payload == {"name": "homeBtn", "deleted": True}

    status, payload = _request("GET", "/api/elements/homeBtn", base=base)
    assert status == 404


def test_element_verify_flags_bad_selector_and_descriptor(capture_server):
    base = f"http://127.0.0.1:{capture_server.port}"

    bad_css = {
        "kind": "browser",
        "selector": {"css": ""},
        "verifyCount": 0,
        "metadata": {},
    }
    _request("POST", "/api/elements/bad", bad_css, base=base)
    status, payload = _request("POST", "/api/elements/bad/verify", {}, base=base)
    assert status == 200
    assert payload["valid"] is False
    assert any("css" in err["path"] for err in payload["errors"])

    bad_desktop = {
        "kind": "desktop",
        "selector": {"locator": {"backend": "uia", "controlType": "Button", "bogus": 1}},
        "verifyCount": 0,
        "metadata": {},
    }
    _request("POST", "/api/elements/badDesktop", bad_desktop, base=base)
    status, payload = _request("POST", "/api/elements/badDesktop/verify", {}, base=base)
    assert status == 200
    assert payload["valid"] is False


def test_element_put_rejects_invalid_document(capture_server):
    base = f"http://127.0.0.1:{capture_server.port}"
    status, payload = _request("POST", "/api/elements/broken", {"kind": "nope"}, base=base)
    assert status == 400
    assert payload["error"] == "BAD_REQUEST"

    status, payload = _request(
        "POST",
        "/api/capture/browser/pick",
        {"sessionId": "missing", "saveAs": "../escape"},
        base=base,
    )
    assert status == 404
