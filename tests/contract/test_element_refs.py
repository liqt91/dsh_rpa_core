"""元素引用解析契约（M46/B1 引用模型；M52 S6 去快照）。

引用模型（与影刀同构）：节点参数携带 `elementRefs: {参数键: 元素名}`，
选择器值**只在流程 `elements/` 目录存一份**，运行期解析最新值。

**M52 S6 起为唯一真值源**：节点 `with` 里不再有值快照（`ActionNode` 直接拒绝
「快照 + 引用」共存），因此解析不到元素时**没有兜底**——硬失败 `ELEMENT_REF_MISSING`。
判据三层：
1. 纯函数（element_refs 模块）：取值口径 / 缺失只报不回落 / 读取器容错；
2. 模型（ActionNode.elementRefs）：ref-only 可解析、双写被拒、未知键仍拒；
3. 编排器端到端：改元素库主定位后，已插入指令运行期拿到新值（B1 的核心
   承诺）；元素缺失 ⇒ `ELEMENT_REF_MISSING` 硬失败、**不执行**、不再有回落事件。
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
    # 形状不对 / 空值 / 未知键 → None（该引用取不到定位值 ⇒ 调用方硬失败）
    assert element_value_for_key(None, "selector") is None
    assert element_value_for_key({"kind": "browser"}, "selector") is None
    assert element_value_for_key({"selector": {"css": ""}}, "selector") is None
    assert element_value_for_key(browser, "text") is None


def test_resolve_element_refs_reports_missing_without_fallback():
    """M52 S6：解析不到只**记入 missing**，绝不回落——`with` 里已经没有快照可回落。"""
    inputs = {"text": "hi"}
    resolution = resolve_element_refs(inputs, {"selector": "ghost"}, lambda name: None)
    assert resolution.missing == [("selector", "ghost")]
    assert "selector" not in inputs  # 既不注入，也没有旧值可留
    assert not resolution.resolved
    assert inputs["text"] == "hi"  # 未引用键不受牵连


def test_resolve_element_refs_injects_fresh_value():
    inputs = {}
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


def test_kind_key_mapping_is_sourced_from_value_table():
    """kind↔参数键映射与取值路径表必须同源（防第二套权威漂移）。

    M52 S4：参数面板的「从元素库选择」与元素库「插入参数」都读这两张表——一旦
    某张表加了键、另一张没跟上，就会出现「面板给了入口、运行期替换不了」的
    静默失配（不报错、只是新来源永远排不上）。
    """
    from rpa_core.runtime.element_refs import (
        _KEY_BY_KIND,
        _VALUE_BY_KEY,
        element_capable_keys,
        element_kind_for_param_key,
        param_key_for_element_kind,
    )

    # 两张表互为反向：键集合必须相等
    assert set(_KEY_BY_KIND.values()) == set(_VALUE_BY_KEY)
    assert element_capable_keys() == frozenset(_VALUE_BY_KEY)

    # 反查只认「取得出定位值」的键
    assert element_kind_for_param_key("selector") == "browser"
    assert element_kind_for_param_key("locator") == "desktop"
    assert element_kind_for_param_key("text") is None
    assert element_kind_for_param_key("elementId") is None

    # 每个 kind 映射到的键都要真能取出值（否则面板会列出选了也填不进去的项）
    browser = {"kind": "browser", "selector": {"css": "#kw"}}
    desktop = {"kind": "desktop", "selector": {"locator": {"automationId": "ok"}}}
    for kind, document, expected in (
        ("browser", browser, "#kw"),
        ("desktop", desktop, {"automationId": "ok"}),
    ):
        key = param_key_for_element_kind(kind)
        assert key is not None
        assert element_value_for_key(document, key) == expected


# ---- 2. 模型兼容 ----------------------------------------------------------------
def test_action_node_accepts_ref_only_and_plain_nodes():
    """ref-only 节点可解析；纯手工节点照旧；未知键仍被 extra=forbid 拦住。"""
    from rpa_core.model.workflow import ActionNode

    ref_only = ActionNode.model_validate(
        {
            "type": "action",
            "id": "step1",
            "command": "browser.getText",
            "with": {"text": "hi"},
            "elementRefs": {"selector": "searchBox"},
        }
    )
    assert ref_only.element_refs == {"selector": "searchBox"}
    assert "selector" not in ref_only.with_  # 引用即唯一真值，不留快照
    # 纯手工节点（无 elementRefs）照常：值就是值
    plain = ActionNode.model_validate(
        {
            "type": "action",
            "id": "step2",
            "command": "x.y",
            "with": {"selector": "#kw"},
        }
    )
    assert plain.element_refs is None
    # extra=forbid 仍然拦未知键（elementRefs 是声明过的字段，不是开了口子）
    with pytest.raises(ValidationError):
        ActionNode.model_validate(
            {"type": "action", "id": "s3", "command": "x.y", "elementRefz": {}}
        )


def test_action_node_rejects_snapshot_alongside_element_ref():
    """M52 S6 硬切：被引用的参数**不许**同时带值快照。

    旧版双写数据（`with.selector` + `elementRefs.selector`）直接报错而不静默迁移——
    两份真值正是「选了元素、selector 却没落进节点」这类事故的土壤（维护者拍板硬切）。
    """
    from rpa_core.model.workflow import ActionNode

    with pytest.raises(ValidationError) as excinfo:
        ActionNode.model_validate(
            {
                "type": "action",
                "id": "step1",
                "command": "browser.getText",
                "with": {"selector": "#old", "text": "hi"},
                "elementRefs": {"selector": "searchBox"},
            }
        )
    assert "selector" in str(excinfo.value)
    assert "值快照" in str(excinfo.value)

    # 未引用的键带着值不算冲突（那就是纯手工参数）
    ok = ActionNode.model_validate(
        {
            "type": "action",
            "id": "step2",
            "command": "browser.getText",
            "with": {"text": "hi"},
            "elementRefs": {"selector": "searchBox"},
        }
    )
    assert ok.element_refs == {"selector": "searchBox"}


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
    return EchoExecutor.seen, events, result


def test_orchestrator_resolves_element_ref_to_fresh_value(tmp_path):
    flow = tmp_path / "flow"
    (flow / "elements").mkdir(parents=True)
    (flow / "elements" / "searchBox.json").write_text(
        json.dumps({"kind": "browser", "selector": {"css": "#fresh"}}),
        encoding="utf-8",
    )
    seen, events, result = _run_once(tmp_path, {}, {"selector": "searchBox"})
    # 节点里**没有**值（ref-only）：运行期必须把元素库的 #fresh 注进去
    assert seen[0]["selector"] == "#fresh"
    assert result.status == "succeeded"
    assert "elementRefFallback" not in events


def test_orchestrator_picks_up_element_edit_between_runs(tmp_path):
    """B1 核心承诺：元素库里改主定位，已插入指令（引用）自动跟着生效。"""
    flow = tmp_path / "flow"
    elements = flow / "elements"
    elements.mkdir(parents=True)
    doc = {"kind": "browser", "selector": {"css": "#v1"}}
    (elements / "searchBox.json").write_text(json.dumps(doc), encoding="utf-8")
    seen, _events, _result = _run_once(tmp_path, {}, {"selector": "searchBox"})
    assert seen[0]["selector"] == "#v1"

    # 元素库里改主定位（不改流程文件）
    doc["selector"]["css"] = "#v2"
    (elements / "searchBox.json").write_text(json.dumps(doc), encoding="utf-8")
    seen, _events, _result = _run_once(tmp_path, {}, {"selector": "searchBox"})
    assert seen[0]["selector"] == "#v2"


def test_orchestrator_hard_fails_when_element_missing(tmp_path):
    """M52 S6：引用是唯一真值 ⇒ 元素不在元素库就**硬失败**，且不执行、不回落。

    这条取代了旧判据「回落快照 + 落 elementRefFallback 事件」——那条语义随快照
    一起去掉了。fail-closed 与本项目对 `ANCHOR_NOT_FOUND` 的口径一致。
    """
    from rpa_core.model.errors import ErrorCode

    seen, events, result = _run_once(tmp_path, {}, {"selector": "ghost"})
    assert seen == []  # 缺元素 ⇒ **不执行**该指令
    assert result.status == "failed"
    assert result.error is not None
    assert result.error["code"] == ErrorCode.ELEMENT_REF_MISSING
    assert "ghost" in result.error["message"]
    assert result.error["details"]["nodeId"] == "step"
    assert "elementRefFallback" not in events  # 旧回落事件已彻底移除


def test_orchestrator_plain_value_nodes_untouched(tmp_path):
    """纯手工值节点（无 elementRefs）：行为与从前完全一致，零事件。"""
    seen, events, result = _run_once(tmp_path, {"selector": "#plain"}, None)
    assert seen[0]["selector"] == "#plain"
    assert result.status == "succeeded"
    assert "elementRefFallback" not in events
