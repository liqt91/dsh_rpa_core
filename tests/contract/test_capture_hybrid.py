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
    available = True  # 类属性：子类置 False 模拟非 Windows（agent 为 UIA 实现）

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.cancelled = False
        self.pick_calls = 0
        self.result = dict(_DESKTOP_DESCRIPTOR)
        self.pick_delay = 0.0
        FakeDesktopSession.instances.append(self)

    def pick(self, timeout_seconds=90):
        self.pick_calls += 1
        if self.pick_delay:
            time.sleep(self.pick_delay)
        return dict(self.result)

    def cancel(self):
        self.cancelled = True

    def close(self):
        pass


class UnavailableDesktopSession(FakeDesktopSession):
    """桌面腿不可用（等价于非 Windows：agent 是 Windows-only UIA 实现）。"""

    available = False


class FakeBridge:
    """扮演 bridge host 的扩展腿；``delay=None`` 表示永不回结果（桌面先赢用）。

    ``send_ack=False`` 模拟**残留 host**：端点连得上、``capture_arm`` 也收得到，
    但没有扩展在听——既不发 ``capture_armed``（ack），也不发任何结果。这正是
    维护者 2026-09-28 那个现场的形状（端点在线 ⇒ 判「扩展在线」⇒ 静默等满超时）。
    """

    def __init__(self, *, delay: float | None = 0.1,
                 browser: str = "msedge", instance_id: str = "hyb1",
                 send_ack: bool = True):
        self.name = endpoint_name(browser, instance_id)
        self.delay = delay
        self.send_ack = send_ack
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
                    if self.send_ack:
                        # 真实扩展收到 arm 就回 capture_armed（见 extension/background.js）；
                        # 父端用它判「扩展真的在响应」而不是「端点在」
                        channel.send(
                            {
                                "type": "capture_armed",
                                "sessionId": message.get("sessionId"),
                            }
                        )
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


# ---- 桌面腿不可用（对称语义；macOS 真机报障 2026-09-18） ----------------------
def test_hybrid_unavailable_desktop_degrades_to_extension(bridge):
    """桌面腿不可用（非 Windows）→ 退化为纯扩展捕获，扩展结果正常返回。

    回归：桌面 agent 是 Windows-only（``desktop_agent`` 的 ``sys.platform != "win32"``
    守卫），非 Windows 上 ``pick()`` 立即返回 ``{"error": "desktop capture requires
    Windows"}``。旧实现按「先回传者胜」把这个**失败**当成「用户捕获了桌面元素」，
    抢在仍可用的扩展腿之前结束会话并 close() 掉它 —— 用户侧表现为「点了捕获元素，
    窗口闪一下就弹回，网页里 Ctrl+Click 毫无反应」（实测 51ms 返回）。
    """
    from rpa_core.capture.extension import ExtensionCaptureSession

    FakeDesktopSession.instances = []
    session = HybridCaptureSession(
        desktop_factory=UnavailableDesktopSession,
        extension_session=ExtensionCaptureSession(),
    )
    try:
        session.start()
        assert session.desktop_offline is True
        assert session.extension_offline is False
        result = session.pick(timeout_seconds=10)
        assert result["kind"] == "browser"
        assert result["selector"]["css"] == "#go"
    finally:
        session.close()


def test_hybrid_failing_desktop_does_not_win(bridge):
    """桌面腿产出**失败**结果（无 ``kind``）不得抢跑：扩展腿仍有机会胜出。

    这类失败不止「平台不支持」一种——agent 崩溃（``_crash_info`` 带 ``error`` +
    ``stderrTail``）、非法输出、agent 超时都是同一形态。判据统一为「有没有有效
    的 ``kind``」，而不是「哪条腿先开口」。
    """
    from rpa_core.capture.extension import ExtensionCaptureSession

    FakeDesktopSession.instances = []
    session = HybridCaptureSession(
        desktop_factory=FakeDesktopSession,
        extension_session=ExtensionCaptureSession(),
    )
    session._desktop.result = {
        "error": "desktop capture agent exited with code 1",
        "stderrTail": "Traceback ...",
    }
    session._desktop.pick_delay = 0.0  # 桌面腿秒失败
    try:
        session.start()
        result = session.pick(timeout_seconds=10)
        assert result["kind"] == "browser"
    finally:
        session.close()


