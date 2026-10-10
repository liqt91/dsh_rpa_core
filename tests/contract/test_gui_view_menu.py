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


# ---- 底部页签（M52 补丁：元素库不再是右侧竖条，与运行日志同片切换）-------------

def _tab_strip_with(window, *labels):
    """找到同时含全部 ``labels`` 的那条页签（找不到返回 None）。"""
    from PySide6.QtWidgets import QTabBar

    wanted = set(labels)
    for bar in window.findChildren(QTabBar):
        texts = [bar.tabText(i) for i in range(bar.count())]
        if wanted <= set(texts):
            return texts
    return None


def _assert_bottom_tab_strip(win, qapp):
    """元素库与运行落在同一片**底部**页签，且元素库在左（对齐影刀）。"""
    from PySide6.QtCore import Qt

    run = win._run_dock()
    els = win._elements_dock()
    run.show()
    els.show()
    qapp.processEvents()

    assert win.dockWidgetArea(els) == Qt.DockWidgetArea.BottomDockWidgetArea, "元素库应落底部"
    assert win.dockWidgetArea(run) == Qt.DockWidgetArea.BottomDockWidgetArea
    texts = _tab_strip_with(win, "元素库", "运行")
    assert texts is not None, "元素库与运行没有落在同一片页签里"
    assert texts.index("元素库") < texts.index("运行"), texts


def test_elements_and_run_share_one_bottom_tab_strip(window, qapp):
    """先建运行再建元素库（反着建）也要落到同一片底部页签。

    两处 Dock 都是**懒创建**，页签先后本会随「用户先点哪个」漂移；这里故意用与预热
    相反的创建顺序，确认 `_stack_bottom_panels` 把顺序定死了、不依赖创建时机。
    """
    _assert_bottom_tab_strip(window, qapp)


def test_elements_dock_tabs_with_run_when_elements_built_first(qapp, catalog, tmp_path):
    """元素库先建（`_prewarm_elements_dock` 的真实现状）也要落到同一片、同样顺序。"""
    from rpa_core.gui.app import MainWindow

    win = MainWindow(catalog, workflows_root=tmp_path / "workflows")
    win.show()
    qapp.processEvents()
    try:
        win._elements_dock()  # 先元素库（预热路径的真实顺序）
        _assert_bottom_tab_strip(win, qapp)
    finally:
        win.close()


def test_elements_and_history_share_bottom_tab_strip(window, qapp):
    """只开「元素库」+「运行历史」（运行面板**尚未建**）时也必须同片页签、不能并列。

    这正是维护者截图里的场景：没跑过流程 ⇒ 运行面板没建，旧实现只在「元素库 + 运行
    都建好」时才 tabify ⇒ 元素库与运行历史各占一片、左右并列。判据钉住它俩同片。
    """
    els = window._elements_dock()
    hist = window._history_dock()
    els.show()
    hist.show()
    qapp.processEvents()

    texts = _tab_strip_with(window, "元素库", "运行历史")
    assert texts is not None, "元素库与运行历史并列了（没落在同一片页签）"
    assert texts.index("元素库") < texts.index("运行历史"), texts


def test_all_bottom_docks_share_one_tab_strip(window, qapp):
    """四个底部面板（元素库/运行/运行历史/数据表格）全部落在**同一条**页签上。"""
    for getter in ("_elements_dock", "_run_dock", "_history_dock", "_table_dock"):
        getattr(window, getter)().show()
    qapp.processEvents()

    labels = ("元素库", "运行", "运行历史", "数据表格")
    texts = _tab_strip_with(window, *labels)
    assert texts is not None, "底部面板没有全部并入同一条页签"
    assert texts[0] == "元素库", f"元素库应排最前（锚），实际 {texts}"


def _current_tab_text(window, *must_include):
    """含全部 ``must_include`` 的那条页签上，**当前选中页**的文本（没有则 None）。"""
    from PySide6.QtWidgets import QTabBar

    wanted = set(must_include)
    for bar in window.findChildren(QTabBar):
        texts = [bar.tabText(i) for i in range(bar.count())]
        if wanted <= set(texts):
            return bar.tabText(bar.currentIndex())
    return None


def test_toggle_buttons_raise_their_tab(window, qapp):
    """点工具栏「运行历史」/「数据表格」后当前页签要切过去。

    同片之后 `show()` 只让「这一片」可见、当前页签可能还停在元素库那页——不 `raise_()`
    用户会以为没打开（M52 补丁修的就是这个）。offscreen 下 `QTabBar.currentIndex` 可读，
    正好钉住「顶页」这个行为。
    """
    window._elements_dock().show()  # 先让锚可见，页签条才生成
    qapp.processEvents()

    window._toggle_history_dock()
    qapp.processEvents()
    assert _current_tab_text(window, "元素库", "运行历史") == "运行历史"

    window._toggle_table_dock()
    qapp.processEvents()
    assert _current_tab_text(window, "元素库", "数据表格") == "数据表格"
