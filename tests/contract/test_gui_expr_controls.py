"""参数表达式控件契约测试（对标影刀的「左 Py 右 fx」）。

覆盖七件事：

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
6. **编辑态双缓冲**（``_expr_slots``）——两种模式各存一份输入内容，来回切不互相
   覆盖；缓冲只活在面板内存里，落盘仍是「模式 + 一份值」（维护者 2026-10-13 核实
   的影刀语义）。
7. **``x-multiline`` ⇒ 多行输入区**（QPlainTextEdit），与 ``x-fx`` / ``x-python``
   可叠加；表达式态底色要用匹配真实控件类型的 QSS 选择器，否则静默不生效。

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

from PySide6.QtWidgets import (  # noqa: E402
    QLineEdit,
    QPlainTextEdit,
    QToolButton,
    QWidget,
)

from rpa_core.gui.param_form import ParamForm  # noqa: E402

_SRC = Path(__file__).resolve().parents[2] / "src" / "rpa_core" / "gui" / "param_form.py"


@pytest.fixture(scope="module")
def qapp():
    from rpa_core.gui.app import build_application

    return build_application()


def _editor(form: ParamForm, name: str) -> QLineEdit | QPlainTextEdit:
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


# ---- 编辑态双缓冲（切模式不丢内容） ----------------------------------------
def _swap_schema() -> dict:
    return {
        "type": "object",
        "properties": {"text": {"type": "string", "x-fx": True, "x-python": True}},
    }


def test_py_toggle_keeps_both_edits(qapp):
    """切到 Python 改一版 → 切回普通仍是原稿 → 再切过去 Python 那版还在。

    维护者 2026-10-13 核实的影刀语义：**只有编辑态两份值**，落盘仍是「模式 +
    一份值」；这条判据钉的就是「编辑态两份、互不覆盖」。
    """
    form = ParamForm(
        _swap_schema(), {"text": "abc"}, variable_groups_provider=lambda: []
    )
    editor = _editor(form, "text")
    py_button = form._expr_rows["text"][0]

    py_button.setChecked(True)
    assert editor.text() == "abc", "首次切过去应继承原内容，而不是清空"
    editor.setText("len(abc)")

    py_button.setChecked(False)
    assert editor.text() == "abc", "切回来必须还原普通模式那一份"

    py_button.setChecked(True)
    assert editor.text() == "len(abc)", "再切过去 Python 那一份也要还在"


def test_first_py_toggle_inherits_current_text(qapp):
    """首次切换继承原内容——点一下 Py 就看到内容消失最劝退（也是他要修的痛点）。"""
    form = ParamForm(
        _swap_schema(), {"text": "abc"}, variable_groups_provider=lambda: []
    )
    editor = _editor(form, "text")
    form._expr_rows["text"][0].setChecked(True)
    assert editor.text() == "abc"


def test_slot_swap_moves_cursor_to_end(qapp):
    """换槽后光标停在末尾——**判据必须打在多行控件上**。

    ``QLineEdit.setText`` 本来就把光标放到行尾（所以单行字段看不出问题），而
    ``QPlainTextEdit.setPlainText`` 会把它复位到 0：切过去接着点 fx 插变量就插到了
    内容最前面（维护者 2026-10-13 复核前的实测 ``web[web]``）。两种控件行为不同，
    打在单行上等于没测。
    """
    form = ParamForm(
        {
            "type": "object",
            "properties": {
                "both": {
                    "type": "string",
                    "x-fx": True,
                    "x-python": True,
                    "x-multiline": True,
                },
            },
        },
        {"both": "line1"},
        variable_groups_provider=lambda: [],
    )
    py_button, _fx_button, editor = form._expr_rows["both"]
    py_button.setChecked(True)

    assert editor.toPlainText() == "line1"
    assert editor.textCursor().position() == len(editor.toPlainText())


def test_slots_never_leak_into_saved_values(qapp):
    """缓冲只活在面板内存：``values()`` 永远只有当前模式那一份，不落第二份值。"""
    form = ParamForm(
        _swap_schema(), {"text": "abc"}, variable_groups_provider=lambda: []
    )
    editor = _editor(form, "text")
    py_button = form._expr_rows["text"][0]
    py_button.setChecked(True)
    editor.setText("len(abc)")
    py_button.setChecked(False)

    assert form.values() == {"text": "abc"}
    assert form.expr_modes() == {}
    assert "_exprValues" not in form.values(), "落盘形状里不许出现第二份值"


def test_switching_back_keeps_tags_alive(qapp):
    """切回普通模式后 ``[变量]`` 必须继续被解析（自动回到 fx 态）。

    否则用户只是切了一趟模式，自己写的引用就变成**不解析的死文本**（运行期走
    ``resolve``，只认整串 ``${var}``）——而换槽是程序换内容，不会触发
    ``textChanged`` 那条「删光标签才撤销」的路径。
    """
    form = ParamForm(
        _swap_schema(),
        {"text": "[web]"},
        expr_modes={"text": "fx"},
        variable_groups_provider=lambda: [],
    )
    py_button = form._expr_rows["text"][0]

    py_button.setChecked(True)
    assert form.expr_modes() == {"text": "python"}

    py_button.setChecked(False)
    assert form.expr_modes() == {"text": "fx"}, "取回带标签的内容应回到 fx 态"


def test_switching_back_to_plain_text_drops_fx(qapp):
    """反向兜底：取回的是**纯文本**（无标签）时不许停在 fx 态。"""
    form = ParamForm(
        _swap_schema(),
        {"text": "普通文本"},
        expr_modes={"text": "fx"},
        variable_groups_provider=lambda: [],
    )
    py_button = form._expr_rows["text"][0]

    py_button.setChecked(True)
    editor = _editor(form, "text")
    editor.setText("count + 1")
    py_button.setChecked(False)

    assert form.expr_modes() == {}, "无标签的纯文本不该留在 fx 态"


# ---- 输入区域（x-multiline，对标影刀「输入区域」） -------------------------
def _multiline_schema() -> dict:
    """把「多行 / 多行+表达式 / 单行」三种组合摆出来。"""
    return {
        "type": "object",
        "properties": {
            "script": {"type": "string", "x-multiline": True},
            "both": {
                "type": "string",
                "x-fx": True,
                "x-python": True,
                "x-multiline": True,
            },
            "single": {"type": "string", "x-fx": True},
        },
    }


def test_multiline_declaration_switches_to_plain_text_edit(qapp):
    """声明 ``x-multiline`` 才换多行控件；没声明仍是单行（读声明，不靠字段名猜）。"""
    form = ParamForm(_multiline_schema(), {}, variable_groups_provider=lambda: [])
    assert isinstance(_editor(form, "script"), QPlainTextEdit)
    assert isinstance(_editor(form, "both"), QPlainTextEdit)
    assert isinstance(_editor(form, "single"), QLineEdit)


def test_multiline_value_survives_collection(qapp):
    """多行内容（含换行）必须完整收回，不被压成一行或截断。"""
    form = ParamForm(
        _multiline_schema(),
        {"script": "const a = 1;\nconst b = 2;\nreturn a + b;"},
        variable_groups_provider=lambda: [],
    )
    assert form.values() == {"script": "const a = 1;\nconst b = 2;\nreturn a + b;"}


def test_multiline_and_expr_declarations_compose(qapp):
    """``x-multiline`` 与 ``x-fx`` / ``x-python`` 可叠加：先多行输入区，再挂两按钮。"""
    form = ParamForm(
        _multiline_schema(), {}, variable_groups_provider=lambda: [("变量", ["web"])]
    )
    py_button, fx_button, editor = form._expr_rows["both"]
    assert isinstance(editor, QPlainTextEdit)
    assert py_button is not None and fx_button is not None

    form._insert_variable("both", editor, "web")
    assert editor.toPlainText() == "[web]"
    assert form.expr_modes() == {"both": "fx"}


def test_multiline_editor_expression_highlight_uses_its_own_selector(qapp):
    """多行控件的表达式底色必须用 ``QPlainTextEdit`` 选择器——写成 ``QLineEdit``
    内联样式会**静默不生效**（看着有值、底色却不出现）。"""
    form = ParamForm(_multiline_schema(), {}, variable_groups_provider=lambda: [])
    py_button, _fx_button, editor = form._expr_rows["both"]
    py_button.setChecked(True)

    style = editor.styleSheet()
    assert "QPlainTextEdit" in style
    assert "QLineEdit" not in style


def test_multiline_slot_swap_works_on_plain_text_edit(qapp):
    """双缓冲对多行编辑器同样成立（换槽走的是适配函数，不是 ``QLineEdit`` 专用 API）。"""
    form = ParamForm(
        {
            "type": "object",
            "properties": {
                "both": {
                    "type": "string",
                    "x-fx": True,
                    "x-python": True,
                    "x-multiline": True,
                },
            },
        },
        {"both": "line1\nline2"},
        variable_groups_provider=lambda: [],
    )
    py_button, _fx_button, editor = form._expr_rows["both"]

    py_button.setChecked(True)
    assert editor.toPlainText() == "line1\nline2"
    editor.setPlainText("len(line1)")

    py_button.setChecked(False)
    assert editor.toPlainText() == "line1\nline2"


# ---- Py 按钮视觉（灰 / 彩两态） --------------------------------------------
def test_py_button_carries_two_state_style(qapp):
    """Py 是模式开关，两态要能一眼分辨——此前只有 Qt 默认按下阴影，维护者看不出
    「点亮了没有」（2026-10-13 报障）。"""
    form = ParamForm(
        _swap_schema(), {}, variable_groups_provider=lambda: []
    )
    py_button, _fx_button, _ = form._expr_rows["text"]

    style = py_button.styleSheet()
    assert "QToolButton:checked" in style, "缺少「已点亮」态样式"
    assert "QToolButton {" in style, "缺少「未点亮」态样式"


def test_py_button_is_widened_for_a_visible_state_block(qapp):
    """纯文字按钮太窄，底色块看不出来 ⇒ 必须显式加宽。"""
    form = ParamForm(
        _swap_schema(), {}, variable_groups_provider=lambda: []
    )
    py_button, _fx_button, _ = form._expr_rows["text"]
    assert py_button.minimumWidth() >= 30


def test_fx_button_shares_the_same_frame(qapp):
    """fx 也带描边：只给 Py 加样式会让同一行按钮一实一虚（此前 fx 是 autoRaise 裸文字）。"""
    form = ParamForm(
        _swap_schema(), {}, variable_groups_provider=lambda: []
    )
    _py_button, fx_button, _ = form._expr_rows["text"]
    assert "border" in fx_button.styleSheet()


# ---- 声明键的事实源唯一 ----------------------------------------------------
_WEB_SRC = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "rpa_core"
    / "devserver"
    / "static"
    / "app.js"
)


def test_both_editors_read_the_same_multiline_declaration(qapp):
    """``x-multiline`` 只能有一个键名，两端各读一次同一份字段声明。

    若各写各的（桌面 ``x-multiline``、Web ``multiline``），以后加字段就得同时记两个
    名字，迟早漂移——而漂移的症状是「一端多行、另一端单行」，不会报错。
    """
    assert '"x-multiline"' in _SRC.read_text(encoding="utf-8")
    assert '"x-multiline"' in _WEB_SRC.read_text(encoding="utf-8")


def test_multiline_declaration_is_only_used_on_string_fields():
    """``x-multiline`` 只对（有效类型为）string 的字段生效，挂在别的类型上会被静默忽略。

    有效类型用 ``param_form._effective_type`` 现算，不在这里复写一遍「取首个非 null」
    ——否则判据和实现在同一个坑上各错一次（``data.setVar.value`` 的 type 就是列表）。
    """
    import json

    from rpa_core.gui.param_form import _effective_type

    commands = Path(__file__).resolve().parents[2] / "commands"
    offenders = []
    for path in sorted(commands.rglob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        props = (data.get("input_schema") or {}).get("properties") or {}
        for key, spec in props.items():
            if spec.get("x-multiline") and _effective_type(spec) != "string":
                offenders.append(f"{data.get('id')}.{key}: {spec.get('type')!r}")
    assert offenders == [], f"x-multiline 只对 string 字段有效：{offenders}"
