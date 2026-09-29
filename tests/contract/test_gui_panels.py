"""元素库 / 数据表格 / 插件与指令清单一览（GUI 功能补齐 切 G/H/I）。

- 数据表格面板：载入/收集往返、加列去重、类型化解析、保存到 TableStore；
- 元素库：摘要、校验、入库、插入到指令参数（browser→selector / desktop→locator）；
- 插件状态文本：能力层探测结果结构化呈现（双浏览器行）。

测试在 offscreen Qt 平台运行；缺 PySide6 时整组跳过。
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")


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
def window(catalog, tmp_path):
    from rpa_core.gui.app import MainWindow

    return MainWindow(catalog, workflows_root=tmp_path / "workflows")


# ---- 数据表格 -----------------------------------------------------------------
def test_table_panel_roundtrip(qapp):
    from rpa_core.gui.table_panel import TablePanel

    panel = TablePanel()
    panel.load(
        {
            "columns": [
                {"key": "name", "label": "名称", "type": "text"},
                {"key": "count", "label": "数量", "type": "number"},
            ],
            "rows": [{"name": "苹果", "count": 3}],
        }
    )
    assert panel.grid.columnCount() == 2
    assert panel.grid.rowCount() == 1
    panel.add_row()
    panel.grid.item(1, 0).setText("香蕉")
    panel.grid.item(1, 1).setText("2.5")
    document = panel.collect()
    assert document["rows"] == [
        {"name": "苹果", "count": 3},
        {"name": "香蕉", "count": 2.5},
    ]


def test_table_panel_add_column_dedupes_key(qapp):
    from rpa_core.gui.table_panel import TablePanel

    panel = TablePanel()
    panel.load({"columns": [], "rows": []})
    panel.add_column("名称")
    panel.add_column("名称")
    keys = [c["key"] for c in panel.collect()["columns"]]
    assert keys == ["名称", "名称_2"]


def test_table_save_and_reload_via_store(window):
    window._save_named_flow("t1")
    window._table_dock()  # 建面板
    window._refresh_table()
    window._table_panel.add_column("标题")
    window._table_panel.add_row()
    window._table_panel.grid.item(0, 0).setText(" hello ")
    window._save_table()

    stored = window._table_store().read("t1")
    assert stored["columns"][0]["label"] == "标题"
    assert stored["rows"] == [{"标题": " hello "}]

    # 文件位置与运行期 data.table.* 命令同契约
    table_file = window._store.directory("t1") / "data" / "table.json"
    assert table_file.is_file()


# ---- 元素库 ------------------------------------------------------------------
def _browser_element() -> dict:
    return {
        "kind": "browser",
        "selector": {"css": "#kw"},
        "verifyCount": 1,
        "metadata": {"url": "https://example.com"},
    }


def test_save_and_verify_element(window):
    window._save_named_flow("e1")
    assert window.save_element_descriptor("searchBox", _browser_element())
    store = window._element_store()
    assert "searchBox" in store.list()

    window._refresh_elements()
    assert window._element_panel.list.count() == 1
    assert "browser" in window._element_panel.list.item(0).text()

    window._verify_element("searchBox")
    message = window.statusBar().currentMessage()
    assert "结构校验通过" in message
    # 限定语是判据本身，不是装饰：这个动作**不连接页面**，迁自影刀的用户会把
    # 裸「校验通过」读成「页面上定位得到」（影刀那侧是真活体校验）。
    # 状态栏是本动作唯一的反馈面（用户不会为确认口径去悬停按钮）——所以钉在这里。
    assert "未验证页面命中" in message


def test_verify_element_reports_structural_failure(window):
    """结构校验失败路径也要带「结构」限定语，且给出 path: message。"""
    window._save_named_flow("e1bad")
    # css 为空串：模型层过得去（selector 是自由 dict），selector 语义层必须拦下
    window.save_element_descriptor("bad", {"kind": "browser", "selector": {"css": "  "}})
    window._verify_element("bad")
    message = window.statusBar().currentMessage()
    assert "结构校验未通过" in message
    assert "selector.css" in message


def test_verify_button_is_labelled_as_structural(window):
    """按钮文案 + tooltip 都与 Web 侧同口径（Web: 按钮 title="结构校验"）。"""
    window._toggle_elements_dock()
    button = window._element_panel.verify_button
    assert button.text() == "结构校验"
    tooltip = button.toolTip()
    assert "不连接页面" in tooltip
    assert "捕获" in tooltip  # 指路：活体验证在捕获时完成


def test_save_element_rejects_invalid_document(window):
    window._save_named_flow("e2")
    assert not window.save_element_descriptor("bad", {"kind": "browser"})
    assert "不合法" in window.statusBar().currentMessage()


def test_insert_element_into_selector(window):
    window._save_named_flow("e3")
    window.save_element_descriptor("searchBox", _browser_element())
    # 选中 browser.getText 节点（有 selector 参数）
    item = window.flow_model.find_by_id("read")
    window.canvas_view.setCurrentIndex(window.flow_model.indexFromItem(item))
    window._insert_element("searchBox")

    from rpa_core.gui.flow_model import ROLE_ARGS_RAW

    holder = item.data(ROLE_ARGS_RAW)
    assert holder.raw["with"]["selector"] == "#kw"


def test_insert_element_kind_mismatch_hint(window):
    window._save_named_flow("e4")
    window.save_element_descriptor(
        "btn",
        {
            "kind": "desktop",
            "selector": {"locator": {"controlType": "Button"}},
            "verifyCount": 1,
        },
    )
    item = window.flow_model.find_by_id("read")  # browser.getText 无 locator 字段
    window.canvas_view.setCurrentIndex(window.flow_model.indexFromItem(item))
    window._insert_element("btn")
    assert "没有匹配" in window.statusBar().currentMessage()


def test_elements_require_named_flow(window):
    window._toggle_elements_dock()  # 示例流程未入库
    assert "流程库" in window._element_panel.hint_label.text()


def test_summarize_element(qapp):
    from rpa_core.gui.element_panel import summarize_element

    assert summarize_element(_browser_element()) == "browser · #kw"
    assert "desktop" in summarize_element(
        {"kind": "desktop", "selector": {"locator": {"name": "确定"}}}
    )


# ---- 插件状态（切 I） ---------------------------------------------------------
def test_extension_status_text_covers_both_browsers(window):
    text = window._extension_status_text()
    assert "chrome" in text and "edge" in text
    assert "浏览器" in text


# ---- bridge host 注册（M23 补强：GUI 对话框补齐 host 注册入口） ---------------
def test_native_host_status_text_per_browser(window, monkeypatch):
    import rpa_core.extension_installer as installer

    states = {
        "edge": {
            "registered": True,
            "extensionId": "edklhodhmpncghhhlpabpppimjakipdi",
            "hostExecutableExists": True,
        },
        "chrome": {"registered": False, "hostExecutableExists": True},
    }
    monkeypatch.setattr(
        installer, "native_host_status", lambda browser: states[browser]
    )
    text = window._native_host_status_text()
    assert "edge：bridge 已注册" in text
    assert "edklhodh" in text
    assert "chrome：bridge 未注册" in text


def test_native_host_status_text_missing_host_entry(window, monkeypatch):
    import rpa_core.extension_installer as installer

    missing = {"registered": False, "hostExecutableExists": False}
    monkeypatch.setattr(installer, "native_host_status", lambda browser: missing)
    text = window._native_host_status_text()
    assert "缺 host 入口" in text


def test_register_bridge_hosts_per_browser_tolerance(window, monkeypatch):
    import rpa_core.extension_installer as installer

    calls = []

    def fake_ensure(browser):
        calls.append(browser)
        if browser == "chrome":
            raise installer.ExtensionInstallError(
                "NATIVE_HOST_MISSING", "未找到 host 入口"
            )
        return {"extensionId": "abcdefghijklmnop"}

    monkeypatch.setattr(installer, "ensure_native_host", fake_ensure)
    text = window._register_bridge_hosts()
    assert calls == ["edge", "chrome"]
    assert "edge：已注册" in text
    assert "chrome：注册失败" in text
    assert "未找到 host 入口" in text

# ---- 捕获确认对话框（M23 G1 剩余：对齐 Web openElementDialog） --------------
def test_element_dialog_browser_defaults_and_result(qapp):
    """捕获确认框 = **捕获即编辑**：就地改主 css，产出仍是 ElementDescriptor 文档。"""
    from PySide6.QtWidgets import QDialog

    from rpa_core.gui.element_panel import ElementDialog

    dialog = ElementDialog(_browser_element(), default_name="el_input")
    assert dialog.windowTitle() == "捕获确认"
    assert dialog.name_edit.text() == "el_input"
    assert dialog.form.css_edit.text() == "#kw"  # 编辑区预填当前主 css
    assert "命中 1 个" in dialog.verify_label.text()
    assert "#1a7f37" in dialog.verify_label.styleSheet()  # 命中 1 = 绿

    dialog.name_edit.setText("searchBox")
    dialog.form.css_edit.setText("#q")
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    name, document = dialog.result_document()
    assert name == "searchBox"
    assert document["selector"] == {"css": "#q"}
    assert document["verifyCount"] == 1
    assert document["metadata"]["url"] == "https://example.com"


def test_element_dialog_desktop_edits_locator_by_fields_not_json(qapp):
    """桌面元素在确认框里直接勾 locator 字段——不再让用户写/看一行 JSON。

    这一条刻意**断言旧的 JSON 文本框已不存在**：它正是「确认框只能改一行 JSON」的
    遗迹，留着就等于两套编辑面（两套必然漂移）。旧的 `json.loads` 校验也只验得出
    「是不是合法 JSON 对象」，验不出「uia locator 一个身份字段都没有」——那是模型
    判据的活。
    """
    from PySide6.QtWidgets import QDialog

    from rpa_core.gui.element_panel import ElementDialog

    descriptor = {
        "kind": "desktop",
        "selector": {"locator": {"backend": "uia", "controlType": "Button", "name": "确定"}},
        "verifyCount": 3,
        "metadata": {"controlType": "Button", "automationId": "okBtn",
                     "windowTitle": "记事本", "name": "确定"},
    }
    dialog = ElementDialog(descriptor, default_name="el_Button")
    assert not hasattr(dialog, "selector_edit"), "确认框不该再有手写 locator 的文本框"
    assert dialog.form.backend_combo.currentText() == "uia"
    assert dialog.form.field_boxes["controlType"].isChecked()
    assert dialog.form.field_edits["name"].text() == "确定"
    assert "命中 3 个" in dialog.verify_label.text()
    assert "#cf222e" in dialog.verify_label.styleSheet()  # 命中非 1 = 红
    assert "controlType: Button" in dialog.meta_label.text()
    assert "window: 记事本" in dialog.meta_label.text()

    # 取消全部身份字段 → 结构校验拦住保存（旧的手写 JSON 校验查不出这一条）
    dialog.form.field_boxes["controlType"].setChecked(False)
    dialog.form.field_boxes["name"].setChecked(False)
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert "身份字段" in dialog.form.info_label.text()

    # 勾回一个身份字段：通过且回写 locator
    dialog.form.field_boxes["automationId"].setChecked(True)
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    _, document = dialog.result_document()
    assert document["selector"]["locator"]["backend"] == "uia"
    assert document["selector"]["locator"]["automationId"] == "okBtn"


def test_element_dialog_rejects_empty_name_or_blank_css(qapp):
    """两道拦截各归其位：元素名归确认框，selector 合法性归编辑区的模型判据。

    分工的理由是「判据的权威唯一」：确认框自己再判一遍 selector 等于立第二套规则，
    而两套规则必然漂移（漂移的表现是「确认框放行的东西，执行器读不了」）。
    """
    from PySide6.QtWidgets import QDialog

    from rpa_core.gui.element_panel import ElementDialog

    dialog = ElementDialog(_browser_element(), default_name="x")
    dialog.name_edit.setText("  ")
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert "元素名" in dialog.error_label.text()

    dialog.name_edit.setText("ok")
    dialog.form.css_edit.setText("")
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert "不能为空" in dialog.form.info_label.text()


def test_element_dialog_reports_which_exit_was_used(qapp):
    """三个出口靠 `intent()` 区分——``QDialogButtonBox`` 的 accepted 信号**不带来源**。

    这不是「多写一个 getter」：三个出口里有两个都产出同一份文档，调用方只能靠意图
    决定后续动作（「保存」收工 / 「保存并继续」重启捕获 / 「重新捕获」丢弃重来）。
    取消与关闭窗口走 Rejected、**不进** `intent()` —— 调用方先看 exec 结果。
    """
    from PySide6.QtWidgets import QDialog, QDialogButtonBox

    from rpa_core.gui.element_panel import ElementDialog

    dialog = ElementDialog(_browser_element(), default_name="a")
    assert dialog.intent() == "save", "什么都没按过时，默认意图就是保存"

    dialog.continue_button.click()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.intent() == "save_and_continue"

    recapture = ElementDialog(_browser_element(), default_name="b")
    recapture.recapture_button.click()
    assert recapture.result() == QDialog.DialogCode.Accepted
    assert recapture.intent() == "recapture"

    # 两个新出口必须留在 ActionRole。AcceptRole 会被 QDialogButtonBox **自动**
    # 接到 accepted → `accept()`，于是「重新捕获」也去校验元素名与定位——
    # 一次毫无意义、还会把用户困在对话框里的校验。这条钉的是那个自动连接本身。
    # （`buttonRole()` 在 PySide6 里是**实例方法**，得从**该按钮所属的**按钮盒上问：
    #  拿另一个对话框的盒子查，返回的是 InvalidRole，看着像「角色接错了」。）
    pairs = ((dialog, dialog.continue_button), (recapture, recapture.recapture_button))
    for owner, button in pairs:
        role = owner.button_box.buttonRole(button)
        assert role == QDialogButtonBox.ButtonRole.ActionRole


def test_element_dialog_save_and_continue_is_validated_like_save(qapp):
    """「保存并继续」**真的会落盘** → 与「保存」走同一套校验；不过关不许关窗。"""
    from PySide6.QtWidgets import QDialog

    from rpa_core.gui.element_panel import ElementDialog

    dialog = ElementDialog(_browser_element(), default_name="")
    dialog.continue_button.click()
    assert dialog.result() != QDialog.DialogCode.Accepted, "空元素名不该放行"
    assert "元素名" in dialog.error_label.text()
    assert dialog.intent() == "save", "窗口没关，就不该记下出口意图"

    dialog.name_edit.setText("ok")
    dialog.continue_button.click()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.intent() == "save_and_continue"


def test_element_dialog_recapture_discards_without_validation(qapp):
    """「重新捕获」必须**不校验**：本次结果当场丢弃，校验没有对象。

    连空元素名都要放行——用户此刻就是还没定名字，而这个名字根本不会落盘。
    若这里被校验拦住，「重新捕获」就退化成「必须先把元素起好名才能重来」。
    """
    from PySide6.QtWidgets import QDialog

    from rpa_core.gui.element_panel import ElementDialog

    dialog = ElementDialog(_browser_element(), default_name="")
    dialog.recapture_button.click()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.intent() == "recapture"
    assert dialog.error_label.text() == "", "重新捕获不该报元素名错误"


def test_element_dialog_preserves_candidates_on_edit(qapp):
    """编辑元素不能抹掉捕获时收集的备选定位。

    回归：``result_document`` 曾从头重建 ``selector``，用户在 GUI 里编辑一次就静默
    丢掉 ``candidates``（以及任何界面不暴露为可编辑项的键）。

    注意这条判据**不因「候选现在可编辑了」而失效**：写回仍然以原 selector 为基底、
    只覆盖被编辑的键（现在由 ``ElementEditorForm.result_document`` 保证）。
    """
    from PySide6.QtWidgets import QDialog

    from rpa_core.gui.element_panel import ElementDialog

    candidates = [
        {"kind": "id", "selector": "#sb_form_q", "matchedCount": 1},
        {"kind": "attribute", "selector": 'input[name="q"]', "matchedCount": 1},
    ]
    descriptor = {
        "kind": "browser",
        "selector": {"css": "#sb_form_q", "candidates": candidates},
        "verifyCount": 1,
        "metadata": {"role": "searchbox", "url": "https://example.com/"},
    }
    dialog = ElementDialog(descriptor, default_name="searchBox")
    assert dialog.form.css_edit.text() == "#sb_form_q"  # 编辑区只放主 css

    dialog.name_edit.setText("searchBox2")
    dialog.form.css_edit.setText("#q")
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    name, document = dialog.result_document()
    assert name == "searchBox2"
    assert document["selector"]["css"] == "#q"  # 用户改的生效
    assert document["selector"]["candidates"] == candidates  # 候选原样保留
    assert document["metadata"]["role"] == "searchbox"


# ---- 捕获确认对话框：候选与语义特征的只读展示（调研发现「有数据无界面」） ------
def _captured_browser_descriptor() -> dict:
    """按 ``content.js buildDescriptor`` 的真实产出形状构造（与契约测试同款）。"""
    return {
        "kind": "browser",
        "selector": {
            "css": "#sb_form_q",
            "candidates": [
                {"kind": "id", "selector": "#sb_form_q", "matchedCount": 1},
                {
                    "kind": "attribute",
                    "selector": 'input[name="q"]',
                    "matchedCount": 3,
                },
            ],
        },
        "verifyCount": 1,
        "metadata": {
            "tag": "textarea",
            "id": "sb_form_q",
            "classes": ["search", "box"],
            "text": "",
            "rect": {"x": 10, "y": 20, "width": 300, "height": 40},
            "role": "searchbox",
            "accessibleName": "搜索",
            "placeholder": "请输入搜索内容",
            "label": "搜索框",
            "containerText": "主页 搜索 更多",
            "url": "https://www.bing.com/",
            "title": "Bing",
        },
    }


def test_element_dialog_shows_candidates_with_matched_counts(qapp):
    """候选必须被展示出来（含捕获时命中数）——此前只入库不展示。

    用户此前既不知道自愈能力存在，也无从在命中多个时挑一个更稳的候选。
    现在它们就在确认框的编辑区里（比只读展示更进一步：点一下即设为主定位）。
    """
    from rpa_core.gui.element_panel import ElementDialog

    dialog = ElementDialog(_captured_browser_descriptor(), default_name="searchBox")
    items = [
        dialog.form.candidate_list.item(index).text()
        for index in range(dialog.form.candidate_list.count())
    ]
    assert len(items) == 2
    assert "备选定位 2 条" in dialog.form.candidates_label.text()
    assert "[id] #sb_form_q" in items[0]
    assert "命中 1" in items[0]
    assert 'input[name="q"]' in items[1]


def test_element_dialog_flags_non_unique_candidate(qapp):
    """``matchedCount > 1`` 的候选要如实标「不唯一」。

    回退到不唯一的候选可能点到别的元素 —— 这正是运行期自愈最危险的一步。
    标出来让用户自己做判断，而不是替他过滤掉（过滤会隐藏真实的可选项）。
    """
    from rpa_core.gui.element_panel import ElementDialog

    dialog = ElementDialog(_captured_browser_descriptor(), default_name="searchBox")
    # 候选现在归编辑区（`ElementEditorForm` 的候选列表，点一下即设为主定位），
    # 不再是确认框里的只读文本块 —— 判据跟着读列表项。
    items = [
        dialog.form.candidate_list.item(index).text()
        for index in range(dialog.form.candidate_list.count())
    ]
    id_line = next(line for line in items if "#sb_form_q" in line)
    attr_line = next(line for line in items if 'input[name="q"]' in line)
    assert "命中 1" in id_line
    assert "不唯一" not in id_line
    assert "命中 3" in attr_line
    assert "不唯一" in attr_line


def test_element_dialog_shows_semantic_features_and_fingerprint(qapp):
    """语义特征与页面指纹要展示（它们正是「命中多个时人工消歧」最有用的信息）。"""
    from rpa_core.gui.element_panel import ElementDialog

    dialog = ElementDialog(_captured_browser_descriptor(), default_name="searchBox")
    meta = dialog.meta_label.text()
    for expected in (
        "role: searchbox",
        "accessibleName: 搜索",
        "placeholder: 请输入搜索内容",
        "label: 搜索框",
        "containerText: 主页 搜索 更多",
        "url: https://www.bing.com/",
        "title: Bing",
    ):
        assert expected in meta, expected
    # 既有行集不能因此丢（tag/id/classes/rect 仍要展示）
    assert "tag: textarea" in meta
    assert "rect: 300×40 @ (10,20)" in meta


def test_element_dialog_candidates_section_is_browser_only(qapp):
    """候选列表是 browser 专属节：「收集到 0 条」与「没有这个概念」不能混成一件事。

    - browser 收集 0 条 —— 编辑区仍要**明说**「没有收集到备选定位」。候选是运行期
      自愈的依据（主选择器失效后按序回退），「一条都没有」本身是有意义的信息，
      静默留白会让人以为界面没显示出来。
    - desktop 没有候选概念（其回退逻辑不落盘）—— 这一节**整节不存在**（属性都没有），
      否则会误导用户去找一个不存在的东西。

    此前这两件事被同一个「隐藏」表达盖住；改成读控件本身后，口径反而更硬：
    桌面侧不是「标签为空」，而是「连控件都没建」。
    """
    from rpa_core.gui.element_panel import ElementDialog

    plain = ElementDialog(
        {"kind": "browser", "selector": {"css": "#kw"}, "verifyCount": 1, "metadata": {}},
        default_name="a",
    )
    assert plain.form.candidate_list.count() == 0
    assert "没有收集到备选定位" in plain.form.candidates_label.text()

    desktop = ElementDialog(
        {
            "kind": "desktop",
            "selector": {"locator": {"controlType": "Button"}},
            "verifyCount": 1,
            "metadata": {"controlType": "Button"},
        },
        default_name="b",
    )
    assert not hasattr(desktop.form, "candidate_list")
    assert not hasattr(desktop.form, "candidates_label")
    # desktop 的元数据里不该冒出 browser 的语义特征行
    assert "role:" not in desktop.meta_label.text()
    assert "url:" not in desktop.meta_label.text()


def _captured_desktop_descriptor() -> dict:
    """真机捕获产物**原样**抄自探针输出。

    来源：``.harness/spike/probe_element_dialog_display.py``（编译靶子 → 真实控件取点 →
    ``desktop-capture-agent --point``）。不是照代码想象的形状。
    """
    return {
        "kind": "desktop",
        "selector": {
            "locator": {
                "backend": "uia",
                "controlType": "Button",
                "automationId": "submitButton",
                "name": "Submit",
            }
        },
        "verifyCount": 1,
        "metadata": {
            "windowHandle": 6225970,
            "windowTitle": "RPA Core Desktop Demo",
            "controlType": "Button",
            "automationId": "submitButton",
            "name": "Submit",
            "className": "WindowsForms10.BUTTON.app.0.34f5582_r8_ad1",
        },
    }


def test_element_dialog_shows_desktop_class_and_name(qapp):
    """桌面元数据要展示 className / name。

    探针实测发现：捕获 agent 一直回传 ``className``（win32 侧定位无窗口文本控件的
    唯一手段，也是 ``classNameRe`` 的输入），但 GUI 从没显示过它。
    """
    from rpa_core.gui.element_panel import ElementDialog

    dialog = ElementDialog(_captured_desktop_descriptor(), default_name="submit")
    meta = dialog.meta_label.text()
    assert "controlType: Button" in meta
    assert "automationId: submitButton" in meta
    assert "name: Submit" in meta
    assert "className: WindowsForms10.BUTTON.app.0.34f5582_r8_ad1" in meta
    assert "window: RPA Core Desktop Demo" in meta


def test_element_dialog_hides_per_session_desktop_values(qapp):
    """``windowHandle`` / ``point`` 刻意不展示。

    它们是本次会话的运行期值（句柄每次启动都变、坐标随窗口位置变），摆出来会诱导
    用户粘进 locator，写出一个下次必定失效的元素。
    """
    from rpa_core.gui.element_panel import ElementDialog

    dialog = ElementDialog(_captured_desktop_descriptor(), default_name="submit")
    meta = dialog.meta_label.text()
    assert "windowHandle" not in meta
    assert "6225970" not in meta


def test_candidates_text_tolerates_missing_or_bad_counts(qapp):
    """``matchedCount`` 允许缺席（契约如此）；坏形状不该让 UI 崩。"""
    from rpa_core.gui.element_panel import candidates_text

    assert candidates_text({"kind": "browser", "selector": {"css": "#a"}}) == ""
    assert candidates_text(
        {"kind": "browser", "selector": {"css": "#a", "candidates": []}}
    ) == ""
    assert candidates_text(
        {"kind": "browser", "selector": {"css": "#a", "candidates": "oops"}}
    ) == ""
    text = candidates_text(
        {
            "kind": "browser",
            "selector": {
                "css": "#a",
                "candidates": [{"kind": "css", "selector": ".x"}, "oops"],
            },
        }
    )
    assert "备选定位 1 条" in text  # 非 dict 的项被跳过，不计入条数
    assert "命中未实测" in text


def test_semantic_meta_text_is_browser_only(qapp):
    """desktop 描述符走这条路必须返回空串（语义特征键只属于 browser）。"""
    from rpa_core.gui.element_panel import semantic_meta_text

    assert semantic_meta_text({"kind": "desktop", "metadata": {"role": "x"}}) == ""
    assert semantic_meta_text({"kind": "browser", "metadata": {}}) == ""


def test_clip_truncates_long_values(qapp):
    """超长值（URL / 容器文本可达 200 字）要截断，避免撑爆对话框。"""
    from rpa_core.gui.element_panel import _META_DISPLAY_LIMIT, _clip

    short = "x" * _META_DISPLAY_LIMIT
    assert _clip(short) == short
    clipped = _clip("x" * (_META_DISPLAY_LIMIT + 50))
    assert len(clipped) == _META_DISPLAY_LIMIT + 1
    assert clipped.endswith("…")
    assert _clip("a\n  b\tc") == "a b c"  # 压平换行/制表，避免行数被撑开


def test_element_dialog_tolerates_non_dict_selector(qapp):
    """selector 形态非法时不应崩在对话框上（合并基底只接受 dict）。"""
    from PySide6.QtWidgets import QDialog

    from rpa_core.gui.element_panel import ElementDialog

    dialog = ElementDialog(
        {"kind": "browser", "selector": "oops", "verifyCount": 1, "metadata": {}},
        default_name="el_x",
    )
    dialog.form.css_edit.setText("#a")
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    _, document = dialog.result_document()
    assert document["selector"] == {"css": "#a"}


# ---- 元素库 dock 预热（M40 追加报障：第一次点元素库卡一下） --------------------
def test_editor_show_prewarms_elements_dock_hidden(window, qapp):
    """窗口首次显示后自动预热元素库 dock：建好但**保持隐藏**，开关语义不变。

    维护者 2026-09-28 报障「点击元素库时第一次也会卡一下，关闭元素库重开就好多了」：
    首开整条路径 65 ms（惰性 import + `ElementPanel` 构造 + `addDockWidget` 触发整窗
    重排 + 首帧），dock 缓存后二次开 20 ms。预热把这笔一次性开销挪到编辑器刚打开的空闲
    时机，跟 `_prewarm_param_panel` 同一思路。
    """
    assert getattr(window, "_elements_dock_widget", None) is None, (
        "构造期不该已经建好 dock（否则预热无从谈「提前」）"
    )

    window.show()
    qapp.processEvents()  # 让 showEvent 里排的零延时预热跑起来

    dock = getattr(window, "_elements_dock_widget", None)
    assert dock is not None, "窗口显示后应已预热元素库 dock"
    # offscreen 下 isVisible() 恒 False（连该显示的也是），可见性要问 isVisibleTo
    assert not dock.isVisibleTo(window), "预热必须保持隐藏，不能自己弹出来"

    window._toggle_elements_dock()
    assert dock.isVisibleTo(window), "预热不许改变 _toggle_elements_dock 的开关语义"
    assert window._elements_dock() is dock, "预热过的 dock 要被复用，不是又建一个"

# ---- 元素库搜索/过滤（A4，对齐影刀元素库面板） --------------------------------
def test_element_panel_search_filters_display_only(qapp):
    """搜索框按名称/摘要过滤**显示**；清空恢复全部；不动元素资产本身。"""
    from rpa_core.gui.element_panel import ElementPanel

    panel = ElementPanel()
    panel.set_elements(
        [
            {"name": "searchBox", "summary": "browser · css #sb_form_q"},
            {"name": "okButton", "summary": "browser · css button.ok"},
            {"name": "记事本", "summary": "desktop · win32 title=记事本"},
        ]
    )
    assert panel.list.count() == 3

    panel.search_edit.setText("button")
    assert panel.list.count() == 1
    assert panel.list.item(0).text().startswith("okButton")

    # 摘要也能搜（用定位串找元素是高频动作）
    panel.search_edit.setText("#sb_form_q")
    assert panel.list.count() == 1
    assert panel.list.item(0).text().startswith("searchBox")

    # 大小写不敏感
    panel.search_edit.setText("SB_FORM")
    assert panel.list.count() == 1

    # 清空恢复全部；current_name 始终读可见项的首列
    panel.search_edit.setText("")
    assert panel.list.count() == 3
    panel.list.setCurrentRow(0)
    assert panel.current_name() == "searchBox"


# ---- 元素引用（M46/B1 引用模型：GUI 侧写入口径） --------------------------------
def test_insert_element_writes_element_ref(window):
    """插入 = 双写：值快照进 with，引用进 elementRefs（运行期按引用取最新值）。"""
    window._save_named_flow("eref1")
    window.save_element_descriptor("searchBox", _browser_element())
    item = window.flow_model.find_by_id("read")
    window.canvas_view.setCurrentIndex(window.flow_model.indexFromItem(item))
    window._insert_element("searchBox")

    from rpa_core.gui.flow_model import ROLE_ARGS_RAW

    holder = item.data(ROLE_ARGS_RAW)
    assert holder.raw["with"]["selector"] == "#kw"
    assert holder.raw["elementRefs"] == {"selector": "searchBox"}
    # 落盘链路过得去校验（elementRefs 是声明过的模型字段）
    document = window._build_document()

    def _find(nodes, node_id):
        for child in nodes:
            if child.get("id") == node_id:
                return child
            for key in ("children", "then", "else"):
                hit = _find(child.get(key) or [], node_id)
                if hit is not None:
                    return hit
        return None

    node = _find(document["root"].get("children") or [], "read")
    assert node is not None
    assert node["elementRefs"] == {"selector": "searchBox"}


def test_manual_param_edit_takes_over_and_drops_element_ref(window):
    """手工改引用参数 = 用户接管：引用摘除（否则运行期元素库值盖掉手工输入）；
    值没动的提交保留引用。"""
    window._save_named_flow("eref2")
    window.save_element_descriptor("searchBox", _browser_element())
    item = window.flow_model.find_by_id("read")
    window.canvas_view.setCurrentIndex(window.flow_model.indexFromItem(item))
    window._insert_element("searchBox")

    from PySide6.QtWidgets import QPushButton

    from rpa_core.gui.flow_model import ROLE_ARGS_RAW
    from rpa_core.gui.param_form import ParamForm

    holder = item.data(ROLE_ARGS_RAW)
    assert holder.raw["elementRefs"] == {"selector": "searchBox"}

    # 不改值直接应用 → 引用保留（如改别的字段）
    apply_button = window.param_holder.findChild(QPushButton)
    apply_button.click()
    assert holder.raw["elementRefs"] == {"selector": "searchBox"}

    # 改 selector 值再应用 → 引用被摘除
    form = window.param_holder.findChild(ParamForm)
    selector_edit = next(
        widget for field, _kind, widget in form._fields if field == "selector"
    )
    selector_edit.setText("#hand-tuned")
    apply_button.click()
    assert holder.raw["with"]["selector"] == "#hand-tuned"
    assert "elementRefs" not in holder.raw


# ---- 活体校验（M47 S1）：确认框里的「校验元素」按钮 -----------------------------
def _pump_until_gui(predicate, timeout_ms: int = 5000) -> bool:
    """泵事件循环直到谓词为真（跨线程信号是队列投递，必须泵才送达）。"""
    import time

    from PySide6.QtWidgets import QApplication

    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_element_dialog_live_verify_reports_count(qapp):
    """校验按钮：工作线程跑注入的回调，命中数回显到标签（替换捕获时旧值）。"""
    from rpa_core.gui.element_panel import ElementDialog

    seen: list[str] = []

    def fake_verify(css: str) -> dict:
        seen.append(css)
        return {"count": 2}

    dialog = ElementDialog(
        _browser_element(), default_name="x", verify_css=fake_verify
    )
    assert dialog.verify_button is not None
    dialog.verify_button.click()
    assert dialog.verify_button.isEnabled() is False  # 校验中禁用（防连点）
    assert _pump_until_gui(
        lambda: dialog.verify_label.text().startswith("当前命中 2")
    )
    assert seen == ["#kw"]  # 用的是编辑区**当前**的 css
    assert dialog.verify_button.isEnabled()


def test_element_dialog_live_verify_error_is_inline(qapp):
    """通道故障是**就地**结构化报错，不弹窗、不抛异常。"""
    from rpa_core.gui.element_panel import ElementDialog

    dialog = ElementDialog(
        _browser_element(),
        default_name="x",
        verify_css=lambda css: {"error": "extension-offline"},
    )
    dialog.verify_button.click()
    assert _pump_until_gui(lambda: "校验失败" in dialog.error_label.text())
    assert "extension-offline" in dialog.error_label.text()
    assert dialog.verify_button.isEnabled()


def test_element_dialog_without_verify_callback_has_no_button(qapp):
    """desktop 元素 / 未注入回调：不摆「校验元素」按钮（永远转圈的按钮不如不摆）。"""
    from rpa_core.gui.element_panel import ElementDialog

    desktop = {
        "kind": "desktop",
        "selector": {"locator": {"backend": "win32", "controlType": "Button"}},
        "metadata": {"controlType": "Button"},
    }
    dialog = ElementDialog(desktop, default_name="y")
    assert dialog.verify_button is None
