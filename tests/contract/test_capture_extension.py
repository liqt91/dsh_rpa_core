"""扩展捕获会话契约（M20/ADR 0015：经 bridge 端点 arm/disarm + 读回结果）。

用**假 bridge 端点**（LocalEndpointServer）扮演 host：断言会话下发 capture_arm、
收到 capture_result 后唤醒 pick、cancel 时下发 capture_disarm。
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from rpa_core import local_transport as lt
from rpa_core.capture import ExtensionCaptureSession
from rpa_core.devserver import DevServer
from rpa_core.extension_exec import endpoint_name

ROOT = Path(__file__).resolve().parents[2]

_DESCRIPTOR = {
    "kind": "browser",
    "selector": {"css": "#go"},
    "verifyCount": 1,
    "metadata": {"tag": "button", "text": "OK"},
}


class FakeBridge:
    """扮演 bridge host 的扩展腿：收 capture_arm → 回 capture_result。"""

    def __init__(self, *, browser: str = "msedge", instance_id: str = "cap1"):
        self.name = endpoint_name(browser, instance_id)
        self.server = lt.LocalEndpointServer(self.name)
        self.armed: list[dict] = []
        self.disarmed = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                channel = self.server.accept(timeout=0.3)
            except lt.LocalTransportError:
                return
            if channel is None:
                continue
            threading.Thread(target=self._serve, args=(channel,), daemon=True).start()

    def _serve(self, channel) -> None:
        try:
            while True:
                try:
                    message = channel.recv()
                except lt.LocalTransportError:
                    return
                if message is None:
                    return
                kind = message.get("type")
                if kind == "capture_arm":
                    self.armed.append(message)
                    channel.send(
                        {
                            "type": "capture_result",
                            "sessionId": message.get("sessionId"),
                            "descriptor": _DESCRIPTOR,
                        }
                    )
                elif kind == "capture_disarm":
                    self.disarmed += 1
        finally:
            channel.close()

    def close(self) -> None:
        self._stop.set()
        self.server.close()
        self._thread.join(timeout=2)


@pytest.fixture()
def bridge():
    fake = FakeBridge()
    try:
        yield fake
    finally:
        fake.close()


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


def test_extension_capture_flow_arms_and_picks(server, bridge):
    base = f"http://127.0.0.1:{server.port}"
    status, payload = _request(
        "POST", "/api/capture/browser/start", {"transport": "extension"}, base=base
    )
    assert status == 200
    session_id = payload["sessionId"]

    picked: dict = {}

    def do_pick():
        picked["result"] = _request(
            "POST", "/api/capture/browser/pick",
            {"sessionId": session_id, "timeoutSeconds": 10}, base=base,
        )

    thread = threading.Thread(target=do_pick)
    thread.start()
    thread.join(timeout=8)
    status, pick_result = picked["result"]
    assert status == 200
    assert pick_result["selector"]["css"] == "#go"
    assert pick_result["verifyCount"] == 1
    # 会话确实向端点下发了 capture_arm（携带会话 id）
    assert bridge.armed, "capture_arm was not sent to the bridge endpoint"
    assert bridge.armed[0]["sessionId"]


def test_extension_capture_save_to_flow(server, bridge):
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
    thread.join(timeout=8)
    status, pick_result = picked["result"]
    assert status == 200
    assert pick_result["savedAs"] == "extBtn"
    assert pick_result["flow"] == "demo"

    status, element = _request(
        "GET", "/api/workflows/demo/elements/extBtn", base=base
    )
    assert status == 200
    assert element["selector"]["css"] == "#go"


def test_extension_capture_cancel_disarms(server, bridge):
    base = f"http://127.0.0.1:{server.port}"
    status, payload = _request(
        "POST", "/api/capture/browser/start", {"transport": "extension"}, base=base
    )
    session_id = payload["sessionId"]
    status, payload = _request(
        "POST", "/api/capture/browser/cancel", {"sessionId": session_id}, base=base
    )
    assert payload["cancelled"] is True
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and bridge.disarmed == 0:
        time.sleep(0.05)
    assert bridge.disarmed >= 1, "capture_disarm was not sent on cancel"


def test_extension_capture_offline_without_endpoint(server):
    """无 bridge 端点（扩展未装/未连）→ pick 明确返回 offline，不挂满超时。"""
    base = f"http://127.0.0.1:{server.port}"
    status, payload = _request(
        "POST", "/api/capture/browser/start", {"transport": "extension"}, base=base
    )
    session_id = payload["sessionId"]
    started = time.monotonic()
    status, pick_result = _request(
        "POST", "/api/capture/browser/pick",
        {"sessionId": session_id, "timeoutSeconds": 30}, base=base,
    )
    assert status == 200
    assert pick_result.get("offline") is True
    assert time.monotonic() - started < 10
