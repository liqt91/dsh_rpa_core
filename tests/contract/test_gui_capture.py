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

import math
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
    # 非 None 时 pick 抛该异常（验「会话异常也必须收场」）
    fail_with: BaseException | None = None

    def __init__(self, *, desktop_factory, **kwargs):
        self.kwargs = kwargs
        self.started = False
        self.closed = False
        self.cancelled = False
        self.pick_timeout = None  # pick 收到的等待上限（供 M41 S5「无上限」判据核对）
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

    @property
    def extension_unresponsive(self) -> bool:
        return False

    def pick(self, timeout_seconds=90):
        self.pick_timeout = timeout_seconds  # 供「无上限」判据核对真正传下来的值
        self._gate.wait(timeout=10)
        # 读 `self.fail_with`（而非 type(self).fail_with）：用例按实例赋值，读类属性
        # 会拿到 None、让「本该抛异常」的路径悄悄走成成功捕获——那会弹真模态对话框，
        # offscreen 下永久阻塞（本片真踩过，整轮测试卡 9 分钟）。
        if self.fail_with is not None:
            raise self.fail_with
        return dict(self.result) if self.result is not None else {"cancelled": True}

    def cancel(self):
        # 真会话 cancel 后 pick 拿到的是 {"cancelled": True}：替身要同形，
        # 否则「取消」会被下游当成一次成功捕获（弹出命名对话框）。
        self.cancelled = True
        self.result = None
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
    FakeHybridSession.fail_with = None
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


def test_capture_second_click_cancels_running_session(window, fake_capture):
    """第二次点「捕获元素」= **取消**进行中的捕获，不是只回一句「进行中」。

    回归（维护者 2026-09-28 报障「再次点击提示已有捕获任务进行中」）：旧行为只拒绝，
    用户被锁到超时——捕获期唯一的确定性出口不能是「等 90 秒」。取消后必须：会话真的
    收到 cancel（桌面 agent 子进程要回收）、主窗还原、浮窗关闭、状态复位。
    """
    window._save_named_flow("cap3")
    window._capture_element()
    fake = fake_capture.instances[-1]
    assert window._capture_float is not None, "捕获期间必须有浮窗（主窗已最小化）"

    window._capture_element()  # 第二次点击 = 取消
    assert len(fake_capture.instances) == 1, "第二次点击不该另起会话"
    assert fake.cancelled, "第二次点击必须真的取消会话（回收桌面 agent）"
    assert "已取消" in window.statusBar().currentMessage()
    assert _pump_until(lambda: window._capture_session is None)
    assert not window.isMinimized(), "取消后主窗要还原"
    assert window._capture_float is None, "取消后浮窗要关闭"


def test_capture_float_reports_both_legs(window, fake_capture):
    """浮窗把两条腿的状态**分列**（用户据此决定去网页里点还是去桌面按 F9）。"""
    window._save_named_flow("cap3b")
    window._capture_element()
    float_window = window._capture_float
    assert float_window is not None
    assert "就绪" in float_window.web_label.text()
    assert "就绪" in float_window.desktop_label.text()
    assert "F9" in float_window.gesture_label.text()
    fake = fake_capture.instances[-1]
    assert _pump_until(lambda: float_window.isVisible())
    fake.result = None
    _release_and_finish(fake, window)


def test_capture_float_cancel_button_cancels_session(window, fake_capture):
    """浮窗的「取消捕获」是捕获期唯一的确定性出口（不依赖任何一条腿响应键盘）。"""
    window._save_named_flow("cap3c")
    window._capture_element()
    float_window = window._capture_float
    fake = fake_capture.instances[-1]
    float_window.cancel_button.click()
    assert fake.cancelled
    assert float_window.cancelling, "按下后按钮要变成「正在取消…」，避免重复点击"
    assert _pump_until(lambda: window._capture_session is None)
    assert window._capture_float is None


def test_capture_pick_exception_still_resets_and_restores(window, fake_capture):
    """pick 抛异常也必须收场：复位会话 + 还原主窗。

    没有这层保护时 `finished` 永不 emit → `_capture_session` 永不复位、主窗永不还原，
    用户侧就是「点了没反应，再点说进行中」，只能重启进程。
    """
    window._save_named_flow("cap8")
    window._capture_element()
    fake = fake_capture.instances[-1]
    fake.result = None  # 双保险：即使异常没抛出来也不该走到命名对话框
    fake.fail_with = RuntimeError("boom")
    fake._gate.set()
    assert _pump_until(lambda: window._capture_session is None)
    assert not window.isMinimized(), "异常后主窗必须还原"
    assert window._capture_float is None
    assert "捕获失败" in window.statusBar().currentMessage()


