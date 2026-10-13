"""参数表达式控件契约测试（对标影刀的「左 Py 右 fx」）。

覆盖四件事：

1. **门控来自 manifest 字段级声明**——``x-fx`` 才给 fx、``x-python`` 才给 Py；
   都没有就不挂表达式行（与 Web 编辑器同一份声明、同一套规则）。
2. **fx 是按钮不是开关**——点击弹出变量列表、选中即插到光标处；不再有「＋变量」
   这个中间按钮。
3. **fx / Py 互斥**，``_expr_modes`` 是唯一真值（运行期据此选解析器）。
4. **标签删光自动撤销 fx 态**（否则字段会「看着是纯文本、运行期却按标签解析」，
   字面 CSS 选择器 ``a[title]`` 正是这种字段）。

测试在 offscreen Qt 平台运行；缺 PySide6 时整组跳过。
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

# 必须在导入 Qt / 创建 QApplication 之前指定离屏平台
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QLineEdit, QToolButton, QWidget  # noqa: E402

from rpa_core.gui.param_form import ParamForm  # noqa: E402

_SRC = Path(__file__).resolve().parents[2] / "src" / "rpa_core" / "gui" / "param_form.py"


@pytest.fixture(scope="module")
def qapp():
    from rpa_core.gui.app import build_application

    return build_application()


def _editor(form: ParamForm, name: str) -> QLineEdit:
    return next(widget for field, _kind, widget in form._fields if field == name)


def _expr_schema() -> dict:
    """一份把四种门控组合都摆出来的 schema（外加不应挂表达式行的字段）。"""
    return {
        "type": "object",
        "properties": {
            "fx_only": {"type": "string", "x-fx": True},
            "py_only": {"type": "string", "x-python": True},
            "both": {"type": "string", "x-fx": True, "x-python": True},
            "plain": {"type": "string"},
            "num": {"type": "integer", "x-fx": True},
            "enum_fx": {"type": "string", "enum": ["a", "b"], "x-fx": True},
        },
    }


# ---- 门控 -----------------------------------------------------------------
def test_expr_controls_gated_by_manifest_declaration(qapp):
    """只有声明 x-fx / x-python 的**纯 string** 字段才挂表达式控件。"""
    form = ParamForm(_expr_schema(), {}, variable_provider=lambda: [])
    assert set(form._expr_rows) == {"fx_only", "py_only", "both"}

    fx_only_py, fx_only_fx, _ = form._expr_rows["fx_only"]
    assert fx_only_py is None and fx_only_fx is not None

    py_only_py, py_only_fx, _ = form._expr_rows["py_only"]
    assert py_only_py is not None and py_only_fx is None

    both_py, both_fx, _ = form._expr_rows["both"]
    assert both_py is not None and both_fx is not None


def test_undeclared_string_field_gets_no_fx(qapp):
    """回归：桌面端此前对**所有**纯 string 字段一律给 fx、不看声明 ⇒ 与 Web 分化。"""
    form = ParamForm(_expr_schema(), {}, variable_provider=lambda: [])
    assert "plain" not in form._expr_rows


# ---- fx：点击插入，不是开关 ------------------------------------------------
def test_fx_inserts_tag_at_cursor_and_enters_fx_mode(qapp):
    form = ParamForm(
        {"type": "object", "properties": {"text": {"type": "string", "x-fx": True}}},
        {"text": "前置"},
        variable_provider=lambda: ["web", "inputs.count"],
    )
    editor = _editor(form, "text")
    assert editor.text() == "前置"
    assert form.expr_modes() == {}

    _py, fx_button, _ = form._expr_rows["text"]
    menu = form._build_variable_menu("text", editor, fx_button)
    texts = [action.text() for action in menu.actions()]
    assert "web" in texts and "inputs.count" in texts
    menu.actions()[0].trigger()

    assert editor.text() == "前置[web]"
    assert form.expr_modes() == {"text": "fx"}


def test_fx_button_is_not_checkable(qapp):
    """fx 不是开关（维护者口径）：它是一个普通按钮，点击只负责弹变量列表。"""
    form = ParamForm(
        {"type": "object", "properties": {"text": {"type": "string", "x-fx": True}}},
        {},
        variable_provider=lambda: [],
    )
    _py, fx_button, _ = form._expr_rows["text"]
    assert fx_button.isCheckable() is False


def test_no_extra_var_button_remains(qapp):
    """「＋变量」这个中间按钮已移除——fx 本身就负责插入，少一步。"""
    form = ParamForm(
        {
            "type": "object",
            "properties": {
                "text": {"type": "string", "x-fx": True, "x-python": True},
            },
        },
        {},
        variable_provider=lambda: [],
    )
    button_texts = [b.text() for b in form.findChildren(QToolButton)]
    assert "＋变量" not in button_texts
    assert set(button_texts) == {"Py", "fx"}


# ---- fx / Py 互斥 ----------------------------------------------------------
def test_py_toggle_is_exclusive_with_fx(qapp):
    form = ParamForm(
        {
            "type": "object",
            "properties": {
                "text": {"type": "string", "x-fx": True, "x-python": True},
            },
        },
        {},
        variable_provider=lambda: ["parameter"],
    )
    editor = _editor(form, "text")
    py_button, _fx_button, _ = form._expr_rows["text"]

    py_button.setChecked(True)
    assert form.expr_modes() == {"text": "python"}

    # Python 模式下 fx 插入的是**变量名原文**（融进表达式），不产生标签
    menu = form._build_variable_menu("text", editor, editor)
    menu.actions()[0].trigger()
    assert editor.text() == "parameter"
    assert form.expr_modes() == {"text": "python"}

    py_button.setChecked(False)
    assert form.expr_modes() == {}


def test_python_mode_initial_state_from_expr_modes(qapp):
    form = ParamForm(
        {
            "type": "object",
            "properties": {
                "text": {"type": "string", "x-fx": True, "x-python": True},
            },
        },
        {"text": "count + 1"},
        expr_modes={"text": "python"},
        variable_provider=lambda: [],
    )
    py_button, _fx_button, _ = form._expr_rows["text"]
    assert py_button.isChecked() is True
    assert form.expr_modes() == {"text": "python"}


# ---- fx 态的生命周期 -------------------------------------------------------
def test_fx_mode_dropped_when_last_tag_removed(qapp):
    form = ParamForm(
        {"type": "object", "properties": {"text": {"type": "string", "x-fx": True}}},
        {"text": "[web]"},
        expr_modes={"text": "fx"},
        variable_provider=lambda: [],
    )
    editor = _editor(form, "text")
    assert form.expr_modes() == {"text": "fx"}

    editor.setText("")
    assert form.expr_modes() == {}, "标签被删光后 fx 态必须撤销"


def test_manual_css_selector_does_not_enter_fx_mode(qapp):
    """手打字面 CSS 属性选择器（``a[title]``）不该把字段拖进 fx 态——

    否则运行期 resolve_tags 会试图解析变量 ``title`` 而报 INVALID_REFERENCE。
    """
    form = ParamForm(
        {"type": "object", "properties": {"text": {"type": "string", "x-fx": True}}},
        {"text": ""},
        variable_provider=lambda: [],
    )
    editor = _editor(form, "text")
    editor.setText("a[title]")
    assert form.expr_modes() == {}


def test_expr_modes_pruned_to_hosted_fields(qapp):
    """未声明 x-fx/x-python 的字段即使 raw 残留旧模式，也不回传（开发期硬切）。"""
    form = ParamForm(
        {"type": "object", "properties": {"plain": {"type": "string"}}},
        {},
        expr_modes={"plain": "fx"},
        variable_provider=lambda: [],
    )
    assert form.expr_modes() == {}


# ---- 回归：控件拓扑 --------------------------------------------------------
def test_expr_buttons_have_no_pre_attached_menu(qapp):
    """回归（维护者报障「切换含 fx 的指令时小框闪现」）：fx 按钮不得预挂 QMenu——
    预挂菜单在 Windows 上会随控件树重挂/销毁产生原生弹层残影；改为点击时按需构建。"""
    form = ParamForm(
        {
            "type": "object",
            "properties": {
                "text": {"type": "string", "x-fx": True, "x-python": True},
            },
        },
        {},
        variable_provider=lambda: [],
    )
    _py, fx_button, _ = form._expr_rows["text"]
    assert fx_button.menu() is None


def test_expr_row_widgets_are_never_windows(qapp):
    """回归：表达式行控件必须创建时就带父级，否则 setVisible(True) 会被 Qt 当顶层
    窗口显示（诊断日志实锤 QToolButton 51x23 以 window 身份 Show）。"""
    form = ParamForm(
        {
            "type": "object",
            "properties": {
                "text": {"type": "string", "x-fx": True, "x-python": True},
            },
        },
        {"text": "[web]"},
        expr_modes={"text": "fx"},
        variable_provider=lambda: ["web"],
    )
    windows = [w for w in form.findChildren(QWidget) if w.isWindow()]
    assert windows == [], f"表单内不应有顶层窗口控件：{windows}"


# ---- 事实源唯一 ------------------------------------------------------------
def test_tag_detection_reuses_runtime_resolver(qapp):
    """fx 态的判据（字段里还有没有标签）必须复用运行期的同一个正则，不许各写一份。"""
    tree = ast.parse(_SRC.read_text(encoding="utf-8"))
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "has_tags" in calls, "param_form 未复用 runtime.resolver.has_tags 判标签"

    from rpa_core.runtime.resolver import has_tags

    assert has_tags("[web]") is True
    assert has_tags("a[title]") is True
    assert has_tags("纯文本") is False
