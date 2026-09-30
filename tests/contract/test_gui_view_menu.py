"""「视图」菜单（Dock 统一入口）判据（M49 P1-2）。

原先五个 Dock 只在自己的操作路径上被动露面，用户没有「我现在想看看数据表格」这种
入口，也没有一键回到出厂布局的手段。本文件判四件事：

1. 五个 Dock 都有菜单条目（含 must-have 名单，防「入口与 Dock 一起删」的假绿灯）；
2. 勾上真的把 Dock 建出来并显示、取消勾选收回（懒创建语义不变）；
3. Dock 自己关掉时菜单勾选态同步（单一事实来源，不出现「勾着但没显示」）；
4. 「恢复默认布局」真的收掉全部 Dock 并把三栏比例打回出厂值。
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6", reason="原生 GUI 为可选能力，需 uv sync --extra gui")


@pytest.fixture(scope="module")
def qapp():
    from rpa_core.gui.app import build_application

    return build_application()


@pytest.fixture(scope="module")
def catalog(qapp):
    from rpa_core.catalog import load_catalog
    from rpa_core.cli import _commands_root

    return load_catalog(_commands_root())


@pytest.fixture()
def window(qapp, catalog, tmp_path):
    from rpa_core.gui.app import MainWindow

    win = MainWindow(catalog, workflows_root=tmp_path / "workflows")
    win.show()
    qapp.processEvents()
    yield win
    win.close()


def _view_menu(window):
    return [a.menu() for a in window.menuBar().actions() if a.text() == "视图"][0]


def _action(window, text):
    return next(a for a in _view_menu(window).actions() if a.text() == text)


# must-have 名单：Dock 入口一个都不能少（只比「菜单里有什么」抓不到悄悄删入口）
EXPECTED_DOCKS = ("元素库", "运行", "运行历史", "数据表格", "变量面板")


def test_view_menu_lists_every_dock(window):
    texts = [a.text() for a in _view_menu(window).actions() if a.text()]
    for label in EXPECTED_DOCKS:
        assert label in texts, f"视图菜单缺 Dock 入口：{label}"
    assert "恢复默认布局" in texts


def test_view_menu_entries_are_checkable(window):
    for label in EXPECTED_DOCKS:
        assert _action(window, label).isCheckable(), label


def test_checked_entry_creates_and_shows_dock(window, qapp):
    action = _action(window, "数据表格")
    assert getattr(window, "_table_dock_widget", None) is None, "点之前不该建 Dock（懒创建）"
    action.setChecked(True)
    qapp.processEvents()
    dock = window._table_dock()
    assert dock.isVisibleTo(window), "勾上后 Dock 应显示"

    action.setChecked(False)
    qapp.processEvents()
    assert not dock.isVisibleTo(window), "取消勾选后 Dock 应收起"


def test_closing_dock_updates_menu_check_state(window, qapp):
    """从 Dock 自己的关闭按钮收起时，菜单勾选态要跟着变（不出现「勾着但没显示」）。"""
    action = _action(window, "元素库")
    action.setChecked(True)
    qapp.processEvents()
    assert action.isChecked()

    window._elements_dock().hide()
    qapp.processEvents()
    assert not action.isChecked()


def test_reset_layout_closes_docks_and_restores_splitter(window, qapp):
    from PySide6.QtWidgets import QDockWidget

    from rpa_core.gui.app import DEFAULT_SPLITTER_SIZES

    for label in ("元素库", "数据表格"):
        _action(window, label).setChecked(True)
    window._splitter.setSizes([600, 300, 300])
    qapp.processEvents()
    skewed = window._splitter.sizes()

    _action(window, "恢复默认布局").trigger()
    qapp.processEvents()
    sizes = window._splitter.sizes()

    assert all(not dock.isVisibleTo(window) for dock in window.findChildren(QDockWidget))
    assert sizes != skewed, "比例没有被打回出厂值"
    total, default_total = sum(sizes), sum(DEFAULT_SPLITTER_SIZES)
    for index, expected in enumerate(DEFAULT_SPLITTER_SIZES):
        assert sizes[index] / total == pytest.approx(expected / default_total, abs=0.03), (
            sizes,
            DEFAULT_SPLITTER_SIZES,
        )
