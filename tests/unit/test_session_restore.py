"""M21：浏览器会话跨进程续接（暂停后「继续」要能接上原来的标签页）。

暂停按 ADR 0005 是「干净收口 + run 进程退出」，「继续」= 从 checkpoint 起新进程。
扩展通道的会话是用户浏览器里的标签页句柄，**不随 run 进程退出而消失**，但
`sessionId → tabId` 的映射是进程内字典——不重建就会以「缺少有效会话」失败。
这里锁定重建逻辑与它带来的行为差异。
"""

import asyncio
import json
from pathlib import Path

from rpa_core.executors import ExecutorRegistry
from rpa_core.executors.base import CommandExecutor
from rpa_core.executors.browser import PlaywrightExecutor, session_bindings_from_scopes
from rpa_core.model.command import CommandInvocation, CommandResult

# 真实的 navigate 快照形态（取自 run_artifacts 里的 checkpoint.json）
SID = "70634944-6791-42f3-9abe-2ac281771e63"
TAB = "285306392"
INSTANCE = "b5530743-eff1-4669-abbe-e444b46cf140"

NAVIGATE_STEP = {
    "value": None,
    "outputs": {
        "sessionId": SID,
        "url": "https://www.baidu.com/",
        "resourceType": "webPage",
        "browserInstance": INSTANCE,
        "browserType": "msedge",
        "tabId": TAB,
    },
    "effects": [
        {
            "kind": "session",
            "status": "committed",
            "resource": f"browser.session:{SID}",
            "details": {
                "operation": "navigate",
                "transport": "extension",
                "tabId": TAB,
                "url": "https://www.baidu.com/",
            },
        }
    ],
}


class _FakeExt:
    """只暴露执行路径用到的三个方法。"""

    def __init__(self):
        self.status_calls = 0
        self.page_calls: list[tuple] = []

    def status(self):
        self.status_calls += 1
        return {"online": True, "hosts": ["msedge"]}

    def page_call(self, tab_id, selector, method, *, args=None, timeout_seconds=None,
                  target_host=None):
        self.page_calls.append((tab_id, method, target_host))
        return {"matchedCount": 1, "result": "hello"}


def _invocation(command_id: str, inputs: dict) -> CommandInvocation:
    return CommandInvocation(
        command_id=command_id,
        command_version="1.0.0",
        run_id="m21-restore",
        step_id="step",
        inputs=inputs,
    )


# --------------------------------------------------------------------------
# 纯函数：从快照里取会话绑定
# --------------------------------------------------------------------------


def test_bindings_come_from_effects_and_outputs():
    tabs, hosts, last = session_bindings_from_scopes({"steps": {"navigate": NAVIGATE_STEP}})
    assert tabs == {SID: TAB}
    assert hosts == {SID: INSTANCE}
    assert last == SID


def test_attach_without_browser_instance_still_restores_tab():
    """attach 拿到的会话没有 browserInstance（所属实例未知），但 tabId 必须还原。"""
    scopes = {"steps": {"att": {
        "outputs": {"sessionId": "sid-attach", "url": "https://x/", "resourceType": "webPage"},
        "effects": [{
            "kind": "session", "resource": "browser.session:sid-attach",
            "details": {"operation": "attach", "tabId": "777"},
        }],
    }}}
    tabs, hosts, last = session_bindings_from_scopes(scopes)
    assert tabs == {"sid-attach": "777"}
    assert hosts == {}
    assert last == "sid-attach"


def test_detached_session_is_not_restored():
    """browser.close 是解绑（operation=detach）——快照里该会话不应复活。"""
    scopes = {"steps": {
        "navigate": NAVIGATE_STEP,
        "close": {
            "outputs": {"sessionId": SID},
            "effects": [{
                "kind": "session", "resource": f"browser.session:{SID}",
                "details": {"operation": "detach"},
            }],
        },
    }}
    tabs, hosts, last = session_bindings_from_scopes(scopes)
    assert tabs == {}
    assert hosts == {}
    assert last is None, "最后活跃会话已被解绑，不该再被当作缺省会话"


