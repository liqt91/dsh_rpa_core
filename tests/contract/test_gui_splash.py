"""启动加载提示契约测试（M41）。

维护者 2026-09-28：「启动前如果要初始化，可以有个加载提示。」

归因（``.harness/spike/probe_m41_startup.py``，offscreen 分段计时，**取首次采样**——
真实进程只经历一次冷启动）：窗口出现前 ≈ 976 ms：

    load_catalog(commands/)   420.5 ms   ← 首次含惰性 import
    build_application()       275.8 ms   ← QApplication 构造 + Qt 平台插件
    HomeWindow(...) 构造       68.5 ms
    home.show() + 首帧        211.3 ms

判据（本文件钉住的三条）：
① 卡片在 ``with`` 内**可见**、退出后**不可见**（含初始化抛异常那条路径）；
② 不抢键盘焦点（初始化期间用户敲的键不能被它吃掉）；
③ 接线：``run_gui`` 用 ``with`` 包住 load_catalog→show，且 ``show()`` 之后泵了一轮事件
   （首帧 211 ms 必须发生在卡片在场时，否则「提示」什么也没盖住）。

测试在 offscreen Qt 平台运行；缺 PySide6 时整组跳过。
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")


@pytest.fixture(scope="module")
def qapp():
    from rpa_core.gui.app import build_application

    return build_application()


def test_splash_visible_inside_with_block_and_closed_after(qapp):
    """① 卡片在 with 内可见，退出即关闭（不留残影挡屏）。"""
    from rpa_core.gui.splash import startup_splash

    with startup_splash(qapp) as splash:
        assert splash.isVisible() is True
    assert splash.isVisible() is False


def test_splash_closes_when_initialization_raises(qapp):
    """①' 初始化抛异常时卡片也必须撤掉——否则它留在屏幕上挡住真正的报错。"""
    from rpa_core.gui import splash as splash_module

    with pytest.raises(RuntimeError, match="boom"):
        with splash_module.startup_splash(qapp) as splash:
            raise RuntimeError("boom")

    assert splash.isVisible() is False


def test_splash_does_not_take_keyboard_focus(qapp):
    """② 不抢焦点：初始化期间用户敲的键不该被卡片吃掉。"""
    from PySide6.QtCore import Qt

    from rpa_core.gui.splash import open_startup_splash

    splash = open_startup_splash(qapp)
    try:
        assert splash.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
        assert splash.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    finally:
        splash.close()


def test_close_startup_splash_is_idempotent():
    """收尾路径可以无条件调（幂等），调用方不必先判空。"""
    from rpa_core.gui.splash import close_startup_splash

    close_startup_splash(None)


def test_open_startup_splash_pumps_events(qapp, monkeypatch):
    """显示后必须**泵一轮事件**，否则卡片要等初始化结束才被绘制。

    offscreen 下「泵没泵」看不出差别（`show()` 之后 `isVisible()` 恒 True），所以这里把
    `processEvents` 换成记录器——这条是**唯一**能自动拦下「忘了泵事件」的判据：
    少了它，加载提示会变成「初始化跑完才画出来」，等于什么都没盖住。
    """
    from rpa_core.gui import splash as splash_module

    calls: list[tuple] = []
    monkeypatch.setattr(qapp, "processEvents", lambda *args, **kwargs: calls.append(args))

    splash = splash_module.open_startup_splash(qapp)
    try:
        assert calls, "open_startup_splash 显示后没有泵事件"
    finally:
        monkeypatch.undo()
        splash.close()


def test_run_gui_wraps_initialization_in_startup_splash():
    """③ 接线：splash 包住 load_catalog→窗口构造→show，show 之后泵一轮事件。

    读源码断言（同 M40 的 `test_run_gui_wires_sliced_refresh_on_workbench` 口径）：
    真实 `run_gui` 会进 `app.exec()` 阻塞，跑不起来；这里钉的是**顺序与缩进**——
    少了任何一条，卡片要么没盖住该盖的那段，要么提前撤掉。
    """
    app_py = Path(__file__).resolve().parents[2] / "src" / "rpa_core" / "gui" / "app.py"
    source = app_py.read_text(encoding="utf-8")
    start = source.index("def run_gui(")
    try:
        end = source.index("\ndef ", start + 1)
    except ValueError:  # run_gui 是文件最后一个函数
        end = len(source)
    body = source[start:end]
    lines = body.splitlines()

    def _first(predicate, label: str) -> int:
        for index, line in enumerate(lines):
            if predicate(line):
                return index
        raise AssertionError(f"run_gui 里找不到 {label}")

    with_line = _first(lambda ln: "with startup_splash(app):" in ln, "with startup_splash(app):")
    catalog_line = _first(lambda ln: "catalog = load_catalog(" in ln, "catalog = load_catalog(")
    show_line = _first(lambda ln: "window.show()" in ln, "window.show()")
    pump_line = _first(
        lambda ln: "app.processEvents()" in ln and ln.startswith("        "),
        "with 块内的 app.processEvents()",
    )

    # 三行都要在 with 块内（8 空格缩进 = 函数体 4 + with 4）
    for index, label in (
        (catalog_line, "catalog = load_catalog"),
        (show_line, "window.show()"),
        (pump_line, "app.processEvents()"),
    ):
        assert lines[index].startswith("        "), f"{label} 不在 with 块内"

    assert with_line < catalog_line, "命令目录必须在卡片起来之后再读"
    assert with_line < show_line < pump_line, "首帧泵事件必须在 show() 之后"
    assert "startup_splash" in body and "app.exec()" in body
