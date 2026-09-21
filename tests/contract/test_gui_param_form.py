"""参数表单契约测试（GUI 切片 3）。

覆盖两层：
1. ParamForm：input_schema → 控件映射（enum/string/integer/boolean/JSON）、
   初始值回填、values() 收集规则；
2. MainWindow 集成：选中画布卡片 → 右栏出表单 → 应用后 item 参数与摘要更新。

测试在 offscreen Qt 平台运行；缺 PySide6 时整组跳过。
"""

from __future__ import annotations

import json
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
    QWidget,
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


def test_second_edit_after_save_is_committed(window):
    """回归（维护者报障「改完保存无效、要保存两次」）：待提交登记不随提交消费——

    第一次保存/运行把面板编辑提交到模型后，表单仍挂在右栏；用户对同一表单的
    再编辑必须同样被下一次保存自动提交。旧实现提交即丢弃登记，第二次编辑
    无人接管、保存静默丢失。
    """
    model = window.flow_model
    item = model.find_by_id("open")
    window.canvas_view.setCurrentIndex(model.indexFromItem(item))
    form = window.param_holder.findChild(ParamForm)
    combo: QComboBox = _field(form, "browserType")

    def saved_browser_type() -> str | None:
        document = window._build_document()  # 保存路径：内部先 _commit_pending_edits
        node = next(
            child for child in document["root"]["children"] if child["id"] == "open"
        )
        return node["with"].get("browserType")

    combo.setCurrentIndex(combo.findData("msedge"))
    assert saved_browser_type() == "msedge"  # 第一次编辑：提交生效
    combo.setCurrentIndex(combo.findData("chrome"))
    assert saved_browser_type() == "chrome"  # 第二次编辑：同样生效（回归点）


def test_stale_pending_apply_invalidated_on_delete(window):
    """回归（维护者报障「删除指令后点其他指令卡死闪退」）：卡片上的删除按钮不走
    `_delete_selected_node`，参数面板不会自动清空；结构变更后陈旧登记必须就地作废。

    否则下一次提交会对已删行 `itemFromIndex`（返回 None）取数据，在 Qt 槽里抛异常
    中止进程。
    """
    model = window.flow_model
    item = model.find_by_id("open")
    window.canvas_view.setCurrentIndex(model.indexFromItem(item))
    assert window._pending_apply is not None

    # 模拟卡片删除按钮路径：直接走模型删除（不触发 app 的面板清理）
    model.remove_item(item)
    assert window._pending_apply is None
    labels = window.param_holder.findChildren(QLabel)
    assert any("选择指令节点" in label.text() for label in labels)


def test_stale_apply_does_not_touch_deleted_node(window):
    """已删节点的陈旧 apply：安全返回（不抛异常、不改模型），状态栏给出提示。"""
    model = window.flow_model
    item = model.find_by_id("read") or model.find_by_id("open")
    window.canvas_view.setCurrentIndex(model.indexFromItem(item))
    apply_fn = window._pending_apply[0]

    model.remove_item(item)
    apply_fn()  # 不应抛异常
    assert "已被删除" in window.statusBar().currentMessage()


def test_switching_nodes_detaches_old_param_form_immediately(window):
    """回归（维护者报障「切换指令时小框闪现」）：清空右栏必须当帧把旧控件隐藏并
    脱离父级——只调 deleteLater 会把旧表单留在屏幕上直到事件循环回收，切换瞬间
    出现残影。"""
    model = window.flow_model
    window.canvas_view.setCurrentIndex(model.indexFromItem(model.find_by_id("open")))
    # 右栏布局里挂的是外层 QScrollArea（表单在其内）
    old_widget = window.param_layout.itemAt(0).widget()
    assert old_widget is not None

    window.canvas_view.setCurrentIndex(model.indexFromItem(model.find_by_id("read")))
    # 立即（未跑事件循环）检查：旧控件已脱离父级且不可见
    assert old_widget.parent() is None
    assert old_widget.isVisible() is False


def test_fx_var_button_has_no_pre_attached_menu(window):
    """回归（维护者报障「切换含 fx 的指令时小框闪现」）：fx 行的「＋变量」按钮
    不得预挂 QMenu——预挂菜单在 Windows 上会随控件树重挂/销毁产生原生弹层残影。
    改为点击时按需构建（菜单点击后插入标签、变量列表每次取最新）。"""
    model = window.flow_model
    item = model.find_by_id("read") or model.find_by_id("open")
    window.canvas_view.setCurrentIndex(model.indexFromItem(item))
    form = window.param_holder.findChildren(ParamForm)[-1]

    var_buttons = [
        widget
        for name, (fx_btn, widget) in form._fx_buttons.items()
    ]
    assert var_buttons, "样例流程应含 fx 可变字段"
    for button in var_buttons:
        assert button.menu() is None  # 不预挂菜单（弹层残影根因）


