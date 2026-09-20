"""M28 S1 元素自愈契约：主选择器失效时按元素资产候选重试，并记录所用候选。

契约（定案）：**不改命令参数、不写工作流文件**——候选以元素资产
（`<flowDir>/elements/*.json`，M10 捕获落盘）为单一事实来源，运行期用失败的
selector 反查取用；资产缺失/不匹配时行为与今天完全一致（不猜测）。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from rpa_core.executors.browser import PlaywrightExecutor
from rpa_core.model.command import CommandInvocation


class _ScriptedExt:
    """按 selector 决定匹配结果的扩展桩，并记录每次 page_call 的 selector。"""

    def __init__(self, responses: dict[str, dict]):
        self._responses = responses
        self.calls: list[str] = []
        self.client = type("C", (), {"status": lambda self: {"online": True}})()

    def status(self):
        return {"online": True}  # 执行器前置检查要求扩展在线

    def page_call(self, tab_id, selector, method, *, args=None, timeout_seconds=1,
                  target_host=None):
        self.calls.append(selector)
        return self._responses.get(
            selector, {"matchedCount": 0, "result": None}
        )


def _write_element(flow_dir: Path, name: str, css: str, candidates: list[dict],
                   matched: int = 1) -> None:
    elements = flow_dir / "elements"
    elements.mkdir(parents=True, exist_ok=True)
    (elements / f"{name}.json").write_text(
        json.dumps(
            {
                "name": name,
                "kind": "browser",
                "selector": {"css": css, "candidates": candidates},
                "metadata": {"matchedCount": matched},
            }
        ),
        encoding="utf-8",
    )


def _invocation(command: str, **inputs) -> CommandInvocation:
    return CommandInvocation(
        command_id=command,
        command_version="1.0.0",
        run_id="run",
        step_id="step",
        inputs=inputs,
    )


def _run(executor: PlaywrightExecutor, invocation: CommandInvocation):
    # 预置会话→tabId（正常由 navigate 建立；这里直接注入以聚焦元素定位路径）
    executor._ext_sessions["s"] = "7"
    executor._last_session_id = "s"
    return asyncio.run(executor.execute(invocation, asyncio.Event()))


def test_falls_back_to_candidate_when_main_selector_misses(tmp_path):
    """主选择器 0 命中 → 按候选顺位重试，命中即成功且证据记录所用候选。"""
    flow_dir = tmp_path / "flows" / "demo"
    _write_element(
        flow_dir, "searchBox", "#kw",
        [
            {"kind": "name", "selector": "input[name=q]", "matchedCount": 1},
            {"kind": "placeholder", "selector": "input[placeholder='搜索']", "matchedCount": 1},
        ],
    )
    ext = _ScriptedExt({
        "#kw": {"matchedCount": 0, "result": None},
        "input[name=q]": {"matchedCount": 1, "result": "ok"},
    })
    executor = PlaywrightExecutor(ext_session=ext, flow_dir=flow_dir)

    result = _run(executor, _invocation("browser.click", selector="#kw", sessionId="s"))

    assert result.status == "success", result.error
    assert ext.calls == ["#kw", "input[name=q]"]  # 命中即止，不再试第三条
    fallback = result.effects[0].details["fallback"]
    assert fallback["mainSelector"] == "#kw"
    assert fallback["usedSelector"] == "input[name=q]"
    assert fallback["kind"] == "name"
    assert fallback["index"] == 1
    assert fallback["candidateCount"] == 2


def test_no_fallback_when_asset_missing_or_selector_mismatch(tmp_path):
    """资产不存在 / selector 不匹配 → 与今天一致（只调用一次，报 NOT_FOUND）。"""
    flow_dir = tmp_path / "flows" / "demo"
    _write_element(flow_dir, "other", ".unrelated", [{"kind": "name", "selector": "x"}])
    ext = _ScriptedExt({})
    executor = PlaywrightExecutor(ext_session=ext, flow_dir=flow_dir)

    result = _run(executor, _invocation("browser.click", selector="#kw", sessionId="s"))

    assert result.status == "error"
    assert result.error.code == "ELEMENT_NOT_FOUND"
    assert ext.calls == ["#kw"]
    assert "candidatesTried" not in result.error.details


def test_all_candidates_miss_reports_count(tmp_path):
    """候选全部未命中 → 仍报 NOT_FOUND，但把「试过 N 条候选」写进错误详情。"""
    flow_dir = tmp_path / "flows" / "demo"
    _write_element(
        flow_dir, "searchBox", "#kw",
        [
            {"kind": "name", "selector": "input[name=q]"},
            {"kind": "placeholder", "selector": "input[placeholder='搜索']"},
        ],
    )
    ext = _ScriptedExt({})
    executor = PlaywrightExecutor(ext_session=ext, flow_dir=flow_dir)

    result = _run(executor, _invocation("browser.getText", selector="#kw", sessionId="s"))

    assert result.status == "error"
    assert ext.calls == ["#kw", "input[name=q]", "input[placeholder='搜索']"]
    assert result.error.details["candidatesTried"] == 2


def test_selector_whitespace_normalized_for_asset_lookup(tmp_path):
    """资产 css 与节点参数只有空白差异时仍能反查到候选（折叠空白后比较）。"""
    flow_dir = tmp_path / "flows" / "demo"
    _write_element(flow_dir, "searchBox", "div  >  #kw", [
        {"kind": "name", "selector": "input[name=q]"},
    ])
    ext = _ScriptedExt({"input[name=q]": {"matchedCount": 1, "result": "ok"}})
    executor = PlaywrightExecutor(ext_session=ext, flow_dir=flow_dir)

    result = _run(executor, _invocation(
        "browser.click", selector="div > #kw", sessionId="s",
    ))

    assert result.status == "success", result.error
    assert ext.calls == ["div > #kw", "input[name=q]"]


def test_no_flow_dir_keeps_behavior_unchanged():
    """没有流程目录（如未注入）→ 自愈不生效，行为与今天一致。"""
    ext = _ScriptedExt({})
    executor = PlaywrightExecutor(ext_session=ext)

    result = _run(executor, _invocation("browser.click", selector="#kw", sessionId="s"))

    assert result.status == "error"
    assert ext.calls == ["#kw"]
