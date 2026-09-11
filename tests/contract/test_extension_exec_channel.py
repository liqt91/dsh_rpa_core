"""自研扩展执行通道合同测试（M15 Phase 1）。

用真实 HTTP 扮演扩展 background（长轮询领命令 + 回结果），覆盖：
- hub 协议：无鉴权长轮询 / 命令下发回收 / 超时 / 在线心跳 / 权限（默认整浏览器 + tabs 收窄）
- 执行器：扩展会话路由（navigate / click / getText / attach / listPages）、
  缺省通道解析（扩展在线 → 优先走扩展）、通道边界（不支持的命令显式报错）
"""

import asyncio
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from rpa_core.devserver import DevServer
from rpa_core.executors import PlaywrightExecutor
from rpa_core.executors.browser_ext import ExtensionExecSession
from rpa_core.extension_exec import ExtensionExecClient
from rpa_core.model.command import CommandInvocation

ROOT = Path(__file__).resolve().parents[2]


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


def _request(method: str, path: str, payload=None, base: str = ""):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    request = urllib.request.Request(f"{base}{path}", data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


class FakeExtension:
    """扮演扩展 background：长轮询领命令 → 回调执行 → 回传结果。

    `host`：宿主浏览器标识（随长轮询 query 上报，None = 不上报，模拟旧版扩展）。
    """

    def __init__(self, base: str, handlers: dict | None = None, host: str | None = "msedge",
                 instance_id: str | None = None):
        self.base = base
        self.handlers: dict = handlers or {}
        self.host = host
        self.instance_id = instance_id
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
        query = "/api/ext/command/next?wait=1"
        if self.host:
            query += f"&host={self.host}&ua={urllib.parse.quote('Mozilla/5.0 ' + self.host)}"
        if self.instance_id:
            query += f"&iid={self.instance_id}"
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


# ---------------------------------------------------------------- hub 协议


def test_command_polling_without_token(server):
    """移除配对后：长轮询无鉴权，直接可访问（devserver 仅绑定 127.0.0.1）。"""
    base = f"http://127.0.0.1:{server.port}"
    status, payload = _request("GET", "/api/ext/command/next?wait=0", base=base)
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
    _request("GET", "/api/ext/command/next?wait=0", base=base)
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


# ---------------------------------------------------- 通道 / 宿主浏览器身份


def test_status_reports_host_browser_after_poll(server):
    """宿主浏览器身份随长轮询上报：/api/ext/status 暴露 host（状态展示用）。"""
    base = f"http://127.0.0.1:{server.port}"
    fake = FakeExtension(base, dict(_DEFAULT_HANDLERS), host="msedge").start()
    try:
        _wait_online(base)
        deadline = time.monotonic() + 3
        payload = {}
        while time.monotonic() < deadline:
            _, payload = _request("GET", "/api/ext/status", base=base)
            if payload.get("host"):
                break
            time.sleep(0.05)
        assert payload["host"]["browser"] == "msedge"
        assert str(payload["host"]["userAgent"]).startswith("Mozilla/5.0")
    finally:
        fake.stop()


def test_legacy_extension_without_host_report_stays_online(server):
    """旧版扩展不上报宿主身份：在线判定不受影响，host 为 None。"""
    base = f"http://127.0.0.1:{server.port}"
    fake = FakeExtension(base, dict(_DEFAULT_HANDLERS), host=None).start()
    try:
        _wait_online(base)
        _, payload = _request("GET", "/api/ext/status", base=base)
        assert payload["online"] is True
        assert payload["host"] is None
    finally:
        fake.stop()


def test_executor_extension_offline_fails_fast_with_actionable_error(server):
    """扩展通道离线：立即失败（不白等 timeoutMs），并给出可照着做的排查步骤。

    回归点：命令是「入队等扩展来领」，离线时原先要躺满 timeoutMs 才以 TIMEOUT 收场，
    用户看到的只是"执行后没有打开浏览器"，无从判断是插件没装、浏览器没开还是配错了。
    """
    base = f"http://127.0.0.1:{server.port}"
    executor = _executor(base)

    async def go():
        try:
            started = time.monotonic()
            result = await executor.execute(
                _invocation("browser.navigate", {"url": "https://a.test/one"}), asyncio.Event()
            )
            elapsed = time.monotonic() - started
            assert result.status == "error", result.outputs
            assert result.error.code == "EXECUTOR_FAILED"
            assert "浏览器执行通道当前离线" in result.error.message
            assert "chrome://extensions" in result.error.message  # 给出可操作排查方向
            assert result.error.details["reason"] == "channel_offline"
            assert elapsed < 5.0, f"离线未快速失败，耗时 {elapsed:.1f}s"
        finally:
            await executor.close()

    asyncio.run(go())


# ---------------------------------------------------- 多浏览器（targetHost）路由


def test_status_reports_hosts_list_for_multiple_browsers(server):
    """多个浏览器的扩展各自上报身份：/api/ext/status 的 hosts 列出在线浏览器名。"""
    base = f"http://127.0.0.1:{server.port}"
    edge = FakeExtension(base, dict(_DEFAULT_HANDLERS), host="msedge").start()
    chrome = FakeExtension(base, dict(_DEFAULT_HANDLERS), host="chrome").start()
    try:
        deadline = time.monotonic() + 5
        hosts: list[str] = []
        while time.monotonic() < deadline and sorted(hosts) != ["chrome", "msedge"]:
            _, payload = _request("GET", "/api/ext/status", base=base)
            hosts = [str(h) for h in (payload.get("hosts") or [])]
            time.sleep(0.05)
        assert sorted(hosts) == ["chrome", "msedge"]
    finally:
        edge.stop()
        chrome.stop()


def test_submit_routes_by_target_host(server):
    """targetHost 明确时，命令仅由对应浏览器的扩展领取（不串台）。"""
    base = f"http://127.0.0.1:{server.port}"
    edge = FakeExtension(base, {"ping": lambda args: {"who": "edge"}}, host="msedge").start()
    chrome = FakeExtension(base, {"ping": lambda args: {"who": "chrome"}}, host="chrome").start()
    try:
        # 等待两个扩展均在线，避免命中 targetHost 快失败
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            _, payload = _request("GET", "/api/ext/status", base=base)
            if sorted(payload.get("hosts") or []) == ["chrome", "msedge"]:
                break
            time.sleep(0.05)

        _, edge_val = _request(
            "POST",
            "/api/ext/command/submit",
            {"op": "ping", "targetHost": "msedge", "timeoutSeconds": 5},
            base=base,
        )
        assert edge_val["ok"] is True and edge_val["value"]["who"] == "edge"

        _, chrome_val = _request(
            "POST",
            "/api/ext/command/submit",
            {"op": "ping", "targetHost": "chrome", "timeoutSeconds": 5},
            base=base,
        )
        assert chrome_val["ok"] is True and chrome_val["value"]["who"] == "chrome"
    finally:
        edge.stop()
        chrome.stop()


def test_submit_target_host_offline_fast_fails(server):
    """明确 targetHost 但目标从未来过（既非浏览器名也非实例 id）→ TARGET_HOST_OFFLINE 快失败。

    区别于「有活跃宿主但目标插件休眠到无人领取」（那类仍入队到超时 TIMEOUT）。
    """
    base = f"http://127.0.0.1:{server.port}"
    started = time.monotonic()
    status, payload = _request(
        "POST",
        "/api/ext/command/submit",
        {"op": "ping", "targetHost": "chrome", "timeoutSeconds": 1},
        base=base,
    )
    elapsed = time.monotonic() - started
    assert status == 200
    assert payload["ok"] is False
    assert payload["error"]["code"] == "TARGET_HOST_OFFLINE"
    assert elapsed < 0.9, f"目标从未在线应快速失败，却耗时 {elapsed:.1f}s"


def test_executor_navigate_with_browser_type_routes_to_chrome(server):
    """browser.navigate 带 browserType=chrome → 命令路由到 Chrome 扩展（不误落 Edge）。"""
    base = f"http://127.0.0.1:{server.port}"
    chrome = FakeExtension(base, dict(_DEFAULT_HANDLERS), host="chrome").start()
    executor = _executor(base)
    try:
        # 等 chrome 上线（此时 Edge 无插件在线）
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            _, payload = _request("GET", "/api/ext/status", base=base)
            if "chrome" in (payload.get("hosts") or []):
                break
            time.sleep(0.05)

        async def go():
            try:
                result = await executor.execute(
                    _invocation(
                        "browser.navigate",
                        {"url": "https://a.test/one", "browserType": "chrome"},
                    ),
                    asyncio.Event(),
                )
                assert result.status == "success", result.error
                assert result.outputs["url"] == "https://a.test/one"
            finally:
                await executor.close()

        asyncio.run(go())
        assert ("tabs.create", {"url": "https://a.test/one", "active": True}) in chrome.seen
    finally:
        chrome.stop()


def test_executor_navigate_with_default_browser_type_routes_to_edge(server):
    """browser.navigate 未显式给 browserType → 默认 msedge，路由到 Edge 扩展。"""
    base = f"http://127.0.0.1:{server.port}"
    edge = FakeExtension(base, dict(_DEFAULT_HANDLERS), host="msedge").start()
    executor = _executor(base)
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            _, payload = _request("GET", "/api/ext/status", base=base)
            if "msedge" in (payload.get("hosts") or []):
                break
            time.sleep(0.05)

        async def go():
            try:
                result = await executor.execute(
                    _invocation("browser.navigate", {"url": "https://a.test/one"}),
                    asyncio.Event(),
                )
                assert result.status == "success", result.error
            finally:
                await executor.close()

        asyncio.run(go())
        assert ("tabs.create", {"url": "https://a.test/one", "active": True}) in edge.seen
    finally:
        edge.stop()


# ---------------------------------------------------- 加载超时后执行（onTimeout）


def test_executor_navigate_timeout_on_error_fails(server):
    """页面未在 timeoutMs 内加载完成且 onTimeout=error（默认）→ 视为超时失败。"""
    base = f"http://127.0.0.1:{server.port}"
    handlers = dict(_DEFAULT_HANDLERS)
    handlers["tabs.create"] = lambda args: {
        "tabId": 99, "url": args["url"], "title": "", "completed": False, "timedOut": True,
    }
    fake = FakeExtension(base, handlers, host="msedge").start()
    executor = _executor(base)
    try:
        _wait_online(base)

        async def go():
            try:
                result = await executor.execute(
                    _invocation(
                        "browser.navigate",
                        {"url": "https://a.test/one", "timeoutMs": 500, "onTimeout": "error"},
                    ),
                    asyncio.Event(),
                )
                assert result.status == "error", result.outputs
                assert result.error.code == "TIMEOUT"
                assert "页面加载超时" in result.error.message
                assert result.error.details["reason"] == "navigate_load_timeout"
            finally:
                await executor.close()

        asyncio.run(go())
    finally:
        fake.stop()


def test_executor_navigate_timeout_on_stop_continues(server):
    """页面加载超时且 onTimeout=stop → 停止网页加载并继续（成功）。"""
    base = f"http://127.0.0.1:{server.port}"
    handlers = dict(_DEFAULT_HANDLERS)
    handlers["tabs.create"] = lambda args: {
        "tabId": 99, "url": args["url"], "title": "", "completed": False, "timedOut": True,
    }
    handlers["tabs.stopLoading"] = lambda args: {"url": args.get("tabId") and "https://a.test/one"}
    fake = FakeExtension(base, handlers, host="msedge").start()
    executor = _executor(base)
    try:
        _wait_online(base)

        async def go():
            try:
                result = await executor.execute(
                    _invocation(
                        "browser.navigate",
                        {"url": "https://a.test/one", "timeoutMs": 500, "onTimeout": "stop"},
                    ),
                    asyncio.Event(),
                )
                assert result.status == "success", result.error
                assert result.outputs["url"] == "https://a.test/one"
            finally:
                await executor.close()

        asyncio.run(go())
        assert ("tabs.stopLoading", {"tabId": "99"}) in fake.seen
    finally:
        fake.stop()


# ---------------------------------------------------- 同浏览器多实例（instanceId）路由


def _wait_instances(base: str, want: set[str], timeout: float = 5.0) -> None:
    """等 /api/ext/status 的 instances 齐集指定 instanceId 集合。

    同浏览器多实例在线时，hosts（去重浏览器名）无法区分，只能按 instances 判断。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _, payload = _request("GET", "/api/ext/status", base=base)
        alive = {
            str(inst.get("instanceId"))
            for inst in (payload.get("instances") or [])
            if inst.get("instanceId")
        }
        if want <= alive:
            return
        time.sleep(0.05)
    raise AssertionError(f"instances {want} not all online, got {alive!r}")


def test_submit_routes_by_instance_id_same_browser(server):
    """同浏览器（均 msedge）多实例：按 instanceId 精确路由，互不串台。

    这正是「一个流程分别操作两个 Edge profile」的基础：hosts 去重成同一个 msedge，
    只有实例 id 能把命令送到正确的那个实例。
    """
    base = f"http://127.0.0.1:{server.port}"
    edge_a = FakeExtension(
        base, {"ping": lambda args: {"who": "edge-a"}}, host="msedge", instance_id="edge-a"
    ).start()
    edge_b = FakeExtension(
        base, {"ping": lambda args: {"who": "edge-b"}}, host="msedge", instance_id="edge-b"
    ).start()
    try:
        _wait_instances(base, {"edge-a", "edge-b"})

        _, a_val = _request(
            "POST",
            "/api/ext/command/submit",
            {"op": "ping", "targetHost": "edge-a", "timeoutSeconds": 5},
            base=base,
        )
        assert a_val["ok"] is True and a_val["value"]["who"] == "edge-a"

        _, b_val = _request(
            "POST",
            "/api/ext/command/submit",
            {"op": "ping", "targetHost": "edge-b", "timeoutSeconds": 5},
            base=base,
        )
        assert b_val["ok"] is True and b_val["value"]["who"] == "edge-b"
    finally:
        edge_a.stop()
        edge_b.stop()


def test_executor_navigate_outputs_browser_instance_fields(server):
    """browser.navigate 输出三字段：browserInstance（实例级唯一 id）/ browserType / tabId。

    browserInstance 来自 Hub 给 tabs.create 结果补带的 instanceId（执行该命令的实例）；
    后续元素操作按该实例路由，而非笼统的浏览器名。
    """
    base = f"http://127.0.0.1:{server.port}"
    fake = FakeExtension(
        base, dict(_DEFAULT_HANDLERS), host="msedge", instance_id="edge-a"
    ).start()
    executor = _executor(base)
    try:
        _wait_instances(base, {"edge-a"})

        async def go():
            try:
                result = await executor.execute(
                    _invocation("browser.navigate", {"url": "https://a.test/one"}),
                    asyncio.Event(),
                )
                assert result.status == "success", result.error
                assert result.outputs["browserInstance"] == "edge-a"
                assert result.outputs["browserType"] == "msedge"
                assert result.outputs["tabId"] == "41"
                session_id = result.outputs["sessionId"]

                # 会话绑定实例后，后续操作按实例 id 精确路由
                clicked = await executor.execute(
                    _invocation("browser.click", {"sessionId": session_id, "selector": "#go"}),
                    asyncio.Event(),
                )
                assert clicked.status == "success", clicked.error
            finally:
                await executor.close()

        asyncio.run(go())
    finally:
        fake.stop()
