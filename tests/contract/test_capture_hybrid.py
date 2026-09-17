"""混合捕获契约（桌面 hover + 扩展，先回传者胜；扩展腿经 bridge 端点，ADR 0015）。

扩展腿用**假 bridge 端点**扮演 host：会话 arm 后由假端点（可延迟）回 capture_result，
据此验证「扩展先赢 / 桌面先赢 / 落库」三条语义。
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
from rpa_core.capture import HybridCaptureSession
from rpa_core.devserver import DevServer
from rpa_core.extension_exec import endpoint_name

ROOT = Path(__file__).resolve().parents[2]

_DESKTOP_DESCRIPTOR = {
    "kind": "desktop",
    "selector": {"locator": {"backend": "uia", "controlType": "Button",
                             "automationId": "submitButton"}},
    "verifyCount": 1,
    "metadata": {"windowTitle": "Demo"},
}
_BROWSER_DESCRIPTOR = {
    "kind": "browser",
    "selector": {"css": "#go"},
    "verifyCount": 1,
    "metadata": {"tag": "button"},
}


class FakeDesktopSession:
    """记录参数的桌面会话假实现（hybrid 包装时由 HybridCaptureSession 持有）。"""

    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.cancelled = False
        self.result = dict(_DESKTOP_DESCRIPTOR)
        self.pick_delay = 0.0
        FakeDesktopSession.instances.append(self)

    def pick(self, timeout_seconds=90):
        if self.pick_delay:
            time.sleep(self.pick_delay)
        return dict(self.result)

    def cancel(self):
        self.cancelled = True

    def close(self):
        pass


class FakeBridge:
    """扮演 bridge host 的扩展腿；``delay=None`` 表示永不回结果（桌面先赢用）。"""

    def __init__(self, *, delay: float | None = 0.1,
                 browser: str = "msedge", instance_id: str = "hyb1"):
        self.name = endpoint_name(browser, instance_id)
        self.delay = delay
        self.server = lt.LocalEndpointServer(self.name)
        self.armed = 0
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
                if message.get("type") == "capture_arm":
                    self.armed += 1
                    if self.delay is None:
                        continue
                    time.sleep(self.delay)
                    channel.send(
                        {
                            "type": "capture_result",
                            "sessionId": message.get("sessionId"),
                            "descriptor": _BROWSER_DESCRIPTOR,
                        }
                    )
        finally:
            channel.close()

    def close(self) -> None:
        self._stop.set()
        self.server.close()
        self._thread.join(timeout=2)


def _factory(**kwargs):
    """模拟 cli 的桌面工厂：hybrid=True 时包 HybridCaptureSession。"""
    if kwargs.pop("hybrid", False):
        return HybridCaptureSession(desktop_factory=FakeDesktopSession, **kwargs)
    return FakeDesktopSession(**kwargs)


@pytest.fixture()
def server(tmp_path):
    FakeDesktopSession.instances = []
    dev = DevServer(
        commands_root=ROOT / "commands",
        workflows_root=tmp_path / "workflows",
        port=0,
        desktop_capture_factory=_factory,
    )
    dev.start()
    try:
        yield dev
    finally:
        dev.stop()


@pytest.fixture()
def bridge():
    fake = FakeBridge()
    try:
        yield fake
    finally:
        fake.close()


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


def _start_hover(base):
    status, payload = _request(
        "POST", "/api/capture/desktop/start", {"hover": True, "timeoutSeconds": 30},
        base=base,
    )
    assert status == 200
    return payload["sessionId"]


def test_hover_defaults_to_hybrid(server, bridge):
    """hover 默认即 hybrid：会话带扩展鸭子类型、pending，且已向端点 arm。"""
    base = f"http://127.0.0.1:{server.port}"
    session_id = _start_hover(base)
    session = server.app._desktop_sessions[session_id]
    assert getattr(session, "is_extension_capture", False)
    assert session.pending
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and bridge.armed == 0:
        time.sleep(0.05)
    assert bridge.armed >= 1, "hybrid 会话未向 bridge 端点 arm 扩展腿"


def test_hybrid_extension_result_wins(server, bridge):
    """扩展先回传 → pick 返回浏览器描述符，桌面 agent 被取消。"""
    base = f"http://127.0.0.1:{server.port}"
    session_id = _start_hover(base)
    session = server.app._desktop_sessions[session_id]
    FakeDesktopSession.instances[-1].pick_delay = 3.0  # 桌面慢，扩展先回传

    status, result = _request(
        "POST", "/api/capture/desktop/pick",
        {"sessionId": session_id, "timeoutSeconds": 10}, base=base,
    )
    assert status == 200
    assert result["kind"] == "browser"
    assert result["selector"]["css"] == "#go"
    assert session._desktop.cancelled  # 桌面侧被回收


def test_hybrid_desktop_result_wins(server):
    """桌面先回传 → pick 返回桌面描述符（扩展端点永不回结果）。"""
    bridge = FakeBridge(delay=None)
    try:
        base = f"http://127.0.0.1:{server.port}"
        session_id = _start_hover(base)
        status, result = _request(
            "POST", "/api/capture/desktop/pick",
            {"sessionId": session_id, "timeoutSeconds": 10}, base=base,
        )
        assert status == 200
        assert result["kind"] == "desktop"
        assert result["selector"]["locator"]["automationId"] == "submitButton"
    finally:
        bridge.close()


def test_hybrid_save_extension_result_to_flow(server, bridge):
    """混合会话里扩展回传的描述符可落库 flow 元素资产。"""
    base = f"http://127.0.0.1:{server.port}"
    session_id = _start_hover(base)
    FakeDesktopSession.instances[-1].pick_delay = 3.0

    status, result = _request(
        "POST", "/api/capture/desktop/pick",
        {"sessionId": session_id, "timeoutSeconds": 10,
         "saveAs": "hybridEl", "flow": "demo"},
        base=base,
    )
    assert status == 200
    assert result["savedAs"] == "hybridEl"
    status, element = _request(
        "GET", "/api/workflows/demo/elements/hybridEl", base=base
    )
    assert status == 200
    assert element["kind"] == "browser"


# ---- 单元级（不经 devserver） -------------------------------------------------
def test_hybrid_offline_extension_degrades_to_desktop():
    """扩展腿离线（无 bridge 端点）→ 退化为纯桌面 hover，桌面结果正常返回。

    回归：离线腿的 result_event 在 start 时已置位，旧实现会被误判为
    「扩展先回传」而秒回 cancelled，桌面腿永远等不到。
    """
    from rpa_core.capture.extension import ExtensionCaptureSession

    ext = ExtensionCaptureSession(endpoint="rpa_core_ext_test_no_such_endpoint")
    session = HybridCaptureSession(
        desktop_factory=FakeDesktopSession, extension_session=ext
    )
    try:
        session.start()
        assert session.extension_offline
        result = session.pick(timeout_seconds=5)
        assert result["kind"] == "desktop"
        assert result["selector"]["locator"]["automationId"] == "submitButton"
    finally:
        session.close()


def test_hybrid_forwards_hybrid_flag_to_desktop_factory():
    """hybrid=True 必须随桌面腿下发：agent 靠它在浏览器内容区让位给扩展。"""
    from rpa_core.capture.extension import ExtensionCaptureSession

    FakeDesktopSession.instances = []
    session = HybridCaptureSession(
        desktop_factory=FakeDesktopSession,
        extension_session=ExtensionCaptureSession(endpoint="x"),
        hover=True,
    )
    assert FakeDesktopSession.instances[-1].kwargs["hybrid"] is True
    assert FakeDesktopSession.instances[-1].kwargs["hover"] is True
    session.close()
