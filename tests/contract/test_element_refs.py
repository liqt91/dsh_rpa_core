"""元素引用解析契约（M46/B1 引用模型）。

引用模型（与影刀同构）：节点参数携带 `elementRefs: {参数键: 元素名}`，
选择器值只在流程 `elements/` 目录存一份，运行期解析最新值。判据三层：
1. 纯函数（element_refs 模块）：取值口径 / 回落语义 / 读取器容错；
2. 模型（ActionNode.elementRefs）：新字段可解析、旧文件兼容、未知键仍拒；
3. 编排器端到端：改元素库主定位后，已插入指令运行期拿到新值（B1 的核心
   承诺）；缺元素回落快照且落 `elementRefFallback` 事件（可观测回落）。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from rpa_core.executors.base import CommandExecutor
from rpa_core.model.command import CommandResult
from rpa_core.runtime.element_refs import (
    element_value_for_key,
    make_element_reader,
    resolve_element_refs,
)


# ---- 1. 纯函数 ----------------------------------------------------------------
def test_element_value_for_key_matches_insert_convention():
    # browser：键 selector → 文档 selector.css
    browser = {"kind": "browser", "selector": {"css": "#kw", "path": []}}
    assert element_value_for_key(browser, "selector") == "#kw"
    # desktop：键 locator → 文档 selector.locator，且允许结构化对象
    desktop = {"kind": "desktop", "selector": {"locator": {"controlType": "Button"}}}
    assert element_value_for_key(desktop, "locator") == {"controlType": "Button"}
    # 形状不对 / 空值 / 未知键 → None（回落快照）
    assert element_value_for_key(None, "selector") is None
    assert element_value_for_key({"kind": "browser"}, "selector") is None
    assert element_value_for_key({"selector": {"css": ""}}, "selector") is None
    assert element_value_for_key(browser, "text") is None


def test_resolve_element_refs_fallback_keeps_snapshot():
    inputs = {"selector": "#snapshot", "text": "hi"}
    reader = lambda name: None  # noqa: E731 - 元素缺失
    resolution = resolve_element_refs(inputs, {"selector": "ghost"}, reader)
    assert resolution.missing == ["ghost"]
    assert inputs["selector"] == "#snapshot"  # 快照保持不动
    assert inputs["text"] == "hi"  # 未引用键不受牵连


def test_resolve_element_refs_overwrites_with_fresh_value():
    inputs = {"selector": "#old"}
    docs = {"searchBox": {"kind": "browser", "selector": {"css": "#fresh"}}}
    resolution = resolve_element_refs(inputs, {"selector": "searchBox"}, docs.get)
    assert resolution.resolved == ["searchBox"]
    assert not resolution.missing
    assert inputs["selector"] == "#fresh"


def test_make_element_reader_tolerates_missing_and_corrupt(tmp_path):
    flow = tmp_path / "flow"
    (flow / "elements").mkdir(parents=True)
    (flow / "elements" / "ok.json").write_text(
        json.dumps({"kind": "browser", "selector": {"css": "#a"}}), encoding="utf-8"
    )
    (flow / "elements" / "bad.json").write_text("{not json", encoding="utf-8")
    (flow / "elements" / "array.json").write_text("[1,2]", encoding="utf-8")
    reader = make_element_reader(flow)
    assert reader("ok") == {"kind": "browser", "selector": {"css": "#a"}}
    assert reader("bad") is None
    assert reader("array") is None
    assert reader("ghost") is None
    assert reader("") is None
    # flow_dir 为 None：读取器恒 None（无目录就没有引用解析）
    assert make_element_reader(None)("anything") is None


# ---- 2. 模型兼容 ----------------------------------------------------------------
def test_action_node_accepts_element_refs_and_old_files():
    from rpa_core.model.workflow import ActionNode

    node = ActionNode.model_validate(
        {
            "type": "action",
            "id": "step1",
            "command": "browser.getText",
            "with": {"selector": "#old"},
            "elementRefs": {"selector": "searchBox"},
        }
    )
    assert node.element_refs == {"selector": "searchBox"}
    # 旧文件（无 elementRefs）照常解析：None = 纯值拷贝语义
    old = ActionNode.model_validate(
        {"type": "action", "id": "step2", "command": "x.y", "with": {}}
    )
    assert old.element_refs is None
    # extra=forbid 仍然拦未知键（elementRefs 是声明过的字段，不是开了口子）
    with pytest.raises(ValidationError):
        ActionNode.model_validate(
            {"type": "action", "id": "s3", "command": "x.y", "elementRefz": {}}
        )


# ---- 3. 编排器端到端 --------------------------------------------------------------
def _manifest():
    return {
        "id": "data.fake",
        "version": "1.0.0",
        "executor": "echo",
        "kind": "transform",
        "risk": "read",
        "capabilities": [],
        "resources": [],
        "stability": "stable",
        "effect": {"kind": "pure", "replay": "safe", "idempotency": "none"},
        "input_schema": {
            "type": "object",
            "properties": {"selector": {"type": "string"}},
        },
        "output_schema": {
            "type": "object",
            "required": ["selector"],
            "properties": {"selector": {"type": "string"}},
        },
        "errors": ["EXECUTOR_FAILED"],
        "implementation": {"handler": "echo:execute"},
    }


class EchoExecutor(CommandExecutor):
    """回显 inputs.selector，供断言运行期真正消费的值。"""

    seen: list = []

    async def execute(self, invocation, cancellation):
        EchoExecutor.seen.append(dict(invocation.inputs or {}))
        return CommandResult.success(
            outputs={"selector": (invocation.inputs or {}).get("selector", "")}
        )


def _run_once(tmp_path: Path, node_with: dict, element_refs: dict | None):
    from rpa_core.catalog import load_catalog
    from rpa_core.compiler import WorkflowCompiler
    from rpa_core.executors import ExecutorRegistry
    from rpa_core.model.workflow import Workflow
    from rpa_core.runtime import Orchestrator

    commands = tmp_path / "commands"
    commands.mkdir(exist_ok=True)
    (commands / "fake.json").write_text(json.dumps(_manifest()), encoding="utf-8")
    catalog = load_catalog(commands)

    raw = {
        "id": "ref-test",
        "name": "ref-test",
        "root": {
            "type": "action",
            "id": "step",
            "command": "data.fake",
            "with": node_with,
        },
    }
    if element_refs is not None:
        raw["root"]["elementRefs"] = element_refs
    plan = WorkflowCompiler(catalog).compile(Workflow.model_validate(raw), set())

    flow_dir = tmp_path / "flow"
    EchoExecutor.seen = []
    executor = EchoExecutor()
    registry = ExecutorRegistry({"echo": executor})
    runner = Orchestrator(catalog, registry, tmp_path / "runs", flow_dir=flow_dir)
    result = asyncio.run(runner.run(plan))
    events = (tmp_path / "runs" / result.run_id / "events.jsonl").read_text(
        encoding="utf-8"
    )
    return EchoExecutor.seen, events


def test_orchestrator_resolves_element_ref_to_fresh_value(tmp_path):
    flow = tmp_path / "flow"
    (flow / "elements").mkdir(parents=True)
    (flow / "elements" / "searchBox.json").write_text(
        json.dumps({"kind": "browser", "selector": {"css": "#fresh"}}),
        encoding="utf-8",
    )
    seen, events = _run_once(
        tmp_path,
        {"selector": "#snapshot"},
        {"selector": "searchBox"},
    )
    # 元素库里写的是 #fresh：运行期必须拿到 #fresh 而不是节点快照 #snapshot
    assert seen[0]["selector"] == "#fresh"
    assert "elementRefFallback" not in events


def test_orchestrator_picks_up_element_edit_between_runs(tmp_path):
    """B1 核心承诺：元素库里改主定位，已插入指令（引用）自动跟着生效。"""
    flow = tmp_path / "flow"
    elements = flow / "elements"
    elements.mkdir(parents=True)
    doc = {"kind": "browser", "selector": {"css": "#v1"}}
    (elements / "searchBox.json").write_text(
        json.dumps(doc), encoding="utf-8"
    )
    seen, _ = _run_once(tmp_path, {"selector": "#snapshot"}, {"selector": "searchBox"})
    assert seen[0]["selector"] == "#v1"

    # 元素库里改主定位（不改流程文件）
    doc["selector"]["css"] = "#v2"
    (elements / "searchBox.json").write_text(json.dumps(doc), encoding="utf-8")
    seen, _ = _run_once(tmp_path, {"selector": "#snapshot"}, {"selector": "searchBox"})
    assert seen[0]["selector"] == "#v2"


def test_orchestrator_falls_back_to_snapshot_with_event(tmp_path):
    seen, events = _run_once(
        tmp_path,
        {"selector": "#snapshot"},
        {"selector": "ghost"},
    )
    assert seen[0]["selector"] == "#snapshot"  # 回落快照，运行不中断
    assert '"elementRefFallback"' in events
    assert '"missing":["ghost"]' in events


def test_orchestrator_ignores_snapshot_only_nodes(tmp_path):
    """旧格式节点（无 elementRefs）：行为与从前完全一致，零事件。"""
    seen, events = _run_once(tmp_path, {"selector": "#plain"}, None)
    assert seen[0]["selector"] == "#plain"
    assert "elementRefFallback" not in events