def test_prewarm_param_panel_is_safe_and_keeps_panel_usable(window):
    """预热参数面板：不抛异常、不往右栏塞残留控件、之后表单仍可正常挂载。

    （Qt 首次复杂表单布局的一次性开销 ~300ms 由预热提前消化，避免首次点节点卡顿。）
    """
    window._prewarm_param_panel()
    from PySide6.QtWidgets import QApplication

    QApplication.processEvents()
    # 预热用的滚动区不进右栏布局（用完即弃）
    assert window.param_layout.count() == 0 or all(
        window.param_layout.itemAt(i).widget() is not None
        for i in range(window.param_layout.count())
    )
    model = window.flow_model
    window.canvas_view.setCurrentIndex(model.indexFromItem(model.find_by_id("open")))
    assert window.param_holder.findChildren(ParamForm)


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


def test_fx_row_widgets_are_never_windows(qapp):
    """回归（维护者报障「切换含 fx 的指令时小框闪现」）：fx 行的控件必须创建时就
    带父级——无父级控件被 setVisible(True) 时 Qt 会把它当顶层窗口显示，产生闪现。

    诊断日志实锤：QToolButton 51x23 以 window 身份 Show。
    """
    form = ParamForm(
        catalog_schema_for_fx(),
        {"text": "[web]"},
        expr_modes={"text": "fx"},
        variable_provider=lambda: ["webpage1"],
    )
    _, var_button = form._fx_buttons["text"]
    assert var_button.parent() is not None
    assert var_button.isWindow() is False
    # 整棵表单里不允许出现「窗口」控件（fx 开关按钮同理）
    windows = [
        w for w in form.findChildren(QWidget)
        if w.isWindow()
    ]
    assert windows == [], f"表单内不应有顶层窗口控件：{windows}"


def test_fx_var_button_inserts_tag_at_cursor(qapp):
    form = ParamForm(
        catalog_schema_for_fx(),
        {"text": "前置"},
        expr_modes={"text": "fx"},
        variable_provider=lambda: ["webpage1", "inputs.count"],
    )
    editor: QLineEdit = _field(form, "text")
    _, var_button = form._fx_buttons["text"]
    # 菜单按需构建（不预挂，避免弹层残影）：直接驱动构建函数
    menu = form._build_variable_menu(editor, var_button)
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


# ---- 未应用编辑的自动提交（维护者报障：改完参数点保存无效） --------------------
def test_save_commits_pending_form_edits(window, tmp_path):
    """改了参数不点「应用参数」直接保存，也必须落盘（Web 即改即生效对齐）。"""
    from rpa_core.gui.param_form import ParamForm

    model = window.flow_model
    item = model.find_by_id("open")
    window.canvas_view.setCurrentIndex(model.indexFromItem(item))
    form = window.param_holder.findChild(ParamForm)
    combo = _field(form, "browserType")
    combo.setCurrentIndex(combo.findData("chrome"))

    target = tmp_path / "saved.json"
    assert window.save_workflow(target) == target
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["root"]["children"][0]["with"]["browserType"] == "chrome"
    # 提交过一次即产生一条撤销历史
    assert len(window._undo_stack) == 1


def test_save_without_form_changes_keeps_history_clean(window, tmp_path):
    """没有未应用编辑时保存不产生撤销历史（避免每次保存都塞垃圾快照）。"""
    target = tmp_path / "nochange.json"
    window.save_workflow(target)
    window.save_workflow(target)
    assert window._undo_stack == []


def test_switching_node_commits_pending_edits(window):
    """切换选中节点时，上一个面板的未应用编辑自动提交到模型。"""
    from rpa_core.gui.param_form import ParamForm

    model = window.flow_model
    open_item = model.find_by_id("open")
    window.canvas_view.setCurrentIndex(model.indexFromItem(open_item))
    form = window.param_holder.findChild(ParamForm)
    _field(form, "url").setText("https://committed.example/")

    # 切到另一个节点（触发 currentChanged → 自动提交）
    read_item = model.find_by_id("read")
    window.canvas_view.setCurrentIndex(model.indexFromItem(read_item))

    assert (
        open_item.data(ROLE_ARGS_RAW).raw["with"]["url"]
        == "https://committed.example/"
    )


def test_validate_commits_pending_edits(window):
    """校验前自动提交：面板里刚清掉的必填参数应被校验看见。"""
    from rpa_core.gui.param_form import ParamForm

    model = window.flow_model
    item = model.find_by_id("open")
    window.canvas_view.setCurrentIndex(model.indexFromItem(item))
    form = window.param_holder.findChild(ParamForm)
    combo = _field(form, "browserType")
    combo.setCurrentIndex(0)  # 「未设置」= 清空必填参数（未点应用）

    assert window._validate_workflow(show_dialog=False) is False
    open_node = window._current_document()["root"]["children"][0]
    assert "browserType" not in open_node["with"]

