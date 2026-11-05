"""catalog 语义投影（agent_view）的契约测试。

投影是「AI 生成 workflow.json」的能力事实源，判据围绕三件事：
1. 覆盖完整：89 条 manifest 每条恰好出现一次（backends 求和 == manifest 数），
   semantic_id 全局唯一，digest 原样透传（catalog_version 闸门）。
2. 合并有据：desktop/backend 全等对合并成一个语义条目；**有分歧的组不合并**，
   各自成条目并进 divergent_groups（「同一语义」是审计结果不是假设）。
3. 卡片可消费：example 恰好覆盖 required、键都在 params 里；element_params
   与 runtime 引用契约同源（browser=selector，desktop 只有 findElement 拿 locator，
   click/input 用的是会话 elementId 句柄，**不该**标 element_params）。

负向两例：合成目录里人为制造语义分歧 ⇒ 拆两条 + 报告；required 引用不存在
的属性 ⇒ 构造 example 时报错（不是静默丢键）。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from rpa_core.catalog import build_agent_view, load_catalog

REPO = Path(__file__).resolve().parents[2]  # tests/unit/xxx.py → tests → 仓库根
COMMANDS = REPO / "commands"


@pytest.fixture(scope="module")
def view() -> dict:
    catalog = load_catalog(COMMANDS)
    return build_agent_view(catalog, element_param_keys=("selector", "locator"))


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(COMMANDS)


def _by_semantic_id(view: dict, semantic_id: str) -> dict:
    return next(c for c in view["commands"] if c["semantic_id"] == semantic_id)


def test_view_covers_every_manifest_exactly_once(view: dict, catalog) -> None:
    assert view["catalog_digest"] == catalog.digest
    assert view["manifest_commands"] == len(catalog)
    assert sum(len(c["backends"]) for c in view["commands"]) == len(catalog)
    assert view["semantic_commands"] == len(view["commands"])
    ids = [c["semantic_id"] for c in view["commands"]]
    assert len(ids) == len(set(ids)), "semantic_id 必须全局唯一"


def test_view_is_json_serializable(view: dict) -> None:
    # bridge / LLM 通道都以 JSON 搬运，NaN/模型对象都不许漏进来
    json.dumps(view, ensure_ascii=False, allow_nan=False)


def test_identical_pair_merges(view: dict) -> None:
    entry = _by_semantic_id(view, "desktop.click")
    assert {b["id"] for b in entry["backends"]} == {"desktop.click", "desktop.win32.click"}
    assert {b["executor"] for b in entry["backends"]} == {"desktop.uia", "desktop.win32"}
    assert len({b["handler"] for b in entry["backends"]}) == 2, "handler 是后端身份，不许被合并抹平"
    assert "divergent" not in json.dumps(
        [g["group"] for g in view["divergent_groups"]], ensure_ascii=False
    ) or "desktop.click" not in [g["group"] for g in view["divergent_groups"]]


def test_divergent_pairs_stay_separate_and_audited(view: dict) -> None:
    groups = {g["group"]: g for g in view["divergent_groups"]}
    assert "desktop.attachWindow" in groups, "attachWindow 的 win32 多 handle 参数，必须拆开"
    assert "input_schema" in groups["desktop.attachWindow"]["differing_fields"]
    # 拆开后的条目 semantic_id 是完整命令 id（含 .win32.），各自只有一个后端
    first, second = groups["desktop.attachWindow"]["semantic_ids"]
    for semantic_id in (first, second):
        entry = _by_semantic_id(view, semantic_id)
        assert len(entry["backends"]) == 1
    # 第一版实测：8 组有分歧（4 对 schema 差、7 对 x_outputs 差，有重叠）
    assert len(groups) == 8


def test_element_params_follow_reference_contract(view: dict) -> None:
    # browser 腿：selector 是元素参数
    assert _by_semantic_id(view, "browser.click")["element_params"] == ["selector"]
    # desktop 腿：只有 findElement 消费 locator（引用绑定只发生在它身上）；
    # click/input 用 findElement 产出的会话 elementId 句柄，不走元素引用
    assert _by_semantic_id(view, "desktop.findElement")["element_params"] == ["locator"]
    assert _by_semantic_id(view, "desktop.click")["element_params"] == []
    assert _by_semantic_id(view, "data.writeText")["element_params"] == []


def test_example_exactly_covers_required(view: dict, catalog) -> None:
    for entry in view["commands"]:
        schema = catalog[entry["backends"][0]["id"]].input_schema
        properties = set(schema.get("properties") or {})
        required = set(schema.get("required") or []) & properties
        assert set(entry["example"]) == required, (
            f"{entry['semantic_id']} 的 example 与 required 不一致"
        )
        assert set(entry["params"]) == properties, (
            f"{entry['semantic_id']} 的 params 覆盖不全"
        )
        for key, value in entry["example"].items():
            prop = schema["properties"][key]
            if prop.get("enum"):
                assert value in prop["enum"], f"{entry['semantic_id']}.{key} 占位值不在枚举内"


def test_synthetic_divergence_is_not_merged(tmp_path: Path) -> None:
    """负向：人为制造 schema 分歧 ⇒ 必须拆两条并上报，不许静默合并。"""
    root = tmp_path / "commands"
    (root / "desktop").mkdir(parents=True)
    (root / "desktop_win32").mkdir(parents=True)
    shutil.copy(COMMANDS / "desktop" / "click.json", root / "desktop" / "click.json")
    mutated = json.loads((COMMANDS / "desktop_win32" / "click.json").read_text(encoding="utf-8"))
    mutated["input_schema"]["properties"]["extraKey"] = {"type": "string"}
    (root / "desktop_win32" / "click.json").write_text(
        json.dumps(mutated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    view = build_agent_view(load_catalog(root), element_param_keys=())
    entries = [c for c in view["commands"] if c["semantic_id"].endswith(".click")]
    assert len(entries) == 2, "schema 分歧的对必须拆成两条"
    assert all(len(c["backends"]) == 1 for c in entries)
    group = next(g for g in view["divergent_groups"] if g["group"] == "desktop.click")
    assert "input_schema" in group["differing_fields"]


def test_required_referencing_missing_property_raises(tmp_path: Path) -> None:
    """负向：required 引用不存在的属性 ⇒ 构造 example 时报错，不许静默丢键。"""
    root = tmp_path / "commands"
    root.mkdir()
    manifest = json.loads((COMMANDS / "browser" / "click.json").read_text(encoding="utf-8"))
    del manifest["input_schema"]["properties"]["selector"]  # required 还留着它
    (root / "click.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="required 引用了不存在的属性"):
        build_agent_view(load_catalog(root), element_param_keys=())
