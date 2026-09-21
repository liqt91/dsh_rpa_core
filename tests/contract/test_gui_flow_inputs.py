"""流程输入声明编辑对话框契约（M26 S2）。

对应 `gui/inputs_dialog.py`：把 `{名称: 默认值}` 这份扁平映射做成可增删改的表格。
测试在 offscreen Qt 平台运行；缺 PySide6 时整组跳过（与本仓其它 GUI 契约测试同口径）。

## 这里锁的四件事

1. **两列，不是四列**：`type`/`required`/`description` 在真实形状里不存在，
   界面上不许出现（出现了就是在暗示一份不存在的契约）。
2. **校验在「确定」之前，且不产出半成品**：不通过 → 留在对话框、`_result` 保持 `None`。
   这条最容易写成「先 accept 再校验」，那样调用方会拿到非法声明。
3. **一次报全部问题**：继承 `validate_entries` 的行为，不逐条挤牙膏。
4. **接线只写 `_workflow_meta`，不碰文件**：落盘由既有 `save_workflow` 负责，
   所以「确定」之后必须**置脏**，否则用户的编辑会在关闭窗口时被静默丢弃。
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip(
    "PySide6", reason="GUI 契约测试需要 gui extra（uv sync --all-groups --extra gui）"
)

from rpa_core.model.inputs import InputDeclarationError  # noqa: E402


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
def window(catalog):
    from rpa_core.gui.app import MainWindow

    return MainWindow(catalog)


def _dialog(declaration=None):
    from rpa_core.gui.inputs_dialog import FlowInputsDialog

    return FlowInputsDialog(declaration)


def _fill(dialog, rows: list[tuple[str, str]]) -> None:
    """把表格内容设为给定行（模拟用户在界面上敲出来的状态）。"""
    dialog.grid.setRowCount(0)
    for name, text in rows:
        dialog.add_row(name, text)


# ------------------------------------------------------------ 1. 形状：只有两列


def test_dialog_has_exactly_two_columns(qapp):
    """两列：名称 + 默认值。多出的列就是一份不存在的契约（type/required/description）。"""
    d = _dialog()
    assert d.grid.columnCount() == 2
    headers = [d.grid.horizontalHeaderItem(i).text() for i in range(2)]
    assert "名称" in headers[0]
    assert "JSON" in headers[1]


def test_no_type_or_required_column(qapp):
    """显式断言：界面上不出现 type / 必填 / 描述 这三列的痕迹。"""
    d = _dialog()
    headers = " ".join(
        d.grid.horizontalHeaderItem(i).text() for i in range(d.grid.columnCount())
    )
    for forbidden in ("类型", "必填", "描述"):
        assert forbidden not in headers


def test_existing_declaration_is_loaded_sorted(qapp):
    d = _dialog({"zeta": 1, "alpha": None, "mid": "s"})
    assert d.grid.rowCount() == 3
    assert [d.grid.item(r, 0).text() for r in range(3)] == ["alpha", "mid", "zeta"]
    # 默认值列是 **JSON 文本**：字符串 "s" 的文本形态带引号（与 RunParamsDialog 同口径）
    assert [d.grid.item(r, 1).text() for r in range(3)] == ["", '"s"', "1"]


def test_none_declaration_loads_empty(qapp):
    for declaration in (None, {}):
        d = _dialog(declaration)
        assert d.grid.rowCount() == 0
        assert d.entries() == []


# ------------------------------------------------------- 2. 校验通过才产出结果


def test_valid_entries_produce_the_declaration(qapp):
    d = _dialog()
    _fill(d, [("url", '"http://x"'), ("retries", "3"), ("opt", "")])
    d._on_accept()

    assert d.result() == d.DialogCode.Accepted
    assert d.declaration() == {"url": "http://x", "retries": 3, "opt": None}


def test_invalid_name_blocks_accept_and_keeps_dialog_open(qapp):
    """非法名 → 不接受、不产出结果（不是「先关掉再说」）。"""
    d = _dialog()
    _fill(d, [("1bad", "1")])
    d._on_accept()

    assert d.result() != d.DialogCode.Accepted
    with pytest.raises(InputDeclarationError):
        d.declaration()
    assert d.error_label.isVisible() or d.error_label.text()


def test_dotted_name_is_rejected_in_the_dialog(qapp):
    """点号名字被拒（与能力层同规则）——否则会「声明在册但永远引用不上」。"""
    d = _dialog()
    _fill(d, [("a.b", "1")])
    d._on_accept()
    assert "点号" in d.error_label.text()


def test_reserved_name_is_rejected_in_the_dialog(qapp):
    d = _dialog()
    _fill(d, [("steps", "1")])
    d._on_accept()
    assert "内置作用域根名" in d.error_label.text()


def test_duplicate_names_are_rejected(qapp):
    d = _dialog()
    _fill(d, [("x", "1"), ("x", "2")])
    d._on_accept()
    assert "重复" in d.error_label.text()


def test_invalid_json_default_is_rejected(qapp):
    d = _dialog()
    _fill(d, [("x", "{oops")])
    d._on_accept()
    assert "x" in d.error_label.text()


def test_bare_word_default_is_rejected(qapp):
    """裸词不是合法 JSON——界面里最容易犯的错。"""
    d = _dialog()
    _fill(d, [("x", "abc")])
    d._on_accept()
    assert "JSON" in d.error_label.text()


def test_all_problems_reported_in_one_go(qapp):
    """一次报全部：表格里多个错，不该「改一个、确定一次、再被拒一次」。"""
    d = _dialog()
    _fill(d, [("1bad", "1"), ("steps", "1"), ("ok", "{oops")])
    d._on_accept()

    message = d.error_label.text()
    assert "1bad" in message
    assert "steps" in message
    assert "ok" in message


def test_error_is_cleared_on_a_later_successful_attempt(qapp):
    """改对了再点确定：旧错误提示必须消失（否则用户以为还没修好）。"""
    d = _dialog()
    _fill(d, [("1bad", "1")])
    d._on_accept()
    assert d.error_label.text()

    _fill(d, [("good", "1")])
    d._on_accept()
    assert d.result() == d.DialogCode.Accepted
    assert d.error_label.text() == ""


def test_accept_cannot_bypass_validation(qapp):
    """防御：直接调 `accept()` 也必须先过校验（防测试或其它代码路径绕过 `_on_accept`）。"""
    d = _dialog()
    _fill(d, [("1bad", "1")])
    d.accept()

    assert d.result() != d.DialogCode.Accepted
    with pytest.raises(InputDeclarationError):
        d.declaration()


def test_empty_table_produces_empty_declaration(qapp):
    """清空所有行 = 取消整个声明（合法，需能保存）。"""
    d = _dialog({"a": 1})
    _fill(d, [])
    d._on_accept()

    assert d.result() == d.DialogCode.Accepted
    assert d.declaration() == {}


# ------------------------------------------------------------- 3. 编辑操作


def test_add_and_remove_rows(qapp):
    d = _dialog()
    assert d.grid.rowCount() == 0
    d.add_row("a", "1")
    d.add_row("b", "2")
    assert d.grid.rowCount() == 2
    assert d.remove_row(0) is True
    assert d.grid.rowCount() == 1
    assert d.grid.item(0, 0).text() == "b"


def test_remove_row_out_of_range_returns_false(qapp):
    d = _dialog({"a": 1})
    assert d.remove_row(-1) is False
    assert d.remove_row(5) is False
    assert d.grid.rowCount() == 1


def test_names_are_stripped_on_collect(qapp):
    """名称两端空白要吞掉：用户在表格里很容易多敲一个空格。"""
    d = _dialog()
    d.add_row("  spaced  ", "1")
    d._on_accept()
    assert d.declaration() == {"spaced": 1}


def test_default_text_whitespace_is_treated_as_null(qapp):
    """纯空白默认值 = null（与能力层同口径）。"""
    d = _dialog()
    d.add_row("x", "   ")
    d._on_accept()
    assert d.declaration() == {"x": None}


def test_nested_default_survives_the_dialog_roundtrip(qapp):
    """嵌套对象/数组经对话框不丢结构（界面上是 JSON 文本，收集时解析回原形）。"""
    d = _dialog({"opts": {"retry": [1, 2], "deep": {"k": "v"}}})
    d._on_accept()
    assert d.declaration() == {"opts": {"retry": [1, 2], "deep": {"k": "v"}}}


def test_roundtrip_without_editing_is_identity(qapp):
    """**核心**：打开对话框直接确定，声明必须逐字节不变（否则「看一眼」就改坏了文件）。"""
    for declaration in (
        {},
        {"a": None},
        {"a": 1, "b": "s", "c": True, "d": 1.5},
        {"obj": {"nested": [1, {"k": "v"}]}},
        {"cn": "中文值"},
    ):
        d = _dialog(declaration)
        d._on_accept()
        assert d.declaration() == declaration


def test_suggest_new_name_avoids_collisions(qapp):
    from rpa_core.gui.inputs_dialog import suggest_new_name

    assert suggest_new_name([]) == "input1"
    assert suggest_new_name(["input1"]) == "input2"
    assert suggest_new_name(["input1", "input2", "input3"]) == "input4"
    assert suggest_new_name(["other"]) == "input1"


# ------------------------------------------------------ 4. 与主窗口的接线


def test_main_window_exposes_the_action(window):
    assert window.flow_inputs_action.text() == "流程输入"
    assert window.flow_inputs_action.isEnabled()


def test_handler_writes_meta_and_marks_dirty(window, monkeypatch):
    """确定后：写入 `_workflow_meta["inputs"]` **并置脏**。

    置脏是硬要求——不置脏的话用户的编辑会在关闭窗口时被静默丢弃（没有保存提示）。
    """
    from rpa_core.gui import app as app_module

    monkeypatch.setattr(
        app_module, "flow_inputs_prompt", lambda current, parent=None: {"url": "http://x"}
    )
    window._set_dirty(False)
    window._edit_flow_inputs()

    assert window._workflow_meta["inputs"] == {"url": "http://x"}
    assert window._dirty is True


def test_handler_treats_cancel_as_a_noop(window, monkeypatch):
    """取消：不改动状态、不置脏（不能因为点开又关掉就把流程标记成已修改）。"""
    from rpa_core.gui import app as app_module

    window._workflow_meta["inputs"] = {"keep": 1}
    monkeypatch.setattr(app_module, "flow_inputs_prompt", lambda current, parent=None: None)
    window._set_dirty(False)
    window._edit_flow_inputs()

    assert window._workflow_meta["inputs"] == {"keep": 1}
    assert window._dirty is False


def test_handler_skips_dirty_when_nothing_changed(window, monkeypatch):
    """确定但内容没变：不置脏（避免「打开看过」就留下未保存标记）。"""
    from rpa_core.gui import app as app_module

    window._workflow_meta["inputs"] = {"same": 1}
    monkeypatch.setattr(
        app_module, "flow_inputs_prompt", lambda current, parent=None: {"same": 1}
    )
    window._set_dirty(False)
    window._edit_flow_inputs()

    assert window._dirty is False


def test_declaration_reaches_the_saved_document(window, monkeypatch):
    """端到端形状：写进 meta 后 `_build_document()` 产出的 inputs 就是新声明。

    这条把「对话框产出的形状」与「保存链路的形状」接上——两处形状不一致是这类功能
    最常见的隐性缺陷（界面看着对、存下来是另一回事）。
    """
    window._workflow_meta["inputs"] = {"url": "http://x", "retries": 3, "opt": None}
    document = window._build_document()

    assert document is not None
    assert document["inputs"] == {"url": "http://x", "retries": 3, "opt": None}


def test_clearing_the_declaration_reaches_the_document(window):
    """清空声明后保存文档里是空 inputs（不是残留旧值）。"""
    window._workflow_meta["inputs"] = {}
    document = window._build_document()
    assert document is not None
    assert document["inputs"] == {}


def test_saved_document_revalidates_through_workflow_model(window):
    """产出的文档必须能被 `Workflow` 接受（形状兼容是真的，不是声称兼容）。"""
    from rpa_core.model.workflow import Workflow

    window._workflow_meta["inputs"] = {"a": None, "b": [1, {"k": "v"}]}
    document = window._build_document()
    assert document is not None
    Workflow.model_validate(document)

    assert [e.name for e in __import__(
        "rpa_core.model.inputs", fromlist=["x"]
    ).entries_from_declaration(document["inputs"])] == ["a", "b"]
