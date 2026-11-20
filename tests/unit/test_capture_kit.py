"""capture_kit 的状态机单测。

为什么单独测：``check_element_pick_contract.py`` 只覆盖 ppx ``capture_poll`` 的
**旧 entry 形状**（直接塞 ``result``），而 ppx 实际走的是本包 ``CaptureController``
的新路径。没有这组用例，新路径改坏了契约门禁仍会全绿。

全部注入假会话/假浮窗/假宿主：不 spawn agent、不开窗口、不碰真实桌面。
"""

from __future__ import annotations

import threading
import time

import pytest

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

    def show(self, *, title, name, selector="", meta="", info="", levels=None,
             nodes=None, descriptor=None, save_label="保存", on_save=None, on_close=None):
        self.shown = {
            "title": title, "name": name, "selector": selector,
            "meta": meta, "info": info, "levels": levels,
            "nodes": nodes, "descriptor": descriptor,
        }
        self._on_save = on_save
        self._on_close = on_close

    def close(self) -> None:
        self.closed = True

    def click_save(self, name: str, selector: str = "",
                   edit_state: dict | None = None) -> str | None:
        error = self._on_save(name, selector, edit_state)
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
    doc = {"kind": "browser", "selector": {"css": "#a"}}
    controller = _make_editing(FakeSession(doc), dialog)
    controller.start()
    state = _wait(controller, {"editing"})
    assert state["state"] == "editing"
    assert dialog.shown == {
        "title": "捕获到元素", "name": "元素1", "selector": "",
        "meta": "m", "info": "", "levels": None, "nodes": None, "descriptor": doc,
    }


def test_captured_desktop_passes_nodes_and_descriptor_to_dialog() -> None:
    """桌面详细编辑模型必须真送到 win32 框（只做在网页端 = 用户根本看不到）。"""
    dialog = FakeEditDialog()
    doc = {
        "kind": "desktop",
        "selector": {"locator": {"controlType": "Button", "name": "发送"}},
        "metadata": {"windowTitle": "微信"},
    }
    nodes = [{"type": "Button", "name": "发送", "selected": True, "attributes": []}]
    controller = CaptureController(
        session_factory=lambda: FakeSession(doc),
        on_captured=lambda captured: captured,
        float_factory=FakeFloat,
        heal_host=FakeHeal(),
        edit_dialog_factory=lambda: dialog,
        edit_model=lambda _doc: {"name": "发送按钮", "nodes": nodes},
    )
    controller.start()
    assert _wait(controller, {"editing"})["state"] == "editing"
    assert dialog.shown["nodes"] == nodes
    assert dialog.shown["descriptor"] == doc
    assert dialog.shown["levels"] is None


