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
    from PySide6.QtWidgets import QDialog

    from rpa_core.gui.element_panel import ElementDialog

    dialog = ElementDialog(_browser_element(), default_name="el_input")
    assert dialog.windowTitle() == "捕获确认"
    assert dialog.name_edit.text() == "el_input"
    assert dialog.selector_edit.text() == "#kw"
    assert "命中 1 个" in dialog.verify_label.text()
    assert "#1a7f37" in dialog.verify_label.styleSheet()  # 命中 1 = 绿

    dialog.name_edit.setText("searchBox")
    dialog.selector_edit.setText("#q")
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    name, document = dialog.result_document()
    assert name == "searchBox"
    assert document["selector"] == {"css": "#q"}
    assert document["verifyCount"] == 1
    assert document["metadata"]["url"] == "https://example.com"


def test_element_dialog_desktop_locator_json_validation(qapp):
    from PySide6.QtWidgets import QDialog

    from rpa_core.gui.element_panel import ElementDialog

    descriptor = {
        "kind": "desktop",
        "selector": {"locator": {"controlType": "Button", "name": "确定"}},
        "verifyCount": 3,
        "metadata": {"controlType": "Button", "automationId": "okBtn",
                     "windowTitle": "记事本"},
    }
    dialog = ElementDialog(descriptor, default_name="el_Button")
    assert "确定" in dialog.selector_edit.text()  # locator JSON 预填
    assert "命中 3 个" in dialog.verify_label.text()
    assert "#cf222e" in dialog.verify_label.styleSheet()  # 命中非 1 = 红
    assert "controlType: Button" in dialog.meta_label.text()
    assert "window: 记事本" in dialog.meta_label.text()

    # 非法 JSON：拒绝关闭并给出内联错误
    dialog.selector_edit.setText("{not json")
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert "JSON" in dialog.error_label.text()

    # 非对象 JSON（数组）：同样拒绝
    dialog.selector_edit.setText("[1, 2]")
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted

    # 改回合法对象：通过且回写 locator
    dialog.selector_edit.setText('{"controlType": "Edit"}')
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    _, document = dialog.result_document()
    assert document["selector"] == {"locator": {"controlType": "Edit"}}


def test_element_dialog_rejects_empty_name_or_selector(qapp):
    from PySide6.QtWidgets import QDialog

    from rpa_core.gui.element_panel import ElementDialog

    dialog = ElementDialog(_browser_element(), default_name="x")
    dialog.name_edit.setText("  ")
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert "元素名" in dialog.error_label.text()

    dialog.name_edit.setText("ok")
    dialog.selector_edit.setText("")
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert "selector" in dialog.error_label.text()


def test_element_dialog_preserves_candidates_on_edit(qapp):
    """编辑元素不能抹掉捕获时收集的备选定位。

    回归：``result_document`` 曾从头重建 ``selector``，用户在 GUI 里编辑一次就静默
    丢掉 ``candidates``（以及任何界面不暴露为可编辑项的键）。

    注意这条判据**不因「候选现在会展示了」而失效**：展示是只读的（``candidates_label``），
    写回仍然只覆盖 ``css``/``locator`` 一个键。展示与写回是两条独立的路径。
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
    assert dialog.selector_edit.text() == "#sb_form_q"  # selector 编辑框只放主 css

    dialog.name_edit.setText("searchBox2")
    dialog.selector_edit.setText("#q")
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
    """
    from rpa_core.gui.element_panel import ElementDialog

    dialog = ElementDialog(_captured_browser_descriptor(), default_name="searchBox")
    text = dialog.candidates_label.text()
    assert "备选定位 2 条" in text
    assert "[id] #sb_form_q" in text
    assert "命中 1" in text
    assert '[attribute] input[name="q"]' in text
    # 候选标签是展示面，不是第二个可编辑的 selector 框
    assert dialog.candidates_label.isHidden() is False


def test_element_dialog_flags_non_unique_candidate(qapp):
    """``matchedCount > 1`` 的候选要如实标「不唯一」。

    回退到不唯一的候选可能点到别的元素 —— 这正是运行期自愈最危险的一步。
    标出来让用户自己做判断，而不是替他过滤掉（过滤会隐藏真实的可选项）。
    """
    from rpa_core.gui.element_panel import ElementDialog

    dialog = ElementDialog(_captured_browser_descriptor(), default_name="searchBox")
    lines = dialog.candidates_label.text().splitlines()
    id_line = next(line for line in lines if "#sb_form_q" in line)
    attr_line = next(line for line in lines if 'input[name="q"]' in line)
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


def test_element_dialog_hides_candidates_section_when_absent(qapp):
    """没有候选时不显示这一节（旧元素文档只有 css，不属于「未展示」）。

    desktop 元素没有候选概念（其回退逻辑不落盘），也必须不显示，
    否则会误导用户去找一个不存在的东西。
    """
    from rpa_core.gui.element_panel import ElementDialog

    plain = ElementDialog(
        {"kind": "browser", "selector": {"css": "#kw"}, "verifyCount": 1, "metadata": {}},
        default_name="a",
    )
    assert plain.candidates_label.text() == ""
    assert plain.candidates_label.isHidden()

    desktop = ElementDialog(
        {
            "kind": "desktop",
            "selector": {"locator": {"controlType": "Button"}},
            "verifyCount": 1,
            "metadata": {"controlType": "Button"},
        },
        default_name="b",
    )
    assert desktop.candidates_label.text() == ""
    assert desktop.candidates_label.isHidden()
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
    dialog.selector_edit.setText("#a")
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    _, document = dialog.result_document()
    assert document["selector"] == {"css": "#a"}