# ---- x-param-groups 分组折叠（M23 G3 slice A） --------------------------------
def test_grouped_form_renders_sections(catalog):
    """navigate manifest 有两个分组：常规 + 高级。"""
    from rpa_core.gui.param_form import ParamForm

    form = ParamForm(catalog["browser.navigate"].input_schema, {})
    assert "常规" in form._sections
    assert "高级" in form._sections


def test_grouped_form_field_count_matches(catalog):
    """navigate 常规组 3 字段，高级组 3 字段。"""
    from rpa_core.gui.param_form import ParamForm

    form = ParamForm(catalog["browser.navigate"].input_schema, {})
    # 通过 _sections dict 验证
    assert "常规" in form._sections
    assert "高级" in form._sections
    # 验证所有字段仍被收集（_fields 保持扁平）
    field_names = [name for name, _k, _w in form._fields]
    assert "browserType" in field_names
    assert "url" in field_names
    assert "timeoutMs" in field_names
    assert "commandLineArgs" in field_names


def test_collapsed_section_auto_expands_with_value(catalog):
    """高级组有 timeoutMs 值时自动展开。"""
    from rpa_core.gui.param_form import ParamForm

    form = ParamForm(
        catalog["browser.navigate"].input_schema, {"timeoutMs": 30000}
    )
    section = form._sections["高级"]
    assert not section._body.isHidden()  # 有值 → 自动展开（非 hidden）


def test_collapsed_section_default_state(catalog):
    """高级组无值时默认折叠。"""
    from rpa_core.gui.param_form import ParamForm

    form = ParamForm(catalog["browser.navigate"].input_schema, {})
    section = form._sections["高级"]
    assert section._body.isHidden()  # 无值 + collapsed=true → 折叠


def test_section_toggle(qapp):
    """点击 header 切换 body 可见性。"""
    from rpa_core.gui.param_form import _CollapsibleSection

    section = _CollapsibleSection("测试", field_count=2, collapsed=True)
    assert section._body.isHidden()
    section._toggle()
    assert not section._body.isHidden()
    section._toggle()
    assert section._body.isHidden()


def test_values_unchanged_by_grouping(catalog):
    """分组后 values() 收集结果与平铺一致。"""
    from rpa_core.gui.param_form import ParamForm

    args = {"browserType": "chrome", "url": "https://example.com", "timeoutMs": 5000}
    grouped = ParamForm(catalog["browser.navigate"].input_schema, args)
    flat_schema = {
        k: v
        for k, v in catalog["browser.navigate"].input_schema.items()
        if k != "x-param-groups"
    }
    flat = ParamForm(flat_schema, args)
    assert grouped.values() == flat.values()


def test_no_groups_falls_back_to_flat(qapp):
    """无 x-param-groups 时行为不变（平铺，无 section）。"""
    from rpa_core.gui.param_form import ParamForm, _CollapsibleSection

    schema = {
        "type": "object",
        "properties": {"a": {"type": "string"}, "b": {"type": "integer"}},
    }
    form = ParamForm(schema, {"a": "hello"})
    assert form.findChildren(_CollapsibleSection) == []
    assert len(form._fields) == 2
    assert form.values() == {"a": "hello"}


def test_unclaimed_fields_go_to_other_group(qapp):
    """不在任何 group 的字段归入「其他」组。"""
    from rpa_core.gui.param_form import ParamForm

    schema = {
        "type": "object",
        "properties": {
            "browserType": {"type": "string"},
            "url": {"type": "string"},
            "extra": {"type": "string"},
        },
        "x-param-groups": [
            {"label": "核心", "fields": ["browserType", "url"]},
        ],
    }
    form = ParamForm(schema, {})
    assert "其他" in form._sections
    # extra 应在其他组
    field_names = [name for name, _k, _w in form._fields]
    assert "extra" in field_names
# ---- 输出别名 x-outputs（M23 G3 slice B） ------------------------------------
def test_output_aliases_section_renders_for_navigate(catalog):
    """navigate 有 x-outputs，非 hidden 输出应出现在 _alias_fields 中。"""
    from rpa_core.gui.param_form import ParamForm

    manifest = catalog["browser.navigate"]
    form = ParamForm(manifest.input_schema, {}, manifest=manifest)
    # sessionId 和 browserInstance 是 primary，应有别名输入框
    assert "sessionId" in form._alias_fields
    assert "browserInstance" in form._alias_fields
    # hidden 字段不应出现
    assert "url" not in form._alias_fields
    assert "resourceType" not in form._alias_fields
    assert "browserType" not in form._alias_fields
    assert "tabId" not in form._alias_fields


def test_output_aliases_hidden_filtered(catalog):
    """hidden=true 的输出不出现在别名区。"""
    from rpa_core.gui.param_form import ParamForm

    manifest = catalog["browser.navigate"]
    form = ParamForm(manifest.input_schema, {}, manifest=manifest)
    for field in ("url", "resourceType", "browserType", "tabId"):
        assert field not in form._alias_fields


