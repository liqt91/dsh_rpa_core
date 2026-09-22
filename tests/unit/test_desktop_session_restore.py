"""M38 S2.1：桌面会话与元素定位器的跨进程续接（暂停后「继续」要接上原来那个窗口）。

暂停按 ADR 0005 是「干净收口 + run 进程退出」，「继续」= 从 checkpoint 起一个**新进程**
（GUI 的「继续」按钮在暂停已落地时就走 `RunManager.resume`）。桌面窗口**不随 run 进程
退出而消失**，消失的是新进程里的两张表：`sessionId → (pid, hwnd)` 与
`elementId → locator`。不还原的话，暂停点之后的第一个会话类命令就以 `SESSION_NOT_FOUND`
失败，而用户的窗口明明还在那儿。

真机证据在 `tests/e2e/test_desktop_pause_resume.py`（跨进程跑一遍，含真实暂停/继续）。
本文件管**结构性**的一面，重点是写侧与读侧的**反漂移**：

| 缺口 | 写侧（effect） | 读侧（还原） |
|---|---|---|
| 会话 | `<前缀><sid>:window:<hwnd>` + `details.processId` | 按同一个前缀切片 |
| 元素 | `<前缀><sid>:element:<eid>` + **`details.locator`** | 按同一个前缀切片并重建缓存 |

两处缺口都是**实测**出来的：探针第一轮 `SESSION_NOT_FOUND`；补上会话之后同一个节点换成
`ELEMENT_NOT_FOUND`——顺序本身就是证据（会话先接回，元素还没）。

所以这里**不复写字面量**：`desktop.attachWindow` / `desktop.findElement` 的**真实函数体**
被驱动起来写 effect，再把这份 effect 原样交给解析器读回去。只改任意一侧（前缀、operation
名、locator 放不放 details）都会红。
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from rpa_core.executors import DesktopExecutor
from rpa_core.executors.base import (
    UIA_SESSION_RESOURCE_PREFIX,
    WIN32_SESSION_RESOURCE_PREFIX,
    DesktopSessionSnapshot,
    desktop_sessions_from_scopes,
)
from rpa_core.executors.desktop import _DesktopSession
from rpa_core.model.command import CommandInvocation
from rpa_core.model.desktop import DesktopLocator

WIN32_ONLY = pytest.mark.skipif(
    sys.platform != "win32", reason="Win32DesktopExecutor 只在 Windows 上存在"
)

# 一套固定的身份：句柄刻意取一个「不像真句柄」的值，避免误以为在测真机。
HWND = 4242
PID = 777


# --------------------------------------------------------------------------
# 测试替身
# --------------------------------------------------------------------------


class _FakeWindow:
    """`attachWindow` 只用到 `.handle` 与 `.process_id()`。"""

    def __init__(self, handle: int = HWND, process_id: int = PID) -> None:
        self.handle = handle
        self._process_id = process_id

    def process_id(self) -> int:
        return self._process_id


class _FakeElement:
    """`desktop.getText` 只用到 `window_text()`。"""

    def __init__(self, text: str) -> None:
        self._text = text

    def window_text(self) -> str:
        return self._text


class _FakeComError(Exception):
    pass


@pytest.fixture
def com_stubs(monkeypatch):
    """让 `DesktopExecutor._execute_sync` 的真实函数体在无 COM 的环境里跑起来。

    函数体内是 `import pythoncom` / `from comtypes import COMError`（延迟导入，为了
    非 Windows 上还能 import 这个模块）——注入替身即可，不必绕开被测代码。
    """
    monkeypatch.setitem(
        sys.modules,
        "pythoncom",
        types.SimpleNamespace(CoInitialize=lambda: None, CoUninitialize=lambda: None),
    )
    monkeypatch.setitem(
        sys.modules, "comtypes", types.SimpleNamespace(COMError=_FakeComError)
    )


def _invocation(command_id: str, inputs: dict[str, Any], *, step_id: str = "step"):
    return CommandInvocation(
        command_id=command_id,
        command_version="1.0.0",
        run_id="m38-s2-1-restore",
        step_id=step_id,
        inputs=inputs,
    )


# --------------------------------------------------------------------------
# 快照的构造器（手写一侧的用例用它们；写侧驱动用例不经过这里）
# --------------------------------------------------------------------------


def _session_effect(
    session_id: str, handle: int = HWND, *, prefix: str = UIA_SESSION_RESOURCE_PREFIX
) -> dict[str, Any]:
    return {
        "kind": "session",
        "resource": f"{prefix}{session_id}:window:{handle}",
        "details": {"operation": "attachWindow", "processId": PID},
    }


def _close_effect(
    session_id: str, handle: int = HWND, *, prefix: str = UIA_SESSION_RESOURCE_PREFIX
) -> dict[str, Any]:
    return {
        "kind": "session",
        "resource": f"{prefix}{session_id}:window:{handle}",
        "details": {"operation": "closeSession"},
    }


def _element_effect(
    session_id: str,
    element_id: str,
    locator: dict[str, Any],
    *,
    prefix: str = UIA_SESSION_RESOURCE_PREFIX,
) -> dict[str, Any]:
    return {
        "kind": "read",
        "resource": f"{prefix}{session_id}:element:{element_id}",
        "details": {"operation": "findElement", "locator": locator},
    }


def _attach_step(session_id: str = "sid-1") -> dict[str, Any]:
    return {"outputs": {"sessionId": session_id}, "effects": [_session_effect(session_id)]}


def _close_step(session_id: str = "sid-1") -> dict[str, Any]:
    return {"outputs": {"sessionId": session_id}, "effects": [_close_effect(session_id)]}


def _element_step(
    session_id: str, element_id: str, locator: dict[str, Any]
) -> dict[str, Any]:
    return {
        "outputs": {"sessionId": session_id},
        "effects": [_element_effect(session_id, element_id, locator)],
    }


def _scopes(**steps: Any) -> dict[str, Any]:
    return {"steps": dict(steps)}


def _parse(
    scopes: Any, *, prefix: str = UIA_SESSION_RESOURCE_PREFIX
) -> tuple[dict[str, DesktopSessionSnapshot], str | None]:
    """读侧入口的短别名（本文件里到处要用，实测口径不因别名而变）。"""
    return desktop_sessions_from_scopes(scopes, resource_prefix=prefix)


def _writes(effects: list[Any]) -> list[dict[str, Any]]:
    """把真实 `EffectRecord` 转成快照里的形状。"""
    return [effect.model_dump(by_alias=True) for effect in effects]


def _locator_payload(locator: dict[str, Any]) -> dict[str, Any]:
    return DesktopLocator.model_validate(locator).model_dump(by_alias=True)


# --------------------------------------------------------------------------
# 纯函数：从快照里取会话绑定与元素定位器
# --------------------------------------------------------------------------


def test_attach_binds_session_and_find_element_fills_the_cache():
    locator = {"automationId": "queryInput", "controlType": "Edit"}
    sessions, last = _parse(
        _scopes(
            attachMain=_attach_step(),
            findInput=_element_step("sid-1", "eid-1", locator),
        )
    )
    assert sessions == {
        "sid-1": DesktopSessionSnapshot(
            process_id=PID, window_handle=HWND, elements={"eid-1": locator}
        )
    }
    assert last == "sid-1"


def test_close_session_is_unbound_and_clears_elements():
    """`closeSession` 是解绑——已关的会话不该复活，否则续跑会把命令发到它上面。"""
    sessions, last = _parse(
        _scopes(
            attachMain=_attach_step(),
            findInput=_element_step("sid-1", "eid-1", {"automationId": "a"}),
            closeMain=_close_step(),
        )
    )
    assert sessions == {}
    assert last is None, "最后活跃会话已被解绑，不该再被当作缺省会话"


def test_elements_are_attached_even_if_they_appear_before_attach():
    """元素先于 attach 出现也要归位：合并是「先收元素、最后统一合并」，不赌 steps 顺序。"""
    locator = {"automationId": "queryInput"}
    sessions, _ = _parse(
        _scopes(
            findInput=_element_step("sid-1", "eid-1", locator),
            attachMain=_attach_step(),
        )
    )
    assert sessions["sid-1"].elements == {"eid-1": locator}


def test_find_element_without_locator_is_not_restored():
    """旧快照（本片之前产的）没有 `details.locator`——该元素不被还原，是如实的降级。"""
    effect = _element_effect("sid-1", "eid-1", {"automationId": "a"})
    effect["details"].pop("locator")
    sessions, _ = _parse(
        _scopes(
            attachMain=_attach_step(),
            findInput={"outputs": {"sessionId": "sid-1"}, "effects": [effect]},
        )
    )
    assert sessions["sid-1"].elements == {}


def test_the_two_backends_prefixes_are_isolated():
    """两后端各一份前缀：uia 的快照不该被 win32 的还原读进来，反之亦然。"""
    uia = _scopes(a=_attach_step())
    win32 = _scopes(
        a={
            "outputs": {"sessionId": "sid-1"},
            "effects": [_session_effect("sid-1", prefix=WIN32_SESSION_RESOURCE_PREFIX)],
        }
    )

    assert _parse(uia, prefix=WIN32_SESSION_RESOURCE_PREFIX) == ({}, None)
    assert _parse(win32) == ({}, None)
    # 各读各的都成立——隔离来自前缀本身，不是来自某一边恰好为空
    assert _parse(uia)[0] != {}
    assert _parse(win32, prefix=WIN32_SESSION_RESOURCE_PREFIX)[0] != {}


def test_bindings_are_fail_safe_on_garbage():
    """快照是外部文件，坏数据不该炸掉恢复过程（与 control_channel 同口径）。"""
    prefix = UIA_SESSION_RESOURCE_PREFIX
    garbage = [
        None,
        [],
        {},
        {"steps": None},
        {"steps": []},
        {"steps": {"a": None}},
        {"steps": {"a": {"effects": "nope"}}},
        {"steps": {"a": {"effects": [None, {}, {"resource": 42}]}}},
        # 前缀对但结构不完整：缺 window 段 / 句柄不是整数 / 句柄为 0 / pid 缺失
        _scopes(a={"effects": [{"resource": f"{prefix}sid-1",
                                "details": {"operation": "attachWindow"}}]}),
        _scopes(a={"effects": [{"resource": f"{prefix}sid-1:window:abc",
                                "details": {"operation": "attachWindow", "processId": PID}}]}),
        _scopes(a={"effects": [{"resource": f"{prefix}sid-1:window:0",
                                "details": {"operation": "attachWindow", "processId": PID}}]}),
    ]
    for scopes in garbage:
        assert _parse(scopes) == ({}, None)


def test_last_active_session_must_exist_in_the_snapshot():
    """`last_sid` 指向没被还原的会话时要置空，否则后续命令会在缺省会话上白跑一趟。"""
    sessions, last = _parse(
        _scopes(
            attachMain={"outputs": {"sessionId": "sid-1"}, "effects": []},
            closeMain=_close_step(),
        )
    )
    assert sessions == {}
    assert last is None


# --------------------------------------------------------------------------
# 写侧 / 读侧反漂移：驱动**真实** attachWindow / findElement 产出的 effect
# --------------------------------------------------------------------------


def test_attach_window_writes_the_prefix_the_restore_reads(com_stubs, monkeypatch):
    """写侧前缀与读侧常量必须一致——改任意一侧，这条红。"""
    executor = DesktopExecutor()
    monkeypatch.setattr(
        executor, "_find_windows_by_title", lambda *args, **kwargs: [_FakeWindow()]
    )

    attached = executor._execute_sync(
        _invocation("desktop.attachWindow", {"title": "Demo"})
    )
    assert attached.status == "success", attached

    session_id = attached.outputs["sessionId"]
    effect = attached.effects[0]
    assert effect.details == {"operation": "attachWindow", "processId": PID}
    assert effect.resource == f"{UIA_SESSION_RESOURCE_PREFIX}{session_id}:window:{HWND}"

    # 把这条**真实** effect 原样交给解析器（就是快照里的形状）
    sessions, last = _parse(
        _scopes(attachMain={"outputs": attached.outputs, "effects": _writes(attached.effects)})
    )
    assert sessions == {session_id: DesktopSessionSnapshot(PID, HWND)}
    assert last == session_id


def test_find_element_writes_the_locator_the_restore_reads(com_stubs, monkeypatch):
    """元素侧的同一件事：`details.locator` 是那个 `elementId` 指向什么的**唯一**来源。"""
    executor = DesktopExecutor()
    monkeypatch.setattr(
        executor, "_find_windows_by_title", lambda *args, **kwargs: [_FakeWindow()]
    )
    attached = executor._execute_sync(
        _invocation("desktop.attachWindow", {"title": "Demo"})
    )
    session_id = attached.outputs["sessionId"]

    locator = {"automationId": "queryInput", "controlType": "Edit"}
    monkeypatch.setattr(executor, "_window_by_handle", lambda handle: _FakeWindow())
    monkeypatch.setattr(executor, "_find", lambda window, loc: [_FakeElement("hello")])
    found = executor._execute_sync(
        _invocation("desktop.findElement", {"sessionId": session_id, "locator": locator})
    )
    assert found.status == "success", found

    element_id = found.outputs["elementId"]
    effect = found.effects[0]
    assert effect.resource == f"{UIA_SESSION_RESOURCE_PREFIX}{session_id}:element:{element_id}"
    assert effect.details["operation"] == "findElement"
    assert effect.details["locator"] == _locator_payload(locator)

    # 端到端：真实写出的两条 effect → 还原 → 元素缓存里有它
    scopes = _scopes(
        attachMain={"outputs": attached.outputs, "effects": _writes(attached.effects)},
        findInput={"outputs": found.outputs, "effects": _writes(found.effects)},
    )
    revived = DesktopExecutor()
    monkeypatch.setattr(
        "rpa_core.executors.desktop.desktop_window_alive", lambda pid, hwnd: True
    )
    revived.restore_from_scopes(scopes)
    assert revived._sessions[session_id].elements == {
        element_id: _locator_payload(locator)
    }


def test_element_id_from_the_old_process_resolves_after_restore(com_stubs, monkeypatch):
    """还原出来的 `elementId` 要真的能被后续命令解析——这才叫「接上」而不是「存下来了」。"""
    locator = {"automationId": "queryInput", "controlType": "Edit"}
    scopes = _scopes(
        attachMain=_attach_step(),
        findInput=_element_step("sid-1", "eid-1", locator),
    )

    executor = DesktopExecutor()
    monkeypatch.setattr(
        "rpa_core.executors.desktop.desktop_window_alive", lambda pid, hwnd: True
    )
    executor.restore_from_scopes(scopes)
    assert executor._last_session_id == "sid-1", "还原后应成为缺省会话"

    resolved: list[Any] = []

    def _find(window: Any, wanted: Any) -> list[Any]:
        resolved.append(wanted.model_dump(by_alias=True))
        return [_FakeElement("hello")]

    monkeypatch.setattr(executor, "_window_by_handle", lambda handle: _FakeWindow())
    monkeypatch.setattr(executor, "_find", _find)
    result = executor._execute_sync(
        _invocation("desktop.getText", {"sessionId": "sid-1", "elementId": "eid-1"})
    )
    assert result.status == "success", result
    assert result.outputs["value"] == "hello"
    # 关键：拿去查找的定位器就是暂停前那次 findElement 的定位器，不是空壳
    assert resolved == [_locator_payload(locator)]


def test_session_only_restore_leaves_the_element_gap_visible(com_stubs, monkeypatch):
    """对照组：只还原会话、元素缓存为空 → 报的是 `ELEMENT_NOT_FOUND`。

    这正是探针里的第二处缺口：会话补上之后，同一个节点从 `SESSION_NOT_FOUND`
    换成这个码。若将来有人把元素还原删掉，上面那条会红，而这条会「变绿得合理」——
    两条一起看才说明缺口的位置。
    """
    executor = DesktopExecutor()
    monkeypatch.setattr(
        "rpa_core.executors.desktop.desktop_window_alive", lambda pid, hwnd: True
    )
    executor.restore_from_scopes(_scopes(attachMain=_attach_step()))

    result = executor._execute_sync(
        _invocation("desktop.getText", {"sessionId": "sid-1", "elementId": "eid-1"})
    )
    assert result.status == "error"
    assert "Desktop element not found" in result.error.message


def test_window_gone_is_not_restored(monkeypatch):
    """窗口已消失（或句柄被回收复用给了别人）时不还原。

    宁可后续命令报 `SESSION_NOT_FOUND`，也不把命令指到别人的窗口上。浏览器侧刻意
    不这么做，理由是那边 tabId 是长随机串、没有句柄复用这回事（见
    `base.desktop_window_alive` 的取舍说明）。
    """
    executor = DesktopExecutor()
    monkeypatch.setattr(
        "rpa_core.executors.desktop.desktop_window_alive", lambda pid, hwnd: False
    )
    executor.restore_from_scopes(_scopes(attachMain=_attach_step()))
    assert executor._sessions == {}
    assert executor._last_session_id is None


def test_restore_is_additive_not_a_reset(monkeypatch):
    """恢复是叠加：同一执行器上已建立的会话不该被踩掉（resume 也可能发生在同进程内）。"""
    executor = DesktopExecutor()
    executor._sessions["live-sid"] = _DesktopSession(
        process_id=1, window_handle=2, elements={}
    )
    executor._last_session_id = "live-sid"

    monkeypatch.setattr(
        "rpa_core.executors.desktop.desktop_window_alive", lambda pid, hwnd: True
    )
    executor.restore_from_scopes(_scopes(attachMain=_attach_step()))
    assert set(executor._sessions) == {"live-sid", "sid-1"}
    assert executor._last_session_id == "sid-1"


# --------------------------------------------------------------------------
# win32 后端：与 uia 同构，前缀不同（同一套断言，防两后端各自漂移）
# --------------------------------------------------------------------------


class _FakeDesktopFactory:
    """`Desktop(backend="win32")` 的替身，只实现 `.windows(handle=...)`。"""

    def __init__(self, windows: list[Any]) -> None:
        self._windows = windows

    def windows(self, handle: int | None = None) -> list[Any]:
        return list(self._windows)


@WIN32_ONLY
def test_win32_backend_prefix_round_trips(monkeypatch):
    from rpa_core.executors import desktop_win32

    executor = desktop_win32.Win32DesktopExecutor()
    monkeypatch.setattr(
        desktop_win32, "Desktop", lambda **kwargs: _FakeDesktopFactory([_FakeWindow()])
    )
    monkeypatch.setattr(
        "rpa_core.executors.desktop_win32.desktop_window_alive", lambda pid, hwnd: True
    )

    # handle 给定 → 走 `Desktop(backend="win32").windows(handle=...)` 那条分支
    attached = executor._execute_sync(
        _invocation("desktop.win32.attachWindow", {"handle": HWND})
    )
    assert attached.status == "success", attached
    session_id = attached.outputs["sessionId"]
    assert attached.effects[0].resource == (
        f"{WIN32_SESSION_RESOURCE_PREFIX}{session_id}:window:{HWND}"
    )

    locator = {"backend": "win32", "title": "Name", "className": "Edit"}
    monkeypatch.setattr(executor, "_find", lambda window, loc: [_FakeElement("hello")])
    found = executor._execute_sync(
        _invocation(
            "desktop.win32.findElement", {"sessionId": session_id, "locator": locator}
        )
    )
    assert found.status == "success", found
    element_id = found.outputs["elementId"]
    assert found.effects[0].resource == (
        f"{WIN32_SESSION_RESOURCE_PREFIX}{session_id}:element:{element_id}"
    )
    assert found.effects[0].details["locator"] == _locator_payload(locator)

    # 用**写侧产出的真实 effect** 走一遍 win32 的还原
    scopes = _scopes(
        attachMain={"outputs": attached.outputs, "effects": _writes(attached.effects)},
        findInput={"outputs": found.outputs, "effects": _writes(found.effects)},
    )
    revived = desktop_win32.Win32DesktopExecutor()
    revived.restore_from_scopes(scopes)
    assert revived._sessions[session_id].elements == {
        element_id: _locator_payload(locator)
    }
    assert revived._last_session_id == session_id

    # 而且 uia 的读侧读不出 win32 的快照（前缀隔离在两个方向上都有用例）
    assert _parse(scopes) == ({}, None)
