import ctypes
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from rpa_core.capture import BrowserCaptureSession, DesktopCaptureSession
from rpa_core.devserver import DevServer

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
        browser_capture_factory=BrowserCaptureSession,
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


def test_browser_persistent_capture_end_to_end(server):
    base = f"http://127.0.0.1:{server.port}"
    status, payload = _request(
        "POST",
        "/api/capture/browser/start",
        {"transport": "persistent", "headless": True, "startUrl": TEST_PAGE},
        base=base,
    )
    assert status == 200
    session_id = payload["sessionId"]

    status, payload = _request(
        "POST",
        "/api/capture/browser/pick",
        {"sessionId": session_id, "clickCss": "#go", "timeoutSeconds": 15},
        base=base,
    )
    assert status == 200
    assert payload["kind"] == "browser"
    assert payload["selector"]["css"] == "#go"
    assert payload["verifyCount"] == 1
    assert payload["metadata"]["tag"] == "button"

    status, payload = _request(
        "POST",
        "/api/capture/browser/pick",
        {"sessionId": session_id, "clickCss": "#go", "saveAs": "goButton", "timeoutSeconds": 15},
        base=base,
    )
    assert status == 200
    assert payload["savedAs"] == "goButton"

    status, element = _request("GET", "/api/elements/goButton", base=base)
    assert status == 200
    assert element["kind"] == "browser"
    assert element["selector"] == {"css": "#go"}

    status, payload = _request(
        "POST", "/api/capture/browser/cancel", {"sessionId": session_id}, base=base
    )
    assert status == 200
    assert payload["cancelled"] is True


@pytest.mark.skipif(sys.platform != "win32", reason="desktop capture requires Windows")
def test_desktop_capture_end_to_end_verifies_hit(server, tmp_path):
    import subprocess as sp

    from pywinauto import Desktop

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
        deadline = time.time() + 15
        window = None
        while time.time() < deadline and window is None:
            matches = Desktop(backend="uia").windows(
                title="RPA Core Desktop Demo", process=proc.pid
            )
            window = matches[0] if matches else None
            if window is None:
                time.sleep(0.3)
        assert window is not None, "test app window did not appear"

        # 多显示器/RDP 下窗口可能落在屏幕外或被遮挡：钉到主屏固定位置并置前
        hwnd = int(window.handle)
        ctypes.windll.user32.MoveWindow(hwnd, 120, 120, 460, 280, True)
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
            {"sessionId": session_id, "saveAs": "submitButton", "timeoutSeconds": 30},
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

        status, element = _request("GET", "/api/elements/submitButton", base=base)
        assert status == 200
        assert element["kind"] == "desktop"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except sp.TimeoutExpired:
            proc.kill()
