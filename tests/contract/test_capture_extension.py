import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from rpa_core.capture import ExtensionCaptureSession
from rpa_core.devserver import DevServer

ROOT = Path(__file__).resolve().parents[2]

_DESCRIPTOR = {
    "kind": "browser",
    "selector": {"css": "#go"},
    "verifyCount": 1,
    "metadata": {"tag": "button", "text": "OK"},
}


@pytest.fixture()
def server(tmp_path):
    dev = DevServer(
        commands_root=ROOT / "commands",
        workflows_root=tmp_path / "workflows",
        port=0,
        browser_capture_factory=ExtensionCaptureSession,
    )
    dev.start()
    try:
        yield dev
    finally:
        dev.stop()


def _request(method: str, path: str, payload=None, base: str = ""):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    request = urllib.request.Request(
        f"{base}{path}", data=data, method=method, headers=headers,
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_extension_capture_flow_via_result_post(server):
    base = f"http://127.0.0.1:{server.port}"
    status, payload = _request(
        "POST", "/api/capture/browser/start", {"transport": "extension"}, base=base
    )
    assert status == 200
    session_id = payload["sessionId"]

    status, payload = _request(
        "GET", "/api/capture/extension/pending", base=base
    )
    assert payload == {"pending": True, "sessionId": session_id}

    picked: dict = {}

    def do_pick():
        picked["result"] = _request(
            "POST", "/api/capture/browser/pick",
            {"sessionId": session_id, "timeoutSeconds": 10}, base=base,
        )

    thread = threading.Thread(target=do_pick)
    thread.start()
    status, payload = _request(
        "POST", "/api/capture/extension/result",
        {"sessionId": session_id, "descriptor": _DESCRIPTOR},
        base=base,
    )
    assert status == 200
    assert payload == {"received": True, "sessionId": session_id}
    thread.join(timeout=5)
    status, pick_result = picked["result"]
    assert status == 200
    assert pick_result["selector"]["css"] == "#go"
    assert pick_result["verifyCount"] == 1

    status, payload = _request(
        "GET", "/api/capture/extension/pending", base=base
    )
    assert payload["pending"] is False


def test_extension_result_unknown_session_404(server):
    base = f"http://127.0.0.1:{server.port}"
    status, payload = _request(
        "POST", "/api/capture/extension/result",
        {"sessionId": "nope", "descriptor": _DESCRIPTOR}, base=base,
    )
    assert status == 404


def test_extension_capture_save_to_flow(server):
    base = f"http://127.0.0.1:{server.port}"
    status, payload = _request(
        "POST", "/api/capture/browser/start", {"transport": "extension"}, base=base
    )
    session_id = payload["sessionId"]

    picked: dict = {}

    def do_pick():
        picked["result"] = _request(
            "POST", "/api/capture/browser/pick",
            {"sessionId": session_id, "timeoutSeconds": 10,
             "saveAs": "extBtn", "flow": "demo"},
            base=base,
        )

    thread = threading.Thread(target=do_pick)
    thread.start()
    _request(
        "POST", "/api/capture/extension/result",
        {"sessionId": session_id, "descriptor": _DESCRIPTOR}, base=base,
    )
    thread.join(timeout=5)
    status, pick_result = picked["result"]
    assert status == 200
    assert pick_result["savedAs"] == "extBtn"
    assert pick_result["flow"] == "demo"

    status, element = _request(
        "GET", "/api/workflows/demo/elements/extBtn", base=base
    )
    assert status == 200
    assert element["selector"]["css"] == "#go"


def test_extension_cancel_unpends(server):
    base = f"http://127.0.0.1:{server.port}"
    status, payload = _request(
        "POST", "/api/capture/browser/start", {"transport": "extension"}, base=base
    )
    session_id = payload["sessionId"]
    status, payload = _request(
        "POST", "/api/capture/browser/cancel", {"sessionId": session_id}, base=base
    )
    assert payload["cancelled"] is True
    status, payload = _request(
        "GET", "/api/capture/extension/pending", base=base
    )
    assert payload["pending"] is False