def test_hybrid_both_legs_unavailable_fails_fast():
    """两条腿都不可用 → 立即返回真实原因，不干等超时、不伪装成「已取消」。"""
    from rpa_core.capture.extension import ExtensionCaptureSession

    ext = ExtensionCaptureSession(endpoint="rpa_core_ext_test_no_such_endpoint")
    session = HybridCaptureSession(
        desktop_factory=UnavailableDesktopSession, extension_session=ext
    )
    try:
        session.start()
        assert session.desktop_offline and session.extension_offline
        started = time.monotonic()
        result = session.pick(timeout_seconds=30)
        elapsed = time.monotonic() - started
        assert elapsed < 2, "两条腿都已出局时应立即收场，而不是等满超时"
        assert result.get("unavailable") is True
        assert result.get("kind") is None
        assert result.get("cancelled") is None, "不得伪装成用户取消"
        assert "Windows" in result["error"]
    finally:
        session.close()


def test_hybrid_desktop_offline_skips_spawning_desktop_pick():
    """桌面腿不可用时不该再跑它的 pick（不持有无意义的等待线程/子进程）。

    与 ``available`` 配合的是这条：不可用的腿不仅不参选，连等都不该等——
    否则每条腿都留一个挂到超时的线程。
    """
    from rpa_core.capture.extension import ExtensionCaptureSession

    FakeDesktopSession.instances = []
    session = HybridCaptureSession(
        desktop_factory=UnavailableDesktopSession,
        extension_session=ExtensionCaptureSession(endpoint="x"),
    )
    try:
        session.start()
        result = session.pick(timeout_seconds=5)
        assert result.get("unavailable") is True
        assert FakeDesktopSession.instances[-1].pick_calls == 0, \
            "不可用的桌面腿不该被 pick 唤醒"
    finally:
        session.close()


# ---- 扩展腿 ack 判活（M40：端点在线 ≠ 扩展在响应） --------------------------
@pytest.fixture()
def isolated_endpoints(monkeypatch):
    """把端点前缀换成测试专用值。

    不隔离的话 ``ExtensionCaptureSession()`` 会连上**开发机真实的**扩展 host
    （本机实测常年有残留 host 端点），用例就变成依赖机器状态了。
    """
    monkeypatch.setenv("RPA_EXT_ENDPOINT_PREFIX", "rpa_core_ext_ack_probe_")


def test_hybrid_extension_without_ack_is_marked_dead(isolated_endpoints):
    """端点连得上但扩展从不确认 arm → 判死并立即收场，不等满超时。

    回归（维护者 2026-09-28 报障「第一次点击没有红框出现，但再次点击提示已有捕获
    任务进行中」）：判活曾经只看「端点能不能连上」，而端点是 host 持有的——残留 host
    照样连得上，但扩展收不到 arm、页面里永远不会出现高亮框；桌面腿又在浏览器内容区
    让位（hybrid），两条腿都产不出东西，用户静默等满 90 秒。
    """
    from rpa_core.capture.extension import ExtensionCaptureSession

    fake = FakeBridge(delay=None, send_ack=False)  # 残留 host 的形状
    try:
        session = HybridCaptureSession(
            desktop_factory=UnavailableDesktopSession,
            extension_session=ExtensionCaptureSession(),
            arm_ack_timeout=0.3,
        )
        try:
            session.start()
            assert not session.extension_offline, "端点连上了，腿不该被判「离线」"
            started = time.monotonic()
            result = session.pick(timeout_seconds=30)
            elapsed = time.monotonic() - started
            assert result.get("unavailable") is True, result
            assert session.extension_unresponsive is True
            assert "未响应" in result["error"]
            assert fake.armed == 1, "判死的前提是 arm 真的发出去了"
            assert elapsed < 5, f"ack 宽限后必须立即收场，实耗 {elapsed:.1f}s"
        finally:
            session.close()
    finally:
        fake.close()


def test_hybrid_extension_ack_keeps_leg_alive(isolated_endpoints):
    """扩展确认了 arm → 腿照旧存活（正常路径零影响：ack 毫秒级到达）。"""
    from rpa_core.capture.extension import ExtensionCaptureSession

    fake = FakeBridge(delay=None, send_ack=True)
    try:
        session = HybridCaptureSession(
            desktop_factory=UnavailableDesktopSession,
            extension_session=ExtensionCaptureSession(),
            arm_ack_timeout=0.3,
        )
        try:
            session.start()
            result = session.pick(timeout_seconds=1)
            assert result.get("timeout") is True, result
            assert session.extension_unresponsive is False
        finally:
            session.close()
    finally:
        fake.close()