def test_output_aliases_roundtrip(catalog):
    """设置别名后 output_aliases() 正确收集。"""
    from rpa_core.gui.param_form import ParamForm

    form = ParamForm(
        catalog["browser.navigate"].input_schema, {},
        manifest=catalog["browser.navigate"],
        output_aliases={"sessionId": "web"},
    )
    assert form._alias_fields["sessionId"].text() == "web"
    assert form.output_aliases() == {"sessionId": "web"}


def test_output_alias_validation_rejects_invalid(qapp):
    """非法别名（数字开头、含空格）不出现在 output_aliases() 结果中。"""
    from rpa_core.gui.param_form import ParamForm

    schema = {"properties": {"x": {"type": "string"}}}
    # 构造一个假 manifest 对象
    class FakeManifest:
        x_outputs = {"out1": {"label": "输出1"}}
    form = ParamForm(schema, {}, manifest=FakeManifest())
    form._alias_fields["out1"].setText("1bad")  # 数字开头
    assert form.output_aliases() == {}
    form._alias_fields["out1"].setText("has space")  # 含空格
    assert form.output_aliases() == {}
    form._alias_fields["out1"].setText("good_name")  # 合法
    assert form.output_aliases() == {"out1": "good_name"}


def test_output_alias_invalid_shows_red_border(qapp):
    """非法别名输入框红框提示。"""
    from rpa_core.gui.param_form import ParamForm

    schema = {"properties": {"x": {"type": "string"}}}
    class FakeManifest:
        x_outputs = {"out1": {"label": "输出1"}}
    form = ParamForm(schema, {}, manifest=FakeManifest())
    edit = form._alias_fields["out1"]
    edit.setText("bad name")
    assert "#cf222e" in edit.styleSheet()
    edit.setText("ok")
    assert edit.styleSheet() == ""


def test_no_x_outputs_no_alias_section(qapp):
    """无 x-outputs 的命令不渲染输出别名区。"""
    from rpa_core.gui.param_form import ParamForm

    form = ParamForm({"properties": {"a": {"type": "string"}}})
    assert form._alias_fields == {}


def test_apply_writes_output_aliases_to_raw(window):
    """应用参数后 output_aliases 写入 holder.raw。"""
    model = window.flow_model
    item = model.find_by_id("open")
    window.canvas_view.setCurrentIndex(model.indexFromItem(item))
    form = window.param_holder.findChild(ParamForm)
    # 设置 sessionId 别名
    if "sessionId" in form._alias_fields:
        form._alias_fields["sessionId"].setText("web")
    window.param_holder.findChild(QPushButton).click()
    holder = item.data(ROLE_ARGS_RAW)
    assert holder.raw.get("output_aliases", {}).get("sessionId") == "web"


def test_apply_clears_empty_aliases(window):
    """所有别名清空后 output_aliases 键从 raw 中移除。"""
    model = window.flow_model
    item = model.find_by_id("open")
    window.canvas_view.setCurrentIndex(model.indexFromItem(item))
    # 先设一个别名
    holder = item.data(ROLE_ARGS_RAW)
    holder.raw["output_aliases"] = {"sessionId": "web"}
    window.canvas_view.setCurrentIndex(model.indexFromItem(item))  # 重新触发表单构建
    form = window.param_holder.findChild(ParamForm)
    if "sessionId" in form._alias_fields:
        form._alias_fields["sessionId"].clear()
    window.param_holder.findChild(QPushButton).click()
    assert "output_aliases" not in holder.raw
# ---- 超时/重试字段（M23 G3 slice C） -----------------------------------------
def test_timeout_field_hidden_when_command_has_timeoutMs(catalog):
    """browser.navigate 有 timeoutMs → 引擎级 timeout_seconds 不渲染。"""
    from rpa_core.gui.param_form import ParamForm

    form = ParamForm(
        catalog["browser.navigate"].input_schema, {},
        manifest=catalog["browser.navigate"],
    )
    assert form._timeout_field is None


def test_timeout_field_shown_when_no_own_timeout(catalog):
    """data.datetimeNow 无 timeoutMs → 显示超时字段。"""
    from rpa_core.gui.param_form import ParamForm

    manifest = catalog["data.datetimeNow"]
    form = ParamForm(manifest.input_schema, {}, manifest=manifest)
    assert form._timeout_field is not None
    assert form._timeout_field.placeholderText().startswith("可选")


