import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from rpa_core.capture import HybridCaptureSession
from rpa_core.devserver import DevServer

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
            import time

            time.sleep(self.pick_delay)
        return dict(self.result)

    def cancel(self):
        self.cancelled = True

    def close(self):
        pass


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


def test_hover_defaults_to_hybrid(server):
    """无鉴权后 hover 默认即 hybrid：会话带扩展鸭子类型 + pending 可见（不再依赖配对）。"""
    base = f"http://127.0.0.1:{server.port}"
    session_id = _start_hover(base)
    session = server.app._desktop_sessions[session_id]
    assert getattr(session, "is_extension_capture", False)
    assert session.pending
    status, payload = _request(
        "GET", "/api/capture/extension/pending", base=base
    )
    assert payload == {"pending": True, "sessionId": session_id}


def test_hybrid_extension_result_wins(server):
    """扩展先回传 → pick 返回浏览器描述符，桌面 agent 被取消。"""
    base = f"http://127.0.0.1:{server.port}"
    session_id = _start_hover(base)
    session = server.app._desktop_sessions[session_id]
    FakeDesktopSession.instances[-1].pick_delay = 3.0  # 桌面慢，扩展先回传

    picked: dict = {}

    def do_pick():
        picked["result"] = _request(
            "POST", "/api/capture/desktop/pick",
            {"sessionId": session_id, "timeoutSeconds": 10}, base=base,
        )

    thread = threading.Thread(target=do_pick)
    thread.start()
    import time

    time.sleep(0.2)
    status, ack = _request(
        "POST", "/api/capture/extension/result",
        {"sessionId": session_id, "descriptor": _BROWSER_DESCRIPTOR},
        base=base,
    )
    assert status == 200
    thread.join(timeout=5)
    status, result = picked["result"]
    assert status == 200
    assert result["kind"] == "browser"
    assert result["selector"]["css"] == "#go"
    assert session._desktop.cancelled  # 桌面侧被回收


def test_hybrid_desktop_result_wins(server):
    """桌面先回传 → pick 返回桌面描述符。"""
    base = f"http://127.0.0.1:{server.port}"
    session_id = _start_hover(base)
    status, result = _request(
        "POST", "/api/capture/desktop/pick",
        {"sessionId": session_id, "timeoutSeconds": 10}, base=base,
    )
    assert status == 200
    assert result["kind"] == "desktop"
    assert result["selector"]["locator"]["automationId"] == "submitButton"


def test_hybrid_save_extension_result_to_flow(server):
    """混合会话里扩展回传的描述符可落库 flow 元素资产。"""
    base = f"http://127.0.0.1:{server.port}"
    session_id = _start_hover(base)
    FakeDesktopSession.instances[-1].pick_delay = 3.0

    picked: dict = {}

    def do_pick():
        picked["result"] = _request(
            "POST", "/api/capture/desktop/pick",
            {"sessionId": session_id, "timeoutSeconds": 10,
             "saveAs": "hybridEl", "flow": "demo"},
            base=base,
        )

    thread = threading.Thread(target=do_pick)
    thread.start()
    import time

    time.sleep(0.2)
    _request(
        "POST", "/api/capture/extension/result",
        {"sessionId": session_id, "descriptor": _BROWSER_DESCRIPTOR},
        base=base,
    )
    thread.join(timeout=5)
    status, result = picked["result"]
    assert result["savedAs"] == "hybridEl"
    status, element = _request(
        "GET", "/api/workflows/demo/elements/hybridEl", base=base
    )
    assert status == 200
    assert element["kind"] == "browser"
