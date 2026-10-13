"""参数表达式控件契约测试（对标影刀的「左 Py 右 fx」）。

覆盖五件事：

1. **门控来自 manifest 字段级声明**——``x-fx`` 才给 fx、``x-python`` 才给 Py；
   都没有就不挂表达式行（与 Web 编辑器同一份声明、同一套规则）。
2. **fx 是按钮不是开关**——点击弹出变量列表、选中即插到光标处；不再有「＋变量」
   这个中间按钮。
3. **fx / Py 互斥**，``_expr_modes`` 是唯一真值（运行期据此选解析器）。
4. **标签删光自动撤销 fx 态**（否则字段会「看着是纯文本、运行期却按标签解析」，
   字面 CSS 选择器 ``a[title]`` 正是这种字段）。
5. **菜单按来源分组、只列用户命名的变量**——组标题是不可点的灰行；空组不渲染；
   菜单内容由 ``variable_groups_provider`` 供给，排除规则见
   ``MainWindow._reference_groups``。

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


def _trigger(menu, text: str) -> None:
    """按文本点菜单项（菜单里还有不可点的组标题，不能靠下标取）。"""
    action = next(a for a in menu.actions() if a.text() == text)
    assert action.isEnabled(), f"菜单项 {text!r} 不应被禁用"
    action.trigger()


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
    form = ParamForm(_expr_schema(), {}, variable_groups_provider=lambda: [])
    assert set(form._expr_rows) == {"fx_only", "py_only", "both"}

    fx_only_py, fx_only_fx, _ = form._expr_rows["fx_only"]
    assert fx_only_py is None and fx_only_fx is not None

    py_only_py, py_only_fx, _ = form._expr_rows["py_only"]
    assert py_only_py is not None and py_only_fx is None

    both_py, both_fx, _ = form._expr_rows["both"]
    assert both_py is not None and both_fx is not None


def test_undeclared_string_field_gets_no_fx(qapp):
    """回归：桌面端此前对**所有**纯 string 字段一律给 fx、不看声明 ⇒ 与 Web 分化。"""
    form = ParamForm(_expr_schema(), {}, variable_groups_provider=lambda: [])
    assert "plain" not in form._expr_rows


# ---- fx：点击插入，不是开关 ------------------------------------------------
def test_fx_inserts_tag_at_cursor_and_enters_fx_mode(qapp):
    form = ParamForm(
        {"type": "object", "properties": {"text": {"type": "string", "x-fx": True}}},
        {"text": "前置"},
        variable_groups_provider=lambda: [("变量", ["web", "inputs.count"])],
    )
    editor = _editor(form, "text")
    assert editor.text() == "前置"
    assert form.expr_modes() == {}

    _py, fx_button, _ = form._expr_rows["text"]
    menu = form._build_variable_menu("text", editor, fx_button)
    texts = [action.text() for action in menu.actions()]
    assert "web" in texts and "inputs.count" in texts
    _trigger(menu, "web")

    assert editor.text() == "前置[web]"
    assert form.expr_modes() == {"text": "fx"}


def test_fx_button_is_not_checkable(qapp):
    """fx 不是开关（维护者口径）：它是一个普通按钮，点击只负责弹变量列表。"""
    form = ParamForm(
        {"type": "object", "properties": {"text": {"type": "string", "x-fx": True}}},
        {},
        variable_groups_provider=lambda: [],
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
        variable_groups_provider=lambda: [],
    )
    button_texts = [b.text() for b in form.findChildren(QToolButton)]
    assert "＋变量" not in button_texts
    assert set(button_texts) == {"Py", "fx"}


# ---- 菜单分组（维护者 2026-10-13：分两组加标题） ---------------------------
def test_variable_menu_renders_group_headers_as_disabled_rows(qapp):
    """组标题是**不可点的灰行**，组间有分隔条（对齐 Web fx 浮层的分组头）。"""
    form = ParamForm(
        {"type": "object", "properties": {"text": {"type": "string", "x-fx": True}}},
        {},
        variable_groups_provider=lambda: [
            ("流程参数", ["inputs.count"]),
            ("输出与赋值变量", ["web"]),
        ],
    )
    _py, fx_button, _ = form._expr_rows["text"]
    menu = form._build_variable_menu("text", _editor(form, "text"), fx_button)

    headers = [a for a in menu.actions() if a.text() in ("流程参数", "输出与赋值变量")]
    assert [a.text() for a in headers] == ["流程参数", "输出与赋值变量"]
    assert all(a.isEnabled() is False for a in headers), "组标题不能可点"

    texts = [a.text() for a in menu.actions()]
    assert "inputs.count" in texts and "web" in texts
    # 两组之间恰好一条分隔条（分组语义，不是纯装饰）
    assert sum(1 for a in menu.actions() if a.isSeparator()) == 1


def test_variable_menu_skips_empty_groups_and_their_separators(qapp):
    """空组不渲染标题、也不留孤零零的分隔条。"""
    form = ParamForm(
        {"type": "object", "properties": {"text": {"type": "string", "x-fx": True}}},
        {},
        variable_groups_provider=lambda: [
            ("流程参数", []),
            ("输出与赋值变量", ["web"]),
        ],
    )
    _py, fx_button, _ = form._expr_rows["text"]
    menu = form._build_variable_menu("text", _editor(form, "text"), fx_button)

    texts = [a.text() for a in menu.actions()]
    assert "流程参数" not in texts
    assert "web" in texts
    assert sum(1 for a in menu.actions() if a.isSeparator()) == 0


def test_variable_menu_shows_placeholder_when_nothing_is_named(qapp):
    """一条变量都没有时给占位灰行，而不是弹出一个空菜单。"""
    form = ParamForm(
        {"type": "object", "properties": {"text": {"type": "string", "x-fx": True}}},
        {},
        variable_groups_provider=lambda: [],
    )
    _py, fx_button, _ = form._expr_rows["text"]
    menu = form._build_variable_menu("text", _editor(form, "text"), fx_button)

    assert [a.text() for a in menu.actions()] == ["（无可引用变量）"]
    assert menu.actions()[0].isEnabled() is False


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
        variable_groups_provider=lambda: [("变量", ["parameter"])],
    )
    editor = _editor(form, "text")
    py_button, _fx_button, _ = form._expr_rows["text"]

    py_button.setChecked(True)
    assert form.expr_modes() == {"text": "python"}

    # Python 模式下 fx 插入的是**变量名原文**（融进表达式），不产生标签
    menu = form._build_variable_menu("text", editor, editor)
    _trigger(menu, "parameter")
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
        variable_groups_provider=lambda: [],
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
        variable_groups_provider=lambda: [],
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
        variable_groups_provider=lambda: [],
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
        variable_groups_provider=lambda: [],
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
        variable_groups_provider=lambda: [],
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
        variable_groups_provider=lambda: [("变量", ["web"])],
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