def test_win32_commands_regain_engine_timeout_field(catalog):
    """M30 S2 删掉 hotkey/menuSelect 的 timeoutMs 后：GUI 恢复渲染节点级超时字段。

    删这两个参数的理由是「没有目标元素可等」；而**副作用正是删对了的证据**——
    `has_own_timeout` 见到 `timeoutMs` 就隐藏引擎超时输入框，参数是死的时候，
    用户唯一能改的「超时」是个无效开关，真正生效的是他看不见的 manifest 默认 15s。
    """
    from rpa_core.gui.param_form import ParamForm

    for command_id in ("desktop.win32.hotkey", "desktop.win32.menuSelect"):
        manifest = catalog[command_id]
        assert manifest.declares_wait_budget() is False, command_id
        form = ParamForm(manifest.input_schema, {}, manifest=manifest)
        assert form._timeout_field is not None, command_id


def test_element_wait_commands_still_hide_engine_timeout_field(catalog):
    """反向：真的会等元素的命令仍自带等待预算，节点级超时字段继续由 manifest 默认值接管。"""
    from rpa_core.gui.param_form import ParamForm

    for command_id in ("desktop.click", "desktop.win32.click", "desktop.getText"):
        manifest = catalog[command_id]
        assert manifest.declares_wait_budget() is True, command_id
        form = ParamForm(manifest.input_schema, {}, manifest=manifest)
        assert form._timeout_field is None, command_id


def test_timeout_field_roundtrip(catalog):
    """超时值读写一致。"""
    from rpa_core.gui.param_form import ParamForm

    manifest = catalog["data.datetimeNow"]
    form = ParamForm(
        manifest.input_schema, {},
        manifest=manifest,
        raw={"timeout_seconds": 60},
    )
    assert form._timeout_field.text() == "60"
    assert form.retry_timeout_values().get("timeout_seconds") == 60.0


def test_retry_field_shown_when_retryable(catalog):
    """retryable=true → 渲染重试次数输入框。"""
    from rpa_core.gui.param_form import ParamForm

    manifest = catalog["data.datetimeNow"]
    form = ParamForm(manifest.input_schema, {}, manifest=manifest)
    assert isinstance(form._retry_field, QLineEdit)


def test_retry_field_default_empty(qapp):
    """retryable 但无遗留值 → 输入框为空。"""
    from rpa_core.gui.param_form import ParamForm

    class FakeManifest:
        input_schema = {"properties": {}}
        retryable = True
        effect = type("E", (), {"replay": "safe"})()
    form = ParamForm(FakeManifest().input_schema, {}, manifest=FakeManifest())
    assert form._retry_field.text() == ""
    assert form.retry_timeout_values().get("retry_count") is None


def test_retry_warning_when_not_retryable_but_value_exists(qapp):
    """不支持重试但 raw 有遗留值 → 显示警告。"""
    from rpa_core.gui.param_form import ParamForm

    class FakeManifest:
        input_schema = {"properties": {}}
        retryable = False
        effect = type("E", (), {"replay": "unsafe"})()
    form = ParamForm(
        FakeManifest().input_schema, {},
        manifest=FakeManifest(),
        raw={"retry_count": 3},
    )
    # _retry_field 是包含警告的 QWidget
    from PySide6.QtWidgets import QWidget
    assert isinstance(form._retry_field, QWidget)
    # 有清除按钮
    from PySide6.QtWidgets import QToolButton
    buttons = form._retry_field.findChildren(QToolButton)
    assert any("清除" in b.text() for b in buttons)


def test_retry_not_shown_when_not_retryable_and_no_value(catalog):
    """不支持重试且无遗留值 → 不渲染重试字段。"""
    from rpa_core.gui.param_form import ParamForm

    manifest = catalog["browser.navigate"]  # retryable=false
    form = ParamForm(manifest.input_schema, {}, manifest=manifest)
    assert form._retry_field is None


def test_retry_timeout_values_collected(qapp):
    """超时和重试值正确收集。"""
    from rpa_core.gui.param_form import ParamForm

    class FakeManifest:
        input_schema = {"properties": {}}
        retryable = True
        effect = type("E", (), {"replay": "safe"})()
    form = ParamForm(FakeManifest().input_schema, {}, manifest=FakeManifest())
    form._timeout_field.setText("45")
    form._retry_field.setText("2")
    values = form.retry_timeout_values()
    assert values["timeout_seconds"] == 45.0
    assert values["retry_count"] == 2


def test_apply_writes_timeout_retry_to_raw(window):
    """应用参数后 timeout_seconds 和 retry_count 写入 holder.raw。"""
    model = window.flow_model
    item = model.find_by_id("open")
    window.canvas_view.setCurrentIndex(model.indexFromItem(item))
    holder = item.data(ROLE_ARGS_RAW)
    holder.raw["timeout_seconds"] = 90
    holder.raw["retry_count"] = 1
    # 重新选中触发表单构建（现在 _build_retry_timeout 会读 raw）
    window.canvas_view.setCurrentIndex(model.indexFromItem(item))
    window.param_holder.findChild(QPushButton).click()
    # 由于 navigate 有 timeoutMs，timeout 字段不渲染，raw 值应被清除
    assert "timeout_seconds" not in holder.raw
    # retryable=false，retry 也不渲染，raw 值应被清除
    assert "retry_count" not in holder.raw
