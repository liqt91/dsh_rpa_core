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


class FakeHeal:
    """替身自愈：绝不碰注册表/浏览器档案。"""

    def __init__(self, changed: bool = False, installed: dict[str, str] | None = None) -> None:
        self.calls = 0
        self._changed = changed
        self._installed = installed or {}

    def __call__(self) -> dict:
        self.calls += 1
        return {
            "changed": self._changed,
            "installed": self._installed,
            "refreshed": ["edge"] if self._changed else [],
        }


def _wait(controller: CaptureController, states: set[str], timeout: float = 2.0) -> dict:
    deadline = time.monotonic() + timeout
    state = controller.poll()
    while time.monotonic() < deadline:
        state = controller.poll()
        if state["state"] in states:
            return state
        time.sleep(0.01)
    return state


def _make(session, *, host=None, float_=None, on_captured=None, heal=None) -> CaptureController:
    return CaptureController(
        session_factory=lambda: session,
        on_captured=on_captured,
        host=host,
        float_factory=lambda: float_ if float_ is not None else FakeFloat(),
        heal_host=heal or FakeHeal(),
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


def test_extension_offline_triggers_host_heal_and_reports_it() -> None:
    """扩展离线 → 幂等重注册 host 白名单，并把结果回给宿主做提示文案。

    背景（2026-11-12 维护者报障）：插件在 Edge 里明明启用，但 host 的
    ``allowed_origins`` 是旧路径派生的 ID ⇒ 浏览器拒绝拉起 host ⇒ 端点 0 个，
    用户侧看起来像「插件没装」。自愈必须真的被调用，且结果要能传到前端。
    """
    heal = FakeHeal(changed=True, installed={"edge": "edkl"})
    controller = _make(FakeSession(None, extension_offline=True), heal=heal)
    launched = controller.start()
    assert launched["ok"] is True
    assert heal.calls == 1
    assert launched["extensionHostRefreshed"] is True
    assert launched["extensionInstalled"] is True
    controller.cancel()


def test_extension_online_does_not_touch_host_registration() -> None:
    heal = FakeHeal()
    controller = _make(FakeSession(None, extension_offline=False), heal=heal)
    launched = controller.start()
    assert heal.calls == 0
    assert launched["extensionInstalled"] is True
    assert launched["extensionHostRefreshed"] is False
    controller.cancel()


def test_heal_failure_never_blocks_capture() -> None:
    def explode() -> dict:
        raise RuntimeError("注册表读写被拒")

    controller = _make(FakeSession(None, extension_offline=True), heal=explode)
    launched = controller.start()
    assert launched["ok"] is True
    assert launched["extensionHostRefreshed"] is False
    controller.cancel()


class _OfflineSession:
    extension_offline = True
    extension_unresponsive = False


def test_web_status_distinguishes_installed_but_disconnected(monkeypatch) -> None:
    """「装了没连通」与「没装」必须给不同文案（这正是本次报障的误导点）。"""
    from rpa_core.capture_kit import engine

    monkeypatch.setattr(engine, "extension_installed", lambda: {"edge": "abc"})
    text, ok = engine.web_status(_OfflineSession())
    assert ok is False
    assert "已装但未连通" in text

    monkeypatch.setattr(engine, "extension_installed", lambda: {})
    text, ok = engine.web_status(_OfflineSession())
    assert ok is False
    assert "先装插件" in text


class FakeEditDialog:
    """win32 编辑框的替身：不开窗，show/click_save/click_cancel 模拟用户操作。"""

    available = True

    def __init__(self) -> None:
        self.shown: dict | None = None
        self.closed = False
        self._on_save = None
        self._on_close = None

    def show(self, *, title, name, selector="", meta="", save_label="保存",
             on_save=None, on_close=None):
        self.shown = {"title": title, "name": name, "selector": selector, "meta": meta}
        self._on_save = on_save
        self._on_close = on_close

    def close(self) -> None:
        self.closed = True

    def click_save(self, name: str, selector: str = "") -> str | None:
        error = self._on_save(name, selector)
        if error:
            return error  # 错误回显留框（真实对话框的 _try_save 同款分支）
        self._on_close(True)
        return None

    def click_cancel(self) -> None:
        self._on_close(False)


def _make_editing(session, dialog, *, on_commit=None, on_discard=None) -> CaptureController:
    return CaptureController(
        session_factory=lambda: session,
        on_captured=lambda doc: doc,
        float_factory=FakeFloat,
        heal_host=FakeHeal(),
        edit_dialog_factory=lambda: dialog,
        edit_model=lambda doc: {"title": "捕获到元素", "name": "元素1", "meta": "m"},
        on_commit=on_commit,
        on_discard=on_discard,
    )


def test_captured_opens_edit_dialog_instead_of_ready() -> None:
    dialog = FakeEditDialog()
    controller = _make_editing(
        FakeSession({"kind": "browser", "selector": {"css": "#a"}}), dialog
    )
    controller.start()
    state = _wait(controller, {"editing"})
    assert state["state"] == "editing"
    assert dialog.shown == {
        "title": "捕获到元素", "name": "元素1", "selector": "", "meta": "m",
    }


def test_edit_dialog_commit_reports_committed_with_name() -> None:
    dialog = FakeEditDialog()
    committed: list[str] = []
    controller = _make_editing(
        FakeSession({"kind": "browser", "selector": {"css": "#a"}}),
        dialog,
        on_commit=lambda name, selector: committed.append((name, selector)) or None,
    )
    controller.start()
    _wait(controller, {"editing"})
    assert dialog.click_save("搜索框", "#q") is None
    state = controller.poll()
    assert state == {"state": "committed", "name": "搜索框"}
    assert committed == [("搜索框", "#q")]


def test_edit_dialog_save_error_stays_editing() -> None:
    dialog = FakeEditDialog()
    controller = _make_editing(
        FakeSession({"kind": "browser", "selector": {"css": "#a"}}),
        dialog,
        on_commit=lambda name, selector: "这个名字被占了",
    )
    controller.start()
    _wait(controller, {"editing"})
    assert dialog.click_save("搜索框") == "这个名字被占了"
    assert controller.poll()["state"] == "editing"


def test_edit_dialog_cancel_reports_discarded() -> None:
    dialog = FakeEditDialog()
    discarded: list[bool] = []
    controller = _make_editing(
        FakeSession({"kind": "browser", "selector": {"css": "#a"}}),
        dialog,
        on_discard=lambda: discarded.append(True),
    )
    controller.start()
    _wait(controller, {"editing"})
    dialog.click_cancel()
    assert controller.poll()["state"] == "discarded"
    assert discarded == [True]


def test_cancel_during_editing_closes_dialog() -> None:
    dialog = FakeEditDialog()
    controller = _make_editing(
        FakeSession({"kind": "browser", "selector": {"css": "#a"}}), dialog
    )
    controller.start()
    _wait(controller, {"editing"})
    controller.cancel()
    assert dialog.closed is True
    assert controller.poll()["state"] == "cancelled"


def test_unavailable_dialog_falls_back_to_ready() -> None:
    """非 Windows（对话框 unavailable）：维持旧 ready 语义，宿主页面接管确认。"""
    dialog = FakeEditDialog()
    dialog.available = False
    controller = _make_editing(
        FakeSession({"kind": "browser", "selector": {"css": "#a"}}), dialog
    )
    controller.start()
    state = _wait(controller, {"ready", "editing"})
    assert state["state"] == "ready"
    assert dialog.shown is None


def test_ensure_extension_host_only_reports_newly_added_ids(monkeypatch, tmp_path) -> None:
    """白名单里已有该 ID ⇒ 不该报「修复了」（否则每次捕获都提示用户重载插件）。"""
    import rpa_core.extension_installer as installer
    from rpa_core.capture_kit import engine

    ext = tmp_path / "extension"
    ext.mkdir()
    (ext / "manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(engine, "extension_dir", lambda: ext)
    monkeypatch.setattr(engine, "extension_installed", lambda: {"edge": "abc"})
    calls: list[str] = []
    monkeypatch.setattr(
        installer,
        "ensure_native_host",
        lambda browser, path: calls.append(browser) or {},
    )

    monkeypatch.setattr(
        installer,
        "native_host_status",
        lambda browser: {"allowedOrigins": ["chrome-extension://old/"]},
    )
    stale = engine.ensure_extension_host()
    assert stale["changed"] is True
    assert calls == ["edge"]

    calls.clear()
    monkeypatch.setattr(
        installer,
        "native_host_status",
        lambda browser: {"allowedOrigins": ["chrome-extension://abc/"]},
    )
    fresh = engine.ensure_extension_host()
    assert fresh["changed"] is False
    # 幂等调用仍然会「重写一次」（修 host 路径），但不当成「修复了白名单」
    assert calls == ["edge"]
