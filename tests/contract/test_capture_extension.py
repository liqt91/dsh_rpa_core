"""扩展捕获会话契约（M20/ADR 0015：经 bridge 端点 arm/disarm + 读回结果）。

用**假 bridge 端点**（LocalEndpointServer）扮演 host：断言会话下发 capture_arm、
收到 capture_result 后唤醒 pick、cancel 时下发 capture_disarm。
"""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from rpa_core import local_transport as lt
from rpa_core.capture import ExtensionCaptureSession, capture_click_label
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


def test_extension_capture_arms_all_online_endpoints():
    """多浏览器并存：start 必须 arm 全部在线端点（先回传者胜）。

    旧实现只连第一个端点、只 arm 一个浏览器——另一个浏览器的网页永远无法
    框选（桌面腿在浏览器内容区让位给扩展，而扩展从未收到 arm）。
    """
    edge = FakeBridge(browser="msedge", instance_id="multi1")
    chrome = FakeBridge(browser="chrome", instance_id="multi2")
    try:
        session = ExtensionCaptureSession()
        session.start()
        assert not session.offline
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not (edge.armed and chrome.armed):
            time.sleep(0.02)
        assert edge.armed and chrome.armed, "两个端点都应收到 capture_arm"
        # FakeBridge 收到 arm 即回传结果：任一先回传即唤醒 pick
        result = session.pick(timeout_seconds=5)
        assert result["selector"]["css"] == "#go"
        session.close()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not (
            edge.disarmed >= 1 and chrome.disarmed >= 1
        ):
            time.sleep(0.02)
        assert edge.disarmed >= 1 and chrome.disarmed >= 1, (
            "close 必须向全部端点下发 capture_disarm"
        )
    finally:
        edge.close()
        chrome.close()


@pytest.mark.parametrize(
    ("platform", "expected"),
    [("darwin", "⌘+Click"), ("win32", "Ctrl+Click"), ("linux", "Ctrl+Click")],
)
def test_capture_click_label_follows_platform(monkeypatch, platform, expected):
    """页内捕获手势文案必须按平台给对。

    回归（macOS 真机 2026-09-19）：macOS 在**系统层**把 Control+Click 改写成"次要点击"，
    浏览器只派发 contextmenu，**永不派发 ctrlKey 的 click** —— 对 Mac 用户提示
    「Ctrl+Click 捕获」等于提示一个不会生效的手势（现象：红框跟着鼠标走，但怎么点都
    捕获不到）。扩展侧已同时接受 ⌘+Click 与右键，提示文案必须同步改成 ⌘+Click。
    """
    monkeypatch.setattr(sys, "platform", platform)
    assert capture_click_label() == expected


def test_browser_capture_hint_uses_platform_gesture_label():
    """扩展侧捕获提示不能写死 Ctrl+Click（否则 Mac 用户被指引到无效手势）。

    content.js 的手势判定（isCaptureModifier/isSecondaryClick）由
    scripts/check_capture_helpers.mjs 覆盖；此处保证**用户可见文案**与之一致。
    """
    content = (ROOT / "extension" / "content.js").read_text(encoding="utf-8")
    assert "isCaptureModifier" in content and "isSecondaryClick" in content, (
        "content.js 必须同时接受修饰键点击与次要点击"
    )
    assert 'addEventListener("contextmenu", onSecondary' in content, (
        "必须挂 contextmenu：macOS 的 Ctrl+Click 只走这条路径"
    )
    assert 'addEventListener("mousedown", onSecondary' in content