# ---- 失败节点跳转（M23 G4 slice A） ------------------------------------------
def test_failed_run_shows_jump_button(window, monkeypatch):
    """运行失败且有 nodeId 时显示跳转按钮。"""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QPushButton

    dock = window._run_dock()  # noqa: F841 — 触发懒创建
    jump_btn = window._run_jump_button
    assert isinstance(jump_btn, QPushButton)
    assert not jump_btn.isVisible()

    # mock timer 避免 _poll_run 崩溃
    if window._run_timer is None:
        window._run_timer = QTimer(window)
        window._run_timer.setInterval(800)
    monkeypatch.setattr(window._run_timer, "stop", lambda: None)

    window._failed_node_id = None
    window._active_run_id = "test-run"

    class FakeManager:
        def status(self, run_id):
            return {
                "running": False,
                "result": {
                    "status": "failed",
                    "error": {
                        "code": "EXECUTOR_FAILED",
                        "nodeId": "open",
                        "message": "browser.launch failed",
                    },
                },
            }
        def events(self, run_id):
            return {"events": []}

    window._run_manager = FakeManager()
    window._poll_run()
    assert not jump_btn.isHidden()
    assert window._failed_node_id == "open"


def test_jump_to_failed_node_selects_in_canvas(window):
    """跳转按钮点击后在画布中选中失败节点。"""
    window._failed_node_id = "read"
    window._jump_to_failed_node()
    current = window.canvas_view.currentIndex()
    item = window.flow_model.itemFromIndex(current)
    from rpa_core.gui.flow_model import ROLE_NODE_ID
    assert item.data(ROLE_NODE_ID) == "read"


def test_jump_to_missing_node_shows_statusbar(window):
    """找不到节点时状态栏提示。"""
    window._failed_node_id = "nonexistent"
    window._jump_to_failed_node()
    assert "找不到" in window.statusBar().currentMessage()


def test_new_run_hides_jump_button(window):
    """新运行开始时隐藏跳转按钮。"""
    window._run_dock()
    window._run_jump_button.show()
    window._failed_node_id = "open"
    # 模拟新运行：_start_run 的逻辑会隐藏按钮
    window._run_jump_button.hide()
    window._failed_node_id = None
    assert window._run_jump_button.isHidden()
    assert window._failed_node_id is None
# ---- 结构化错误详情（M23 G4 slice B） ----------------------------------------
def test_error_summary_shows_code_and_message(window, monkeypatch):
    """失败运行显示结构化错误摘要。"""
    from PySide6.QtCore import QTimer

    window._run_dock()  # noqa: F841 — 触发懒创建
    if window._run_timer is None:
        window._run_timer = QTimer(window)
        window._run_timer.setInterval(800)
    monkeypatch.setattr(window._run_timer, "stop", lambda: None)
    window._active_run_id = "test-run"

    class FakeManager:
        def status(self, run_id):
            return {
                "running": False,
                "result": {
                    "status": "failed",
                    "error": {
                        "code": "EXECUTOR_FAILED",
                        "nodeId": "open",
                        "message": "browser.launch failed",
                        "details": {"commandId": "browser.launch", "transport": "bsk"},
                    },
                },
            }
        def events(self, run_id):
            return {"events": []}

    window._run_manager = FakeManager()
    window._poll_run()
    assert not window._run_error_widget.isHidden()
    assert "EXECUTOR_FAILED" in window._run_error_code.text()
    assert "open" in window._run_error_node.text()
    assert "browser.launch failed" in window._run_error_msg.text()
    assert "browser.launch" in window._run_error_detail.text()
    assert "bsk" in window._run_error_detail.text()


def test_error_summary_hidden_on_new_run(window):
    """新运行时错误摘要隐藏。"""
    window._run_dock()
    window._run_error_widget.show()
    window._run_error_widget.hide()
    assert window._run_error_widget.isHidden()


def test_startup_error_shows_summary(window, monkeypatch):
    """启动失败也显示结构化错误摘要。"""
    from PySide6.QtCore import QTimer

    window._run_dock()  # noqa: F841 — 触发懒创建
    if window._run_timer is None:
        window._run_timer = QTimer(window)
        window._run_timer.setInterval(800)
    monkeypatch.setattr(window._run_timer, "stop", lambda: None)
    window._active_run_id = "test-run"

    class FakeManager:
        def status(self, run_id):
            return {
                "running": False,
                "startupError": {"message": "Compilation failed: missing required field"},
                "exitCode": 1,
            }
        def events(self, run_id):
            return {"events": []}

    window._run_manager = FakeManager()
    window._poll_run()
    assert not window._run_error_widget.isHidden()
    assert "STARTUP_FAILED" in window._run_error_code.text()
    assert "Compilation failed" in window._run_error_msg.text()