def test_capture_late_result_from_replaced_session_is_ignored(
    window, fake_capture, monkeypatch
):
    """已被替换的旧会话的迟到结果不得影响当前会话（取消后立刻重开的竞态）。

    「第二次点击 = 取消」之后用户可以马上再点一次重开捕获，两个 work() 线程会短暂
    并存：旧线程收尾时若照旧复位状态，就会把**新会话**的会话与窗口状态一并清掉。

    这里必须桩掉确认对话框：判据一旦被破坏（守卫被摘），这条迟到结果会一路走到
    `_confirm_element_save` → 真模态对话框 → offscreen 下**永久阻塞**，负向验证拿到
    的就不是「干净变红」而是「挂住」。桩成「用户取消」后，破坏表现为干净的断言失败。
    """
    monkeypatch.setattr(
        type(window), "_confirm_element_save", lambda self, result: None
    )
    window._save_named_flow("cap9")
    window._capture_element()
    old_session = fake_capture.instances[-1]
    # 模拟「取消中」：当前会话换成另一个对象（用户已重开），此时旧会话回报结果
    new_session = object()
    window._capture_session = new_session
    window._on_element_captured(dict(_BROWSER_DESCRIPTOR), old_session)
    assert window._capture_session is new_session, "旧会话的迟到结果把新会话清掉了"
    assert "已保存元素" not in window.statusBar().currentMessage()
    # 收掉后台仍在等的那条 work() 线程：result=None → 返回 cancelled，不会弹对话框
    old_session.result = None
    old_session._gate.set()
    window._capture_session = None


def test_capture_channel_status_texts_split_three_states():
    """通道文案三态分离：离线（去装插件）/ 未响应（重开浏览器）/ 就绪，不能合并。"""
    from rpa_core.gui.app import capture_desktop_status, capture_web_status

    class _Session:
        def __init__(self, offline=False, unresponsive=False, desktop_offline=False):
            self.extension_offline = offline
            self.extension_unresponsive = unresponsive
            self.desktop_offline = desktop_offline

    offline_text, offline_ok = capture_web_status(_Session(offline=True))
    unresponsive_text, unresponsive_ok = capture_web_status(_Session(unresponsive=True))
    ready_text, ready_ok = capture_web_status(_Session())
    assert not offline_ok and "离线" in offline_text
    assert not unresponsive_ok and "未响应" in unresponsive_text
    assert ready_ok and "就绪" in ready_text
    # 三种形态的文案两两不同：合并成一句会让用户分不清该去装插件还是重开浏览器
    assert len({offline_text, unresponsive_text, ready_text}) == 3

    desktop_text, desktop_ok = capture_desktop_status(_Session(desktop_offline=True))
    assert not desktop_ok and "Windows" in desktop_text
    assert capture_desktop_status(_Session())[1] is True


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


def test_capture_float_dodges_the_cursor(window, fake_capture):
    """浮窗躲开鼠标路径（M41 S3）：鼠标靠近默认位置时翻到另一侧。

    回归（维护者 2026-09-28 报「鼠标即将移动到悬浮框的时候，悬浮框移动到屏幕
    另一侧，避免挡住实际需要捕获的元素」）：浮窗是**真实窗口**（不吃点击穿透），
    压在鼠标路径上会同时挡住视觉与点击——用户会「点不中」它下方的元素。
    """
    from PySide6.QtCore import QPoint

    window._save_named_flow("cap3d")
    window._capture_element()
    float_window = window._capture_float
    assert float_window is not None

    float_window.avoid_cursor(None)  # 不避让 → 默认位置
    home = QPoint(float_window.x(), float_window.y())

    float_window.avoid_cursor(QPoint(home.x() + 10, home.y() + 10))  # 鼠标压上来
    dodged = QPoint(float_window.x(), float_window.y())
    assert dodged != home, "鼠标压上来时必须让开"
    assert dodged.x() < home.x() and dodged.y() < home.y(), "要让到另一侧（左上）"

    float_window.avoid_cursor(QPoint(0, 0))  # 鼠标走开 → 回默认位置
    assert QPoint(float_window.x(), float_window.y()) == home, "不能一去不返"

    fake = fake_capture.instances[-1]
    fake.result = None
    _release_and_finish(fake, window)


def test_capture_float_keeps_place_once_the_user_moves_it(window):
    """用户手动拖过之后不再自动避让（把位置决定权还给用户）。"""
    import inspect

    from PySide6.QtCore import QPoint

    from rpa_core.gui import capture_float as cf

    float_window = window._capture_float_window()
    float_window.avoid_cursor(None)
    home = QPoint(float_window.x(), float_window.y())

    # 拖动入口必须**自己**置位（不是靠调用方补设）——接一半就会「拖完又被自动挪走」
    assert "user_positioned = True" in inspect.getsource(
        cf.CaptureFloatWindow.mouseMoveEvent
    )

    float_window.user_positioned = True
    float_window.avoid_cursor(QPoint(home.x() + 10, home.y() + 10))
    assert QPoint(float_window.x(), float_window.y()) == home, "拖过后不得再自动挪"


