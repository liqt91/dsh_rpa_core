"""参数表单契约测试（GUI 切片 3）。

覆盖两层：
1. ParamForm：input_schema → 控件映射（enum/string/integer/boolean/JSON）、
   初始值回填、values() 收集规则；
2. MainWindow 集成：选中画布卡片 → 右栏出表单 → 应用后 item 参数与摘要更新。

测试在 offscreen Qt 平台运行；缺 PySide6 时整组跳过。
"""

from __future__ import annotations

import os

# 必须在导入 Qt / 创建 QApplication 之前指定离屏平台
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

# 无显示环境必须在导入 Qt 前指定
pytest.importorskip("PySide6")

from PySide6.QtGui import QIntValidator  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QCheckBox,
    QComboBox,
    QLabel,
    QLineEdit,
    QPushButton,
)

from rpa_core.gui.flow_model import (  # noqa: E402
    ROLE_ARGS_RAW,
    ROLE_ARGS_SUMMARY,
    control_node_title,
)
from rpa_core.gui.param_form import (  # noqa: E402
    ControlNodeForm,
    ParamForm,
    format_literal,
    parse_literal,
)


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


def _field(form: ParamForm, name: str):
    """按字段名取控件。"""
    return next(widget for field, _kind, widget in form._fields if field == name)


# ---- ParamForm 单元 -------------------------------------------------------
def test_enum_field_has_unset_entry_and_chinese_labels(catalog):
    form = ParamForm(catalog["browser.navigate"].input_schema, {})
    combo: QComboBox = _field(form, "browserType")
    # 首项「未设置」+ 两个枚举值
    assert combo.count() == 3
    assert combo.itemData(0) is None
    assert combo.itemText(0) == "未设置"
    # x-enum-labels 提供中文显示
    texts = [combo.itemText(i) for i in range(combo.count())]
    assert "Edge" in texts and "Chrome" in texts


def test_initial_args_select_enum_value(catalog):
    form = ParamForm(
        catalog["browser.navigate"].input_schema, {"browserType": "chrome"}
    )
    combo: QComboBox = _field(form, "browserType")
    assert combo.currentData() == "chrome"


def test_string_integer_boolean_widget_types(catalog):
    form = ParamForm(
        catalog["browser.navigate"].input_schema, {"timeoutMs": 30000}
    )
    url_edit: QLineEdit = _field(form, "url")
    assert isinstance(url_edit, QLineEdit) and url_edit.text() == ""

    timeout_edit: QLineEdit = _field(form, "timeoutMs")
    assert isinstance(timeout_edit.validator(), QIntValidator)
    assert timeout_edit.text() == "30000"

    # boolean 字段（找一个带 boolean 参数的命令：data.table? 用合成 schema 验证）
    form_bool = ParamForm(
        {"properties": {"overwrite": {"type": "boolean"}}},
        {"overwrite": True},
    )
    assert isinstance(_field(form_bool, "overwrite"), QCheckBox)
    assert _field(form_bool, "overwrite").isChecked()


def test_values_skip_unset_and_empty(catalog):
    form = ParamForm(catalog["browser.navigate"].input_schema, {})
    # 全部未设置 → 空 dict
    assert form.values() == {}
    _field(form, "url").setText("https://example.com")
    values = form.values()
    assert values == {"url": "https://example.com"}
    assert "browserType" not in values  # enum 未设置被跳过


def test_integer_collected_as_int_and_empty_skipped(catalog):
    form = ParamForm(catalog["browser.navigate"].input_schema, {})
    _field(form, "timeoutMs").setText("5000")
    values = form.values()
    assert values["timeoutMs"] == 5000
    assert isinstance(values["timeoutMs"], int)


def test_json_array_roundtrip_and_bad_json_raises(catalog):
    form = ParamForm(
        catalog["browser.navigate"].input_schema,
        {"commandLineArgs": ["--incognito"]},
    )
    json_edit: QLineEdit = _field(form, "commandLineArgs")
    assert json_edit.text() == '["--incognito"]'
    values = form.values()
    assert values["commandLineArgs"] == ["--incognito"]

    json_edit.setText("[not-json")
    with pytest.raises(ValueError, match="commandLineArgs"):
        form.values()


def test_nullable_type_list_falls_back_to_string(qapp):
    form = ParamForm(
        {"properties": {"note": {"type": ["string", "null"]}}},
        {"note": "hi"},
    )
    widget = _field(form, "note")
    assert isinstance(widget, QLineEdit)
    assert widget.text() == "hi"


def test_required_field_label_has_star(catalog):
    form = ParamForm(catalog["browser.navigate"].input_schema, {})
    labels = form.findChildren(QLabel)
    required_label = next(label for label in labels if label.text().startswith("browserType"))
    assert required_label.text().endswith("*")