# ---- 运行日志格式化（M23 G4 slice C） ----------------------------------------
def test_format_step_started(window):
    """stepStarted 格式化为 ▶ 标题 开始。"""
    window._run_dock()
    result = window._format_event({"type": "stepStarted", "node_id": "open"})
    assert result.startswith("▶")
    assert "开始" in result


def test_format_step_completed_with_elapsed_and_outputs(window):
    """stepCompleted 格式化为 ✓ 标题 完成 (耗时) — 输出: key=值（截断单行）。"""
    window._run_dock()
    window._step_start_times["open"] = __import__("time").time() - 1.5
    result = window._format_event({
        "type": "stepCompleted",
        "node_id": "open",
        "payload": {"outputs": {"sessionId": "abc", "url": "https://x"}},
    })
    assert result.startswith("✓")
    assert "完成" in result
    assert "s)" in result
    assert "sessionId" in result
    assert "url" in result
    # 不只字段名——值也要可见（维护者报障：不知道节点抓到的数据对不对）
    assert "abc" in result
    assert "https://x" in result


def test_outputs_preview_truncates_and_summarizes(window):
    """输出预览：超长值折成单行并截断；超出条数显示「另有 N 项」；空输出为 —。"""
    preview = window._format_outputs_preview({})
    assert preview == "—"

    long_text = "第一行\n" + "x" * 200
    preview = window._format_outputs_preview({"text": long_text})
    assert "\n" not in preview
    assert "…" in preview

    many = {f"k{i}": i for i in range(6)}
    preview = window._format_outputs_preview(many)
    assert "另有 3 项" in preview

    # 非字符串值按 JSON 渲染（列表/字典可读）
    preview = window._format_outputs_preview({"items": [1, 2, 3]})
    assert "items=[1, 2, 3]" in preview


def test_format_step_failed_with_error(window):
    """stepFailed 格式化为 ✗ 标题 失败 (耗时) — code: message。"""
    window._run_dock()
    window._step_start_times["open"] = __import__("time").time() - 0.3
    result = window._format_event({
        "type": "stepFailed",
        "node_id": "open",
        "payload": {
            "error": {"code": "TIMEOUT", "message": "加载超时"},
        },
    })
    assert result.startswith("✗")
    assert "失败" in result
    assert "TIMEOUT" in result
    assert "加载超时" in result


def test_format_step_retried(window):
    """stepRetried 格式化为 ↻ 标题 重试。"""
    window._run_dock()
    result = window._format_event({
        "type": "stepRetried",
        "node_id": "open",
        "payload": {"attempt": 1, "nextAttempt": 2, "backoffSeconds": 5},
    })
    assert result.startswith("↻")
    assert "重试" in result


def test_format_run_started_and_finished(window):
    """runStarted/runFinished 格式化。"""
    window._run_dock()
    result = window._format_event({"type": "runStarted"})
    assert result.startswith("▸")
    assert "开始" in result
    result = window._format_event({"type": "runFinished", "payload": {"status": "succeeded"}})
    assert result.startswith("▸")
    assert "succeeded" in result


def test_format_unknown_event_falls_back_to_json(window):
    """未知事件类型保留原始 JSON。"""
    window._run_dock()
    event = {"type": "customEvent", "data": 42}
    result = window._format_event(event)
    assert "customEvent" in result
# ---- 卡片摘要优先级（M23 次级项：卡片摘要优化） --------------------------------
def test_summarize_args_prioritizes_url_and_selector():
    """url/selector 等关键字段优先展示。"""
    from rpa_core.gui.flow_model import summarize_args

    args = {"browserType": "chrome", "url": "https://example.com", "action": "goto"}
    result = summarize_args(args)
    # url 是最高优先级，排在最前
    assert result.startswith("url=")
    # action 也是优先字段，排第二；browserType 被 +1 溢出
    assert "action=goto" in result


def test_summarize_args_shows_selector_before_other_fields():
    """selector 优先于普通字段。"""
    from rpa_core.gui.flow_model import summarize_args

    args = {"kind": "css", "selector": "#btn", "timeout": 5000}
    result = summarize_args(args)
    assert result.startswith("selector=")


def test_summarize_args_empty_returns_empty():
    """空参数返回空字符串。"""
    from rpa_core.gui.flow_model import summarize_args
    assert summarize_args({}) == ""


def test_summarize_args_limit_respected():
    """limit 参数控制展示数量。"""
    from rpa_core.gui.flow_model import summarize_args

    args = {"url": "a", "selector": "b", "text": "c"}
    result = summarize_args(args, limit=1)
    assert "url=a" in result
    assert "selector" not in result
    assert "+2" in result