def test_bindings_survive_the_real_checkpoint_shape(tmp_path):
    """用一份真实落盘的 checkpoint.json 走一遍（防 snapshot 结构与代码认知漂移）。"""
    run_dir = Path(__file__).resolve().parents[2] / "run_artifacts" / (
        "ef754194-ec81-4c1d-a605-3c7eff3a4afd"
    )
    checkpoint = run_dir / "checkpoint.json"
    if not checkpoint.is_file():
        return  # 该 run 产物是开发期本地证据，不进版本库
    scopes = json.loads(checkpoint.read_text(encoding="utf-8"))["scopes"]
    tabs, hosts, last = session_bindings_from_scopes(scopes)
    assert list(tabs) == ["70634944-6791-42f3-9abe-2ac281771e63"]
    assert list(tabs.values()) == ["285306392"]
    assert hosts == {"70634944-6791-42f3-9abe-2ac281771e63":
                     "b5530743-eff1-4669-abbe-e444b46cf140"}


def test_bindings_are_fail_safe_on_garbage():
    for scopes in (None, [], {}, {"steps": None}, {"steps": []},
                   {"steps": {"a": None}}, {"steps": {"a": {"effects": "nope"}}},
                   {"steps": {"a": {"effects": [None, {}, {"resource": 42}]}}}):
        assert session_bindings_from_scopes(scopes) == ({}, {}, None)


# --------------------------------------------------------------------------
# 执行器：恢复前失败、恢复后可用
# --------------------------------------------------------------------------


def test_unrestored_session_reports_missing_session():
    """对照组：不重建映射时（= 当前 resume 的真实缺口），后续命令直接失败。"""
    ext = _FakeExt()
    executor = PlaywrightExecutor(ext_session=ext)

    result = asyncio.run(executor.execute(
        _invocation("browser.getText", {"sessionId": SID, "selector": "#a"}),
        asyncio.Event(),
    ))
    assert result.status == "error"
    assert "缺少有效会话" in result.error.message
    assert ext.page_calls == []


def test_restored_session_is_usable_and_routes_to_original_tab():
    ext = _FakeExt()
    executor = PlaywrightExecutor(ext_session=ext)
    executor.restore_from_scopes({"steps": {"navigate": NAVIGATE_STEP}})

    # 显式带 sessionId：路由到恢复出来的 tabId 与原实例
    explicit = asyncio.run(executor.execute(
        _invocation("browser.getText", {"sessionId": SID, "selector": "#a"}),
        asyncio.Event(),
    ))
    assert explicit.status == "success"
    assert explicit.outputs["value"] == "hello"
    assert ext.page_calls == [(TAB, "getText", INSTANCE)]

    # 省略 sessionId（流程里更常见）：按恢复的「最后活跃会话」回退，同样命中
    implicit = asyncio.run(executor.execute(
        _invocation("browser.getText", {"selector": "#b"}),
        asyncio.Event(),
    ))
    assert implicit.status == "success"
    assert ext.page_calls[-1] == (TAB, "getText", INSTANCE)


def test_restore_keeps_existing_bindings_untouched():
    """恢复是叠加，不是清空：同一执行器上已建立的会话不该被踩掉。"""
    executor = PlaywrightExecutor(ext_session=_FakeExt())
    executor._ext_sessions["live-sid"] = "999"
    executor.restore_from_scopes({"steps": {"navigate": NAVIGATE_STEP}})
    assert executor._ext_sessions == {"live-sid": "999", SID: TAB}


# --------------------------------------------------------------------------
# 注册表钩子：尽力而为，绝不阻断 resume
# --------------------------------------------------------------------------


class _RestoringExecutor(CommandExecutor):
    def __init__(self):
        self.seen = None

    def restore_from_scopes(self, scopes):
        self.seen = scopes

    async def execute(self, invocation, cancellation):
        return CommandResult.success()


class _ExplodingExecutor(CommandExecutor):
    def restore_from_scopes(self, scopes):
        raise RuntimeError("boom")

    async def execute(self, invocation, cancellation):
        return CommandResult.success()


class _PlainExecutor(CommandExecutor):
    """未实现恢复钩子：必须被安静跳过。"""

    async def execute(self, invocation, cancellation):
        return CommandResult.success()


def test_registry_calls_restore_hook_best_effort():
    restoring = _RestoringExecutor()
    registry = ExecutorRegistry({
        "a": restoring,
        "b": _ExplodingExecutor(),
        "c": _PlainExecutor(),
    })
    scopes = {"steps": {"navigate": NAVIGATE_STEP}}
    registry.restore_from_scopes(scopes)  # 不得抛
    assert restoring.seen == scopes
