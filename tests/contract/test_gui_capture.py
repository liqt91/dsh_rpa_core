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

    类属性 ``offline`` 控制 extension_offline（默认在线）；``result``
    为 None 时 pick 返回 {"cancelled": True}（避免触发命名对话框）。
    """

    instances: list = []
    offline = False

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
    from PySide6.QtWidgets import QMessageBox

    from rpa_core.gui import element_panel

    window._save_named_flow("cap1d")
    original = {
        "kind": "browser",
        "selector": {"css": "#old"},
        "verifyCount": 1,
        "metadata": {"tag": "input"},
    }
    assert window.save_element_descriptor("dup", original)

    class FakeDialog:
        def __init__(self, descriptor, *, default_name, parent=None):
            pass

        def exec(self):
            from PySide6.QtWidgets import QDialog

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


def test_capture_cancelled_on_window_close(window, fake_capture):
    window._save_named_flow("cap4")
    window._capture_element()
    fake = fake_capture.instances[-1]
    fake.result = None
    window._shutdown_run_manager()
    assert fake.cancelled
    assert window._capture_session is None
    _release_and_finish(fake, window)