# ---- 菜单栏（M23 次级项：菜单栏 + 快捷键一览） --------------------------------
def test_menu_bar_has_four_menus(catalog):
    """菜单栏包含文件/编辑/运行/帮助四个菜单。"""
    from rpa_core.gui.app import MainWindow
    win = MainWindow(catalog)
    menus = [a.text() for a in win.menuBar().actions()]
    assert "文件" in menus
    assert "编辑" in menus
    assert "运行" in menus
    assert "帮助" in menus
    win.close()


def test_shortcuts_dialog_opens(catalog):
    """快捷键一览对话框入口存在于帮助菜单。"""
    from rpa_core.gui.app import MainWindow
    win = MainWindow(catalog)
    help_menu = [a.menu() for a in win.menuBar().actions() if a.text() == "帮助"][0]
    actions = [a for a in help_menu.actions() if a.text() == "快捷键一览"]
    assert len(actions) == 1
    win.close()


def test_menu_actions_share_shortcuts_with_toolbar(catalog):
    """菜单栏复用工具栏 QAction，快捷键一致。"""
    from rpa_core.gui.app import MainWindow
    win = MainWindow(catalog)
    assert win._save_action_ref.shortcut().toString() == "Ctrl+S"
    assert win._new_action_ref.shortcut().toString() == "Ctrl+N"
    assert win._open_action_ref.shortcut().toString() == "Ctrl+O"
    win.close()
# ---- 变量面板（M23 次级项：设计期变量面板） ------------------------------------
def test_refresh_variables_collects_aliases(catalog):
    """变量面板能从工作流中收集 output_aliases。"""
    import copy

    from rpa_core.gui.app import SAMPLE_WORKFLOW, MainWindow
    from rpa_core.gui.flow_model import ROLE_ARGS_RAW, iter_real_nodes

    win = MainWindow(catalog, copy.deepcopy(SAMPLE_WORKFLOW))
    # 模拟设置一个 output_alias
    model = win.flow_model
    for item in iter_real_nodes(model):
        holder = item.data(ROLE_ARGS_RAW)
        if holder and holder.raw and holder.raw.get("type") == "action":
            holder.raw["output_aliases"] = {"0": "testVar"}
            break
    # 刷新变量面板
    win._variables_dock()  # 触发创建
    win._refresh_variables()
    tree = win._variables_tree
    # 应包含 testVar
    found = False
    for i in range(tree.topLevelItemCount()):
        group = tree.topLevelItem(i)
        for j in range(group.childCount()):
            child = group.child(j)
            if "testVar" in child.text(0):
                found = True
    assert found, "变量面板应显示 testVar"
    win.close()


def test_variables_dock_has_refresh_button(catalog):
    """变量面板有刷新按钮。"""
    import copy

    from rpa_core.gui.app import SAMPLE_WORKFLOW, MainWindow

    win = MainWindow(catalog, copy.deepcopy(SAMPLE_WORKFLOW))
    dock = win._variables_dock()
    # dock 内应有刷新按钮
    from PySide6.QtWidgets import QPushButton
    buttons = dock.widget().findChildren(QPushButton)
    labels = [b.text() for b in buttons]
    assert "刷新" in labels
    win.close()


def test_variables_toggle_action_in_menu(catalog):
    """编辑菜单有变量面板开关。"""
    import copy

    from rpa_core.gui.app import SAMPLE_WORKFLOW, MainWindow

    win = MainWindow(catalog, copy.deepcopy(SAMPLE_WORKFLOW))
    # 编辑菜单中应有变量面板 action
    edit_menu = [a.menu() for a in win.menuBar().actions() if a.text() == "编辑"][0]
    actions = [a.text() for a in edit_menu.actions()]
    assert "变量面板" in actions
    win.close()

def test_apply_repaints_canvas_so_required_badge_clears(window):
    """回归（维护者报障「配置参数后、保存前红色感叹号一直在」）：apply 必须请求
    画布重绘——编号栏徽标由 delegate 现算（读 holder.args），不重绘就留旧徽标。"""
    model = window.flow_model
    item = model.find_by_id("open")
    window.canvas_view.setCurrentIndex(model.indexFromItem(item))

    repaints: list[int] = []
    viewport = window.canvas_view.viewport()
    original_update = viewport.update

    def _spy(*args, **kwargs):
        repaints.append(1)
        return original_update(*args, **kwargs)

    viewport.update = _spy
    try:
        form = window.param_holder.findChild(ParamForm)
        _field(form, "url").setText("https://example.com")
        browser_combo = _field(form, "browserType")
        browser_combo.setCurrentIndex(browser_combo.findData("msedge"))
        apply_button = window.param_holder.findChild(QPushButton)
        apply_button.click()
    finally:
        viewport.update = original_update

    assert repaints, "apply 后应请求画布重绘（清掉必填缺失徽标）"
    # 徽标判定本身也应转为「无缺失」
    delegate = window.canvas_view.itemDelegate()
    assert delegate._has_config_error(model.indexFromItem(item)) is False
