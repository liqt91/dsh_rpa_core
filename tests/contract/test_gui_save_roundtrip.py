"""编辑闭环契约测试（GUI 切片 4）：模型回写 AST 与保存 workflow.json。

覆盖：
- 未编辑时回写与原文档结构一致（含 if then/else、forEach items/itemVar、
  try catch/error_var、return value 等 GUI 不编辑字段的保留）；
- 参数修改、同级拖拽重排、移入 then 分组后能正确回写；
- 落盘文件可被 ``Workflow`` 重新校验，格式与 devserver 约定一致；
- 脏标记随参数应用/拖拽变化，保存后清除。

offscreen Qt 平台；缺 PySide6 时整组跳过。
"""

from __future__ import annotations

import copy
import json
import os

# 必须在导入 Qt / 创建 QApplication 之前指定离屏平台
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QPushButton  # noqa: E402

from rpa_core.gui.app import SAMPLE_WORKFLOW, MainWindow  # noqa: E402
from rpa_core.gui.flow_model import (  # noqa: E402
    ROLE_ARGS_RAW,
    ROLE_IS_VIRTUAL,
    ROLE_NODE_TYPE,
    build_model_from_workflow,
    model_to_workflow,
)
from rpa_core.gui.param_form import ParamForm  # noqa: E402
from rpa_core.model.workflow import Workflow  # noqa: E402

# 工作流级字段（回写 meta 时保留）
_META_KEYS = ("schema_version", "id", "name", "inputs", "timeout_seconds")


@pytest.fixture(scope="module")
def qapp():
    from rpa_core.gui.app import build_application

    return build_application()


@pytest.fixture(scope="module")
def catalog(qapp):
    from rpa_core.catalog import load_catalog
    from rpa_core.cli import _commands_root

    return load_catalog(_commands_root())


def _roundtrip(document: dict) -> dict:
    """构建模型后立即回写（不经主窗口）。"""
    model = build_model_from_workflow(document)
    meta = {key: document[key] for key in document if key != "root"}
    return model_to_workflow(model, meta)


def _drop(model, source_id: str, target_item, row: int = -1) -> bool:
    """模拟 QTreeView InternalMove：取源行 mime 后投递到目标容器。"""
    source = model.find_by_id(source_id)
    mime = model.mimeData([model.indexFromItem(source)])
    return model.dropMimeData(
        mime, Qt.DropAction.MoveAction, row, 0, model.indexFromItem(target_item)
    )


# ---- 回写 roundtrip -------------------------------------------------------
def test_untouched_sample_roundtrips_identically():
    assert _roundtrip(SAMPLE_WORKFLOW) == SAMPLE_WORKFLOW


def test_try_catch_and_error_var_preserved():
    doc = {
        "schema_version": "1.0", "id": "try-demo", "name": "异常",
        "root": {
            "type": "try", "id": "t",
            "children": [
                {"type": "action", "id": "a1", "command": "data.setVar",
                 "with": {"name": "x", "value": 1}},
            ],
            "catch": [
                {"type": "action", "id": "a2", "command": "workflow.sleep",
                 "with": {"seconds": 1}},
            ],
            "error_var": "err",
        },
    }
    result = _roundtrip(doc)
    assert result == doc
    assert result["root"]["catch"][0]["id"] == "a2"
    assert result["root"]["error_var"] == "err"


def test_else_omitted_when_empty_and_absent_in_source():
    # else 为空且源文档省略 else 键：回写不应自行补出
    doc = {
        "schema_version": "1.0", "id": "if-demo", "name": "分支",
        "root": {
            "type": "if", "id": "i",
            "condition": {"op": "truthy", "left": "${x}"},
            "then": [
                {"type": "action", "id": "a1", "command": "workflow.sleep",
                 "with": {"seconds": 1}},
            ],
        },
    }
    result = _roundtrip(doc)
    assert "else" not in result["root"]
    assert result == doc


def test_param_edit_serialized_to_with():
    model = build_model_from_workflow(copy.deepcopy(SAMPLE_WORKFLOW))
    item = model.find_by_id("open")
    holder = item.data(ROLE_ARGS_RAW)
    holder.args = {"url": "https://changed.example", "timeoutMs": 9000}
    holder.raw["with"] = dict(holder.args)

    meta = {key: SAMPLE_WORKFLOW[key] for key in _META_KEYS if key in SAMPLE_WORKFLOW}
    doc = model_to_workflow(model, meta)
    first_action = doc["root"]["children"][0]
    assert first_action["with"] == {
        "url": "https://changed.example", "timeoutMs": 9000
    }


