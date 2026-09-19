"""元素捕获契约测试（M23 切 G1：单入口混合捕获）。

- 捕获走 HybridCaptureSession（网页扩展腿 + 桌面 hover 腿），会话 start arm 扩展腿；
- 捕获期间主窗最小化（不遮挡目标），结束还原；
- 扩展离线时状态栏显式提示网页区域不可捕获；
- 同名进行中会话拦截（防重入）；
- 关闭窗口取消进行中的捕获会话。

测试在 offscreen Qt 平台运行；缺 PySide6 时整组跳过。
HybridCaptureSession 以假实现替换（不拉起真实 desktop_agent 子进程 / bridge 端点）。
"""

from __future__ import annotations

import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from rpa_core.capture import capture_click_label  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from rpa_core.gui.app import build_application

    return build_application()


@pytest.fixture(scope="module")
def catalog(qapp):
    from rpa_core.catalog import load_catalog
    from rpa_core.cli import _commands_root

    return load_catalog(_commands_root())


_ALIVE_WINDOWS = []  # 会话期保活（同 test_gui_run：offscreen 下避免悬挂删除竞态）


@pytest.fixture()
def window(catalog, tmp_path):
    from rpa_core.gui.app import MainWindow

    win = MainWindow(catalog, workflows_root=tmp_path / "workflows")
    _ALIVE_WINDOWS.append(win)
    yield win
    win._shutdown_run_manager()


_BROWSER_DESCRIPTOR = {
    "kind": "browser",
    "selector": {"css": "#kw"},
    "verifyCount": 1,
    "metadata": {"tag": "input", "url": "https://example.com"},
}


class FakeHybridSession:
    """记录接线的混合会话假实现；pick 阻塞直到测试放行。

    类属性 ``offline`` 控制 extension_offline（默认在线）；``desktop_unavailable``
    控制桌面腿可用性（默认可用，与 Windows 真机一致）；``result``
    为 None 时 pick 返回 {"cancelled": True}（避免触发命名对话框）。

    开关与属性名必须不同：同名类属性会被 property 覆盖，``type(self).x`` 取到的是
    property 对象而非布尔值（ruff F811）。
    """

    instances: list = []
    offline = False
    desktop_unavailable = False

    def __init__(self, *, desktop_factory, **kwargs):
        self.kwargs = kwargs
        self.started = False
        self.closed = False
        self.cancelled = False
        self.result: dict | None = dict(_BROWSER_DESCRIPTOR)
        self._gate = threading.Event()
        FakeHybridSession.instances.append(self)

    def start(self):
        self.started = True

    @property
    def extension_offline(self) -> bool:
        return self.offline

    @property
    def desktop_offline(self) -> bool:
        return type(self).desktop_unavailable

    def pick(self, timeout_seconds=90):
        self._gate.wait(timeout=10)
        return dict(self.result) if self.result is not None else {"cancelled": True}

    def cancel(self):
        self.cancelled = True
        self._gate.set()

    def close(self):
        self.closed = True
        self._gate.set()


@pytest.fixture()
def fake_capture(monkeypatch):
    import rpa_core.capture as capture_mod

    FakeHybridSession.instances = []
    FakeHybridSession.offline = False
    FakeHybridSession.desktop_unavailable = False
    monkeypatch.setattr(capture_mod, "HybridCaptureSession", FakeHybridSession)
    return FakeHybridSession


def _pump_until(predicate, timeout: float = 5.0) -> bool:
    from PySide6.QtWidgets import QApplication

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _release_and_finish(fake, window) -> None:
    """放行 pick 并泵到桥信号处理完（_on_element_captured 已执行）。"""
    fake._gate.set()
    assert _pump_until(lambda: window._capture_session is None)


def test_capture_uses_hybrid_session_and_saves_browser_element(
    window, fake_capture, monkeypatch
):
    window._save_named_flow("cap1")
    # 捕获确认对话框由 app._confirm_element_save 承担（ElementDialog 另行单测）
    monkeypatch.setattr(
        type(window),
        "_confirm_element_save",
        lambda self, result: ("searchBox", result),
    )

    window._capture_element()
    fake = fake_capture.instances[-1]
    assert fake.started, "混合会话未 arm 扩展腿"
    assert fake.kwargs.get("hover") is True
    # 捕获期间主窗最小化（不遮挡目标）
    assert _pump_until(lambda: window.isMinimized())

    _release_and_finish(fake, window)
    assert not window.isMinimized(), "捕获结束后主窗未还原"
    store = window._element_store()
    assert "searchBox" in store.list()
    assert store.read("searchBox")["kind"] == "browser"
    assert fake.closed


