"""桌面通道参数契约（M30 S2：把「声明了不生效」的 `timeoutMs` 做成真的等待预算）。

背景：`desktop.{click,getText,input}` 与 `desktop.win32.{click,getText,input}` 都声明了
`timeoutMs`，但实现是一次性 `_find`——元素还没渲染出来就直接 `ELEMENT_NOT_FOUND`；
而浏览器通道同一个参数是**真等**（共享定位器把它当等待选择器的超时）。同一份语义在两个
通道里行为相反，这条测试锁三件事：

1. 等待语义本身：轮询、超时、**零预算时一次都不多查**（向后兼容是硬要求）；
2. `timeoutMs` 同时抬高**节点超时**与**执行器操作超时**的上限——否则用户设 30s 会在引擎默认的
   15s 收到 `TIMEOUT`，报错原因看上去是「超时」而不是「元素没出现」（同一个参数在两层里打架）；
3. `desktop.win32.hotkey` / `menuSelect` 不再声明 `timeoutMs`：全局按键（`send_keys`）与同步
   走菜单栏（`_menu_select`）**没有「目标元素」可等**，兑现不了就删掉，不留假开关。

真机边界：桌面 E2E 会抢前台，AGENTS 已定「按需启用」。执行器路径用打桩的 `_find` 与伪元素覆盖
逻辑分支，**不冒充真机结论**（等待真实窗口/控件的时序仍待维护者需要时按任务单清单复验）。
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from rpa_core.catalog import load_catalog
from rpa_core.executors.base import wait_for_element
from rpa_core.model.command import CommandInvocation, resolve_node_timeout_seconds

ROOT = Path(__file__).resolve().parents[2]

# 会等元素的命令（两后端同名）：timeoutMs 的语义 = 等待目标元素存在的最长时间
ELEMENT_WAIT_COMMANDS = ("click", "getText", "input")
# 只有 win32 后端有的命令：没有「目标元素」可等
WIN32_ONLY_COMMANDS = ("hotkey", "menuSelect")


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(ROOT / "commands")


# ---------------------------------------------------------------- 1. 等待语义


def test_zero_budget_queries_once_without_sleeping():
    """未声明等待预算（或给 0）时：只查一次，且**不引入任何等待**——行为与修复前完全一致。"""
    calls: list[int] = []

    def find():
        calls.append(1)
        return []

    started = time.perf_counter()
    outcome = wait_for_element(find, 0)
    outcome_none = wait_for_element(find, None)
    elapsed = time.perf_counter() - started

    assert outcome.matched is False
    assert outcome.polls == 1
    assert outcome_none.polls == 1
    assert len(calls) == 2  # 两次调用各查一次，没有额外轮询
    assert elapsed < 0.09  # 小于一个轮询间隔：证明没睡过


def test_late_match_waits_until_element_appears():
    calls: list[int] = []

    def find():
        calls.append(1)
        return [object()] if len(calls) >= 3 else []

    outcome = wait_for_element(find, 400, poll_interval_seconds=0.05)

    assert outcome.matched is True
    assert outcome.polls == 3
    assert len(outcome.matches) == 1
    # 经过两次 50ms 轮询间隔，真的等了。下界用 0.9× 名义值：
    # 断言的是「确实等了两次间隔」，不是「sleep 有多准」——后者在 Windows 上本来就有抖动。
    assert outcome.waited_ms >= 90


def test_exhausted_budget_reports_what_it_waited():
    """等不到时要能说清「等了多久、查了几次」，而不是一句 ELEMENT_NOT_FOUND。"""
    started = time.perf_counter()
    outcome = wait_for_element(lambda: [], 250, poll_interval_seconds=0.05)
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert outcome.matched is False
    assert outcome.matches == []
    assert outcome.polls >= 2
    assert outcome.waited_ms >= 200  # 真的等满了，不是「声称等过」
    assert elapsed_ms >= 200
    assert outcome.details()["waitedMs"] >= 200
    assert outcome.details()["polls"] >= 2


def test_match_set_is_preserved_for_index_picking():
    """保留整个匹配集合：win32 后端还要按 found_index 选序，只回传第一个会吃掉那个语义。"""
    first, second = object(), object()

    outcome = wait_for_element(lambda: [first, second], 100)

    assert outcome.matched is True
    assert outcome.matches == [first, second]


# ------------------------------------------------- 2. 各超时层尊重等待预算


def test_wait_budget_raises_node_timeout_above_manifest_default(catalog):
    """引擎节点超时不得反过来掐断用户显式给出的等待预算。"""
    manifest = catalog["desktop.click"]
    assert manifest.declares_wait_budget() is True
    assert manifest.default_timeout_seconds == 15

    # 未给 timeoutMs → 完全不变
    assert resolve_node_timeout_seconds(manifest, {}, None) == 15
    # 给 30s → 节点超时必须 ≥ 31s（预算 + 1s 余量）
    assert resolve_node_timeout_seconds(manifest, {"timeoutMs": 30_000}, None) == 31.0
    # 节点显式超时更宽 → 以节点为准
    assert resolve_node_timeout_seconds(manifest, {"timeoutMs": 5_000}, 60) == 60
    # 节点显式超时更窄 → 让位给等待预算（这正是「两个超时打架」要修的一侧）
    assert resolve_node_timeout_seconds(manifest, {"timeoutMs": 30_000}, 5) == 31.0


def test_wait_budget_values_are_normalized(catalog):
    manifest = catalog["desktop.getText"]

    assert manifest.wait_budget_ms({"timeoutMs": 4_000}) == 4_000
    assert manifest.wait_budget_ms({}) == 0
    assert manifest.wait_budget_ms(None) == 0
    for bad in ("abc", -5, 0, "", None):
        assert manifest.wait_budget_ms({"timeoutMs": bad}) == 0


def test_command_without_wait_budget_ignores_stray_value(catalog):
    """没声明 timeoutMs 的命令，输入里残留同名值也不改变任何超时（不构成隐藏的第二套语义）。"""
    manifest = catalog["desktop.win32.hotkey"]

    assert manifest.declares_wait_budget() is False
    assert manifest.wait_budget_ms({"timeoutMs": 60_000}) == 0
    assert resolve_node_timeout_seconds(manifest, {"timeoutMs": 60_000}, None) == 15


# ------------------------------------------------------------- 3. manifest 面


def _manifest(backend: str, name: str) -> dict:
    return json.loads(
        (ROOT / "commands" / backend / f"{name}.json").read_text(encoding="utf-8")
    )


def test_element_commands_declare_wait_budget_on_both_backends():
    for name in ELEMENT_WAIT_COMMANDS:
        for backend, prefix in (("desktop", "desktop"), ("desktop_win32", "desktop.win32")):
            properties = _manifest(backend, name)["input_schema"]["properties"]
            assert "timeoutMs" in properties, f"{prefix}.{name}"
            assert properties["timeoutMs"]["type"] == "integer"


def test_win32_commands_without_target_element_drop_wait_budget():
    """兑现不了的参数删掉：`send_keys` 是全局按键、`_menu_select` 同步走菜单栏，没有目标可等。

    删掉之后 GUI 会恢复渲染节点级「超时（秒）」字段（`gui/param_form.py` 的
    `has_own_timeout` 见到 `timeoutMs` 就隐藏它），于是「节点到底有没有超时」重新变得可回答。
    """
    for name in WIN32_ONLY_COMMANDS:
        properties = _manifest("desktop_win32", name)["input_schema"]["properties"]
        assert "timeoutMs" not in properties, name


# ------------------------------------------------- 4. 执行器路径（打桩，非真机）


def _session(elements: dict[str, dict]) -> object:
    from rpa_core.executors.desktop import _DesktopSession

    return _DesktopSession(process_id=1, window_handle=2, elements=elements)


def _armed_executor(appear_at: int):
    """装好会话与 `_find` 桩：第 `appear_at` 次查找才命中。"""
    from rpa_core.executors.desktop import DesktopExecutor

    executor = DesktopExecutor()
    executor._sessions["s"] = _session({"el": {"automationId": "nameInput", "controlType": "Edit"}})
    executor._window_by_handle = lambda handle: object()  # type: ignore[method-assign]

    calls = {"count": 0}
    element = MagicMock()

    def fake_find(window, locator):
        calls["count"] += 1
        return [element] if calls["count"] >= appear_at else []

    executor._find = fake_find  # type: ignore[method-assign]
    return executor, calls, element


def _execute(executor, command_id: str, inputs: dict):
    invocation = CommandInvocation(
        command_id=command_id,
        command_version="1.0.0",
        run_id="run",
        step_id="step",
        inputs=inputs,
    )
    return asyncio.run(executor.execute(invocation, asyncio.Event()))


DESKTOP_ONLY = pytest.mark.skipif(
    sys.platform != "win32", reason="DesktopExecutor 在非 win32 上是 PLATFORM_UNSUPPORTED 占位"
)


@DESKTOP_ONLY
def test_click_waits_for_element_that_appears_late():
    """修复前这里必然失败：一次性查找在元素出现前就报 ELEMENT_NOT_FOUND。"""
    executor, calls, element = _armed_executor(appear_at=3)

    result = _execute(
        executor, "desktop.click", {"sessionId": "s", "elementId": "el", "timeoutMs": 400}
    )

    assert result.status == "success", result.error
    assert calls["count"] == 3
    element.click_input.assert_called_once()


@DESKTOP_ONLY
def test_click_without_budget_stays_single_shot():
    executor, calls, _element = _armed_executor(appear_at=2)

    result = _execute(executor, "desktop.click", {"sessionId": "s", "elementId": "el"})

    assert result.status == "error"
    assert result.error.code == "ELEMENT_NOT_FOUND"
    assert calls["count"] == 1  # 未声明等待预算 → 不引入额外轮询
    assert result.error.details["polls"] == 1


@DESKTOP_ONLY
def test_exhausted_wait_surfaces_waited_ms_and_polls():
    executor, calls, _element = _armed_executor(appear_at=999)

    result = _execute(
        executor, "desktop.click", {"sessionId": "s", "elementId": "el", "timeoutMs": 250}
    )

    assert result.status == "error"
    assert result.error.code == "ELEMENT_NOT_FOUND"
    assert calls["count"] >= 2
    assert result.error.details["waitedMs"] >= 200
    assert result.error.details["polls"] >= 2


@DESKTOP_ONLY
def test_input_command_shares_the_same_wait_semantics(monkeypatch):
    """desktop.input 与 click 共享等待预算；clipboard 模式断言「调度了粘贴动作」。

    M36 事故：本用例最初只桩了 `_find`，剪贴板写入与 `send_keys("^v")` 走了**真实
    全局路径**——每跑一次套件就清空维护者剪贴板、向前台聚焦的输入框粘贴一次
    "hi"（2026-09-21 维护者实证）。现在剪贴板与键击全部打桩（conftest 的 autouse
    守卫 `_block_global_input` 也会硬拦真实调用）；「粘贴真的落在目标控件」属于
    真机效果，归 `RPA_DESKTOP_E2E=1` 的桌面 E2E，不冒充。
    """
    import pywinauto
    import win32clipboard

    synth: list[tuple] = []
    monkeypatch.setattr(win32clipboard, "OpenClipboard", lambda: synth.append(("open",)))
    monkeypatch.setattr(win32clipboard, "EmptyClipboard", lambda: synth.append(("empty",)))
    monkeypatch.setattr(
        win32clipboard, "SetClipboardText",
        lambda text, fmt=None: synth.append(("set", text)),
    )
    monkeypatch.setattr(win32clipboard, "CloseClipboard", lambda: synth.append(("close",)))
    monkeypatch.setattr(
        pywinauto.keyboard, "send_keys", lambda keys: synth.append(("keys", keys))
    )

    executor, calls, element = _armed_executor(appear_at=2)

    result = _execute(
        executor,
        "desktop.input",
        {"sessionId": "s", "elementId": "el", "text": "hi", "mode": "clipboard", "timeoutMs": 300},
    )

    assert result.status == "success", result.error
    assert calls["count"] == 2
    element.set_focus.assert_called()
    assert ("set", "hi") in synth, "剪贴板应被（桩）写入待输入文本"
    assert ("keys", "^v") in synth, "clipboard 模式应调度一次 Ctrl+V 粘贴"
