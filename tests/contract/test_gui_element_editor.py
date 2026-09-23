"""元素编辑器（M39 ③-1 离线版）：候选可选 / 桌面 locator 字段化 / 就地结构校验。

在 offscreen Qt 平台运行；缺 PySide6 时整组跳过。
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


def _browser_document() -> dict:
    """浏览器元素：主 css + 三条候选（含一条不唯一、一条未实测）。"""
    return {
        "kind": "browser",
        "selector": {
            "css": "#sb_form_q",
            "candidates": [
                {"kind": "id", "selector": "#sb_form_q", "matchedCount": 1},
                {"kind": "attribute", "selector": 'input[name="q"]', "matchedCount": 3},
                {"kind": "attribute", "selector": "[data-testid=search]"},  # 未实测
            ],
        },
        "verifyCount": 1,
        "metadata": {"tag": "textarea", "role": "searchbox", "url": "https://x/"},
    }


def _desktop_document() -> dict:
    """桌面元素：真机捕获产物形状（capture agent 只产 uia locator）。"""
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
            "windowTitle": "RPA Core Desktop Demo",
            "controlType": "Button",
            "automationId": "submitButton",
            "name": "Submit",
            "className": "WindowsForms10.BUTTON.app.0.34f5582_r8_ad1",
        },
    }


# ---- 字段集按 backend 分（实测：两执行器消费的字段完全不重叠） ----------------
def test_backend_field_sets_are_disjoint_and_match_the_executors(qapp):
    """字段表必须与执行器实际消费的字段一致，且两个后端不重叠。

    这条是**事实钉子**：`desktop.py::_find` 只读 automation_id / control_type / name，
    `desktop_win32.py::_find` 只读 title / class_name / class_name_re / control_id /
    found_index。谁改了两边的消费面，这里必须跟着改（否则界面会提供死字段）。
    """
    from rpa_core.gui.element_editor import fields_for

    uia = {key for key, _kind, _hint in fields_for("uia")}
    win32 = {key for key, _kind, _hint in fields_for("win32")}
    assert uia == {"controlType", "automationId", "name"}
    assert win32 == {"title", "className", "classNameRe", "controlId", "foundIndex"}
    assert not (uia & win32)


def test_never_offers_fields_no_executor_consumes(qapp):
    """``menuPath`` / ``handle`` 两边都不消费 → 任何 backend 都不提供。

    ``menuPath`` 只作为 ``desktop_win32.menuSelect`` 的**命令输入**被读，从不来自
    locator；``handle`` 是运行期值。提供它们＝在 UI 上生产静默死字段。
    """
    from rpa_core.gui.element_editor import (
        ALL_FIELD_KEYS,
        declared_locator_keys,
        fields_for,
    )

    for backend in ("uia", "win32"):
        offered = {key for key, _kind, _hint in fields_for(backend)}
        assert "menuPath" not in offered
        assert "handle" not in offered
    assert "menuPath" not in ALL_FIELD_KEYS
    assert "handle" not in ALL_FIELD_KEYS
    # 声明键从模型取（不是手抄）：正因为模型真声明了 menuPath / handle，才需要标出它们
    declared = set(declared_locator_keys())
    assert {"backend", "menuPath", "handle", "foundIndex"} <= declared
    # 提供的字段必须是模型声明过的子集，否则组装出的 locator 会被 extra=forbid 拒掉
    assert set(ALL_FIELD_KEYS) <= declared


def test_field_tables_match_what_executors_actually_read(qapp):
    """字段表 vs 执行器**实际读取**的 locator 字段：双边必须逐字一致。

    这是「参数消费」在 UI 层的对应物，判据自维护：用 AST 扫执行器里全部
    ``locator.<字段>`` 读取点，再折回别名与界面字段表比对。

    - 表里有、执行器不读 → 界面在生产静默死字段（本次就抓出 uia 下 className 这种情况）
    - 执行器读、表里没有 → 用户永远改不了这个字段

    任何一边变了这里就红，不需要有人记得同步两份清单。
    """
    import ast
    from pathlib import Path

    from rpa_core.gui.element_editor import fields_for
    from rpa_core.model.desktop import DesktopLocator

    snake_to_alias = {
        name: (field.alias or name)
        for name, field in DesktopLocator.model_fields.items()
    }
    executors = Path(__file__).resolve().parents[2] / "src" / "rpa_core" / "executors"
    for backend, filename in (("uia", "desktop.py"), ("win32", "desktop_win32.py")):
        tree = ast.parse((executors / filename).read_text(encoding="utf-8"))
        read: set[str] = set()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "locator"
            ):
                read.add(node.attr)
        # model_dump 等非字段访问会落回原名，用字段表筛掉
        read_aliases = {
            snake_to_alias[name] for name in read if name in snake_to_alias
        }
        read_aliases.discard("backend")
        offered = {key for key, _kind, _hint in fields_for(backend)}
        assert offered == read_aliases, f"{backend}: 界面 {offered} vs 执行器 {read_aliases}"


def test_compose_locator_ignores_cross_backend_fields(qapp):
    """给了对面后端的字段也不组装——界面漏一个字段不该写进死字段。"""
    from rpa_core.gui.element_editor import compose_locator

    assert compose_locator("uia", {}) == {"backend": "uia"}
    assert compose_locator("uia", {"className": "X", "title": "T"}) == {
        "backend": "uia"
    }
    assert compose_locator("win32", {"automationId": "X"}) == {"backend": "win32"}


def test_compose_locator_trims_and_casts_integers(qapp):
    from rpa_core.gui.element_editor import LocatorFieldError, compose_locator

    locator = compose_locator(
        "win32", {"title": " RPA Demo ", "controlId": " 42 ", "foundIndex": "0"}
    )
    assert locator == {
        "backend": "win32",
        "title": "RPA Demo",
        "controlId": 42,
        "foundIndex": 0,
    }

    with pytest.raises(LocatorFieldError) as err:
        compose_locator("win32", {"controlId": "not-a-number"})
    assert "controlId" in str(err.value)


def test_split_locator_only_returns_its_own_backend_fields(qapp):
    from rpa_core.gui.element_editor import split_locator

    assert split_locator(
        {"backend": "win32", "title": "T", "className": "C"}
    ) == {"title": "T", "className": "C"}
    assert split_locator({"backend": "uia", "className": "X"}) == {}


def test_inert_locator_keys_flags_dead_fields(qapp):
    from rpa_core.gui.element_editor import inert_locator_keys

    assert inert_locator_keys({"backend": "uia", "controlType": "Button"}) == []
    assert inert_locator_keys({"backend": "uia", "className": "X"}) == ["className"]
    # menuPath 在**两个**后端都是死字段（只作为命令输入被读）
    assert inert_locator_keys({"backend": "win32", "menuPath": ["a"]}) == ["menuPath"]


# ---- 模型报错的中文映射（把映射钉死，防止静默退回英文） ----------------------
def test_every_locator_model_rule_is_translated(qapp):
    """逐条触发 ``DesktopLocator`` 的规则，断言**每条都被译成中文**。

    这条是映射表的存在性证明：模型改了措辞、映射表没跟上 → 这里立刻红，
    而不是让用户突然看到一段英文 pydantic 报错。
    """
    from pydantic import ValidationError

    from rpa_core.gui.element_editor import translate_locator_message
    from rpa_core.model.desktop import DesktopLocator

    triggered = [
        {"backend": "abc"},
        {"backend": "uia"},
        {"backend": "win32"},
        {"backend": "win32", "className": "X", "classNameRe": "Y"},
        {"backend": "win32", "menuPath": []},
    ]
    seen = 0
    for case in triggered:
        with pytest.raises(ValidationError) as err:
            DesktopLocator.model_validate(case)
        for error in err.value.errors():
            raw = str(error.get("msg", error))
            seen += 1
            assert translate_locator_message(raw) != raw, raw
    assert seen == len(triggered)


def test_unknown_message_passes_through_untranslated(qapp):
    """译不出的消息原样返回——不吞、不编，用户至少能看到原文。"""
    from rpa_core.gui.element_editor import translate_locator_message

    assert translate_locator_message("something brand new") == "something brand new"


def test_locator_problems_reports_each_rule(qapp):
    from rpa_core.gui.element_editor import locator_problems

    assert locator_problems({"backend": "uia", "controlType": "Button"}) == []
    assert locator_problems({"backend": "uia"}) == [
        "uia 定位至少要勾一个身份字段（controlType / automationId / name）"
    ]
    assert locator_problems({"backend": "win32"}) == [
        "win32 定位至少要勾一个身份字段"
        "（title / className / classNameRe / controlId）"
    ]
    assert locator_problems(
        {"backend": "win32", "className": "X", "classNameRe": "Y"}
    ) == ["className（等值）与 classNameRe（正则）互斥，只能留一个"]


def test_css_problems_rejects_blank(qapp):
    from rpa_core.gui.element_editor import css_problems

    assert css_problems("#a") == []
    assert css_problems("   ") == ["主选择器（css）不能为空"]


# ---- 候选提升（交换语义，不是单向覆盖） --------------------------------------
def test_promotable_requires_measured_positive_count(qapp):
    """``matchedCount`` 必须是有实测的正整数；bool 不算 int。"""
    from rpa_core.gui.element_editor import promotable

    assert promotable({"matchedCount": 1})
    assert promotable({"matchedCount": 3})
    assert not promotable({"matchedCount": 0})
    assert not promotable({})
    assert not promotable({"matchedCount": None})
    assert not promotable({"matchedCount": True})
    assert not promotable({"matchedCount": "3"})


def test_promote_candidate_swaps_old_primary_back_into_candidates(qapp):
    """提升候选时旧主定位按序放回队首，其实测命中数就是 verifyCount。"""
    from rpa_core.gui.element_editor import promote_candidate

    candidates = [
        {"kind": "attribute", "selector": 'input[name="q"]', "matchedCount": 3},
        {"kind": "attribute", "selector": "[data-testid=search]", "matchedCount": 1},
    ]
    kept, css, count, note = promote_candidate(
        candidates, 1, current_css="old.css", current_count=2
    )
    assert css == "[data-testid=search]"
    assert count == 1
    assert note is None
    # 旧主定位排到队首（candidates 顺序 = 回退优先级，它原本最强）
    assert kept[0] == {"kind": "css", "selector": "old.css", "matchedCount": 2}
    assert [item["selector"] for item in kept[1:]] == ['input[name="q"]']
    # 提升是「交换」，不是「覆盖」：一条备选都没少
    assert len(kept) == len(candidates)
    assert candidates[0]["selector"] == 'input[name="q"]'  # 入参不被就地改写


def test_promote_candidate_reports_when_old_primary_cannot_be_kept(qapp):
    """旧主定位命中数为 0 无法表示成合法候选（契约要求 ≥1）→ 返回提示而非静默丢弃。"""
    from rpa_core.gui.element_editor import promote_candidate

    kept, css, count, note = promote_candidate(
        [{"kind": "id", "selector": "#a", "matchedCount": 1}],
        0,
        current_css="ghost.css",
        current_count=0,
    )
    assert css == "#a"
    assert count == 1
    assert note is not None and "ghost.css" in note
    assert [item["selector"] for item in kept] == []


def test_promote_candidate_on_same_selector_does_not_duplicate(qapp):
    """提升的候选与主选择器相同时不产生重复条目。"""
    from rpa_core.gui.element_editor import promote_candidate

    kept, css, count, note = promote_candidate(
        [{"kind": "id", "selector": "#same", "matchedCount": 4}],
        0,
        current_css="#same",
        current_count=1,
    )
    assert css == "#same"
    assert count == 4
    assert kept == []
    assert note is None


def test_promote_candidate_rejects_bad_index(qapp):
    from rpa_core.gui.element_editor import promote_candidate

    with pytest.raises(IndexError):
        promote_candidate([], 0, current_css="", current_count=1)


# ---- 对话框：浏览器 -----------------------------------------------------------
def test_editor_browser_promotes_candidate(qapp):
    from rpa_core.gui.element_editor import ElementEditorDialog

    dialog = ElementEditorDialog(_browser_document(), name="searchBox")
    assert dialog.css_edit.text() == "#sb_form_q"
    assert dialog.candidate_list.count() == 3
    assert "不唯一" in dialog.candidate_list.item(1).text()
    assert "命中未实测" in dialog.candidate_list.item(2).text()

    # 未选中任何候选：按钮禁用（不能靠「第一个」隐式生效）
    assert not dialog.promote_button.isEnabled()
    dialog.candidate_list.setCurrentRow(1)  # 不唯一的那条，但实测过 → 可选
    assert dialog.promote_button.isEnabled()
    dialog._promote()

    assert dialog.css_edit.text() == 'input[name="q"]'
    document = dialog.result_document()
    assert document["verifyCount"] == 3  # 新主定位的实测命中数
    assert document["selector"]["candidates"][0] == {
        "kind": "css",
        "selector": "#sb_form_q",
        "matchedCount": 1,
    }
    assert len(document["selector"]["candidates"]) == 3  # 交换而非丢失

    # 未实测的候选不允许提升：verifyCount 必须是实测值，宁可不给按钮
    dialog.candidate_list.setCurrentRow(2)
    assert not dialog.promote_button.isEnabled()


def test_editor_browser_blocks_blank_css(qapp):
    from PySide6.QtWidgets import QDialog

    from rpa_core.gui.element_editor import ElementEditorDialog

    dialog = ElementEditorDialog(_browser_document(), name="searchBox")
    dialog.css_edit.setText("   ")
    assert "不能为空" in dialog.info_label.text()
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted

    dialog.css_edit.setText("#q")
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_editor_browser_output_passes_the_real_contract(qapp):
    """产出必须能被真实契约接受（模型 + selector_errors），编辑器不承诺别的。"""
    from rpa_core.gui.element_editor import ElementEditorDialog
    from rpa_core.model.capture import selector_errors, validate_element_document

    dialog = ElementEditorDialog(_browser_document(), name="searchBox")
    dialog.candidate_list.setCurrentRow(1)
    dialog._promote()
    dialog.accept()
    element = validate_element_document(dialog.result_document())
    assert selector_errors(element) == []
    assert element.metadata["role"] == "searchbox"  # 捕获的元数据原样保留


# ---- 对话框：桌面 -------------------------------------------------------------
def test_editor_desktop_shows_only_the_active_backend_rows(qapp):
    """uia 元素只显示 uia 的三行；win32 行隐藏（提供它们＝生产死字段）。"""
    from rpa_core.gui.element_editor import ElementEditorDialog, fields_for

    dialog = ElementEditorDialog(_desktop_document(), name="submit")
    assert dialog.backend_combo.currentText() == "uia"
    for key in {k for k, _kd, _h in fields_for("uia")}:
        assert dialog.field_boxes[key].isVisibleTo(dialog), key
    for key in {k for k, _kd, _h in fields_for("win32")}:
        assert not dialog.field_boxes[key].isVisibleTo(dialog), key

    dialog.backend_combo.setCurrentText("win32")
    for key in {k for k, _kd, _h in fields_for("win32")}:
        assert dialog.field_boxes[key].isVisibleTo(dialog), key
    for key in {k for k, _kd, _h in fields_for("uia")}:
        assert not dialog.field_boxes[key].isVisibleTo(dialog), key


def test_editor_desktop_prefills_from_locator_and_metadata(qapp):
    from rpa_core.gui.element_editor import ElementEditorDialog

    dialog = ElementEditorDialog(_desktop_document(), name="submit")
    # locator 里有的字段：勾上
    assert dialog.field_boxes["controlType"].isChecked()
    assert dialog.field_boxes["automationId"].isChecked()
    assert dialog.field_edits["name"].text() == "Submit"
    # locator 里没有、但捕获回传过 className：值预填好，只是没勾（等用户决定）
    assert not dialog.field_boxes["className"].isChecked()
    assert dialog.field_edits["className"].text().startswith("WindowsForms10.BUTTON")
    # 未勾的字段不进 locator
    assert "className" not in dialog.result_document()["selector"]["locator"]

    dialog.accept()
    locator = dialog.result_document()["selector"]["locator"]
    assert locator["automationId"] == "submitButton"
    assert locator["backend"] == "uia"


def test_editor_desktop_switching_backend_rewrites_the_locator(qapp):
    """换 backend 等于换一套定位机制：字段不通用，locator 随之重写并提示死字段。"""
    from PySide6.QtWidgets import QDialog

    from rpa_core.gui.element_editor import ElementEditorDialog

    dialog = ElementEditorDialog(_desktop_document(), name="submit")
    # uia 下 className 是死字段（执行器读不到）——预填了值但没勾，所以不在 locator 里
    assert "className" not in dialog._compose()
    dialog.backend_combo.setCurrentText("win32")
    # 换成 win32 之后，uia 的三行不入 locator，模型立刻报「至少一个身份字段」
    assert dialog._compose() == {"backend": "win32"}
    assert "至少要勾一个身份字段" in dialog.info_label.text()
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted

    dialog.field_boxes["classNameRe"].setChecked(True)
    dialog.field_edits["classNameRe"].setText(r"WindowsForms10\.BUTTON\..*")
    assert dialog.info_label.text() == ""
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.result_document()["selector"]["locator"] == {
        "backend": "win32",
        "classNameRe": r"WindowsForms10\.BUTTON\..*",
    }


def test_editor_desktop_flags_inert_fields_from_the_original_locator(qapp):
    """原 locator 里的死字段要在界面里说出来（保存会移除，不是静默改用户文件）。"""
    from rpa_core.gui.element_editor import ElementEditorDialog

    document = _desktop_document()
    document["selector"]["locator"]["menuPath"] = ["文件", "新建"]
    dialog = ElementEditorDialog(document, name="submit")
    assert "menuPath" in dialog.field_hint.text()
    assert "保存会移除" in dialog.field_hint.text()
    # 死字段确实不会被写回
    assert "menuPath" not in dialog.result_document()["selector"]["locator"]


def test_editor_desktop_blocks_mutually_exclusive_class_fields(qapp):
    """className / classNameRe 互斥必须**当场**拦下，而不是等保存才报错。"""
    from PySide6.QtWidgets import QDialog

    from rpa_core.gui.element_editor import ElementEditorDialog

    dialog = ElementEditorDialog(_desktop_document(), name="submit")
    dialog.backend_combo.setCurrentText("win32")
    dialog.field_boxes["className"].setChecked(True)
    dialog.field_boxes["classNameRe"].setChecked(True)
    dialog.field_edits["classNameRe"].setText(r"WindowsForms10\.BUTTON.*")

    assert "互斥" in dialog.info_label.text()
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted

    # 取消其中一个即可保存，且 locator 里只剩留下的那个
    dialog.field_boxes["className"].setChecked(False)
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    locator = dialog.result_document()["selector"]["locator"]
    assert "className" not in locator
    assert locator["classNameRe"] == r"WindowsForms10\.BUTTON.*"


def test_editor_desktop_blocks_bad_integer_and_missing_identity(qapp):
    from PySide6.QtWidgets import QDialog

    from rpa_core.gui.element_editor import ElementEditorDialog

    dialog = ElementEditorDialog(_desktop_document(), name="submit")
    dialog.backend_combo.setCurrentText("win32")
    dialog.field_boxes["controlId"].setChecked(True)
    dialog.field_edits["controlId"].setText("abc")
    assert "整数" in dialog.info_label.text()
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted

    # 把身份字段全清掉（win32 侧）：模型要报「至少一个身份字段」，界面也必须拦
    dialog.field_boxes["controlId"].setChecked(False)
    for key in ("title", "className", "classNameRe"):
        dialog.field_boxes[key].setChecked(False)
    assert "至少要勾一个" in dialog.info_label.text()
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted


def test_editor_desktop_output_passes_the_real_contract(qapp):
    from rpa_core.gui.element_editor import ElementEditorDialog
    from rpa_core.model.capture import selector_errors, validate_element_document

    dialog = ElementEditorDialog(_desktop_document(), name="submit")
    dialog.accept()
    element = validate_element_document(dialog.result_document())
    assert selector_errors(element) == []


def test_editor_preserves_keys_it_does_not_expose(qapp):
    """界面没暴露的 selector 子键（如自定义扩展字段）必须原样保留。"""
    from rpa_core.gui.element_editor import ElementEditorDialog

    document = _browser_document()
    document["selector"]["note"] = "手工加的备注"
    dialog = ElementEditorDialog(document, name="searchBox")
    dialog.accept()
    result = dialog.result_document()
    assert result["selector"]["note"] == "手工加的备注"
    assert result["selector"]["candidates"] == document["selector"]["candidates"]


# ---- 元素库入口 ---------------------------------------------------------------
def _fake_editor(result_document: dict | None):
    """替身编辑器。必须是 QWidget：app 侧用 ``present_window`` 置顶对话框，
    那一串调用（setWindowFlag/show/raise_/alert）在纯 Python 类上会 AttributeError。
    """
    from PySide6.QtWidgets import QDialog

    class FakeEditor(QDialog):
        seen: list[tuple[str, dict]] = []

        def __init__(self, document, *, name, parent=None):
            super().__init__(parent)
            type(self).seen.append((name, document))

        def exec(self):
            if result_document is None:
                return QDialog.DialogCode.Rejected
            return QDialog.DialogCode.Accepted

        def result_document(self):
            return result_document

    FakeEditor.seen = []
    return FakeEditor


def test_element_library_wires_the_edit_entry(window, monkeypatch):
    """面板要有「编辑」按钮，且双击走同一条路径（不出现行为分叉）。"""
    from rpa_core.gui import element_editor

    window._save_named_flow("ed1")
    window.save_element_descriptor("searchBox", _browser_document())
    window._toggle_elements_dock()
    window._refresh_elements()
    panel = window._element_panel
    assert panel.edit_button.text() == "编辑"
    assert "不用手写" in panel.edit_button.toolTip()

    fake = _fake_editor(None)
    monkeypatch.setattr(element_editor, "ElementEditorDialog", fake)
    panel.list.setCurrentRow(0)
    panel.edit_button.click()
    panel.list.itemDoubleClicked.emit(panel.list.item(0))
    # 按钮与双击都必须带着**选中元素的真实名**进编辑器
    assert [name for name, _doc in fake.seen] == ["searchBox", "searchBox"]


def test_edit_element_writes_back(window, monkeypatch):
    """编辑器保存 → 文档落盘（通过 app 的真实入库路径）。"""
    from rpa_core.gui import element_editor

    window._save_named_flow("ed2")
    window.save_element_descriptor("searchBox", _browser_document())

    fake = _fake_editor(
        {
            "kind": "browser",
            "selector": {"css": "#promoted"},
            "verifyCount": 3,
            "metadata": {"role": "searchbox"},
        }
    )
    monkeypatch.setattr(element_editor, "ElementEditorDialog", fake)
    window._edit_element("searchBox")

    # 打开时喂进去的是**盘上的当前文档**（否则编辑器显示的可能不是实际存的）
    assert fake.seen[0][1]["selector"]["css"] == "#sb_form_q"

    stored = window._element_store().read("searchBox")
    assert stored["selector"]["css"] == "#promoted"
    assert stored["verifyCount"] == 3
    assert "已保存元素 searchBox" in window.statusBar().currentMessage()


def test_edit_element_cancel_changes_nothing(window, monkeypatch):
    from rpa_core.gui import element_editor

    window._save_named_flow("ed3")
    window.save_element_descriptor("searchBox", _browser_document())

    monkeypatch.setattr(element_editor, "ElementEditorDialog", _fake_editor(None))
    window._edit_element("searchBox")
    assert window._element_store().read("searchBox")["selector"]["css"] == "#sb_form_q"


def test_edit_element_tolerates_broken_document(window):
    """坏元素文件不该让 GUI 崩（读取失败就地报错）。"""
    window._save_named_flow("ed4")
    store = window._element_store()
    store.root.mkdir(parents=True, exist_ok=True)
    (store.root / "broken.json").write_text("{not json", encoding="utf-8")
    window._edit_element("broken")
    assert "读取元素失败" in window.statusBar().currentMessage()
