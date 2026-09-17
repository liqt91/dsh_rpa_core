"""GUI 内嵌扩展通道网关契约测试（ADR 0014 §10 落地）。

背景：扩展轮询地址硬编码 127.0.0.1:8765；GUI 独立运行（无 devserver）时
浏览器指令的扩展通道无人承接，「打开网页」拉起浏览器却等不到插件上线
（browser_launch_no_host）。ExtLoopbackGateway 补齐这一环。

覆盖：
- 网关只挂 /api/ext/* 与 /api/capture/*，其余路由 404；
- 假扩展经网关完成「心跳上线 → 领命令 → 回结果」全链路；
- GUI _ensure_ext_hub：探测复用 devserver / 内嵌网关 / 端口占用回退；
- GUI 运行链路 E2E：navigate 经内嵌网关 + 假扩展真实跑通 succeeded。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from rpa_core.catalog import load_catalog  # noqa: E402
from rpa_core.cli import _commands_root  # noqa: E402
from rpa_core.devserver.server import ExtLoopbackGateway, probe_ext_hub  # noqa: E402


def _request(method: str, path: str, payload=None, base: str = ""):
    """与 test_extension_exec_channel 同款的最小 HTTP 助手（本套件既有惯例）。"""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    request = urllib.request.Request(
        f"{base}{path}", data=data, method=method, headers=headers
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


class FakeExtension:
    """扮演扩展 background：长轮询领命令 → 回调执行 → 回传结果。

    从 test_extension_exec_channel 拷贝（不跨模块 import：该模块顶层会拉起
    executors → pywinauto COM 初始化，与本文件的 Qt 进程同存会 0xC0000409）。
    """

    def __init__(self, base: str, handlers: dict | None = None,
                 host: str | None = "msedge"):
        import threading

        self.base = base
        self.handlers: dict = handlers or {}
        self.host = host
        self.seen: list[tuple[str, dict]] = []
        self._stop = False
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop = True
        self._thread.join(timeout=3)

    def _loop(self):
        query = "/api/ext/command/next?wait=1"
        if self.host:
            query += f"&host={self.host}"
        while not self._stop:
            status, payload = _request("GET", query, base=self.base)
            if status != 200:
                time.sleep(0.2)
                continue
            command = payload.get("command")
            if not command:
                continue
            self._handle(command)

    def _handle(self, command: dict):
        op = str(command.get("op") or "")
        args = command.get("args") or {}
        self.seen.append((op, args))
        handler = self.handlers.get(op)
        try:
            if handler is None:
                raise RuntimeError(f"fake extension: unsupported op {op}")
            value = handler(args) or {}
            body = {"id": command["id"], "ok": True, "value": value}
        except Exception as exc:  # noqa: BLE001 - 假扩展把异常转成错误结果
            body = {
                "id": command["id"],
                "ok": False,
                "error": {"code": "EXECUTOR_FAILED", "message": str(exc)},
            }
        _request("POST", "/api/ext/command/result", body, base=self.base)


@pytest.fixture(scope="module")
def qapp():
    from rpa_core.gui.app import build_application

    return build_application()


@pytest.fixture(scope="module")
def catalog(qapp):  # 依赖 qapp：Qt 控件构造前必须先有 QApplication
    return load_catalog(_commands_root())


@pytest.fixture()
def gateway(catalog, tmp_path):
    gw = ExtLoopbackGateway(
        catalog=catalog, workflows_root=tmp_path / "workflows", port=0
    )
    gw.start()
    try:
        yield gw
    finally:
        gw.stop()


# ---- 网关路由 -----------------------------------------------------------------
def test_gateway_serves_ext_routes(gateway):
    status, payload = _request(
        "GET", "/api/ext/command/next?wait=0", base=gateway.base_url
    )
    assert status == 200
    assert payload == {"command": None}
    status, payload = _request("GET", "/api/ext/status", base=gateway.base_url)
    assert status == 200
    assert "online" in payload


def test_gateway_rejects_non_ext_routes(gateway):
    for path in ("/", "/api/catalog", "/api/workflows", "/static/app.js"):
        status, _ = _request("GET", path, base=gateway.base_url)
        assert status == 404, path


def test_gateway_capture_extension_pending_route(gateway):
    """捕获通道同挂在网关上（/api/capture/extension/* 不被 404）。"""
    status, _ = _request(
        "GET", "/api/capture/extension/pending", base=gateway.base_url
    )
    assert status == 200


def test_probe_ext_hub(gateway):
    assert probe_ext_hub(gateway.base_url)
    assert not probe_ext_hub("http://127.0.0.1:9")  # 必然拒绝连接


def test_gateway_full_command_roundtrip(gateway):
    """假扩展经网关：心跳上报宿主 → 领取 tabs.create → 回结果。"""
    fake = FakeExtension(gateway.base_url, host="msedge")
    fake.start()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            _, status_payload = _request(
                "GET", "/api/ext/status", base=gateway.base_url
            )
            if status_payload.get("online"):
                break
            time.sleep(0.1)
        else:
            raise AssertionError("假扩展未在 5s 内上线")

        _ = _request(
            "POST",
            "/api/ext/command/submit",
            {"op": "tabs.create", "args": {"url": "https://a.test/", "active": True},
             "targetHost": "msedge"},
            base=gateway.base_url,
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not fake.seen:
            time.sleep(0.05)
        assert fake.seen and fake.seen[0][0] == "tabs.create"
    finally:
        fake.stop()


# ---- GUI 接线 ------------------------------------------------------------------
@pytest.fixture()
def window(catalog, tmp_path):
    from rpa_core.gui.app import MainWindow

    return MainWindow(catalog, workflows_root=tmp_path / "workflows")


def test_gui_reuses_running_devserver(window, monkeypatch):
    monkeypatch.setattr(
        "rpa_core.devserver.server.probe_ext_hub", lambda url: True
    )
    # probe 命中 → 直接复用，不启动内嵌网关
    from rpa_core.extension_exec import DEFAULT_HUB_URL

    assert window._ensure_ext_hub() == DEFAULT_HUB_URL
    assert window._ext_gateway is None


def test_gui_embeds_gateway_when_no_devserver(window, monkeypatch):
    monkeypatch.setattr(
        "rpa_core.devserver.server.probe_ext_hub", lambda url: False
    )
    url = window._ensure_ext_hub()
    assert window._ext_gateway is not None
    assert url == window._ext_gateway.base_url
    # 幂等：再次调用复用同一网关
    assert window._ensure_ext_hub() == url
    window._shutdown_run_manager()
    assert window._ext_gateway is None


def test_gui_falls_back_when_port_occupied(window, monkeypatch):
    monkeypatch.setattr(
        "rpa_core.devserver.server.probe_ext_hub", lambda url: False
    )

    class _Occupied:
        def __init__(self, **kwargs):
            raise OSError(10048, "address in use")

    monkeypatch.setattr(
        "rpa_core.devserver.server.ExtLoopbackGateway", _Occupied
    )
    from rpa_core.extension_exec import DEFAULT_HUB_URL

    assert window._ensure_ext_hub() == DEFAULT_HUB_URL
    assert window._ext_gateway is None
    assert "8765" in window.statusBar().currentMessage()


def test_gui_run_via_embedded_gateway(window, monkeypatch, tmp_path):
    """E2E：GUI 内嵌网关 + 假扩展，navigate 流程真实跑通（子进程经 RPA_EXT_HUB_URL 回连）。"""
    # 网关绑到随机端口（测试不占 8765），RunManager.hub_url 指向它
    from rpa_core.devserver.server import ExtLoopbackGateway

    gateway = ExtLoopbackGateway(
        catalog=window.catalog, workflows_root=window._store.root, port=0
    )
    gateway.start()
    monkeypatch.setattr(window, "_ensure_ext_hub", lambda: gateway.base_url)
    fake = FakeExtension(
        gateway.base_url,
        host="msedge",
        handlers={
            "tabs.create": lambda args: {
                "tabId": 41, "url": args["url"], "title": "demo",
            },
            "tabs.waitLoad": lambda args: {
                "tabId": args["tabId"], "url": "https://www.baidu.com/",
            },
        },
    )
    fake.start()
    try:
        document = {
            "schema_version": "1.0",
            "id": "t", "name": "t",
            "root": {
                "type": "sequence", "id": "root",
                "children": [
                    {"type": "action", "id": "open", "command": "browser.navigate",
                     "with": {"url": "baidu.com", "browserType": "msedge",
                              "timeoutMs": 10000}},
                ],
            },
        }
        window._store.write("t", document)
        window._open_named_flow("t")
        run_id = window._start_run("t")
        assert run_id is not None

        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            window._poll_run()
            if window.run_action.isEnabled():
                break
            time.sleep(0.2)
        else:
            raise AssertionError("运行超时未结束")

        assert "succeeded" in window._run_status_label.text(), (
            window._run_events_view.toPlainText()
        )
        # 无 scheme 的 url 被 executor 补全为 https
        assert fake.seen[0][1]["url"] == "https://baidu.com"
    finally:
        fake.stop()
        gateway.stop()