def test_sibling_reorder_and_preserved_fields():
    doc = json.loads(json.dumps(SAMPLE_WORKFLOW))  # 深拷贝
    model = build_model_from_workflow(doc)
    root = model.item(0)
    # 把第 0 行（open）移到 if（第 1 行，移动后索引）之后
    assert _drop(model, "open", root, row=2)
    meta = {key: doc[key] for key in doc if key != "root"}
    result = model_to_workflow(model, meta)

    ids = [child["id"] for child in result["root"]["children"]]
    assert ids == ["check", "open", "loop", "done"]
    # GUI 不编辑的结构字段原样保留
    assert result["root"]["children"][0]["condition"] == {
        "op": "truthy", "left": "${data}"
    }
    loop = next(child for child in result["root"]["children"] if child["id"] == "loop")
    assert loop["items"] == "${rows}"
    assert loop["item_var"] == "row"


def test_move_action_into_then_group():
    doc = json.loads(json.dumps(SAMPLE_WORKFLOW))
    model = build_model_from_workflow(doc)
    if_item = model.find_by_id("check")
    then_group = next(
        if_item.child(row) for row in range(if_item.rowCount())
        if if_item.child(row).data(ROLE_IS_VIRTUAL)
        and if_item.child(row).data(ROLE_NODE_TYPE) == "branch-then"
    )
    assert _drop(model, "done", then_group)

    meta = {key: doc[key] for key in doc if key != "root"}
    result = model_to_workflow(model, meta)
    check = next(
        child for child in result["root"]["children"] if child["id"] == "check"
    )
    then_ids = [node["id"] for node in check["then"]]
    assert "done" in then_ids
    # return 移入后根下不再有 done
    root_ids = [child["id"] for child in result["root"]["children"]]
    assert "done" not in root_ids


# ---- 主窗口保存与脏标记 ---------------------------------------------------
def test_save_workflow_writes_valid_file_and_clears_dirty(catalog, tmp_path):
    window = MainWindow(catalog, SAMPLE_WORKFLOW)
    assert window._dirty is False

    # 模拟参数应用：选中卡片 → 改 url → 点应用
    item = window.flow_model.find_by_id("open")
    window.canvas_view.setCurrentIndex(window.flow_model.indexFromItem(item))

    param_form = window.param_holder.findChild(ParamForm)
    field = next(
        widget for field_name, _kind, widget in param_form._fields
        if field_name == "url"
    )
    field.setText("https://saved.example")
    window.param_holder.findChild(QPushButton).click()
    assert window._dirty is True
    assert window.windowTitle().startswith("•")

    target = tmp_path / "myflow" / "workflow.json"
    saved = window.save_workflow(target)
    assert saved == target
    assert target.exists()
    assert window._dirty is False
    assert not window.windowTitle().startswith("•")
    assert window.flow_path == target

    written = json.loads(target.read_text(encoding="utf-8"))
    # 落盘文件必须通过工作流模型校验
    Workflow.model_validate(written)
    assert written["root"]["children"][0]["with"]["url"] == "https://saved.example"
    # 写入格式：UTF-8、indent=2、末尾换行
    raw = target.read_bytes()
    assert raw.endswith(b"\n")
    assert b'\n  "schema_version"' in raw


def test_drag_drop_marks_window_dirty(catalog):
    window = MainWindow(catalog, SAMPLE_WORKFLOW)
    root = window.flow_model.item(0)
    assert _drop(window.flow_model, "open", root, row=2)
    assert window._dirty is True


def test_pydantic_workflow_roundtrip_and_save(catalog, tmp_path):
    workflow = Workflow.model_validate(SAMPLE_WORKFLOW)
    window = MainWindow(catalog, workflow)
    # pydantic 输入的 meta 含 timeout_seconds/inputs 默认值
    assert window._workflow_meta["schema_version"] == "1.0"
    target = tmp_path / "workflow.json"
    assert window.save_workflow(target) == target
    Workflow.model_validate_json(target.read_text(encoding="utf-8"))
