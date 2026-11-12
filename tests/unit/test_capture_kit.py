"""capture_kit 的状态机单测。

为什么单独测：``check_element_pick_contract.py`` 只覆盖 ppx ``capture_poll`` 的
**旧 entry 形状**（直接塞 ``result``），而 ppx 实际走的是本包 ``CaptureController``
的新路径。没有这组用例，新路径改坏了契约门禁仍会全绿。

全部注入假会话/假浮窗/假宿主：不 spawn agent、不开窗口、不碰真实桌面。
"""

from __future__ import annotations

import threading
import time

from rpa_core.capture_kit import CaptureController


class FakeSession:
    def __init__(
        self,
        pick_result,
        *,
        desktop_offline: bool = False,
        extension_offline: bool = False,
        armed: bool = False,
        unresponsive: bool = False,
    ) -> None:
        self._pick_result = pick_result
        self.desktop_offline = desktop_offline
        self.extension_offline = extension_offline
        self.extension_unresponsive = unresponsive
        self.armed = armed
        self.cancelled = False
        self.closed = False
        self.started = False

    def start(self) -> None:
        self.started = True

    def pick(self, timeout_seconds=None):
        return self._pick_result

    def cancel(self) -> None:
        self.cancelled = True

    def close(self) -> None:
        self.closed = True


class BlockingSession(FakeSession):
    """pick 卡住直到 cancel()，用来验证「取消 → restored」不依赖 pick 自己返回。"""

    def __init__(self) -> None:
        super().__init__(None)
        self._gate = threading.Event()

    def pick(self, timeout_seconds=None):
        self._gate.wait(2)
        return {"cancelled": True}

    def cancel(self) -> None:
        super().cancel()
        self._gate.set()


class FakeFloat:
    def __init__(self) -> None:
        self.shown = False
        self.hidden = False
        self.status = None
        self.on_cancel = None

    def show(self, gesture, web, desktop, web_ok, desktop_ok, on_cancel=None) -> None:
        self.shown = True
        self.status = (web, desktop, web_ok, desktop_ok)
        self.on_cancel = on_cancel

    def set_status(self, web, desktop, web_ok, desktop_ok) -> None:
        self.status = (web, desktop, web_ok, desktop_ok)

    def hide(self) -> None:
        self.hidden = True


class FakeHost:
    def __init__(self) -> None:
        self.minimized = 0
        self.restored = 0

    def minimize(self) -> None:
        self.minimized += 1

    def restore(self) -> None:
        self.restored += 1


def _wait(controller: CaptureController, states: set[str], timeout: float = 2.0) -> dict:
    deadline = time.monotonic() + timeout
    state = controller.poll()
    while time.monotonic() < deadline:
        state = controller.poll()
        if state["state"] in states:
            return state
        time.sleep(0.01)
    return state


def _make(session, *, host=None, float_=None, on_captured=None) -> CaptureController:
    return CaptureController(
        session_factory=lambda: session,
        on_captured=on_captured,
        host=host,
        float_factory=lambda: float_ if float_ is not None else FakeFloat(),
    )


def test_start_minimizes_and_ready_persists_then_restores() -> None:
    host = FakeHost()
    window = FakeFloat()
    saved: dict = {}

    def persist(descriptor):
        saved.update(descriptor)
        return {**descriptor, "savedAs": "userField"}

    controller = _make(
        FakeSession({"kind": "browser", "selector": {"css": "#a"}}),
        host=host,
        float_=window,
        on_captured=persist,
    )
    launched = controller.start()
    assert launched["ok"] is True
    assert launched["extensionOffline"] is False
    assert host.minimized == 1 and window.shown is True

    state = _wait(controller, {"ready"})
    assert state["state"] == "ready", state
    assert saved["kind"] == "browser"
    assert state["result"]["savedAs"] == "userField"
    assert window.hidden is True
    assert host.restored == 1


def test_cancelled_result_is_cancelled_and_restores() -> None:
    host = FakeHost()
    window = FakeFloat()
    controller = _make(FakeSession({"cancelled": True}), host=host, float_=window)
    controller.start()
    assert _wait(controller, {"cancelled"})["state"] == "cancelled"
    assert window.hidden is True
    assert host.restored == 1


def test_cancel_while_pending_restores_without_waiting_pick() -> None:
    host = FakeHost()
    window = FakeFloat()
    session = BlockingSession()
    controller = _make(session, host=host, float_=window)
    controller.start()
    assert controller.poll()["state"] == "pending"
    controller.cancel()
    assert _wait(controller, {"cancelled"})["state"] == "cancelled"
    assert session.cancelled is True
    assert host.restored >= 1
    assert window.hidden is True


def test_unavailable_is_offline_with_reason() -> None:
    controller = _make(
        FakeSession({"unavailable": True, "error": "没有可用通道"}),
    )
    controller.start()
    state = _wait(controller, {"offline"})
    assert state["state"] == "offline"
    assert state["reason"] == "没有可用通道"


def test_error_is_failed_with_reason() -> None:
    controller = _make(FakeSession({"error": "boom"}))
    controller.start()
    state = _wait(controller, {"failed"})
    assert state["state"] == "failed"
    assert state["reason"] == "boom"


def test_persist_failure_becomes_failed_not_pending_forever() -> None:
    def explode(descriptor):
        raise ValueError("磁盘满了")

    controller = _make(
        FakeSession({"kind": "desktop", "selector": {"locator": {}}}),
        on_captured=explode,
    )
    controller.start()
    state = _wait(controller, {"failed"})
    assert state["state"] == "failed"
    assert "磁盘满了" in state["reason"]


def test_both_channels_offline_blocks_without_minimize_or_float() -> None:
    host = FakeHost()
    window = FakeFloat()
    controller = _make(
        FakeSession(None, desktop_offline=True, extension_offline=True),
        host=host,
        float_=window,
    )
    launched = controller.start()
    assert launched["ok"] is False
    assert launched["code"] == "NO_CAPTURE_CHANNEL"
    assert host.minimized == 0
    assert window.shown is False