# ---- MainWindow 集成 ------------------------------------------------------
def test_param_placeholder_shown_initially(window):
    labels = window.param_holder.findChildren(QLabel)
    assert any("选择指令节点" in label.text() for label in labels)


def test_selecting_action_card_shows_form_and_apply_updates_item(window):
    model = window.flow_model
    item = model.find_by_id("open")
    index = model.indexFromItem(item)
    window.canvas_view.setCurrentIndex(index)

    form = window.param_holder.findChild(ParamForm)
    assert form is not None

    # 模拟编辑：选 browserType=msedge、改 url
    browser_combo: QComboBox = _field(form, "browserType")
    browser_combo.setCurrentIndex(browser_combo.findData("msedge"))
    _field(form, "url").setText("https://trae.example.cn/path")

    apply_button = window.param_holder.findChild(QPushButton)
    apply_button.click()

    holder = item.data(ROLE_ARGS_RAW)
    assert holder.args["url"] == "https://trae.example.cn/path"
    assert holder.args["browserType"] == "msedge"
    summary = item.data(ROLE_ARGS_SUMMARY)
    assert "browserType=msedge" in summary


def test_selecting_control_nodes_show_control_forms(window):
    model = window.flow_model

    def current_form() -> ControlNodeForm:
        # 旧表单 deleteLater 在 offscreen 下未必即时销毁，取最后一个（最新）表单
        forms = window.param_holder.findChildren(ControlNodeForm)
        assert forms
        return forms[-1]

    # 顶层 if 容器（sequence root 已扁平化，直接选顶层容器节点）：
    # 现在显示条件表单（条件操作符/左值/右值）
    check_item = model.find_by_id("check")  # SAMPLE_WORKFLOW 的 if 节点
    assert check_item is not None, "SAMPLE_WORKFLOW 应包含 if 节点 'check'"
    window.canvas_view.setCurrentIndex(model.indexFromItem(check_item))
    assert current_form().node_type == "if"

    # return 节点：显示返回值字段
    window.canvas_view.setCurrentIndex(model.indexFromItem(model.find_by_id("done")))
    form = current_form()
    assert form.node_type == "return"
    assert "value" in form._widgets


def test_bad_json_apply_reports_statusbar_without_raising(window):
    model = window.flow_model
    index = model.indexFromItem(model.find_by_id("open"))
    window.canvas_view.setCurrentIndex(index)
    form = window.param_holder.findChild(ParamForm)
    _field(form, "commandLineArgs").setText("{bad")
    window.param_holder.findChild(QPushButton).click()
    assert "commandLineArgs" in window.statusBar().currentMessage()


# ---- 控制流节点表单（切 A） -------------------------------------------------
def test_parse_literal_matches_web_semantics():
    assert parse_literal("") == ""
    assert parse_literal("${x}") == "${x}"  # 引用保留字符串
    assert parse_literal("42") == 42
    assert parse_literal("[1, 2]") == [1, 2]
    assert parse_literal("true") is True
    assert parse_literal('"quoted"') == "quoted"
    assert parse_literal("{bad") == "{bad"  # JSON 失败回退原始字符串
    assert parse_literal("hello") == "hello"


def test_format_literal_roundtrip():
    assert format_literal("${x}") == "${x}"
    assert format_literal(None) == ""
    assert format_literal(42) == "42"
    assert format_literal(["a"]) == '["a"]'


def test_control_form_if_collects_condition(qapp):
    form = ControlNodeForm("if", {"condition": {"op": "eq", "left": "${n}", "right": 3}})
    assert form._widgets["op"].currentData() == "eq"
    assert form._widgets["left"].text() == "${n}"
    assert form._widgets["right"].text() == "3"
    updates = form.apply_values()
    assert updates == {"condition": {"op": "eq", "left": "${n}", "right": 3}}


def test_control_form_if_empty_right_omits_key(qapp):
    form = ControlNodeForm("if", {"condition": {"op": "truthy", "left": "${x}", "right": 1}})
    form._widgets["right"].setText("")
    updates = form.apply_values()
    assert updates == {"condition": {"op": "truthy", "left": "${x}"}}


def test_control_form_for_each_validates_inputs(qapp):
    form = ControlNodeForm("forEach", {"items": ["a"], "item_var": "row"})
    assert form.apply_values() == {"items": ["a"], "item_var": "row"}

    form._widgets["items"].setText("")  # 空 = []
    assert form.apply_values()["items"] == []

    form._widgets["items"].setText("{bad")
    with pytest.raises(ValueError, match="items"):
        form.apply_values()

    form._widgets["items"].setText("[]")
    form._widgets["item_var"].setText("9bad")
    with pytest.raises(ValueError, match="item_var"):
        form.apply_values()


