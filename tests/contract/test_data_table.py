"""data.table.* 流程数据表格命令的 worker 层测试。

直接调用 python_worker.execute()（同步），flowDir 由调用方模拟 orchestrator 注入。
"""

import json
from pathlib import Path

import pytest

from rpa_core.model.command import CommandInvocation, CommandManifest
from rpa_core.workers import python_worker as pw

ROOT = Path(__file__).resolve().parents[2]


def _inv(cid: str, flow_dir, **inputs) -> CommandInvocation:
    return CommandInvocation(
        command_id=cid,
        command_version="1.0.0",
        run_id="run",
        step_id="t",
        inputs={"flowDir": str(flow_dir), **inputs},
    )


def _manifest(cid: str) -> CommandManifest:
    rel = cid.removeprefix("data.table.")  # getCell/maxSetCell...
    name = rel[0].lower() + rel[1:]
    path = ROOT / "commands" / "data" / "table" / f"{name}.json"
    return CommandManifest.model_validate_json(path.read_text(encoding="utf-8"))


def test_manifest_declares_runtime_flow_dir():
    """表命令 manifest 声明 flowDir 注入（x-runtime.inject）且 input_schema 收录。"""
    for cid in (
        "data.table.getCell",
        "data.table.setCell",
        "data.table.appendRow",
        "data.table.deleteRow",
        "data.table.clear",
        "data.table.exportCsv",
    ):
        manifest = _manifest(cid)
        assert manifest.executor == "python.worker"
        assert "flowDir" in (manifest.x_runtime or {}).get("inject", [])
        assert "flowDir" in manifest.input_schema["properties"]


def test_table_cmd_requires_flow_dir(tmp_path):
    result = pw.execute(
        CommandInvocation(
            command_id="data.table.clear",
            command_version="1.0.0",
            run_id="r", step_id="t", inputs={},
        )
    )
    assert result.status == "error"
    assert result.error.code.value == "INVALID_INPUT"


def test_append_cumulates(tmp_path):
    result1 = pw.execute(_inv("data.table.appendRow", tmp_path, row={"name": "张三"}))
    assert result1.status == "success" and result1.outputs["rowCount"] == 1
    result2 = pw.execute(_inv("data.table.appendRow", tmp_path, row={"name": "李四"}))
    assert result2.status == "success" and result2.outputs["rowCount"] == 2
    obj = json.loads((tmp_path / "data" / "table.json").read_text(encoding="utf-8"))
    assert len(obj["rows"]) == 2
    assert obj["rows"][1]["name"] == "李四"


def test_set_cell_pads_rows(tmp_path):
    result = pw.execute(
        _inv("data.table.setCell", tmp_path, row=5, column="姓名", value="王五")
    )
    assert result.status == "success"
    obj = json.loads((tmp_path / "data" / "table.json").read_text(encoding="utf-8"))
    assert len(obj["rows"]) == 5
    assert obj["rows"][4]["姓名"] == "王五"


def test_get_cell_reads_and_out_of_range_none(tmp_path):
    _seed = {"schema_version": 1, "columns": [{"key": "k", "label": "分数", "type": "text"}],
             "rows": [{"k": "90"}], "updated_at": ""}
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "table.json").write_text(json.dumps(_seed), encoding="utf-8")

    ok = pw.execute(_inv("data.table.getCell", tmp_path, row=1, column="分数", varName="x"))
    assert ok.status == "success" and ok.outputs == {"varName": "x", "value": "90"}

    miss = pw.execute(_inv("data.table.getCell", tmp_path, row=9, column="分数", varName="x"))
    assert miss.status == "success" and miss.outputs["value"] is None


def test_delete_row_out_of_range(tmp_path):
    pw.execute(_inv("data.table.appendRow", tmp_path, row={"name": "张三"}))
    result = pw.execute(_inv("data.table.deleteRow", tmp_path, row=5))
    assert result.status == "error"
    assert result.error.code.value == "INVALID_INPUT"


def test_clear_keeps_columns(tmp_path):
    pw.execute(_inv("data.table.appendRow", tmp_path, row={"name": "张三"}))
    result = pw.execute(_inv("data.table.clear", tmp_path))
    assert result.status == "success" and result.outputs["rowCount"] == 0
    obj = json.loads((tmp_path / "data" / "table.json").read_text(encoding="utf-8"))
    assert obj["rows"] == []


def test_export_csv_default_with_bom(tmp_path):
    pw.execute(_inv("data.table.appendRow", tmp_path, row={"姓名": "张三", "年龄": 18}))
    pw.execute(_inv("data.table.appendRow", tmp_path, row={"姓名": "李四", "年龄": 20}))
    result = pw.execute(_inv("data.table.exportCsv", tmp_path))
    assert result.status == "success"
    csv_path = result.outputs["path"]
    blob = Path(csv_path).read_bytes()
    # 带 UTF-8 BOM，便于 Excel 打开中文
    assert blob.startswith(b"\xef\xbb\xbf")
    text = blob.decode("utf-8-sig")
    lines = [line for line in text.splitlines() if line]
    assert "\ufffd" not in text
    assert "姓名" in lines[0] and "年龄" in lines[0]
    assert lines[1] == "张三,18"


def test_export_csv_escape_denied(tmp_path):
    result = pw.execute(_inv("data.table.exportCsv", tmp_path, path="../evil.csv"))
    assert result.status == "error"
    assert result.error.code.value == "CAPABILITY_DENIED"


def test_validates_table_name(tmp_path):
    from rpa_core.workers.data_table import validate_table_name

    with pytest.raises(ValueError):
        validate_table_name("../evil")


def test_atomic_write_no_leftover_tmp(tmp_path):
    pw.execute(_inv("data.table.appendRow", tmp_path, row={"name": "张三"}))
    assert not list((tmp_path / "data").glob(".table-*.tmp"))


def test_write_commands_return_effect_evidence(tmp_path):
    """带 effect 的表格命令须返回与 manifest.kind 匹配的 COMMITTED 证据（orchestrator 校验）。"""
    cases = {
        "data.table.appendRow": {"kind": "unsafe-write", "row": {"name": "甲"}},
        "data.table.setCell": {"kind": "idempotent-write", "row": 1, "column": "x", "value": "1"},
        "data.table.deleteRow": {"kind": "unsafe-write", "row": 1},
        "data.table.clear": {"kind": "idempotent-write"},
        "data.table.getCell": {"kind": "read", "row": 1, "column": "name", "varName": "v"},
    }
    pw.execute(_inv("data.table.appendRow", tmp_path, row={"name": "甲"}))
    for cid, extra in cases.items():
        result = pw.execute(_inv(cid, tmp_path, **extra))
        assert result.status == "success", (cid, result)
        assert len(result.effects) == 1, cid
        effect = result.effects[0]
        assert effect.kind.value == extra["kind"], cid
        assert effect.status.value == "committed", cid


def test_export_csv_returns_committed_effect(tmp_path):
    pw.execute(_inv("data.table.appendRow", tmp_path, row={"姓名": "张三"}))
    result = pw.execute(_inv("data.table.exportCsv", tmp_path))
    assert result.status == "success"
    effect = result.effects[0]
    assert effect.kind.value == "idempotent-write"
    assert effect.status.value == "committed"
    assert effect.idempotency_key is not None