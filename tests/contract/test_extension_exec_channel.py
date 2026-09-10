"""自研扩展执行通道合同测试（M15 Phase 1）。

用真实 HTTP 扮演扩展 background（长轮询领命令 + 回结果），覆盖：
- hub 协议：token 鉴权 / 命令下发回收 / 超时 / 在线心跳 / 权限（默认整浏览器 + tabs 收窄）
- 执行器：扩展会话路由（navigate / click / getText / attach / listPages）、
  缺省通道解析（扩展在线 → 优先走扩展）、bsk 式边界（不支持的命令显式报错）
"""

import asyncio
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from rpa_core.devserver import DevServer
from rpa_core.executors import PlaywrightExecutor
from rpa_core.executors.browser_ext import ExtensionExecSession
from rpa_core.extension_exec import ExtensionExecClient
from rpa_core.model.command import CommandInvocation

ROOT = Path(__file__).resolve().parents[2]
TOKEN = "tok-ext-1"


@pytest.fixture()
def server(tmp_path):
    dev = DevServer(
        commands_root=ROOT / "commands",
        workflows_root=tmp_path / "workflows",
        port=0,
    )
    dev.start()
    try:
        yield dev
    finally:
        dev.stop()


def _request(method: str, path: str, payload=None, base: str = "", token: str | None = None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    if token:
        headers["X-Capture-Token"] = token
    request = urllib.request.Request(f"{base}{path}", data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


class FakeExtension:
    """扮演扩展 background：长轮询领命令 → 回调执行 → 回传结果。"""

    def __init__(self, base: str, handlers: dict | None = None):
        self.base = base
        self.token = TOKEN
        self.handlers: dict = handlers or {}
        self.seen: list[tuple[str, dict]] = []
        self._stop = False
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stop = True
        self._thread.join(timeout=3)

    def _loop(self):
        while not self._stop:
            status, payload = _request(
                "GET", "/api/ext/command/next?wait=1", base=self.base, token=self.token
            )
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
        _request("POST", "/api/ext/command/result", body, base=self.base, token=self.token)


# ---------------------------------------------------------------- hub 协议


def test_command_polling_requires_token(server):
    base = f"http://127.0.0.1:{server.port}"
    status, _ = _request("GET", "/api/ext/command/next?wait=0", base=base)
    assert status == 403
    status, payload = _request(
        "GET", "/api/ext/command/next?wait=0", base=base, token=TOKEN
    )
    assert status == 200
    assert payload == {"command": None}


def test_submit_roundtrip_with_fake_extension(server):
    base = f"http://127.0.0.1:{server.port}"
    fake = FakeExtension(base, {"ping": lambda args: {"version": "0.2.0"}}).start()
    try:
        status, payload = _request(
            "POST",
            "/api/ext/command/submit",
            {"op": "ping", "timeoutSeconds": 5},
            base=base,
        )
        assert status == 200
        assert payload["ok"] is True
        assert payload["value"] == {"version": "0.2.0"}
        assert fake.seen[0][0] == "ping"
    finally:
        fake.stop()


def test_submit_timeout_without_extension(server):
    base = f"http://127.0.0.1:{server.port}"
    status, payload = _request(
        "POST",
        "/api/ext/command/submit",
        {"op": "ping", "timeoutSeconds": 0.3},
        base=base,
    )
    assert status == 200
    assert payload["ok"] is False
    assert payload["timedOut"] is True
    assert payload["error"]["code"] == "TIMEOUT"


def test_status_reports_online_after_poll(server):
    base = f"http://127.0.0.1:{server.port}"
    status, payload = _request("GET", "/api/ext/status", base=base)
    assert payload["online"] is False
    assert payload["permissions"] == {"mode": "browser"}  # 默认整个浏览器
    _request("GET", "/api/ext/command/next?wait=0", base=base, token=TOKEN)
    status, payload = _request("GET", "/api/ext/status", base=base)
    assert payload["online"] is True
    assert payload["lastPollSecondsAgo"] is not None


def test_permissions_default_browser_then_tabs_scope(server):
    base = f"http://127.0.0.1:{server.port}"
    status, payload = _request("GET", "/api/ext/permissions", base=base)
    assert payload == {"mode": "browser"}

    status, payload = _request(
        "POST", "/api/ext/permissions", {"mode": "tabs", "tabIds": ["7"]}, base=base
    )
    assert status == 200
    assert payload == {"mode": "tabs", "tabIds": ["7"]}

    # 收窄后：范围外的 tabId 被宿主拒绝（无需扩展参与）
    status, payload = _request(
        "POST",
        "/api/ext/command/submit",
        {"op": "page.call", "args": {"tabId": "8"}, "timeoutSeconds": 0.3},
        base=base,
    )
    assert payload["ok"] is False
    assert payload["error"]["code"] == "PERMISSION_DENIED"

    status, payload = _request(
        "POST", "/api/ext/permissions", {"mode": "bogus"}, base=base
    )
    assert status == 400
    assert payload["error"] == "BAD_REQUEST"


# ---------------------------------------------------------------- 执行器侧


def _invocation(command_id: str, inputs: dict) -> CommandInvocation:
    return CommandInvocation(
        command_id=command_id,
        command_version="1.0.0",
        run_id="run",
        step_id="step",
        inputs=inputs,
    )


def _executor(base: str) -> PlaywrightExecutor:
    client = ExtensionExecClient(base_url=base)
    return PlaywrightExecutor(ext_session=ExtensionExecSession(client=client))


def _wait_online(base: str, timeout: float = 5.0) -> None:
    """等假扩展完成首次长轮询（宿主 online 判定基于最近一次轮询时间）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, payload = _request("GET", "/api/ext/status", base=base)
        if status == 200 and payload.get("online"):
            return
        time.sleep(0.05)
    raise AssertionError("fake extension did not come online")


_DEFAULT_HANDLERS = {
    "tabs.create": lambda args: {"tabId": 41, "url": args["url"], "title": "demo"},
    "tabs.navigate": lambda args: {"tabId": args["tabId"], "url": args["url"]},
    "tabs.history": lambda args: {"tabId": args["tabId"], "url": "https://a.test/back"},
    "tabs.list": lambda args: {
        "tabs": [
            {"id": 41, "index": 0, "url": "https://a.test/one", "title": "one", "active": True},
            {"id": 42, "index": 1, "url": "https://b.test/two", "title": "two", "active": False},
        ]
    },
    "page.call": lambda args: {
        "click": {"matchedCount": 1, "result": True},
        "hover": {"matchedCount": 1, "result": True},
        "input": {"matchedCount": 1, "result": "hello"},
        "getText": {"matchedCount": 1, "result": "页面标题"},
        "count": {"matchedCount": 1, "result": 1},
        "scroll": {"matchedCount": 1, "result": 320},
        "select": {"matchedCount": 1, "result": "v2"},
        "check": {"matchedCount": 1, "result": True},
    }.get(args["method"], {"matchedCount": 0, "result": None}),
    "page.eval": lambda args: {"result": 42},
    "cookies.get": lambda args: {"value": "cookie-value"},
}


def test_executor_navigate_prefers_extension_when_online(server):
    """缺省 transport：扩展在线 → 优先走扩展（一等公民），sessionId 属扩展会话。"""
    base = f"http://127.0.0.1:{server.port}"
    fake = FakeExtension(base, dict(_DEFAULT_HANDLERS)).start()
    executor = _executor(base)
    _wait_online(base)

    async def go():
        try:
            result = await executor.execute(
                _invocation("browser.navigate", {"url": "https://a.test/one"}), asyncio.Event()
            )
            assert result.status == "success", result.error
            assert result.outputs["url"] == "https://a.test/one"
            assert result.outputs["resourceType"] == "webPage"
            session_id = result.outputs["sessionId"]

            # 后续命令按会话归属继续走扩展通道
            clicked = await executor.execute(
                _invocation("browser.click", {"sessionId": session_id, "selector": "#go"}),
                asyncio.Event(),
            )
            assert clicked.status == "success", clicked.error
            assert clicked.outputs["matchedCount"] == 1

            text = await executor.execute(
                _invocation("browser.getText", {"sessionId": session_id, "selector": "h1"}),
                asyncio.Event(),
            )
            assert text.status == "success", text.error
            assert text.outputs["value"] == "页面标题"

            history = await executor.execute(
                _invocation("browser.navigate", {"sessionId": session_id, "action": "reload"}),
                asyncio.Event(),
            )
            assert history.status == "success", history.error
        finally:
            await executor.close()

    try:
        asyncio.run(go())
        assert ("tabs.create", {"url": "https://a.test/one", "active": True}) in fake.seen
        assert ("tabs.history", {"tabId": "41", "action": "reload"}) in fake.seen
        assert [op for op, _ in fake.seen][:1] == ["tabs.create"]
    finally:
        fake.stop()


def test_executor_attach_matches_existing_tab(server):
    base = f"http://127.0.0.1:{server.port}"
    fake = FakeExtension(base, dict(_DEFAULT_HANDLERS)).start()
    executor = _executor(base)
    _wait_online(base)

    async def go():
        try:
            opened = await executor.execute(
                _invocation("browser.navigate", {"url": "https://a.test/one"}), asyncio.Event()
            )
            assert opened.status == "success", opened.error
            anchor = opened.outputs["sessionId"]

            attached = await executor.execute(
                _invocation(
                    "browser.attach",
                    {"sessionId": anchor, "pattern": "b.test", "matchBy": "url"},
                ),
                asyncio.Event(),
            )
            assert attached.status == "success", attached.error
            assert attached.outputs["url"] == "https://b.test/two"

            listed = await executor.execute(
                _invocation("browser.listPages", {"sessionId": anchor}), asyncio.Event()
            )
            assert listed.status == "success", listed.error
            assert listed.outputs["count"] == 2

            missing = await executor.execute(
                _invocation(
                    "browser.attach",
                    {"sessionId": anchor, "pattern": "nope.test", "matchBy": "url"},
                ),
                asyncio.Event(),
            )
            assert missing.status == "error"
            assert missing.error.code == "ELEMENT_NOT_FOUND"
        finally:
            await executor.close()

    try:
        asyncio.run(go())
    finally:
        fake.stop()


def test_executor_extension_boundary_and_offline_fallback(server):
    """扩展通道：未支持命令显式报错；扩展离线时缺省通道不再走扩展。"""
    base = f"http://127.0.0.1:{server.port}"
    fake = FakeExtension(base, dict(_DEFAULT_HANDLERS)).start()
    executor = _executor(base)
    _wait_online(base)

    async def go():
        try:
            opened = await executor.execute(
                _invocation("browser.navigate", {"url": "https://a.test/one"}), asyncio.Event()
            )
            assert opened.status == "success", opened.error
            session_id = opened.outputs["sessionId"]

            upload = await executor.execute(
                _invocation(
                    "browser.upload",
                    {"sessionId": session_id, "selector": "#f", "files": ["a"]},
                ),
                asyncio.Event(),
            )
            assert upload.status == "error"
            assert upload.error.code == "COMMAND_NOT_FOUND"

            closed = await executor.execute(
                _invocation("browser.close", {"sessionId": session_id}), asyncio.Event()
            )
            assert closed.status == "success"  # 解绑，不代关用户标签页
        finally:
            await executor.close()

    try:
        asyncio.run(go())
    finally:
        fake.stop()

    # 扩展离线（无 devserver/扩展在监听）：缺省 transport 不再走扩展，回退 playwright 分支
    offline = _executor("http://127.0.0.1:9")

    async def offline_check():
        try:
            assert await offline._use_extension({}) is False
            assert await offline._use_extension({"transport": "extension"}) is True
        finally:
            await offline.close()

    asyncio.run(offline_check())
