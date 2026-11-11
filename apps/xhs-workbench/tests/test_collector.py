"""CLI 拼装与终态解析：六种终态不折叠，缺字段不静默当成功。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from src.collector import (
    STATUS_GROUPS,
    build_run_command,
    parse_result,
    step_output,
)

WF = "apps/xhs-workbench/workflows/xhs_product_search.json"


def test_build_run_command_feeds_inputs_as_json() -> None:
    cmd = build_run_command(WF, "AI效率工具", "run_artifacts/xhs")
    assert cmd[:2] == ["rpa-core", "run"]
    assert cmd[2] == WF
    assert json.loads(cmd[cmd.index("--inputs") + 1]) == {"keyword": "AI效率工具"}
    assert cmd[cmd.index("--artifacts") + 1] == "run_artifacts/xhs"


def test_build_run_command_merges_extra_inputs() -> None:
    cmd = build_run_command(
        WF,
        "知识库",
        "out",
        binary="my-rpa",
        extra_inputs={"workspace": "out", "keyword": "覆盖后的词"},
    )
    assert cmd[0] == "my-rpa"
    payload = json.loads(cmd[cmd.index("--inputs") + 1])
    assert payload == {"keyword": "覆盖后的词", "workspace": "out"}


@pytest.mark.parametrize("status", sorted(STATUS_GROUPS))
def test_every_documented_status_keeps_its_group(tmp_path: Path, status: str) -> None:
    payload = {
        "run_id": "run-1",
        "status": status,
        "outputs": {},
        "error": None,
    }
    path = tmp_path / "result.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    outcome = parse_result(path)
    assert outcome.status == status
    assert outcome.group == STATUS_GROUPS[status]
    assert outcome.run_id == "run-1"


def test_unknown_status_is_not_silently_ok(tmp_path: Path) -> None:
    path = tmp_path / "result.json"
    path.write_text(json.dumps({"status": "mystery"}), encoding="utf-8")
    assert parse_result(path).group == "unknown"


def test_step_output_reads_nested_shape(tmp_path: Path) -> None:
    payload = {
        "status": "succeeded",
        "outputs": {"grabItems": {"outputs": {"result": "[{\"title\":\"x\"}]"}}},
    }
    path = tmp_path / "result.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert step_output(parse_result(path), "grabItems", "result") == '[{"title":"x"}]'
    with pytest.raises(KeyError):
        step_output(parse_result(path), "grabItems", "missing")
    with pytest.raises(KeyError):
        step_output(parse_result(path), "otherStep", "result")


def test_non_object_result_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "result.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError):
        parse_result(path)