def test_capture_confirm_dialog_cancel_saves_nothing(
    window, fake_capture, monkeypatch
):
    """确认对话框取消：不入库、不置脏元素列表。"""
    window._save_named_flow("cap1c")
    monkeypatch.setattr(
        type(window), "_confirm_element_save", lambda self, result: None
    )
    window._capture_element()
    fake = fake_capture.instances[-1]
    _release_and_finish(fake, window)
    assert window._element_store().list() == []


def test_capture_overwrite_same_name_needs_confirm(window, monkeypatch):
    """同名覆盖保护：确认框选「否」则不落库（对齐 Web 的 confirm 语义）。"""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDialog, QMessageBox

    from rpa_core.gui import element_panel

    window._save_named_flow("cap1d")
    original = {
        "kind": "browser",
        "selector": {"css": "#old"},
        "verifyCount": 1,
        "metadata": {"tag": "input"},
    }
    assert window.save_element_descriptor("dup", original)

    created: list[QDialog] = []

    class FakeDialog(QDialog):
        """真 QDialog 子类：_confirm_element_save 会调 present_window()，
        它需要 setWindowFlag/show/raise_/activateWindow 全套 QWidget 契约。"""

        def __init__(self, descriptor, *, default_name, parent=None):
            super().__init__(parent)
            created.append(self)

        def exec(self):
            return QDialog.DialogCode.Accepted

        def result_document(self):
            return "dup", {
                "kind": "browser",
                "selector": {"css": "#new"},
                "verifyCount": 1,
                "metadata": {"tag": "input"},
            }

    monkeypatch.setattr(element_panel, "ElementDialog", FakeDialog)

    # 选择「不覆盖」→ 返回 None，原元素不变
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No),
    )
    assert window._confirm_element_save(original) is None
    assert window._element_store().read("dup")["selector"]["css"] == "#old"
    # 命名对话框必须被置顶：捕获时用户在浏览器里操作，本进程是后台应用，
    # macOS 会忽略自激活请求，不置顶的话对话框停在浏览器后面、用户得先点一次
    # Dock 才看得见（真机 2026-09-19 实测）。
    assert created[0].windowFlags() & Qt.WindowType.WindowStaysOnTopHint

    # 选择「覆盖」→ 返回新文档
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )
    confirmed = window._confirm_element_save(original)
    assert confirmed is not None
    assert confirmed[0] == "dup" and confirmed[1]["selector"]["css"] == "#new"


def test_capture_extension_offline_hint(window, fake_capture, monkeypatch):
    window._save_named_flow("cap2")
    fake_capture.offline = True  # 插件离线：状态栏显式提示网页区域不可捕获
    # 离线确认框：选择「继续（仅桌面捕获）」——offscreen 不能弹真 QMessageBox
    monkeypatch.setattr(
        type(window), "_confirm_capture_offline", lambda self: True
    )
    window._capture_element()
    assert "插件离线" in window.statusBar().currentMessage()
    fake = fake_capture.instances[-1]
    fake.result = None  # 取消路径：不触发命名对话框
    _release_and_finish(fake, window)


def test_capture_extension_offline_cancel(window, fake_capture, monkeypatch):
    """扩展离线 + 用户选择「取消」：会话立即关闭（回收桌面 agent），不最小化、
    不进入捕获等待——否则用户会在网页里白点 90 秒直到超时。"""
    window._save_named_flow("cap2b")
    fake_capture.offline = True
    monkeypatch.setattr(
        type(window), "_confirm_capture_offline", lambda self: False
    )
    window._capture_element()
    fake = fake_capture.instances[-1]
    assert fake.closed, "取消后必须关闭会话（回收桌面 agent 子进程）"
    assert window._capture_session is None
    assert not window.isMinimized()
    assert "已取消" in window.statusBar().currentMessage()


def test_capture_reentrant_guard(window, fake_capture):
    window._save_named_flow("cap3")
    window._capture_element()
    window._capture_element()  # 第二次点击被拦截
    assert len(fake_capture.instances) == 1
    assert "进行中" in window.statusBar().currentMessage()
    fake = fake_capture.instances[-1]
    fake.result = None
    _release_and_finish(fake, window)