def test_control_form_try_and_return(qapp):
    try_form = ControlNodeForm("try", {"error_var": "err"})
    assert try_form.apply_values() == {"error_var": "err"}

    return_form = ControlNodeForm("return", {"value": "ok"})
    assert return_form.apply_values() == {"value": "ok"}
    return_form._widgets["value"].setText("")
    assert return_form.apply_values() == {"value": None}


def test_control_node_title_omits_none_right(qapp):
    title = control_node_title("if", {"condition": {"op": "truthy", "left": "${x}"}})
    assert title == "如果（${x} truthy）"
    title = control_node_title(
        "if", {"condition": {"op": "eq", "left": "${n}", "right": 3}}
    )
    assert title == "如果（${n} eq 3）"


def test_applying_if_condition_updates_raw_and_title(window):
    from rpa_core.gui.flow_model import model_to_workflow

    model = window.flow_model
    item = model.find_by_id("check")
    window.canvas_view.setCurrentIndex(model.indexFromItem(item))
    form = window.param_holder.findChild(ControlNodeForm)
    form._widgets["op"].setCurrentIndex(form._widgets["op"].findData("gte"))
    form._widgets["left"].setText("${changed}")
    form._widgets["right"].setText("60")
    window.param_holder.findChild(QPushButton).click()

    holder = item.data(ROLE_ARGS_RAW)
    assert holder.raw["condition"] == {"op": "gte", "left": "${changed}", "right": 60}
    assert item.text() == "如果（${changed} gte 60）"
    assert window._dirty

    document = model_to_workflow(model, window._workflow_meta)
    if_node = next(
        n for n in document["root"]["children"] if n["id"] == "check"
    )
    assert if_node["condition"]["op"] == "gte"
    assert if_node["condition"]["right"] == 60


# ---- fx 变量引用模式（切 C） --------------------------------------------------
def test_fx_toggle_records_expr_mode(qapp):
    form = ParamForm(
        catalog_schema_for_fx(),
        {"text": "hello"},
        variable_provider=lambda: [],
    )
    fx_button, var_button = form._fx_buttons["text"]
    assert not fx_button.isChecked()
    assert not var_button.isVisible()

    fx_button.setChecked(True)
    assert form.expr_modes() == {"text": "fx"}
    fx_button.setChecked(False)
    assert form.expr_modes() == {}


def test_fx_initial_mode_from_expr_modes(qapp):
    form = ParamForm(
        catalog_schema_for_fx(),
        {"text": "[web]"},
        expr_modes={"text": "fx"},
        variable_provider=lambda: [],
    )
    fx_button, _ = form._fx_buttons["text"]
    assert fx_button.isChecked()
    assert form.expr_modes() == {"text": "fx"}


def test_fx_var_button_inserts_tag_at_cursor(qapp):
    form = ParamForm(
        catalog_schema_for_fx(),
        {"text": "前置"},
        expr_modes={"text": "fx"},
        variable_provider=lambda: ["webpage1", "inputs.count"],
    )
    editor: QLineEdit = _field(form, "text")
    _, var_button = form._fx_buttons["text"]
    menu = var_button.menu()
    menu.aboutToShow.emit()  # 触发菜单重建（真实场景由弹出动作触发）
    texts = [action.text() for action in menu.actions()]
    assert "webpage1" in texts and "inputs.count" in texts
    menu.actions()[0].trigger()
    assert editor.text() == "前置[webpage1]"


def catalog_schema_for_fx() -> dict:
    return {
        "type": "object",
        "properties": {"text": {"type": "string"}},
    }


def test_apply_persists_expr_modes_to_raw(window):
    from rpa_core.gui.flow_model import model_to_workflow

    model = window.flow_model
    item = model.find_by_id("read")  # browser.getText，含 string 字段 selector
    window.canvas_view.setCurrentIndex(model.indexFromItem(item))
    form = window.param_holder.findChild(ParamForm)
    fx_button, _ = form._fx_buttons["selector"]
    fx_button.setChecked(True)
    window.param_holder.findChild(QPushButton).click()

    holder = item.data(ROLE_ARGS_RAW)
    assert holder.raw["_exprModes"] == {"selector": "fx"}
    document = model_to_workflow(model, window._workflow_meta)
    if_node = next(n for n in document["root"]["children"] if n["id"] == "check")
    read_node = if_node["then"][0]
    assert read_node["_exprModes"] == {"selector": "fx"}


def test_collect_reference_paths_includes_aliases_inputs_steps(window):
    window._workflow_meta["inputs"] = {"count": 3}
    paths = window._collect_reference_paths()
    assert "inputs.count" in paths
    assert "steps.read.outputs.text" in paths or any(
        p.startswith("steps.read.outputs.") for p in paths
    )
    assert "loop.item" in paths and "error.code" in paths