def test_edit_dialog_commit_reports_committed_with_name() -> None:
    dialog = FakeEditDialog()
    committed: list[str] = []
    controller = _make_editing(
        FakeSession({"kind": "browser", "selector": {"css": "#a"}}),
        dialog,
        on_commit=lambda name, selector, edit_state=None:
            committed.append((name, selector)) or None,
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
        on_commit=lambda name, selector, edit_state=None: "这个名字被占了",
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


# -- 捕获期 Esc（M71）：全局键状态轮询，不依赖焦点 ---------------------------
def _wait_calls(calls: list[str], expected: int, timeout: float = 0.5) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and len(calls) < expected:
        time.sleep(0.01)


def test_float_esc_cancels_on_rising_edge_only(monkeypatch) -> None:
    """按一下 Esc 触发一次取消；按住不放不重复起线程；松开再按能再触发。"""
    from rpa_core.capture_kit import float_window as fw

    calls: list[str] = []
    win = fw.CaptureFloat()
    win._mode = fw._MODE_CAPTURE
    win._on_cancel = lambda: calls.append("cancel")
    down = {"value": False}
    monkeypatch.setattr(fw, "_escape_down", lambda: down["value"])

    down["value"] = True
    win._poll_escape()
    _wait_calls(calls, 1)
    assert calls == ["cancel"]

    win._poll_escape()  # 仍按着：按下沿只算一次
    assert calls == ["cancel"]

    down["value"] = False
    win._poll_escape()  # 松开
    down["value"] = True
    win._poll_escape()  # 再按
    _wait_calls(calls, 2)
    assert len(calls) == 2


def test_float_esc_ignored_outside_capture(monkeypatch) -> None:
    """提示形态 / 没接回调时 Esc 不该顺手取消（编辑态浮窗本来就已 hide）。"""
    from rpa_core.capture_kit import float_window as fw

    calls: list[str] = []
    win = fw.CaptureFloat()
    win._on_cancel = lambda: calls.append("cancel")
    monkeypatch.setattr(fw, "_escape_down", lambda: True)

    win._mode = fw._MODE_NOTICE
    win._poll_escape()
    assert calls == []

    win._mode = fw._MODE_CAPTURE
    win._on_cancel = None
    win._poll_escape()
    assert calls == []


def test_float_timer_drives_escape_poll(monkeypatch) -> None:
    """真实链路：40ms 定时器 → _wnd_proc → Esc 轮询 → on_cancel。"""
    win32con = pytest.importorskip("win32con")
    from rpa_core.capture_kit import float_window as fw

    calls: list[str] = []
    win = fw.CaptureFloat()
    win._mode = fw._MODE_CAPTURE
    win._on_cancel = lambda: calls.append("cancel")
    monkeypatch.setattr(fw, "_escape_down", lambda: True)
    monkeypatch.setattr(win, "_avoid_cursor", lambda hwnd: None)
    win._wnd_proc(0, win32con.WM_TIMER, fw._TIMER_AVOID, 0)
    _wait_calls(calls, 1)
    assert calls == ["cancel"]


def test_float_cancel_callback_ends_controller_session() -> None:
    """浮窗按钮与 Esc 共用同一个 on_cancel：它必须真的把捕获会话收掉。"""
    host = FakeHost()
    window = FakeFloat()
    controller = _make(BlockingSession(), host=host, float_=window)
    controller.start()
    assert window.on_cancel is not None
    window.on_cancel()
    assert _wait(controller, {"cancelled"}) == {"state": "cancelled"}
    assert host.restored >= 1 and window.hidden is True


# ---- 桌面节点区初始视图（M99）------------------------------------------------


def _node_dialog(nodes: list[dict]) -> object:
    from rpa_core.capture_kit.edit_dialog import ElementEditDialog

    dialog = ElementEditDialog()
    dialog._nodes = nodes
    return dialog


def _chain(total: int, selected: int) -> list[dict]:
    return [
        {"type": "Group", "name": "", "selected": index < selected}
        for index in range(total)
    ]


def test_node_view_opens_on_target_when_chain_exceeds_screen() -> None:
    """★ 微信搜索框实测 16 层，节点区只放 10 行。

    旧实现把偏移写死成 0 ⇒ 用户一开框看到的是一屏无名容器，目标那行被挤到
    窗口外，被当成「层级展示不全」。判据盯的是**目标可见**，不是某个具体数字。
    """
    from rpa_core.capture_kit import edit_dialog

    nodes = _chain(16, selected=16)
    row, scroll = _node_dialog(nodes)._initial_node_view()

    assert row == 15, "默认选中行应为最后一条（即目标节点）"
    assert scroll <= row < scroll + edit_dialog._NODE_ROWS, "目标行必须落在可视区内"
    assert scroll == 16 - edit_dialog._NODE_ROWS


def test_node_view_keeps_top_when_chain_fits_screen() -> None:
    """反向：层数没超一屏时不该平白滚动（否则顶部层级反而看不见）。"""
    nodes = _chain(4, selected=4)
    row, scroll = _node_dialog(nodes)._initial_node_view()

    assert scroll == 0
    assert row == 3


def test_node_view_falls_back_to_last_when_nothing_selected() -> None:
    """没有 selected 标记时按「最后一条 = 目标」处理，不能停在首行。"""
    from rpa_core.capture_kit import edit_dialog

    nodes = [{"type": "Group", "selected": False} for _ in range(14)]
    row, scroll = _node_dialog(nodes)._initial_node_view()

    assert row == 13
    assert scroll <= row < scroll + edit_dialog._NODE_ROWS


def test_node_view_of_empty_chain() -> None:
    assert _node_dialog([])._initial_node_view() == (0, 0)


@pytest.mark.parametrize(
    ("scroll", "total", "expected"),
    [
        (0, 16, 0),
        (6, 16, 6),
        (99, 16, 6),   # 超上界夹回
        (-3, 16, 0),   # 负偏移夹回
        (5, 4, 0),     # 不足一屏恒 0
    ],
)
def test_clamp_node_scroll(scroll: int, total: int, expected: int) -> None:
    """滚动偏移必须夹在 ``[0, total - _NODE_ROWS]``，否则目标会被滚出可视区。"""
    from rpa_core.capture_kit.edit_dialog import _clamp_node_scroll

    assert _clamp_node_scroll(scroll, total) == expected


def test_setup_node_scroll_keeps_initial_offset(monkeypatch) -> None:
    """接线判据：``_build_desktop_children`` 算出的初始偏移不能被建滚动条时清零。

    只测 ``_initial_node_view`` 会漏掉这一步——真机上把偏移写死成 0 的正是
    ``_setup_node_scroll``，纯函数绿而接线红就是假绿灯。
    """
    win32gui = pytest.importorskip("win32gui")
    dialog = _node_dialog(_chain(16, selected=16))
    seen: dict[str, tuple] = {}

    monkeypatch.setattr(
        win32gui, "SetScrollInfo",
        lambda hwnd, bar, info, redraw: seen.setdefault("info", info),
    )
    dialog._selected_node, dialog._node_scroll = dialog._initial_node_view()
    dialog._setup_node_scroll(0)

    from rpa_core.capture_kit import edit_dialog

    assert dialog._node_scroll == 16 - edit_dialog._NODE_ROWS
    assert dialog._selected_node == 15, "属性面板要显示目标自己的属性，不是窗口的"
    assert seen["info"][4] == dialog._node_scroll, "滚动条位置必须与偏移同步"


# ---- 网页层级区：滚轮 / 拖动 / 勾选态回写 -----------------------------------


def _web_levels(total: int, included: tuple[int, ...] = ()) -> list[dict]:
    wanted = set(included)
    return [
        {
            "tag": "div",
            "included": index in wanted,
            "id": {"on": False, "value": ""},
            "classes": [],
            "nth": {"on": False, "value": ""},
        }
        for index in range(total)
    ]


def _mock_level_dialog(monkeypatch, levels: list[dict]) -> tuple:
    """给层级区控件做桩：滚动条位置、勾选回写、平移与批量移动调用。

    返回 ``(dialog, checks, set_calls, scroll_calls, scroll_ex_calls, move_calls)``。
    """
    win32gui = pytest.importorskip("win32gui")
    win32con = pytest.importorskip("win32con")
    from rpa_core.capture_kit import edit_dialog
    from rpa_core.capture_kit.edit_dialog import ElementEditDialog

    dialog = ElementEditDialog()
    dialog._levels = levels
    dialog._level_boxes = [2000 + i for i in range(len(levels))]
    dialog._level_check_boxes = [1000 + i for i in range(len(levels))]
    checks = {1000 + i: 0 for i in range(len(levels))}
    set_calls: list[tuple[int, int]] = []
    scroll_calls: list[tuple[tuple, bool]] = []
    scroll_ex_calls: list[tuple] = []
    move_calls: list[list] = []

    def send(hwnd, msg, wp, lp):
        if msg == win32con.BM_GETCHECK:
            return checks.get(hwnd, 0)
        if msg == win32con.BM_SETCHECK:
            checks[hwnd] = wp
            set_calls.append((hwnd, wp))
        return 0

    monkeypatch.setattr(win32gui, "SendMessage", send)
    monkeypatch.setattr(win32gui, "SetWindowPos", lambda *a: None)
    monkeypatch.setattr(win32gui, "ShowWindow", lambda *a: None)
    monkeypatch.setattr(
        win32gui, "SetScrollInfo",
        lambda hwnd, bar, info, redraw: scroll_calls.append((info, redraw)),
    )
    # 位图平移与批量移动都不碰真窗口（桩句柄无效，真调会把单测打死）
    monkeypatch.setattr(win32gui, "ScrollWindowEx", lambda *a: scroll_ex_calls.append(a))
    monkeypatch.setattr(win32gui, "InvalidateRect", lambda *a: None)
    monkeypatch.setattr(
        edit_dialog._win32, "move_windows",
        lambda moves: move_calls.append(list(moves)) or True,
    )
    return dialog, checks, set_calls, scroll_calls, scroll_ex_calls, move_calls


def test_mouse_wheel_scrolls_levels_and_rewrites_checks(monkeypatch) -> None:
    """多层级必须能用滚轮看；滚动后复选框按模型回写，不能只信上一次的画法。"""
    win32con = pytest.importorskip("win32con")
    dialog, checks, set_calls, scroll_calls, _sx, _moves = _mock_level_dialog(
        monkeypatch, _web_levels(15, (2, 14))
    )
    # WM_MOUSEWHEEL: 高 16 位是带符号 delta，向下滚一格是 -120。
    # 直接走 _wnd_proc，纯函数绿而没接线的情况也算红。
    dialog._wnd_proc(0, win32con.WM_MOUSEWHEEL, ((-120) & 0xFFFF) << 16, 0)

    assert dialog._level_scroll == 3
    assert scroll_calls and scroll_calls[-1][0][4] == 3, "滚动条位置没跟上层偏移"
    assert set_calls and (1002, 1) in set_calls, "勾选态没从模型回写"
    assert checks[1002] == 1

    dialog._on_mouse_wheel(0, 120 << 16)
    assert dialog._level_scroll == 0, "反向滚轮要能滚回顶部"


def test_thumbtrack_never_writes_scrollbar_and_is_throttled(monkeypatch) -> None:
    """拖滚动块：一条都不写位置（滑块归 Windows 管），重排按 25ms 合并。

    真机事故（M108）：拖动时每移动一点就一条 `SB_THUMBTRACK`，每次全量重排
    19ms ⇒ 100 条消息耗 1916ms、界面 646ms 完全不响应 = 用户说的「点滚动条
    假死」。上一版只把 `SetScrollInfo` 的 redraw 关掉、位置照样写，所以仍卡。
    """
    win32con = pytest.importorskip("win32con")
    dialog, _checks, _sets, scroll_calls, _sx, _moves = _mock_level_dialog(
        monkeypatch, _web_levels(15)
    )
    layouts: list[int] = []
    monkeypatch.setattr(dialog, "_layout_levels", lambda hwnd: layouts.append(dialog._level_scroll))

    dialog._on_vscroll(0, (4 << 16) | win32con.SB_THUMBTRACK)
    assert dialog._level_scroll == 4, "偏移要跟着走"
    assert scroll_calls == [], "拖动中绝不写滚动条位置（会和拖动循环抢滑块）"
    assert layouts == [4], "第一条要重排一帧"

    dialog._on_vscroll(0, (5 << 16) | win32con.SB_THUMBTRACK)
    assert dialog._level_scroll == 5
    assert layouts == [4], "25ms 内的第二条只记偏移，不再重排"

    dialog._on_vscroll(0, (5 << 16) | win32con.SB_THUMBPOSITION)
    assert scroll_calls and scroll_calls[-1][0][4] == 5, "松手才写位置对齐滑块"
    assert layouts == [4, 5], "松手立刻补齐布局"


def test_smooth_scroll_moves_only_edges(monkeypatch) -> None:
    """滚一格只动「进出视口」的两行，其余交给 ScrollWindowEx 位图平移。

    逐个 SetWindowPos 摆一屏要 16ms（M108 真机），滚动块拖动时根本跟不上。
    """
    win32con = pytest.importorskip("win32con")
    dialog, _checks, _sets, _scrolls, scroll_ex, move_calls = _mock_level_dialog(
        monkeypatch, _web_levels(15)
    )
    dialog._layout_levels(0)  # 首帧全量
    move_calls.clear()

    dialog._on_vscroll(0, win32con.SB_LINEDOWN)

    assert scroll_ex, "滚一格必须走 ScrollWindowEx 位图平移"
    assert scroll_ex[-1][2] == -32, "偏移 +1 行 = 内容上移一行"
    assert len(move_calls) == 1, "一帧只提交一次批量移动"
    handles = {move[0] for move in move_calls[-1]}
    assert handles == {1000, 2000, 1010, 2010}, (
        "只该动滚出去的 0 层与滚进来的 10 层（方框+文字各一），"
        f"实际动了 {sorted(handles)}"
    )
    # 显隐必须并进同一批：单独的 ShowWindow 一次 0.5~1.3ms，一帧四次就够呛
    shows = {move[0]: move[3] for move in move_calls[-1]}
    assert shows[1000] is False and shows[2000] is False, "滚出去的行要隐藏"
    assert shows[1010] is True and shows[2010] is True, "滚进来的行要显示"


def test_level_layout_syncs_checks_from_model(monkeypatch) -> None:
    """滚动重排后，组合里有哪层，方框就必须是勾着的。"""
    dialog, checks, set_calls, _scrolls, _sx, _moves = _mock_level_dialog(
        monkeypatch, _web_levels(15, (2,))
    )
    dialog._layout_levels(0)
    assert (1002, 1) in set_calls
    assert checks[1002] == 1
