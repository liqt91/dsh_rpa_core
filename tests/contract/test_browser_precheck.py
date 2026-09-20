"""M28 S2 执行前预检契约：命中但不可安全操作时必须显式失败，且报错可定位。

契约（定案）：
- 预检在**扩展侧**执行（`extension/background.js` 的 `domOp`），失败时以结构化
  `precheck: {code, message, blockedBy?}` 随返回值回传，而不是抛异常——异常 message
  会被 `errorCode()` 的文本启发式误判成 ELEMENT_NOT_FOUND。
- 执行器把 `precheck` 翻成稳定错误码（`ELEMENT_COVERED` / `ELEMENT_DISABLED` /
  `ELEMENT_NOT_VISIBLE`），并**带上「谁挡住了」**（`blockedBy` / `blockedByLabel`）。
- 主选择器命中但预检不过 → 不再按候选重试：元素找到了，换候选只会把「点错地方」
  换成「点到另一个元素」，比失败更危险。
- 候选命中但预检不过 → 继续试下一条候选（候选本就是为了绕开定位失效）。

「预检通过」的行为由 `tests/contract/test_browser_element_fallback.py` 覆盖
（payload 无 `precheck`）；本文件只覆盖失败路径与错误整形。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from rpa_core.executors.browser import PlaywrightExecutor
from rpa_core.model.command import CommandInvocation


class _PrecheckExt:
    """按 selector 回预设 payload（可含 precheck）的扩展桩，并记录调用序列。"""

    def __init__(self, responses: dict[str, dict]):
        self._responses = responses
        self.calls: list[str] = []

    def status(self):
        return {"online": True}  # 执行器前置检查要求扩展在线

    def page_call(self, tab_id, selector, method, *, args=None, timeout_seconds=1,
                  target_host=None):
        self.calls.append(selector)
        return self._responses.get(selector, {"matchedCount": 0, "result": None})


def _write_element(flow_dir: Path, name: str, css: str, candidates: list[dict]) -> None:
    elements = flow_dir / "elements"
    elements.mkdir(parents=True, exist_ok=True)
    (elements / f"{name}.json").write_text(
        json.dumps(
            {
                "name": name,
                "kind": "browser",
                "selector": {"css": css, "candidates": candidates},
                "metadata": {"matchedCount": 1},
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
    executor._ext_sessions["s"] = "7"
    executor._last_session_id = "s"
    return asyncio.run(executor.execute(invocation, asyncio.Event()))


def _covered(code: str = "ELEMENT_COVERED") -> dict:
    return {
        "matchedCount": 1,
        "result": None,
        "precheck": {
            "code": code,
            "message": "目标元素被其它元素遮挡，点击会落在遮挡者身上",
            "blockedBy": {"tag": "div", "id": "mask", "className": "overlay  modal"},
            "blockedByLabel": "div#mask.overlay.modal",
        },
    }


def test_covered_click_fails_with_blocker_details():
    """被遮挡的 click 必须显式失败，并说明「谁挡住了」（不再是静默点到遮罩层）。"""
    ext = _PrecheckExt({"#ok": _covered()})
    executor = PlaywrightExecutor(ext_session=ext)

    result = _run(executor, _invocation("browser.click", selector="#ok", sessionId="s"))

    assert result.status == "error", result
    assert result.error.code == "ELEMENT_COVERED"
    assert result.error.details["blockedBy"]["id"] == "mask"
    assert result.error.details["blockedByLabel"] == "div#mask.overlay.modal"


def test_disabled_element_reports_disabled():
    """禁用元素（含 aria-disabled / inert）报 ELEMENT_DISABLED。"""
    ext = _PrecheckExt({
        "#btn": {
            "matchedCount": 1,
            "result": None,
            "precheck": {"code": "ELEMENT_DISABLED", "message": "目标元素处于禁用状态"},
        }
    })
    executor = PlaywrightExecutor(ext_session=ext)

    result = _run(executor, _invocation("browser.click", selector="#btn", sessionId="s"))

    assert result.status == "error"
    assert result.error.code == "ELEMENT_DISABLED"


def test_invisible_element_reports_not_visible():
    """零尺寸/隐藏/不在视口内报 ELEMENT_NOT_VISIBLE（区别于「找不到」）。"""
    ext = _PrecheckExt({
        "#ghost": {
            "matchedCount": 1,
            "result": None,
            "precheck": {"code": "ELEMENT_NOT_VISIBLE", "message": "目标元素不可见"},
        }
    })
    executor = PlaywrightExecutor(ext_session=ext)

    result = _run(executor, _invocation("browser.click", selector="#ghost", sessionId="s"))

    assert result.status == "error"
    assert result.error.code == "ELEMENT_NOT_VISIBLE"


def test_main_hit_precheck_failure_does_not_try_candidates(tmp_path):
    """主选择器命中但预检不过 → 不再按候选重试（换候选等于换一个元素点，更危险）。"""
    flow_dir = tmp_path / "flows" / "demo"
    _write_element(flow_dir, "ok", "#ok", [{"kind": "name", "selector": "button[name=ok]"}])
    ext = _PrecheckExt({
        "#ok": _covered(),
        "button[name=ok]": {"matchedCount": 1, "result": True},
    })
    executor = PlaywrightExecutor(ext_session=ext, flow_dir=flow_dir)

    result = _run(executor, _invocation("browser.click", selector="#ok", sessionId="s"))

    assert result.status == "error"
    assert result.error.code == "ELEMENT_COVERED"
    assert ext.calls == ["#ok"]  # 只调用一次，没去试候选


def test_candidate_precheck_failure_falls_through_to_next(tmp_path):
    """候选命中但预检不过 → 继续试下一条候选，最终报出最后一条候选的预检原因。"""
    flow_dir = tmp_path / "flows" / "demo"
    _write_element(
        flow_dir, "ok", "#gone",
        [
            {"kind": "name", "selector": "button[name=a]"},
            {"kind": "name", "selector": "button[name=b]"},
        ],
    )
    ext = _PrecheckExt({
        "#gone": {"matchedCount": 0, "result": None},
        "button[name=a]": _covered(),
        "button[name=b]": {
            "matchedCount": 1,
            "result": None,
            "precheck": {"code": "ELEMENT_DISABLED", "message": "目标元素处于禁用状态"},
        },
    })
    executor = PlaywrightExecutor(ext_session=ext, flow_dir=flow_dir)

    result = _run(executor, _invocation("browser.click", selector="#gone", sessionId="s"))

    assert result.status == "error"
    assert result.error.code == "ELEMENT_DISABLED"  # 最后一条候选的原因
    assert ext.calls == ["#gone", "button[name=a]", "button[name=b]"]
    assert result.error.details["candidatesTried"] == 2


def test_candidate_covered_then_later_candidate_succeeds(tmp_path):
    """第一条候选被遮挡、第二条候选正常 → 成功，且证据记录所用候选。"""
    flow_dir = tmp_path / "flows" / "demo"
    _write_element(
        flow_dir, "ok", "#gone",
        [
            {"kind": "name", "selector": "button[name=a]"},
            {"kind": "name", "selector": "button[name=b]"},
        ],
    )
    ext = _PrecheckExt({
        "#gone": {"matchedCount": 0, "result": None},
        "button[name=a]": _covered(),
        "button[name=b]": {"matchedCount": 1, "result": True},
    })
    executor = PlaywrightExecutor(ext_session=ext, flow_dir=flow_dir)

    result = _run(executor, _invocation("browser.click", selector="#gone", sessionId="s"))

    assert result.status == "success", result.error
    assert ext.calls == ["#gone", "button[name=a]", "button[name=b]"]
    assert result.effects[0].details["fallback"]["usedSelector"] == "button[name=b]"


def test_read_commands_still_pass_through_without_precheck():
    """读取类命令（getText）不带 precheck 时照旧成功——预检只管可操作动作。"""
    ext = _PrecheckExt({"#label": {"matchedCount": 1, "result": "hello"}})
    executor = PlaywrightExecutor(ext_session=ext)

    result = _run(executor, _invocation("browser.getText", selector="#label", sessionId="s"))

    assert result.status == "success", result.error
    assert result.outputs["value"] == "hello"


def test_drag_main_hit_precheck_failure_reports_blocker():
    """drag 与 click 同口径：主元素被遮挡时显式失败（S1 遗留的 drag 定位已统一）。"""
    ext = _PrecheckExt({"#src": _covered()})
    executor = PlaywrightExecutor(ext_session=ext)

    result = _run(executor, _invocation(
        "browser.drag", selector="#src", targetSelector="#dst", sessionId="s",
    ))

    assert result.status == "error"
    assert result.error.code == "ELEMENT_COVERED"
    assert result.error.details["blockedByLabel"] == "div#mask.overlay.modal"


def test_drag_uses_element_candidates_for_self_healing(tmp_path):
    """drag 主选择器失效时也能靠元素资产候选自愈（与 click/input 一致）。"""
    flow_dir = tmp_path / "flows" / "demo"
    _write_element(flow_dir, "card", "#card", [{"kind": "role", "selector": "div[role=card]"}])
    ext = _PrecheckExt({
        "#card": {"matchedCount": 0, "result": None},
        "div[role=card]": {"matchedCount": 1, "result": True},
    })
    executor = PlaywrightExecutor(ext_session=ext, flow_dir=flow_dir)

    result = _run(executor, _invocation(
        "browser.drag", selector="#card", targetSelector="#dst", sessionId="s",
    ))

    assert result.status == "success", result.error
    assert ext.calls == ["#card", "div[role=card]"]
    assert result.effects[0].details["fallback"]["kind"] == "role"


def test_unknown_precheck_code_falls_back_to_not_found_path():
    """扩展回了未声明的 precheck code → 不误报，退回原来的 matchedCount 判定。"""
    ext = _PrecheckExt({
        "#x": {
            "matchedCount": 0,
            "result": None,
            "precheck": {"code": "SOMETHING_NEW", "message": "未知分类"},
        }
    })
    executor = PlaywrightExecutor(ext_session=ext)

    result = _run(executor, _invocation("browser.click", selector="#x", sessionId="s"))

    assert result.status == "error"
    assert result.error.code == "ELEMENT_NOT_FOUND"
