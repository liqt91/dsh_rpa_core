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


# ---- 节点树（M44 S5）：勾层级 → 按 fragment 拼回主选择器 ----------------------
def _path_document() -> dict:
    """带祖先链的浏览器元素：path 形状与 content.js ``pathFor`` 的产出一致。"""
    return {
        "kind": "browser",
        "selector": {
            "css": "body > div.wrap > button.ok:nth-of-type(2)",
            "path": [
                {"tag": "body", "id": None, "classes": [],
                 "nthOfType": None, "fragment": "body"},
                {"tag": "div", "id": None, "classes": ["wrap"],
                 "nthOfType": 1, "fragment": "div.wrap"},
                {"tag": "button", "id": None, "classes": ["ok"],
                 "nthOfType": 2, "fragment": "button.ok:nth-of-type(2)"},
            ],
        },
        "verifyCount": 1,
        "metadata": {"tag": "button"},
    }


def test_path_tree_composes_css_from_checked_levels(qapp):
    """勾选层级决定主选择器：取消祖先 → css 变成剩余层级的 join。

    影刀节点树的用途正在这里：6 级全路径脆，截短成末两级往往更稳 —— 但此前
    用户只能对着 `css` 一行文本手写，捕获到的层级信息**有数据没入口**。
    """
    from PySide6.QtCore import Qt

    from rpa_core.gui.element_editor import ElementEditorForm

    form = ElementEditorForm(_path_document())
    assert form.path_list.count() == 3
    assert all(
        form.path_list.item(i).checkState() == Qt.CheckState.Checked
        for i in range(3)
    ), "css 是全路径 → 建树时应当全勾"

    form.path_list.item(0).setCheckState(Qt.CheckState.Unchecked)
    assert form.css_edit.text() == "div.wrap > button.ok:nth-of-type(2)"
    assert not form.blocked


def test_path_tree_leaf_cannot_be_unchecked(qapp):
    """末级（目标本身）取消勾选会被勾回——「不指向目标的定位方案」不成立。"""
    from PySide6.QtCore import Qt

    from rpa_core.gui.element_editor import ElementEditorForm

    form = ElementEditorForm(_path_document())
    form.path_list.item(2).setCheckState(Qt.CheckState.Unchecked)
    assert form.path_list.item(2).checkState() == Qt.CheckState.Checked
    assert form.css_edit.text() == "body > div.wrap > button.ok:nth-of-type(2)"


def test_path_tree_derives_checks_from_shortened_css(qapp):
    """重开元素时按 css 反推勾选态（css 是路径后缀 → 只勾后缀）。

    不做双向同步（css 解析回层级不可靠），但**建树这一次**必须让勾选态与
    落盘的 css 一致：否则用户上轮截短的祖先，这轮随便点一下就被全路径覆盖。
    """
    from PySide6.QtCore import Qt

    from rpa_core.gui.element_editor import ElementEditorForm

    doc = _path_document()
    doc["selector"]["css"] = "div.wrap > button.ok:nth-of-type(2)"
    form = ElementEditorForm(doc)
    assert form.path_list.item(0).checkState() == Qt.CheckState.Unchecked
    assert form.path_list.item(1).checkState() == Qt.CheckState.Checked
    assert form.path_list.item(2).checkState() == Qt.CheckState.Checked

    # css 不出自这条路径（如候选提升来的）：回退全勾，下次勾选才重写
    doc["selector"]["css"] = 'input[name="q"]'
    form = ElementEditorForm(doc)
    assert all(
        form.path_list.item(i).checkState() == Qt.CheckState.Checked
        for i in range(3)
    )


def test_path_tree_absent_without_path(qapp):
    """老元素没有 path：不建树也不摆空壳（树是加分项，不是门槛）。"""
    from rpa_core.gui.element_editor import ElementEditorForm

    form = ElementEditorForm(_browser_document())
    assert not hasattr(form, "path_list")
    assert not hasattr(form, "path_label")


def test_path_survives_edit_roundtrip(qapp):
    """编辑（含勾层级重写 css）不得抹掉 `selector.path` —— 它是树的原料。

    回归口径同 candidates：``result_document`` 以原 selector 为基底覆盖被编辑的键，
    界面上没有直接编辑 path 的地方，写回必须原样保留。
    """

    from rpa_core.gui.element_editor import ElementEditorForm

    form = ElementEditorForm(_path_document())
    form.css_edit.setText("button.ok:nth-of-type(2)")
    selector = form.result_document()["selector"]
    assert selector["css"] == "button.ok:nth-of-type(2)"
    assert selector["path"] == _path_document()["selector"]["path"]


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

        def __init__(
            self, document, *, name,
            preview_css=None, clear_preview_css=None, parent=None,
        ):
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


