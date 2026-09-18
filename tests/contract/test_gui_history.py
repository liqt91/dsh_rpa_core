"""撤销/重做与复制粘贴契约测试（GUI 功能补齐 切 B）。

覆盖：
- 结构变更（插入/删除）与参数应用都产生可撤销历史；
- 撤销/重做重建画布且保留历史栈本身；
- 复制粘贴：全树 id 重映射唯一、别名撞车改名 + 子树引用同步改写；
- 新流程载入清空历史。

测试在 offscreen Qt 平台运行；缺 PySide6 时整组跳过。
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QLineEdit, QPushButton  # noqa: E402

from rpa_core.gui.flow_model import (  # noqa: E402
    ROLE_ARGS_RAW,
    clone_for_paste,
    iter_real_nodes,
    subtree_to_ast,
)
from rpa_core.gui.param_form import ControlNodeForm, ParamForm  # noqa: E402


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


def _doc_ids(document: dict) -> list[str]:
    ids = []

    def walk(node):
        ids.append(node["id"])
        for key in ("children", "then", "else", "catch"):
            for child in node.get(key) or []:
                walk(child)

    walk(document["root"])
    return ids


def _field(form: ParamForm, name: str):
    return next(widget for field, _kind, widget in form._fields if field == name)


def _model_aliases(window) -> set[str]:
    """模型中所有 action 节点声明的 output_aliases 值。"""
    aliases: set[str] = set()
    for item in iter_real_nodes(window.flow_model):
        holder = item.data(ROLE_ARGS_RAW)
        if holder is not None and holder.raw:
            aliases.update((holder.raw.get("output_aliases") or {}).values())
    return aliases


# ---- 撤销 / 重做 -------------------------------------------------------------
def test_insert_is_undoable_and_redoable(window):
    before = _doc_ids(window._current_document())
    window.add_command("workflow.sleep")
    after = _doc_ids(window._current_document())
    assert len(after) == len(before) + 1

    window._undo()
    assert _doc_ids(window._current_document()) == before
    assert window._dirty  # 撤销也是一种未保存修改

    window._redo()
    assert _doc_ids(window._current_document()) == after


def test_delete_is_undoable(window):
    before = _doc_ids(window._current_document())
    item = window.flow_model.find_by_id("done")
    window.canvas_view.setCurrentIndex(window.flow_model.indexFromItem(item))
    window._delete_selected_node()
    assert "done" not in _doc_ids(window._current_document())

    window._undo()
    assert _doc_ids(window._current_document()) == before


def test_param_apply_is_undoable(window):
    model = window.flow_model
    item = model.find_by_id("open")
    window.canvas_view.setCurrentIndex(model.indexFromItem(item))
    form = window.param_holder.findChild(ParamForm)
    _field(form, "url").setText("https://changed.example/")
    window.param_holder.findChild(QPushButton).click()
    assert item.data(ROLE_ARGS_RAW).args["url"] == "https://changed.example/"

    window._undo()
    restored = window.flow_model.find_by_id("open")
    assert restored.data(ROLE_ARGS_RAW).raw["with"]["url"] == "https://example.com"


def test_control_form_apply_is_undoable(window):
    model = window.flow_model
    item = model.find_by_id("check")
    window.canvas_view.setCurrentIndex(model.indexFromItem(item))
    form = window.param_holder.findChild(ControlNodeForm)
    form._widgets["left"].setText("${changed}")
    window.param_holder.findChild(QPushButton).click()
    assert item.data(ROLE_ARGS_RAW).raw["condition"]["left"] == "${changed}"

    window._undo()
    restored = window.flow_model.find_by_id("check")
    assert (
        restored.data(ROLE_ARGS_RAW).raw["condition"]["left"]
        == "${steps.open.outputs.sessionId}"
    )


def test_new_workflow_clears_history(window):
    window.add_command("workflow.sleep")
    assert window._undo_stack
    # offscreen 下 QMessageBox 会崩溃：先清脏标记绕过「放弃修改」确认框
    window._set_dirty(False)
    window._new_action()
    assert not window._undo_stack and not window._redo_stack


def test_edit_sensitive_actions_disabled_while_editing_text(window):
    edit = QLineEdit(window)
    window._on_focus_changed(None, edit)
    assert not window.undo_action.isEnabled()
    assert not window.copy_action.isEnabled()
    assert not window.delete_action.isEnabled()
    window._on_focus_changed(edit, None)
    assert window.undo_action.isEnabled()


# ---- 复制 / 粘贴 -------------------------------------------------------------
def test_copy_paste_remaps_ids(window):
    model = window.flow_model
    check = model.find_by_id("check")
    window.canvas_view.setCurrentIndex(model.indexFromItem(check))
    window._copy_selected()
    assert window._clipboard is not None

    window._paste_clipboard()
    ids = _doc_ids(window._current_document())
    assert len(ids) == len(set(ids)), "粘贴后全树 id 必须唯一"
    assert ids.count("check") == 1


def test_paste_twice_gives_distinct_ids(window):
    model = window.flow_model
    item = model.find_by_id("open")
    window.canvas_view.setCurrentIndex(model.indexFromItem(item))
    window._copy_selected()
    window._paste_clipboard()
    window._paste_clipboard()
    ids = _doc_ids(window._current_document())
    assert len(ids) == len(set(ids))


def test_clone_for_paste_renames_colliding_alias_and_rewrites_refs(window):
    model = window.flow_model
    # 先在模型里放一个声明了别名 web 的节点
    model.insert_subtree(
        {
            "type": "action",
            "id": "mk",
            "command": "data.setVar",
            "with": {"varName": "web", "value": "x"},
            "output_aliases": {"value": "web"},
        },
        None,
    )
    assert "web" in _model_aliases(window)

    # 粘贴一个自带别名 web 且内部引用 ${web} 的子树：别名改名 + 引用改写
    subtree = {
        "type": "sequence",
        "id": "grp",
        "children": [
            {
                "type": "action",
                "id": "a",
                "command": "data.setVar",
                "with": {"varName": "web", "value": "y"},
                "output_aliases": {"value": "web"},
            },
            {
                "type": "action",
                "id": "b",
                "command": "data.writeText",
                "with": {"workspace": ".", "path": "o.txt", "text": "${web}"},
            },
        ],
    }
    cloned = clone_for_paste(model, subtree)
    children = cloned["children"]
    assert children[0]["output_aliases"]["value"] == "web_copy"
    assert children[1]["with"]["text"] == "${web_copy}"


def test_copy_virtual_row_is_rejected(window):
    model = window.flow_model
    check = model.find_by_id("check")
    end_row = check.child(check.rowCount() - 1)  # 「结束 如果」标记行
    window.canvas_view.setCurrentIndex(model.indexFromItem(end_row))
    window._copy_selected()
    assert window._clipboard is None


def test_subtree_to_ast_roundtrip(window):
    model = window.flow_model
    check = model.find_by_id("check")
    ast = subtree_to_ast(check)
    assert ast["id"] == "check"
    assert ast["then"][0]["id"] == "read"
    assert ast["else"][0]["id"] == "wait"
    # 深拷贝：改动剪贴板不影响模型
    ast["then"][0]["with"]["selector"] = "h2"
    assert check.data(ROLE_ARGS_RAW).raw["then"][0]["with"]["selector"] == "h1"


def test_paste_and_delete_restore_selection_signals(window):
    """回归：粘贴/删除期间的选中信号屏蔽必须成对恢复。

    blockSignals(True) 返回的是**之前**的阻塞状态，旧实现据此决定是否解除，
    导致操作后选中信号永久阻塞（点击收敛不重绘、参数面板不切换）。
    """
    model = window.flow_model
    check = model.find_by_id("check")
    window.canvas_view.setCurrentIndex(model.indexFromItem(check))

    window._copy_selected()
    window._paste_clipboard()
    assert window.canvas_view.selectionModel().signalsBlocked() is False

    window._delete_selected_node()
    assert window.canvas_view.selectionModel().signalsBlocked() is False
