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
)
from rpa_core.gui.param_form import ParamForm  # noqa: E402


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


def test_selecting_container_and_return_show_hints(window):
    model = window.flow_model

    # 顶层 if 容器（sequence root 已扁平化，直接选顶层容器节点）
    check_item = model.find_by_id("check")  # SAMPLE_WORKFLOW 的 if 节点
    assert check_item is not None, "SAMPLE_WORKFLOW 应包含 if 节点 'check'"
    root_index = model.indexFromItem(check_item)
    window.canvas_view.setCurrentIndex(root_index)
    labels = window.param_holder.findChildren(QLabel)
    assert any("不接受参数" in label.text() for label in labels)

    # return 节点
    return_index = model.indexFromItem(model.find_by_id("done"))
    window.canvas_view.setCurrentIndex(return_index)
    labels = window.param_holder.findChildren(QLabel)
    assert any("后续切片" in label.text() for label in labels)


def test_bad_json_apply_reports_statusbar_without_raising(window):
    model = window.flow_model
    index = model.indexFromItem(model.find_by_id("open"))
    window.canvas_view.setCurrentIndex(index)
    form = window.param_holder.findChild(ParamForm)
    _field(form, "commandLineArgs").setText("{bad")
    window.param_holder.findChild(QPushButton).click()
    assert "commandLineArgs" in window.statusBar().currentMessage()