# ---- R2（M44 残留）：候选提升后树的勾选态必须立刻回读 ------------------------
def _path_document_with_candidates() -> dict:
    doc = _path_document()
    doc["selector"]["candidates"] = [
        {
            "kind": "css",
            "selector": "div.wrap > button.ok:nth-of-type(2)",
            "matchedCount": 2,
        },
        {"kind": "css", "selector": "button.ok", "matchedCount": 3},
    ]
    return doc


def test_path_tree_resyncs_checks_after_candidate_promotion(qapp):
    """提升候选会改主 css，树的勾选态必须**跟着**回读。

    M44 残留 R2：勾选态反推只在建树时做一次——提升后树停在旧勾选，中间态里
    「树显示的层级」与「主选择器实际是哪条」是两回事，下一次勾选才会被重写。
    """
    from PySide6.QtCore import Qt

    from rpa_core.gui.element_editor import ElementEditorForm

    form = ElementEditorForm(_path_document_with_candidates())
    assert form.css_edit.text() == "body > div.wrap > button.ok:nth-of-type(2)"
    assert all(
        form.path_list.item(i).checkState() == Qt.CheckState.Checked
        for i in range(3)
    )

    # 截短祖先 → 勾选态与 css 一致（末两级）
    form.path_list.item(0).setCheckState(Qt.CheckState.Unchecked)
    assert form.css_edit.text() == "div.wrap > button.ok:nth-of-type(2)"

    # 提升的候选恰是路径后缀：回读后勾选态维持「末两级」
    form.candidate_list.setCurrentRow(0)
    form._promote()
    assert form.css_edit.text() == "div.wrap > button.ok:nth-of-type(2)"
    assert form.path_list.item(0).checkState() == Qt.CheckState.Unchecked
    assert form.path_list.item(1).checkState() == Qt.CheckState.Checked
    assert form.path_list.item(2).checkState() == Qt.CheckState.Checked

    # 提升的候选**不**出自路径：回读后回退「完整路径视角」（全勾）
    form.candidate_list.setCurrentRow(0)
    form._promote()
    assert form.css_edit.text() == "button.ok"
    assert all(
        form.path_list.item(i).checkState() == Qt.CheckState.Checked
        for i in range(3)
    ), "提升出不属于路径的 css 后，树应回退全勾而不是停在旧勾选"


# ---- Web 属性表（A1）：逐属性勾选 + 等于/包含编译回 fragment ------------------
def test_attribute_rows_initial_state_derived_from_fragment(qapp):
    """初态从**当前 fragment** 反推：捕获用了哪些属性就勾哪些。

    content.js 口径：带 id 的层 fragment 就是 `#id`（tag 不出现）；无 id 层只取
    **首类** + 同名兄弟时补 :nth-of-type —— 所以首类勾、次类不勾是「如实还原」，
    次类的勾选权正是属性表相对节点树新增的颗粒度。
    """
    from rpa_core.gui.element_editor import attribute_rows

    # 无 id 层：tag + 首类 + nth 都在 fragment 里
    entry = {
        "tag": "button", "id": None, "classes": ["ok", "btn-primary"],
        "nthOfType": 2, "fragment": "button.ok:nth-of-type(2)",
    }
    rows = attribute_rows(entry, "button.ok:nth-of-type(2)")
    assert [(r["attr"], r["value"], r["checked"], r["locked"]) for r in rows] == [
        ("tag", "button", True, True),
        ("class", "ok", True, False),
        ("class", "btn-primary", False, False),
        ("nth-of-type", "2", True, False),
    ]

    # 带 id 层：id 勾、类不勾（fragment 是 #id，tag 不参与）
    entry2 = {
        "tag": "div", "id": "main", "classes": ["a", "b"],
        "nthOfType": None, "fragment": "#main",
    }
    rows2 = attribute_rows(entry2, "#main")
    assert [(r["attr"], r["checked"]) for r in rows2] == [
        ("tag", True), ("id", True), ("class", False), ("class", False),
    ]


