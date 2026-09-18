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
    assert "校验通过" in window.statusBar().currentMessage()


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