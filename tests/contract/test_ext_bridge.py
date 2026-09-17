"""扩展 bridge host 合同测试（M20 / ADR 0015 S2）。

真实 spawn ``python -m rpa_core.workers.ext_bridge`` 子进程，测试进程扮演两个角色：
① 扩展（驱动 host 的 stdin/stdout Native Messaging 帧）；
② 执行器客户端（经本地端点连接 host）。
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from rpa_core import local_transport as lt
from rpa_core.local_transport import Channel, read_message, write_message


class _HostProc:
    """host 子进程 + 假扩展（驱动 stdio 帧）。"""

    def __init__(self, browser: str = "msedge", instance_id: str = ""):
        self.browser = browser
        self.instance_id = instance_id or f"contract-{os.getpid()}-{int(time.time() * 1000)}"
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "rpa_core.workers.ext_bridge"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=str(_repo_root()),
        )
        assert self.proc.stdin is not None and self.proc.stdout is not None
        self.endpoint = lt.endpoint_name(self.browser, self.instance_id)
        self.ready: dict[str, Any] = {}

    def handshake(self, **extra: Any) -> dict[str, Any]:
        self.to_extension(
            {
                "type": "hello",
                "browser": self.browser,
                "instanceId": self.instance_id,
                "extVersion": "0.3.0",
                **extra,
            }
        )
        self.ready = self.from_extension(timeout=15.0)
        return self.ready

    def to_extension(self, payload: dict[str, Any]) -> None:
        write_message(self.proc.stdin, payload)

    def from_extension(self, timeout: float = 15.0) -> dict[str, Any]:
        return _call_with_timeout(lambda: read_message(self.proc.stdout), timeout)

    def connect(self, timeout: float = 15.0) -> Channel:
        deadline = time.monotonic() + timeout
        last: Exception | None = None
        while time.monotonic() < deadline:
            try:
                return lt.connect(self.endpoint, timeout=0.5)
            except lt.LocalTransportError as exc:
                last = exc
                time.sleep(0.05)
        raise AssertionError(f"endpoint never became available: {last}")

    def close(self) -> None:
        try:
            if self.proc.stdin is not None:
                self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover - 兜底
            self.proc.kill()
            self.proc.wait(timeout=5)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _call_with_timeout(fn, timeout: float):
    """在守护线程里执行阻塞调用，超时抛断言——避免管道误挂拖死门禁。"""
    result: queue.Queue = queue.Queue(maxsize=1)

    def work() -> None:
        try:
            result.put(("ok", fn()))
        except Exception as exc:  # noqa: BLE001
            result.put(("err", exc))

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    try:
        kind, value = result.get(timeout=timeout)
    except queue.Empty:
        raise AssertionError(f"operation timed out after {timeout}s") from None
    if kind == "err":
        raise value
    return value


def _recv(channel: Channel, timeout: float = 15.0) -> dict[str, Any]:
    return _call_with_timeout(channel.recv, timeout)


def _sync(channel: Channel) -> None:
    """连接返回 ≠ host 已 accept 注册；用一次 status 往返确认已登记。"""
    channel.send({"type": "status"})
    assert _recv(channel)["online"] is True


@pytest.fixture()
def host():
    node = _HostProc()
    try:
        yield node
    finally:
        node.close()


# -- 握手与端点 ----------------------------------------------------------------


def test_handshake_binds_endpoint_and_reports_identity(host):
    ready = host.handshake()
    assert ready["type"] == "ready"
    assert ready["browser"] == "msedge"
    assert ready["instanceId"] == host.instance_id
    assert ready["endpoint"] == host.endpoint
    assert isinstance(ready["pid"], int) and ready["pid"] > 0
    assert host.endpoint in lt.list_endpoints()


def test_client_status_reports_online(host):
    host.handshake()
    with host.connect() as client:
        client.send({"type": "status"})
        status = _recv(client)
    assert status["type"] == "status"
    assert status["online"] is True
    assert status["browser"] == "msedge"
    assert status["instanceId"] == host.instance_id


def test_submit_is_relayed_and_result_routed_back(host):
    host.handshake()
    with host.connect() as client:
        client.send(
            {
                "type": "submit",
                "id": "cmd-1",
                "op": "tabs.list",
                "args": {},
                "timeoutSeconds": 5,
            }
        )
        forwarded = host.from_extension(timeout=15.0)
        assert forwarded["type"] == "command"
        assert forwarded["id"] == "cmd-1"
        assert forwarded["op"] == "tabs.list"
        host.to_extension(
            {"type": "result", "id": "cmd-1", "ok": True, "value": {"tabs": []}}
        )
        result = _recv(client)
    assert result == {"type": "result", "id": "cmd-1", "ok": True, "value": {"tabs": []}}


def test_submit_timeout_is_enforced_by_host(host):
    host.handshake()
    with host.connect() as client:
        client.send(
            {
                "type": "submit",
                "id": "cmd-slow",
                "op": "tabs.list",
                "args": {},
                "timeoutSeconds": 0.3,
            }
        )
        assert host.from_extension(timeout=15.0)["type"] == "command"
        result = _recv(client, timeout=15.0)
        assert result["ok"] is False
        assert result["timedOut"] is True
        assert result["error"]["code"] == "TIMEOUT"
        # 超时后 host 会 best-effort 通知扩展取消
        cancel = host.from_extension(timeout=15.0)
        assert cancel == {"type": "cancel", "id": "cmd-slow"}


def test_capture_arm_is_passthrough(host):
    host.handshake()
    with host.connect() as client:
        client.send({"type": "capture_arm", "sessionId": "s1"})
        forwarded = host.from_extension(timeout=15.0)
    assert forwarded == {"type": "capture_arm", "sessionId": "s1"}


def test_extension_initiated_messages_broadcast_to_clients(host):
    host.handshake()
    with host.connect() as first, host.connect() as second:
        _sync(first)
        _sync(second)
        host.to_extension({"type": "capture_result", "descriptor": {"selector": "#a"}})
        assert _recv(first)["type"] == "capture_result"
        assert _recv(second)["type"] == "capture_result"


def test_concurrent_clients_are_routed_by_command_id(host):
    host.handshake()
    with host.connect() as first, host.connect() as second:
        _sync(first)
        _sync(second)
        first.send({"type": "submit", "id": "a-1", "op": "x", "args": {}})
        second.send({"type": "submit", "id": "b-1", "op": "y", "args": {}})
        seen = {
            host.from_extension(timeout=15.0)["id"],
            host.from_extension(timeout=15.0)["id"],
        }
        assert seen == {"a-1", "b-1"}
        host.to_extension({"type": "result", "id": "b-1", "ok": True, "value": {"who": "b"}})
        host.to_extension({"type": "result", "id": "a-1", "ok": True, "value": {"who": "a"}})
        assert _recv(second)["value"] == {"who": "b"}
        assert _recv(first)["value"] == {"who": "a"}


def test_unsupported_client_message_returns_error(host):
    host.handshake()
    with host.connect() as client:
        client.send({"type": "nonsense"})
        reply = _recv(client)
    assert reply["type"] == "error"
    assert reply["error"]["code"] == "INVALID_INPUT"


def test_extension_eof_shuts_host_down_and_removes_endpoint(host):
    host.handshake()
    endpoint = host.endpoint
    with host.connect() as client:
        client.send({"type": "status"})
        assert _recv(client)["online"] is True
    host.proc.stdin.close()
    assert host.proc.wait(timeout=10) == 0
    _wait_until(lambda: endpoint not in lt.list_endpoints())


def _wait_until(predicate, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("condition not met in time")


# -- 执行器侧客户端（ExtensionExecClient，S5）---------------------------------


def test_exec_client_status_reports_online(host):
    from rpa_core.extension_exec import ExtensionExecClient

    host.handshake()
    client = ExtensionExecClient()
    status = client.status()
    assert status["online"] is True
    assert status["hosts"] == ["msedge"]
    assert client.online() is True
    assert client.host()["browser"] == "msedge"


def test_client_submit_roundtrip(host):
    from rpa_core.extension_exec import ExtensionExecClient

    host.handshake()
    client = ExtensionExecClient()
    box: dict = {}

    def do_submit():
        box["value"] = client.submit("tabs.list", {}, timeout_seconds=10)

    thread = threading.Thread(target=do_submit, daemon=True)
    thread.start()
    command = host.from_extension()
    assert command["type"] == "command"
    assert command["op"] == "tabs.list"
    host.to_extension(
        {"type": "result", "id": command["id"], "ok": True, "value": {"tabs": [{"id": 1}]}}
    )
    thread.join(timeout=10)
    assert box["value"] == {"tabs": [{"id": 1}]}


def test_client_submit_propagates_extension_error(host):
    from rpa_core.extension_exec import ExtensionChannelError, ExtensionExecClient

    host.handshake()
    client = ExtensionExecClient()
    box: dict = {}

    def do_submit():
        try:
            client.submit("page.call", {"method": "click"}, timeout_seconds=10)
        except ExtensionChannelError as exc:
            box["error"] = exc

    thread = threading.Thread(target=do_submit, daemon=True)
    thread.start()
    command = host.from_extension()
    host.to_extension(
        {
            "type": "result",
            "id": command["id"],
            "ok": False,
            "error": {"code": "ELEMENT_NOT_FOUND", "message": "no element"},
        }
    )
    thread.join(timeout=10)
    assert box["error"].code == "ELEMENT_NOT_FOUND"


def test_client_target_host_offline_fails_fast(host):
    from rpa_core.extension_exec import ExtensionChannelError, ExtensionExecClient

    host.handshake()  # 只有 msedge 端点
    client = ExtensionExecClient()
    started = time.monotonic()
    with pytest.raises(ExtensionChannelError) as excinfo:
        client.submit("tabs.list", {}, timeout_seconds=10, target_host="chrome")
    assert excinfo.value.code == "TARGET_HOST_OFFLINE"
    assert time.monotonic() - started < 3.0


def test_client_routes_by_target_host():
    from rpa_core.extension_exec import ExtensionExecClient

    edge_host = _HostProc(browser="msedge", instance_id="route-edge")
    chrome_host = _HostProc(browser="chrome", instance_id="route-chrome")
    try:
        edge_host.handshake()
        chrome_host.handshake()
        client = ExtensionExecClient()
        assert set(client.status()["hosts"]) == {"msedge", "chrome"}
        box: dict = {}

        def do_submit():
            box["value"] = client.submit(
                "tabs.list", {}, timeout_seconds=10, target_host="chrome"
            )

        thread = threading.Thread(target=do_submit, daemon=True)
        thread.start()
        # 命令只应到达 chrome 宿主
        command = chrome_host.from_extension()
        chrome_host.to_extension(
            {"type": "result", "id": command["id"], "ok": True, "value": {"who": "chrome"}}
        )
        thread.join(timeout=10)
        assert box["value"] == {"who": "chrome"}
    finally:
        edge_host.close()
        chrome_host.close()


def test_client_tabs_create_carries_instance_id(host):
    from rpa_core.extension_exec import ExtensionExecClient

    host.handshake()
    client = ExtensionExecClient()
    box: dict = {}

    def do_submit():
        box["value"] = client.submit("tabs.create", {"url": "https://a.test/"}, timeout_seconds=10)

    thread = threading.Thread(target=do_submit, daemon=True)
    thread.start()
    command = host.from_extension()
    host.to_extension(
        {
            "type": "result",
            "id": command["id"],
            "ok": True,
            "value": {"tabId": 7, "url": "https://a.test/"},
        }
    )
    thread.join(timeout=10)
    assert box["value"]["instanceId"] == host.instance_id


def test_client_offline_without_endpoint():
    from rpa_core.extension_exec import ExtensionChannelError, ExtensionExecClient

    client = ExtensionExecClient()
    assert client.online() is False
    with pytest.raises(ExtensionChannelError) as excinfo:
        client.submit("tabs.list", {}, timeout_seconds=1)
    assert excinfo.value.code == "CHANNEL_OFFLINE"