def test_compile_fragment_match_modes(qapp):
    from rpa_core.gui.element_editor import (
        ATTR_CONTAINS,
        ATTR_EQUALS,
        compile_fragment,
    )

    entry = {"tag": "div", "id": "main", "classes": ["wrap"], "nthOfType": 3}

    def rows(**kw):
        return [
            {"attr": "tag", "value": "div", "mode": None, "checked": True,
             "locked": True},
            {"attr": "id", "value": "main",
             "mode": kw.get("id_mode", ATTR_EQUALS),
             "checked": kw.get("id", False), "locked": False},
            {"attr": "class", "value": "wrap", "mode": kw.get("mode", ATTR_EQUALS),
             "checked": kw.get("cls", True), "locked": False},
            {"attr": "nth-of-type", "value": "3", "mode": None,
             "checked": kw.get("nth", True), "locked": False},
        ]

    # id 等于：整层 = #id（捕获口径：等值 id 本身唯一，其余属性不参与）
    assert compile_fragment(entry, rows(id=True)) == "#main"
    # id 勾「包含」：不能再用 #id 简写 → [id*=…]；class/nth 照常参与
    # （只有 id「等于」才整层接管——等值 id 本身唯一）
    assert compile_fragment(entry, rows(id=True, id_mode=ATTR_CONTAINS)) == (
        'div[id*="main"].wrap:nth-of-type(3)'
    )
    # 取消 class、勾 id 包含
    assert compile_fragment(
        entry, rows(cls=False, nth=False, id=True, id_mode=ATTR_CONTAINS)
    ) == 'div[id*="main"]'
    # class 等于 / 包含
    assert compile_fragment(entry, rows()) == "div.wrap:nth-of-type(3)"
    assert compile_fragment(entry, rows(mode=ATTR_CONTAINS)) == (
        'div[class*="wrap"]:nth-of-type(3)'
    )
    # 取消 nth
    assert compile_fragment(entry, rows(nth=False)) == "div.wrap"
    # 值含引号：包含编译必须转义（否则拼出非法选择器）
    quote_entry = {"tag": "div", "id": None, "classes": ['a"b'], "nthOfType": None}
    quote_rows = [
        {"attr": "tag", "value": "div", "mode": None, "checked": True,
         "locked": True},
        {"attr": "class", "value": 'a"b', "mode": ATTR_CONTAINS,
         "checked": True, "locked": False},
    ]
    assert compile_fragment(quote_entry, quote_rows) == 'div[class*="a\\"b"]'


def test_attr_table_rewrites_css_with_match_mode(qapp):
    """表单级：改匹配方式/勾选 → 该层 fragment 重写 → 主选择器跟着重写。"""
    from rpa_core.gui.element_editor import ElementEditorForm

    form = ElementEditorForm(_path_document())
    # 默认选中末级（目标层）：button.ok:nth-of-type(2) → 行 = tag/class/nth
    assert form.path_list.currentRow() == 2
    assert form.attr_table.rowCount() == 3

    # class 行（第 1 行）改「包含」→ 该层变 [class*="ok"]，主选择器重写
    combo = form.attr_table.cellWidget(1, 2)
    assert combo is not None
    combo.setCurrentIndex(1)
    assert form.css_edit.text() == (
        'body > div.wrap > button[class*="ok"]:nth-of-type(2)'
    )
    assert not form.blocked

    # 取消 nth（第 2 行勾选框）→ fragment 去掉 :nth-of-type
    nth_box = form.attr_table.cellWidget(2, 0)
    nth_box.setChecked(False)
    assert form.css_edit.text() == 'body > div.wrap > button[class*="ok"]'

    # 树行文本与 fragment 同步（树的下一轮勾选拼回的也是新 fragment）
    assert form.path_list.item(2).text() == 'button[class*="ok"]'

    # tag 行的勾选框锁定（不勾 tag 的层不指向任何东西）
    assert not form.attr_table.cellWidget(0, 0).isEnabled()


def test_attr_table_follows_tree_selection(qapp):
    """属性表随树选中行切换：切到中间层（div.wrap）显示该层的属性。"""
    from rpa_core.gui.element_editor import ElementEditorForm

    form = ElementEditorForm(_path_document())
    form.path_list.setCurrentRow(1)  # div.wrap：tag + class + nth
    assert form.attr_table.rowCount() == 3
    assert form.attr_table.item(1, 1).text() == "class"
    assert form.attr_table.item(1, 3).text() == "wrap"
    # 该层 class 改「包含」只影响中间层，末级不受牵连
    form.attr_table.cellWidget(1, 2).setCurrentIndex(1)
    assert form.css_edit.text() == (
        'body > div[class*="wrap"] > button.ok:nth-of-type(2)'
    )


# ---- M48：编辑中预览（通道回调注入式） ---------------------------------------

def _preview_stub_log():
    """可观察的预览/清场替身：记录调用，返回结构化结果（同通道形状）。"""
    log = {"preview": [], "clear": [], "result": {"count": 1}}

    def preview(css):
        log["preview"].append(css)
        return dict(log["result"])

    def clear():
        log["clear"].append(True)
        return {"count": 0}

    return log, preview, clear


def _drain_qt_events(qtbot_like_wait=0.05):
    """给工作线程留出 emit 窗口后泵一次事件循环（offscreen 无 app.processEvents 桩）。"""
    import time

    from PySide6.QtCore import QCoreApplication

    time.sleep(qtbot_like_wait)
    QCoreApplication.processEvents()


