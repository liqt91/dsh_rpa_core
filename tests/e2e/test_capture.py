import ctypes
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from rpa_core import local_transport as lt
from rpa_core.capture import DesktopCaptureSession, ExtensionCaptureSession
from rpa_core.devserver import DevServer
from rpa_core.extension_exec import endpoint_name

ROOT = Path(__file__).resolve().parents[2]

TEST_PAGE = (
    "data:text/html,<html><body>"
    "<button id='go' name='confirm'>OK</button>"
    "<span>捕获冒烟页面</span>"
    "</body></html>"
)


@pytest.fixture()
def server(tmp_path):
    dev = DevServer(
        commands_root=ROOT / "commands",
        workflows_root=tmp_path / "workflows",
        port=0,
        browser_capture_factory=ExtensionCaptureSession,
        desktop_capture_factory=DesktopCaptureSession,
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
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


class _FakeBridge:
    """扮演 bridge host 的扩展腿：收 capture_arm → 回 capture_result（ADR 0015）。"""

    def __init__(self, descriptor: dict):
        self.name = endpoint_name("msedge", "e2e-cap")
        self.descriptor = descriptor
        self.server = lt.LocalEndpointServer(self.name)
        self.armed = 0
        self._stop = threading.Event()
        threading.Thread(target=self._loop, daemon=True).start()

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
                    channel.send(
                        {
                            "type": "capture_result",
                            "sessionId": message.get("sessionId"),
                            "descriptor": self.descriptor,
                        }
                    )
        finally:
            channel.close()

    def close(self) -> None:
        self._stop.set()
        self.server.close()


def test_browser_capture_extension_only_end_to_end(server):
    """浏览器捕获已收敛为自研扩展单通道：persistent 拒绝，extension 经 bridge 端点回传。"""
    base = f"http://127.0.0.1:{server.port}"

    # persistent/user-browser 通道已随 playwright 移除 → 必须拒绝
    status, payload = _request(
        "POST",
        "/api/capture/browser/start",
        {"transport": "persistent", "headless": True},
        base=base,
    )
    assert status == 400
    assert payload["error"] == "BAD_REQUEST"

    descriptor = {
        "kind": "browser",
        "selector": {"css": "#go"},
        "verifyCount": 1,
        "metadata": {"tag": "button"},
    }
    bridge = _FakeBridge(descriptor)
    try:
        # 仅自研扩展单通道：会话经 bridge 端点 arm 扩展
        status, payload = _request(
            "POST",
            "/api/capture/browser/start",
            {"transport": "extension"},
            base=base,
        )
        assert status == 200
        session_id = payload["sessionId"]

        status, payload = _request(
            "POST",
            "/api/capture/browser/pick",
            {"sessionId": session_id, "timeoutSeconds": 15},
            base=base,
        )
        assert status == 200
        assert payload["kind"] == "browser"
        assert payload["selector"]["css"] == "#go"
        assert payload["verifyCount"] == 1
        assert payload["metadata"]["tag"] == "button"
        assert bridge.armed >= 1

        status, payload = _request(
            "POST", "/api/capture/browser/cancel", {"sessionId": session_id}, base=base
        )
        assert status == 200
        assert payload["cancelled"] is True
    finally:
        bridge.close()


@pytest.mark.skipif(sys.platform != "win32", reason="desktop capture requires Windows")
@pytest.mark.skipif(
    os.environ.get("RPA_DESKTOP_E2E") != "1",
    reason="真实桌面 E2E 会弹窗抢焦点，缺省跳过；设 RPA_DESKTOP_E2E=1 启用",
)
def test_desktop_capture_end_to_end_verifies_hit(server, tmp_path):
    import subprocess as sp

    from pywinauto.controls.uiawrapper import UIAWrapper
    from pywinauto.uia_element_info import UIAElementInfo

    exe = tmp_path / "RpaCoreDesktopDemo.exe"
    csc = "C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    sp.run(
        [
            csc,
            "/nologo",
            "/target:winexe",
            f"/out:{exe}",
            "/r:System.Windows.Forms.dll",
            "/r:System.Drawing.dll",
            str(ROOT / "testapps" / "desktop" / "Program.cs"),
        ],
        check=True,
        capture_output=True,
    )
    proc = sp.Popen([str(exe)])
    base = f"http://127.0.0.1:{server.port}"
    try:
        # Win32 FindWindowW 毫秒级探测等待窗口，避免全桌面 UIA 枚举
        # （与 executor 的 attach 路径一致：只定位单窗口，不触发慢 provider 全树遍历）。
        user32 = ctypes.windll.user32
        find_window = user32.FindWindowW
        title = "RPA Core Desktop Demo"
        deadline = time.time() + 15
        hwnd = 0
        while time.time() < deadline and not hwnd:
            hwnd = find_window(None, title)
            if not hwnd:
                time.sleep(0.3)
        assert hwnd, "test app window did not appear"
        window = UIAWrapper(UIAElementInfo(hwnd))

        # 多显示器/RDP 下窗口可能落在屏幕外或被遮挡：钉到主屏固定位置并置前
        user32.MoveWindow(hwnd, 120, 120, 460, 280, True)
        window.set_focus()
        time.sleep(0.5)

        buttons = [
            wrapper
            for wrapper in window.descendants(control_type="Button")
            if getattr(wrapper.element_info, "automation_id", None) == "submitButton"
        ]
        assert buttons, "submitButton not found in test app"
        rect = buttons[0].rectangle()
        point = {"x": rect.left + rect.width() // 2, "y": rect.top + rect.height() // 2}

        status, payload = _request(
            "POST",
            "/api/capture/desktop/start",
            {"point": point, "windowHandle": hwnd, "timeoutSeconds": 15},
            base=base,
        )
        assert status == 200
        assert payload["mode"] == "point"
        session_id = payload["sessionId"]

        status, payload = _request(
            "POST",
            "/api/capture/desktop/pick",
            {
                "sessionId": session_id,
                "saveAs": "submitButton",
                "flow": "demo",
                "timeoutSeconds": 30,
            },
            base=base,
        )
        assert status == 200
        assert payload["kind"] == "desktop"
        locator = payload["selector"]["locator"]
        assert locator.get("automationId") == "submitButton", json.dumps(
            payload, ensure_ascii=False
        )
        assert payload["verifyCount"] == 1
        assert payload["metadata"]["controlType"] == "Button"

        # 元素描述符经真实执行器回验命中（可回验命中验收）
        import asyncio

        from rpa_core.executors import DesktopExecutor
        from rpa_core.model.command import CommandInvocation

        executor = DesktopExecutor()
        attached = asyncio.run(
            executor.execute(
                CommandInvocation(
                    command_id="desktop.attachWindow",
                    command_version="1.0.0",
                    run_id="capture-e2e",
                    step_id="attach",
                    inputs={"title": "RPA Core Desktop Demo", "processId": proc.pid},
                ),
                asyncio.Event(),
            )
        )
        assert attached.status == "success", attached.model_dump_json()
        found = asyncio.run(
            executor.execute(
                CommandInvocation(
                    command_id="desktop.findElement",
                    command_version="1.0.0",
                    run_id="capture-e2e",
                    step_id="find",
                    inputs={
                        "sessionId": attached.outputs["sessionId"],
                        "locator": locator,
                    },
                ),
                asyncio.Event(),
            )
        )
        assert found.status == "success", found.model_dump_json()
        assert found.outputs["matchedCount"] == 1

        status, element = _request("GET", "/api/workflows/demo/elements/submitButton", base=base)
        assert status == 200
        assert element["kind"] == "desktop"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except sp.TimeoutExpired:
            proc.kill()