def test_capture_desktop_offline_hint(window, fake_capture, monkeypatch):
    """桌面腿不可用（非 Windows）而扩展在线 → 仍可捕获，提示说明桌面不可用。

    回归（macOS 真机 2026-09-18）：此前的提示承诺「桌面也可用 F9」，
    而桌面 agent 是 Windows-only —— 提示与能力不符。
    """
    window._save_named_flow("cap5")
    fake_capture.desktop_unavailable = True
    window._capture_element()
    fake = fake_capture.instances[-1]
    assert fake.started
    message = window.statusBar().currentMessage()
    assert "仅支持 Windows" in message
    assert "F9" not in message, "桌面不可用时不应再承诺 F9"
    # 回归（macOS 真机 2026-09-19）：状态栏曾写死 Ctrl+Click，而 macOS 上该手势被系统
    # 改写成右键、不会派发 click —— 提示必须跟随平台（Mac 用 ⌘+Click）。
    assert capture_click_label() in message, "捕获手势提示必须与平台一致"
    fake.result = None
    _release_and_finish(fake, window)


def test_capture_both_legs_unavailable_fails_fast(window, fake_capture, monkeypatch):
    """两条腿都不可用 → 不弹「仅桌面捕获」的假选项，不最小化，直接给真实原因。

    回归（macOS 真机 2026-09-18）：旧实现只在扩展腿离线时弹确认框，用户选「是」
    后进入仅桌面捕获——而桌面腿在 macOS 上必然失败，用户白等 90 秒。
    """
    window._save_named_flow("cap6")
    fake_capture.offline = True
    fake_capture.desktop_unavailable = True
    called = {"offline_confirm": False}

    def _spy(self):
        called["offline_confirm"] = True
        return True

    monkeypatch.setattr(type(window), "_confirm_capture_offline", _spy)
    window._capture_element()
    fake = fake_capture.instances[-1]
    assert not called["offline_confirm"], "两条腿都不可用时不该问「是否仅桌面捕获」"
    assert fake.closed, "应直接关闭会话（不留下无意义的等待）"
    assert window._capture_session is None
    assert not window.isMinimized(), "根本没机会捕获，不该最小化主窗"
    message = window.statusBar().currentMessage()
    assert "无法捕获" in message
    assert "Windows" in message and "插件" in message


def test_capture_reports_unavailable_reason(window, fake_capture):
    """pick 返回 unavailable/error → 状态栏给真实原因，不伪装成「已取消」。"""
    window._save_named_flow("cap7")
    window._capture_element()
    fake = fake_capture.instances[-1]
    fake.result = {"unavailable": True, "error": "desktop capture requires Windows"}
    _release_and_finish(fake, window)
    message = window.statusBar().currentMessage()
    assert "desktop capture requires Windows" in message
    assert "已取消" not in message


def test_capture_cancelled_on_window_close(window, fake_capture):
    window._save_named_flow("cap4")
    window._capture_element()
    fake = fake_capture.instances[-1]
    fake.result = None
    window._shutdown_run_manager()
    assert fake.cancelled
    assert window._capture_session is None
    _release_and_finish(fake, window)


def test_present_window_only_forces_top_for_short_lived_dialogs(qapp):
    """present_window 的 macOS 语义：短命对话框置顶，常驻窗口不置顶。

    macOS 会忽略后台应用的自激活请求（防焦点窃取），此时 raise()/
    activateWindow() 都不足以让新窗口出现在最前，窗口会停在浏览器之后、
    必须点一次 Dock 才看得见。对必须被看见的模态对话框，临时
    WindowStaysOnTopHint 是纯 Qt（零依赖）可用的硬保证；反过来常驻窗口若被
    置顶会一直压住用户其它应用，所以默认不置顶。
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDialog, QMainWindow

    from rpa_core.gui.app import present_window

    dialog = QDialog()
    assert not (dialog.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
    present_window(dialog, always_on_top=True)
    assert dialog.windowFlags() & Qt.WindowType.WindowStaysOnTopHint

    persistent = QMainWindow()
    present_window(persistent)
    assert not (persistent.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)

    dialog.close()
    persistent.close()