def test_form_live_preview_dispatch_and_label(qapp):
    """css 改动 → 防抖到期后在工作线程调 preview 回调，标签回显命中。"""
    from rpa_core.gui.element_editor import ElementEditorForm

    log, preview, clear = _preview_stub_log()
    form = ElementEditorForm(_browser_document())
    form.enable_live_preview(preview, clear)
    assert not form.preview_label.isHidden()  # 注入即显示，桌面表单才整个没有

    form.css_edit.setText("#sb_form_q2")  # 触发 textChanged → 防抖重启
    assert form._preview_timer.isActive()  # 只排程，未到期不打通道
    assert log["preview"] == []

    form._preview_timer.stop()
    form._run_preview()  # 直接驱动到期分支（不等真实 500ms）
    _drain_qt_events()
    assert log["preview"] == ["#sb_form_q2"]
    assert "命中 1 个" in form.preview_label.text()
    assert log["clear"] == []  # 正常预览不清场


def test_form_preview_empty_css_clears_instead_of_querying(qapp):
    """css 清空：不发查找、就地清场——旧框不能赖着冒充新选择器的命中。"""
    from rpa_core.gui.element_editor import ElementEditorForm

    log, preview, clear = _preview_stub_log()
    form = ElementEditorForm(_browser_document())
    form.enable_live_preview(preview, clear)

    form.css_edit.setText("   ")
    form._preview_timer.stop()
    form._run_preview()
    _drain_qt_events()
    assert log["preview"] == []
    assert log["clear"] == [True]
    assert form.preview_label.text() == ""


def test_form_preview_error_shows_inline(qapp):
    """通道故障落到预览标签（结构化 error 与异常都要可见，不静默）。"""
    from rpa_core.gui.element_editor import ElementEditorForm

    def boom(_css):
        raise RuntimeError("channel down")

    log, _preview, clear = _preview_stub_log()
    form = ElementEditorForm(_browser_document())
    form.enable_live_preview(boom, clear)
    form._preview_timer.stop()
    form._run_preview()
    _drain_qt_events()
    assert "预览失败" in form.preview_label.text()
    assert "channel down" in form.preview_label.text()


def test_form_stale_preview_result_is_discarded(qapp):
    """防抖后再来一轮时，上一轮的迟到结果不得覆盖显示（seq 配对）。"""
    from rpa_core.gui.element_editor import ElementEditorForm

    form = ElementEditorForm(_browser_document())
    log, preview, clear = _preview_stub_log()
    form.enable_live_preview(preview, clear)
    form._preview_timer.stop()
    form._run_preview()  # seq=1 在途
    form._run_preview()  # seq=2：模拟用户又改了一轮
    form._on_preview_done({"kind": "preview", "seq": 1, "count": 7})
    assert "预览中…" in form.preview_label.text() or "7" not in form.preview_label.text()
    form._on_preview_done({"kind": "preview", "seq": 2, "count": 7})
    assert "命中 7 个" in form.preview_label.text()


def test_desktop_form_has_no_preview_surface(qapp):
    """桌面元素没有「页面」可高亮：enable_live_preview 必须整体无效。"""
    from rpa_core.gui.element_editor import ElementEditorForm

    log, preview, clear = _preview_stub_log()
    form = ElementEditorForm(_desktop_document())
    form.enable_live_preview(preview, clear)
    assert not hasattr(form, "preview_label") or form.preview_label.isHidden()
    form._run_preview() if hasattr(form, "_preview_timer") else None
    _drain_qt_events()
    assert log["preview"] == [] and log["clear"] == []


def test_dialog_close_sends_clear_preview(qapp):
    """关窗收场：ElementEditorDialog finished → shutdown_preview 发 clear。"""
    from rpa_core.gui.element_editor import ElementEditorDialog

    log, preview, clear = _preview_stub_log()
    dialog = ElementEditorDialog(
        _browser_document(), name="el_x", preview_css=preview, clear_preview_css=clear
    )
    dialog.form._preview_timer.stop()
    dialog.finished.emit(0)  # 不 exec（模态会挂测试），直接驱动 finished 接线
    _drain_qt_events()
    assert log["clear"] == [True]


def test_dialog_without_preview_callback_has_no_preview(qapp):
    """未注入回调（如 desktop / 测试注入 None）：不启用预览、关窗也不发 clear。"""
    from rpa_core.gui.element_editor import ElementEditorDialog

    log, preview, clear = _preview_stub_log()
    dialog = ElementEditorDialog(_browser_document(), name="el_y")
    dialog.form._preview_timer.stop()
    dialog.finished.emit(0)
    _drain_qt_events()
    assert log["preview"] == [] and log["clear"] == []