def test_capture_float_avoidance_is_wired_into_the_capture_flow(window, fake_capture):
    """四处接线各钉一条（摘任一处都是静默退化，行为判据未必看得见）。"""
    import inspect

    from rpa_core.gui import app as app_module
    from rpa_core.gui import capture_float as cf

    # ① 初始定位就带鼠标（否则浮窗打开的一瞬间就压在鼠标下）
    assert "avoid_cursor(" in inspect.getsource(
        app_module.MainWindow._show_capture_float
    )
    # ② 避让节拍真的在跑（只有初始定位 = 鼠标追上来后就不再让）
    assert "avoid_cursor(" in inspect.getsource(
        app_module.MainWindow._tick_capture_avoid
    )
    # ③ 会话结束必须停表（否则浮窗拆除后定时器还在空打）
    assert "_capture_avoid_timer" in inspect.getsource(
        app_module.MainWindow._close_capture_float
    )
    # ④ 定位真的走纯函数（否则几何判据是一纸空文）
    assert "capture_float_origin(" in inspect.getsource(
        cf.CaptureFloatWindow.avoid_cursor
    )

    window._save_named_flow("cap3e")
    window._capture_element()
    timer = window._capture_avoid_timer
    assert timer is not None, "避让节拍必须启动"
    assert timer.isActive()
    assert timer.interval() <= 50, "节拍要快于鼠标移动，否则「即将」来不及"
    fake = fake_capture.instances[-1]
    fake.result = None
    _release_and_finish(fake, window)
    assert window._capture_avoid_timer is None, "收尾要停表并清空避让计时器"


def test_capture_float_never_shows_a_countdown(window, fake_capture):
    """浮窗不显示倒计时（M41 S4）：上限是兜底，不是用户要管的时限。

    判据打在**浮窗真实文案**上，而不是「`CaptureFloatWindow` 没有 `tick` 方法」——
    形状断言换个方法名就绕过了。文案也不只扫构造那一刻：倒计时是**定时器周期性刷新**
    的（500ms 节拍），所以先让节拍真的跑一轮再扫。
    """
    import inspect
    import re

    from PySide6.QtWidgets import QApplication, QLabel

    from rpa_core.gui import capture_float as cf

    window._save_named_flow("cap3f")
    window._capture_element()
    fake = fake_capture.instances[-1]
    float_window = window._capture_float
    assert float_window is not None
    assert window._capture_timer is not None and window._capture_timer.isActive()

    # 让 500ms 的状态节拍至少跑一轮：倒计时若被加回来，是由它周期刷新的
    deadline = time.monotonic() + 0.7
    while time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.02)

    texts = " | ".join(label.text() for label in float_window.findChildren(QLabel))
    assert "剩余" not in texts, f"浮窗不该显示倒计时，实际文案：{texts}"
    assert "倒计时" not in texts, f"浮窗不该显示倒计时，实际文案：{texts}"
    # 更粗的形状：任何「N 秒」都不该出现在浮窗上（换个字面量也绕不过）
    assert not re.search(r"\d+\s*秒", texts), f"浮窗不该出现秒数：{texts}"
    # 缺口要清干净：不留「有方法/参数但没人用」的形状
    assert not hasattr(cf.CaptureFloatWindow, "tick")
    assert "timeout_seconds" not in inspect.signature(
        cf.CaptureFloatWindow.show_capture
    ).parameters

    fake.result = None
    _release_and_finish(fake, window)


def test_capture_never_gives_up_on_its_own(window, fake_capture):
    """GUI 不自动放弃捕获（M41 S5）：等多久由用户决定。

    维护者 2026-09-28 拍板去掉 90 秒自动收场——它唯一的作用是「替用户放弃」，代价却是
    把**认真挑元素超过 90 秒**的用户已经挑好的元素静默丢掉（`pick` 返回 `{"timeout": True}`，
    没有 descriptor，状态栏只说一句「请重试」）。收场出口本就完备：捕获到结果 / 两条腿都
    出局（`_exhausted` **立即**收场，不等 deadline）/ 用户取消（浮窗按钮 · 再点捕获 · Esc）。

    判据钉**三处**：常量本身、构造会话时传下去的值、`pick` 实际收到的值。只钉常量会漏掉
    「常量是 inf 但没传下去」这一类（本仓反复踩到「纯函数/常量正确 ≠ 真的用上了」）。
    """
    from rpa_core.gui import app as gui_app

    assert math.isinf(gui_app.CAPTURE_TIMEOUT_SECONDS), (
        "捕获等待必须无上限（不再自动放弃）；若要改回有限值，先回答"
        "「超时把用户已挑好的元素丢掉」怎么处理"
    )
    window._save_named_flow("cap3g")
    window._capture_element()
    fake = fake_capture.instances[-1]
    assert math.isinf(fake.kwargs.get("timeout_seconds", 0)), (
        f"构造会话时必须把无上限传下去，实际：{fake.kwargs.get('timeout_seconds')!r}"
    )
    assert math.isinf(fake.pick_timeout), (
        f"pick 必须用同一个无上限值，实际：{fake.pick_timeout!r}"
    )

    fake.result = None
    _release_and_finish(fake, window)
