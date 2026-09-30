"""过渡动画与「返回流程列表」判据（M49 P2）。

两组判据：

**动画**：① 环境变量能整关（``RPA_GUI_ANIMATIONS=0``）；② 关掉时窗口必须被置于完全
可见（不能停在半透明）；③ 控件淡入结束后特效要摘掉（残留会让后续 draw/hover 打架）；
④ 跳转链接线真的接上了（首次出现才淡入）。

**往返导航**：编辑器的「返回流程列表」只在存在工作台时出现；脏数据先问「是否放弃」，
用户取消要留在编辑器；关窗文案按「回工作台 / 退出」分叉——这是原先最容易误解的一点
（从工作台进来的编辑器，关窗提示写着「退出」，用户以为要退出整个程序）。
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6", reason="原生 GUI 为可选能力，需 uv sync --extra gui")

from PySide6.QtWidgets import QWidget  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from rpa_core.gui.app import build_application

    return build_application()


@pytest.fixture(scope="module")
def catalog(qapp):
    from rpa_core.catalog import load_catalog
    from rpa_core.cli import _commands_root

    return load_catalog(_commands_root())


@pytest.fixture(autouse=True)
def no_workbench(monkeypatch):
    """默认没有工作台：避免上一个用例留下的窗口被当做「工作台」再被触碰。"""
    from rpa_core.gui import app as app_module

    monkeypatch.setattr(app_module, "_HOME_WINDOW", None, raising=False)
    yield


@pytest.fixture()
def workbench(qapp, monkeypatch):
    """一个**活到用例结束**的工作台替身（被 GC 会让 C++ 对象悬空 → 原生崩溃）。"""
    from rpa_core.gui import app as app_module

    widget = QWidget()
    monkeypatch.setattr(app_module, "_HOME_WINDOW", widget, raising=False)
    yield widget
    widget.close()


class _FakeCloseEvent:
    def __init__(self) -> None:
        self.accepted = False
        self.ignored = False

    def accept(self) -> None:
        self.accepted = True

    def ignore(self) -> None:
        self.ignored = True


# ---- 动画 ---------------------------------------------------------------------


def test_animations_can_be_switched_off(monkeypatch):
    from rpa_core.gui import motion

    monkeypatch.delenv("RPA_GUI_ANIMATIONS", raising=False)
    assert motion.animations_enabled() is True
    monkeypatch.setenv("RPA_GUI_ANIMATIONS", "1")
    assert motion.animations_enabled() is True
    monkeypatch.setenv("RPA_GUI_ANIMATIONS", "0")
    assert motion.animations_enabled() is False


def test_fade_window_animates_opacity(qapp, monkeypatch):
    from rpa_core.gui import motion

    monkeypatch.delenv("RPA_GUI_ANIMATIONS", raising=False)
    window = QWidget()
    animation = motion.fade_window(window, duration=120)
    assert animation is not None
    assert animation.startValue() == 0.0
    assert animation.endValue() == 1.0
    assert animation.duration() == 120
    window.close()


def test_fade_window_fails_visible_when_disabled(qapp, monkeypatch):
    """关掉动画时必须把窗口置为完全可见——不能停在半透明（失败安全）。"""
    from rpa_core.gui import motion

    monkeypatch.setenv("RPA_GUI_ANIMATIONS", "0")
    window = QWidget()
    window.setWindowOpacity(0.05)  # 模拟上一次动画被中断在半透明状态
    assert motion.fade_window(window) is None
    assert window.windowOpacity() == pytest.approx(1.0)
    window.close()


def test_fade_widget_removes_effect_after_finish(qapp, monkeypatch):
    from rpa_core.gui import motion

    monkeypatch.delenv("RPA_GUI_ANIMATIONS", raising=False)
    widget = QWidget()
    animation = motion.fade_widget(widget, duration=60)
    assert animation is not None
    assert widget.graphicsEffect() is not None, "动画进行中应装着不透明度特效"
    animation.setCurrentTime(60)  # 直接推到终点，不依赖真实时间
    qapp.processEvents()
    assert widget.graphicsEffect() is None, "动画结束后不该留下不透明度特效"


def test_fade_widget_disabled_leaves_no_effect(qapp, monkeypatch):
    from rpa_core.gui import motion

    monkeypatch.setenv("RPA_GUI_ANIMATIONS", "0")
    widget = QWidget()
    assert motion.fade_widget(widget) is None
    assert widget.graphicsEffect() is None


def test_dock_first_show_fades_only_once(qapp, catalog, tmp_path, monkeypatch):
    """Dock 只在首次出现时淡入（反复开关不做动画，否则每次都闪）。"""
    from rpa_core.gui import app as app_module
    from rpa_core.gui.app import MainWindow

    calls: list[object] = []
    monkeypatch.setattr(app_module, "fade_widget", lambda w, **k: calls.append(w))
    window = MainWindow(catalog, workflows_root=tmp_path / "workflows")
    view_menu = [a.menu() for a in window.menuBar().actions() if a.text() == "视图"][0]

    def _action(text):
        return next(a for a in view_menu.actions() if a.text() == text)

    _action("数据表格").setChecked(True)
    _action("数据表格").setChecked(False)
    _action("数据表格").setChecked(True)
    assert len(calls) == 1, f"淡入被调用了 {len(calls)} 次（应只在首次出现）"
    window.close()


# ---- 往返导航 -----------------------------------------------------------------


def _window(catalog, tmp_path):
    from rpa_core.gui.app import MainWindow

    return MainWindow(catalog, workflows_root=tmp_path / "workflows")


def test_back_action_visible_only_with_workbench(qapp, catalog, tmp_path, workbench):
    """从工作台进来的编辑器显示「返回流程列表」；直接开 workflow 的（无工作台）不显示。"""
    with_home = _window(catalog, tmp_path)
    assert with_home._back_action.isVisible()
    assert "返回流程列表" in with_home._back_action.text()
    with_home.close()


def test_back_action_hidden_without_workbench(qapp, catalog, tmp_path):
    without = _window(catalog, tmp_path)
    assert not without._back_action.isVisible()
    without.close()


def test_back_to_home_closes_when_clean(qapp, catalog, tmp_path):
    window = _window(catalog, tmp_path)
    window.show()
    qapp.processEvents()
    window._back_to_home()
    assert not window.isVisible()


def test_back_to_home_asks_exactly_once_when_dirty(
    qapp, catalog, tmp_path, monkeypatch, workbench
):
    """脏数据回工作台**只问一次**（早先的实现会先问「放弃」再问「保存后返回」）。"""
    from rpa_core.gui import app as app_module

    asked: list[str] = []

    def _fake_question(_parent, _title, text, *args, **kwargs):
        asked.append(text)
        return app_module.QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(app_module.QMessageBox, "question", staticmethod(_fake_question))
    window = _window(catalog, tmp_path)
    window.show()
    window._dirty = True
    window._back_to_home()
    assert len(asked) == 1, f"弹了 {len(asked)} 次确认：{asked}"
    assert "返回流程列表" in asked[0]
    assert window.isVisible(), "用户取消后应留在编辑器"
    window._dirty = False
    window.close()


def test_back_to_home_closes_after_discard_confirmed(qapp, catalog, tmp_path, monkeypatch):
    from rpa_core.gui import app as app_module

    monkeypatch.setattr(
        app_module.QMessageBox,
        "question",
        staticmethod(lambda *a, **k: app_module.QMessageBox.StandardButton.Discard),
    )
    window = _window(catalog, tmp_path)
    window.show()
    window._dirty = True
    window._back_to_home()
    assert not window.isVisible()


def test_close_prompt_says_back_to_list_when_workbench_exists(
    qapp, catalog, tmp_path, monkeypatch, workbench
):
    """关窗文案分叉：有工作台说「返回流程列表」，没有才说「退出」。"""
    from rpa_core.gui import app as app_module

    captured: list[tuple] = []

    def _fake_question(*args, **kwargs):
        captured.append(args)
        return app_module.QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(app_module.QMessageBox, "question", staticmethod(_fake_question))

    window2 = _window(catalog, tmp_path)
    window2._dirty = True
    window2.closeEvent(_FakeCloseEvent())
    assert "返回流程列表" in captured[-1][2]
    assert "退出" not in captured[-1][2]
    window2.close()
