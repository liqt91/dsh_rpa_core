"""命令面板（Ctrl+P）判据（M49 P3）。

分三层判：

1. **纯函数层**：``rank_entries`` 的匹配档位与稳定性（不需要窗口，也不需要键盘事件）；
2. **接线层**：Ctrl+P 的 QAction 存在、键位/上下文正确、挂在编辑菜单、注册在窗口上；
   三条分支（插入指令 / 跳转节点 / 取消）各一条判据，用 ``palette_prompt`` 这个模块级
   接缝驱动——它同时是「取消不产生副作用」的判据入口；
3. **外壳层**：对话框自身的过滤/选中/↑↓ 交互（离屏直接驱动控件，不 ``exec()``）。
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6", reason="原生 GUI 为可选能力，需 uv sync --extra gui")

from rpa_core.gui.palette import (  # noqa: E402
    KIND_COMMAND,
    KIND_NODE,
    MAX_RESULTS,
    PaletteEntry,
    rank_entries,
)


def _entry(label: str, payload: str, detail: str = "") -> PaletteEntry:
    return PaletteEntry(KIND_COMMAND, label, detail, payload)


# ---- 1. 纯函数层：排序 -------------------------------------------------------


def test_rank_prefers_label_over_payload_over_detail() -> None:
    """同一次查询命中不同位置时的优先级：标题 > 标识 > 副文本。"""
    by_label = _entry("打开网页", "browser.navigate")
    by_payload = _entry("导航到页面", "open.navigate")
    by_detail = _entry("别的名字", "x.y", "说明里提到 打开网页")

    ranked = rank_entries([by_detail, by_payload, by_label], "打开网页")
    assert ranked == [by_label, by_detail], "标题命中要排在副文本命中之前"

    ranked = rank_entries([by_detail, by_payload, by_label], "navigate")
    assert ranked[0] is by_payload, "标识命中要排在副文本命中之前"


def test_rank_supports_command_id_subsequence() -> None:
    """打 id 缩写（"brnav"）也要能找到 browser.navigate。"""
    entry = _entry("打开网页", "browser.navigate")
    assert rank_entries([entry], "brnav") == [entry]
    # 顺序不对就不是子序列，不该命中
    assert rank_entries([entry], "navbr") == []


def test_rank_empty_query_keeps_order_and_applies_limit() -> None:
    entries = [_entry(f"命令{index}", f"a.b{index}") for index in range(MAX_RESULTS + 5)]
    assert rank_entries(entries, "") == entries[:MAX_RESULTS]
    assert rank_entries(entries, "", limit=3) == entries[:3]


def test_rank_is_stable_for_equal_scores() -> None:
    """同档位同位置时保持输入顺序（否则同一个查询两次打开可能给出不同排序）。"""
    entries = [_entry("同名", f"a.b{index}") for index in range(5)]
    assert rank_entries(entries, "同名") == entries


def test_rank_returns_empty_when_nothing_matches() -> None:
    assert rank_entries([_entry("打开网页", "browser.navigate")], "zzz") == []


# ---- 2. 接线层：Ctrl+P 与三条分支 --------------------------------------------


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
    # 收场前清脏标记：插入指令的用例会把窗口置脏，而 closeEvent 在脏窗口上会弹
    # 「是否保存」的模态框——离屏环境没人点它，整个测试进程会**永久挂住**
    win._dirty = False
    win.close()


def test_palette_action_is_ctrl_p_registered_and_in_edit_menu(window):
    from PySide6.QtCore import Qt

    action = window.palette_action
    assert action.shortcut().toString() == "Ctrl+P"
    assert action.shortcutContext() == Qt.ShortcutContext.WindowShortcut
    assert action in window.actions(), "没在窗口上注册，快捷键不会生效"
    edit_menu = [a.menu() for a in window.menuBar().actions() if a.text() == "编辑"][0]
    assert action in edit_menu.actions(), "命令面板要在编辑菜单里可见（快捷键不写在界面上）"


def test_shortcuts_help_lists_palette_shortcut():
    """快捷键一览必须同步收录 Ctrl+P（新增快捷键最容易漏掉的一处）。"""
    from rpa_core.gui.app import SHORTCUTS_HELP

    keys = [key for key, _desc in SHORTCUTS_HELP]
    assert len(keys) == len(set(keys)), f"快捷键一览有重复键位：{keys}"
    assert ("Ctrl+P", "命令面板") in SHORTCUTS_HELP


def test_palette_entries_cover_catalog_commands_and_canvas_nodes(window):
    """两类条目都要有，且节点集合与画布枚举**逐项相等**（抓枚举漂移）。"""
    from rpa_core.gui.app import _CONTROL_COMMANDS
    from rpa_core.gui.flow_model import ROLE_NODE_ID

    entries = window._palette_entries()
    kinds = {entry.kind for entry in entries}
    assert kinds == {KIND_COMMAND, KIND_NODE}

    commands = {e.payload for e in entries if e.kind == KIND_COMMAND}
    # must-have：catalog 指令与控制指令都在（只比「两类都有」抓不到整类被换掉）
    assert {"browser.navigate", "data.appendText", "@else"} <= commands
    # 逐项相等：可插入集 = catalog ∪ 控制指令。少了说明有命令搜不到，
    # 多了说明面板在造左树里没有的条目（两处菜单口径会分叉）
    control_ids = {command_id for command_id, _label, _tip in _CONTROL_COMMANDS}
    assert commands == set(window.catalog) | control_ids

    enumerated = {
        str(item.data(ROLE_NODE_ID))
        for item in window._iter_canvas_items()
        if item.data(ROLE_NODE_ID)
    }
    nodes = {e.payload for e in entries if e.kind == KIND_NODE}
    assert nodes == enumerated, f"面板节点集与画布枚举不一致：{nodes ^ enumerated}"
    assert {"open", "read"} <= nodes  # must-have：示例流程里的已知节点


def test_show_palette_inserts_command(window, monkeypatch, qapp):
    """选中指令条目 → 走 add_command（画布新增节点并选中）。"""
    from rpa_core.gui import app as app_module
    from rpa_core.gui.flow_model import ROLE_COMMAND_ID

    before = len(window._iter_canvas_items())
    chosen = PaletteEntry(KIND_COMMAND, "追加文本", "data.appendText", "data.appendText")
    monkeypatch.setattr(app_module, "palette_prompt", lambda entries, parent=None: chosen)

    window._show_command_palette()
    qapp.processEvents()

    assert len(window._iter_canvas_items()) == before + 1, "指令没有被插入画布"
    current = window.flow_model.itemFromIndex(window.canvas_view.currentIndex())
    assert current.data(ROLE_COMMAND_ID) == "data.appendText"


def test_show_palette_jumps_to_node(window, monkeypatch, qapp):
    """选中节点条目 → 画布定位到该节点（不是新增）。"""
    from rpa_core.gui import app as app_module
    from rpa_core.gui.flow_model import ROLE_NODE_ID

    before = len(window._iter_canvas_items())
    chosen = PaletteEntry(KIND_NODE, "读取标题", "read", "read")
    monkeypatch.setattr(app_module, "palette_prompt", lambda entries, parent=None: chosen)

    window._show_command_palette()
    qapp.processEvents()

    assert len(window._iter_canvas_items()) == before, "跳转不该改动流程"
    current = window.flow_model.itemFromIndex(window.canvas_view.currentIndex())
    assert current.data(ROLE_NODE_ID) == "read"
    assert "read" in window.statusBar().currentMessage()


def test_show_palette_passes_entries_and_parent(window, monkeypatch):
    """接缝要拿到完整条目与窗口作父（父窗口决定模态归属）。"""
    from rpa_core.gui import app as app_module

    seen: dict = {}

    def fake(entries, parent=None):
        seen["entries"] = entries
        seen["parent"] = parent
        return None

    monkeypatch.setattr(app_module, "palette_prompt", fake)
    window._show_command_palette()

    assert seen["parent"] is window
    assert any(e.payload == "browser.navigate" for e in seen["entries"])


def test_show_palette_cancel_has_no_side_effect(window, monkeypatch, qapp):
    """取消（返回 None）不插节点、不跳转、不改选中项。"""
    from rpa_core.gui import app as app_module

    before_items = len(window._iter_canvas_items())
    before_index = window.canvas_view.currentIndex()
    monkeypatch.setattr(app_module, "palette_prompt", lambda entries, parent=None: None)

    window._show_command_palette()
    qapp.processEvents()

    assert len(window._iter_canvas_items()) == before_items
    assert window.canvas_view.currentIndex() == before_index


def test_reveal_canvas_node_reports_missing_node(window):
    """节点已被删掉时只提示，不静默失败、不抛异常。"""
    assert window._reveal_canvas_node("不存在的节点") is False
    assert "不存在" in window.statusBar().currentMessage()


def test_palette_prompt_returns_none_on_reject(monkeypatch):
    """``palette_prompt`` 的取消分支（exec 返回 Rejected）——外壳与结果解耦的那一层。"""
    from rpa_core.gui import palette

    class _StubDialog:
        def __init__(self, entries, parent=None) -> None:
            self.chosen = None

        def exec(self) -> int:
            return 0  # QDialog.DialogCode.Rejected

    monkeypatch.setattr(palette, "PaletteDialog", _StubDialog)
    assert palette.palette_prompt([_entry("打开网页", "browser.navigate")]) is None


def test_palette_prompt_returns_chosen_on_accept(monkeypatch):
    from rpa_core.gui import palette

    chosen = _entry("打开网页", "browser.navigate")

    class _StubDialog:
        def __init__(self, entries, parent=None) -> None:
            self.chosen = chosen

        def exec(self) -> int:
            return 1  # QDialog.DialogCode.Accepted

    monkeypatch.setattr(palette, "PaletteDialog", _StubDialog)
    assert palette.palette_prompt([chosen]) is chosen


# ---- 3. 外壳层：对话框交互 ---------------------------------------------------


def _entries() -> list[PaletteEntry]:
    return [
        PaletteEntry(KIND_COMMAND, "打开网页", "browser.navigate", "browser.navigate"),
        PaletteEntry(KIND_COMMAND, "读取数据", "data.readText", "data.readText"),
        PaletteEntry(KIND_NODE, "读取标题", "read", "read"),
    ]


def test_dialog_filters_and_accepts_current_row(qapp):
    from rpa_core.gui.palette import PaletteDialog

    dialog = PaletteDialog(_entries())
    assert dialog.list.count() == 3

    dialog.input.setText("read")
    # 只命中「读取数据」(payload data.readText) 与「读取标题」(payload read)；
    # 长度短者靠前 —— 这是 _match_rank 里「短文本更可能是用户想要的」那条规则的体现
    assert [entry.payload for entry in dialog._shown] == ["read", "data.readText"]

    dialog.list.setCurrentRow(1)
    dialog.accept_current()
    assert dialog.chosen is not None
    assert dialog.chosen.payload == "data.readText"
    dialog.deleteLater()


def test_dialog_reports_empty_result(qapp):
    from rpa_core.gui.palette import PaletteDialog

    dialog = PaletteDialog(_entries())
    dialog.input.setText("zzz")
    assert dialog.list.count() == 0
    assert dialog.list.currentRow() == -1
    assert "没有匹配项" in dialog.hint.text()

    dialog.accept_current()  # 无选中项时不得崩溃、不得产出结果
    assert dialog.chosen is None
    dialog.deleteLater()


def test_dialog_arrow_keys_move_selection(qapp):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    from rpa_core.gui.palette import PaletteDialog

    dialog = PaletteDialog(_entries())
    assert dialog.list.currentRow() == 0

    down = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier)
    assert dialog.eventFilter(dialog.input, down) is True
    assert dialog.list.currentRow() == 1

    up = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Up, Qt.KeyboardModifier.NoModifier)
    assert dialog.eventFilter(dialog.input, up) is True
    assert dialog.list.currentRow() == 0

    # 首行再按 ↑ 不越界
    assert dialog.eventFilter(dialog.input, up) is True
    assert dialog.list.currentRow() == 0

    # 其它按键交回基类（不吞输入）
    other = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_A, Qt.KeyboardModifier.NoModifier)
    assert dialog.eventFilter(dialog.input, other) is False
    dialog.deleteLater()
