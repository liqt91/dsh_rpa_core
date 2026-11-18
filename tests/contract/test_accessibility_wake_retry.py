"""M87：无障碍唤醒必须能**反复唤醒**，且要在 hit-test 之前发生。

2026-11-17 真机报障：「有几秒钟可以捕获微信控件了，但是之后又失效了，只能捕获外框」。

两个独立缺陷叠在一起：

1. ``_WAKE_ATTEMPTED`` 是「每进程只试一次」。微信会在若干秒后自己把 gate 关回 0
   （trace 里 ``before=0`` 就是证据），于是第一帧之后永远不再重试——树死了没人救。
2. 唤醒调用写在 ``_hover_hit`` 的 ``try`` 里、``_element_from_point`` **之后**。树休眠时
   ``ElementFromPoint`` 可能直接抛 ``E_ACCESSDENIED``，异常跳进 ``except: pass``，唤醒
   永远轮不到 ⇒ 「休眠 → 命中失败 → 不唤醒 → 一直休眠」的死锁，用户只看到外框。

判据刻意不验「函数被调用过」，而是验**时间维度上的行为**：节流窗口内不重复开句柄、
窗口过后必须再试、``force`` 必须绕过节流、不可恢复的失败不刷日志。
"""

from __future__ import annotations

from dataclasses import dataclass

from rpa_core.capture import accessibility, desktop_agent
from rpa_core.capture.accessibility import WakeResult


@dataclass
class _Clock:
    now: float = 1000.0

    def __call__(self) -> float:
        return self.now


def _setup(monkeypatch, results: list[WakeResult]):
    """把唤醒的外部依赖全换成可观测的替身；返回 (调用记录, 时钟, trace 记录)。"""
    calls: list[tuple[int, float]] = []
    clock = _Clock()
    traces: list[tuple[str, dict]] = []

    def fake_activate(hwnd: int, **_kw) -> WakeResult:
        calls.append((hwnd, clock.now))
        return results[min(len(calls) - 1, len(results) - 1)]

    monkeypatch.setattr(desktop_agent, "_WAKE_ACCESSIBILITY", True)
    monkeypatch.setattr(desktop_agent, "_WAKE_LAST", {})
    monkeypatch.setattr(desktop_agent, "_WAKE_HOPELESS", set())
    monkeypatch.setattr(desktop_agent, "_WAKE_STATE", {})
    monkeypatch.setattr(desktop_agent, "_class_name_of", lambda h: "Qt51514QWindowIcon")  # noqa: ARG005
    monkeypatch.setattr(accessibility, "_pid_of_window", lambda h: 4256)  # noqa: ARG005
    monkeypatch.setattr(accessibility, "activate", fake_activate)
    monkeypatch.setattr(desktop_agent.time, "monotonic", clock)
    monkeypatch.setattr(
        desktop_agent, "_trace",
        lambda side, mark, **kw: traces.append((mark, kw)),  # noqa: ARG005
    )
    return calls, clock, traces


_ACTIVATED = WakeResult(True, "activated", 4256, "w.dll", 0xB50ABC8, 0, 1)
_ALREADY = WakeResult(True, "already_active", 4256, "w.dll", 0xB50ABC8, 1, 1)


def test_rewakes_after_gate_closed_by_target(monkeypatch) -> None:
    """★ 主判据：gate 被目标关回去后，节流窗口一过必须**再试**。

    旧实现（每进程只试一次）在这里只会调用 1 次，第二条断言必红。
    """
    calls, clock, _ = _setup(monkeypatch, [_ACTIVATED, _ACTIVATED, _ACTIVATED])

    desktop_agent._maybe_wake_accessibility(1234)
    assert len(calls) == 1, "第一次悬停应当唤醒"

    clock.now += 0.2
    desktop_agent._maybe_wake_accessibility(1234)
    assert len(calls) == 1, "节流窗口内不该反复开进程句柄"

    clock.now += 2.0
    desktop_agent._maybe_wake_accessibility(1234)
    assert len(calls) == 2, "★ 窗口过后必须重试——否则微信关掉 gate 后就永远只剩外框"

    clock.now += 5.0
    desktop_agent._maybe_wake_accessibility(1234)
    assert len(calls) == 3, "失效不是一次性的，用户悬停多久就要兜多久"


def test_force_bypasses_throttle(monkeypatch) -> None:
    """按下捕获键那一刻的状态必须真实：force 不受节流限制。"""
    calls, _clock, _ = _setup(monkeypatch, [_ALREADY, _ALREADY])

    desktop_agent._maybe_wake_accessibility(1234, force=True)
    desktop_agent._maybe_wake_accessibility(1234, force=True)
    assert len(calls) == 2, "force 两次就该唤醒两次"


def test_hopeless_pid_is_not_retried(monkeypatch) -> None:
    """dll 里找不到 gate（版本不支持）是终局——反复试只会刷屏。"""
    calls, clock, _ = _setup(monkeypatch, [WakeResult(False, "no_gate", 4256)])

    desktop_agent._maybe_wake_accessibility(1234)
    for _ in range(5):
        clock.now += 10.0
        desktop_agent._maybe_wake_accessibility(1234)

    assert len(calls) == 1, f"no_gate 之后不该再试，实际试了 {len(calls)} 次"


def test_trace_does_not_flood(monkeypatch) -> None:
    """`already_active` 只在状态翻转时记一条；`activated`（真写完）每次都记。"""
    calls, clock, traces = _setup(monkeypatch, [_ALREADY, _ALREADY, _ACTIVATED, _ACTIVATED])

    for _ in range(4):
        desktop_agent._maybe_wake_accessibility(1234)
        clock.now += 2.0

    assert len(calls) == 4
    wakes = [kw for mark, kw in traces if mark == "accessibility_wake"]
    assert len(wakes) == 3, f"期望 3 条（首次 already_active + 两次 activated），实际 {len(wakes)}"
    assert wakes[0]["reason"] == "already_active"
    # 两条 activated 是「树曾被关掉」的硬证据，必须逐次留痕（带 before=0）
    assert [w["reason"] for w in wakes[1:]] == ["activated", "activated"]
    assert all(w["before"] == 0 for w in wakes[1:])


def test_wake_runs_even_when_hit_test_raises(monkeypatch) -> None:
    """★ 第二个缺陷：树休眠时 ElementFromPoint 抛异常，唤醒仍必须先执行。"""
    calls, _clock, _ = _setup(monkeypatch, [_ACTIVATED])

    def boom(x, y):  # noqa: ARG001
        raise OSError("E_ACCESSDENIED")

    monkeypatch.setattr(desktop_agent, "_element_from_point", boom)
    monkeypatch.setattr(desktop_agent, "_win32_root_at", lambda x, y: 1234)  # noqa: ARG005

    desktop_agent._hover_hit(100, 200, exclude_hwnd=0, allow_scoped=False)

    assert calls == [(1234, 1000.0)], "唤醒必须在 hit-test 之前发生，否则休眠时永远轮不到"


def test_wake_skipped_for_non_qt_window(monkeypatch) -> None:
    """非 Qt 窗口不该被写内存（很多原生应用本来就只有一层）。"""
    calls, _clock, _ = _setup(monkeypatch, [_ACTIVATED])
    monkeypatch.setattr(desktop_agent, "_class_name_of", lambda h: "Chrome_WidgetWin_1")  # noqa: ARG005

    desktop_agent._maybe_wake_accessibility(1234)
    assert calls == []


def test_returns_true_only_when_gate_was_written(monkeypatch) -> None:
    """返回值语义：True = 这一次把 gate 从 0 写成了 1（树刚被救活）。"""
    _calls, _clock, _ = _setup(monkeypatch, [_ALREADY, _ACTIVATED])

    assert desktop_agent._maybe_wake_accessibility(1234) is False, "already_active 不算救活"
    assert desktop_agent._maybe_wake_accessibility(1234, force=True) is True, "activated 才算"


def test_hover_repaints_when_tree_revived_while_mouse_still(monkeypatch) -> None:
    """★ 鼠标停着不动时，树被救活也必须立刻重画——否则红框永远停在整窗外框上。

    hover 主循环的命中测试只在 ``moved`` 时跑（省 COM 开销），所以「救活」必须能
    自己踢一次 hit-test，不能等用户再挪鼠标。
    """
    from ctypes import wintypes

    hits: list[tuple[int, int]] = []
    wakes: list[int] = []
    win_rect = wintypes.RECT(0, 0, 1920, 1080)

    class _Overlay:
        hwnd = 1

        def show_rect(self, *a):  # noqa: ANN002
            pass

        def hide(self):  # noqa: D102
            pass

        def pump(self):  # noqa: D102
            pass

        def destroy(self):  # noqa: D102
            pass

    def fake_wake(root, **_kw):
        wakes.append(root)
        # 第 1 次由「首次移动」那一帧顺手消耗；第 3 次才是「树被关掉后救活」，
        # 此时鼠标已经静止 —— 正是要验的那个时刻。
        return len(wakes) == 3

    def fake_hit(x, y, exclude, allow_scoped=True):  # noqa: ANN001, ARG001
        hits.append((x, y))
        return win_rect, 1234, None, False, [], None

    monkeypatch.setattr(desktop_agent, "_HoverOverlay", lambda: _Overlay())
    monkeypatch.setattr(desktop_agent, "_cursor_pos", lambda: (100, 200))  # 鼠标完全静止
    monkeypatch.setattr(desktop_agent, "_hotkey_pressed", lambda vk: False)  # noqa: ARG005
    monkeypatch.setattr(desktop_agent, "_poll_click_hook_event", lambda: False)
    monkeypatch.setattr(desktop_agent, "_start_click_hook", lambda *a, **k: None)
    monkeypatch.setattr(desktop_agent, "_stop_click_hook", lambda: None)
    monkeypatch.setattr(desktop_agent, "_in_browser_content", lambda x, y: False)  # noqa: ARG005
    monkeypatch.setattr(desktop_agent, "_win32_root_at", lambda x, y: 1234)  # noqa: ARG005
    monkeypatch.setattr(desktop_agent, "_maybe_wake_accessibility", fake_wake)
    monkeypatch.setattr(desktop_agent, "_hover_hit", fake_hit)
    monkeypatch.setattr(desktop_agent, "_WAKE_INTERVAL", 0.05)

    desktop_agent._hover_capture(desktop_agent.VK_F9, 0.5)

    assert len(hits) >= 2, (
        f"鼠标没动却只命中 {len(hits)} 次——救活后没有强制重画，"
        "用户看到的就是「卡在整窗外框」"
    )
    assert len(wakes) >= 1


# --------------------------------------------------------------------------
# 稳态不该打扰目标：每秒 poke 一次会让微信反复重建一百多个节点的 provider 树
# --------------------------------------------------------------------------

def _stub_activate(monkeypatch, *, before: int, pokes: list) -> None:
    state = {"value": before}

    def fake_write(pid, addr, val):  # noqa: ARG001
        state["value"] = val
        return True

    monkeypatch.setattr(accessibility, "_pid_of_window", lambda h: 4242)  # noqa: ARG005
    monkeypatch.setattr(
        accessibility, "module_base_and_path",
        lambda pid, name: (0x10000000, "X:/Weixin.dll"),  # noqa: ARG005
    )
    monkeypatch.setattr(
        accessibility, "gate_for_dll", lambda p: accessibility.GateScan(0x1234, 1, 1, 0x20)  # noqa: ARG005
    )
    monkeypatch.setattr(accessibility, "read_byte", lambda pid, addr: state["value"])  # noqa: ARG005
    monkeypatch.setattr(accessibility, "write_byte", fake_write)
    monkeypatch.setattr(accessibility, "poke_provider", lambda hwnd: pokes.append(hwnd))


def test_activate_skips_poke_in_steady_state(monkeypatch) -> None:
    """`poke=False` 且 gate 已是 1 ⇒ 不 poke（保活每 1s 一次，不能每次都打扰目标）。"""
    pokes: list[int] = []
    _stub_activate(monkeypatch, before=1, pokes=pokes)

    result = accessibility.activate(0x1111, poke=False)

    assert result.reason == "already_active"
    assert pokes == [], "稳态不该让目标重建 provider 树"


def test_activate_pokes_after_write_even_if_poke_false(monkeypatch) -> None:
    """★ 但只要写过字节就必须 poke——否则 provider 不会重建，写了等于没写。"""
    pokes: list[int] = []
    _stub_activate(monkeypatch, before=0, pokes=pokes)

    result = accessibility.activate(0x1111, poke=False)

    assert result.reason == "activated"
    assert pokes == [0x1111], "写过 gate 必须唤醒 provider"


def test_steady_state_passes_poke_false(monkeypatch) -> None:
    """agent 侧：首次（状态未知）poke，之后稳态传 poke=False。"""
    seen: list[bool] = []
    monkeypatch.setattr(desktop_agent, "_WAKE_ACCESSIBILITY", True)
    monkeypatch.setattr(desktop_agent, "_WAKE_LAST", {})
    monkeypatch.setattr(desktop_agent, "_WAKE_HOPELESS", set())
    monkeypatch.setattr(desktop_agent, "_WAKE_STATE", {})
    monkeypatch.setattr(desktop_agent, "_class_name_of", lambda h: "Qt51514QWindowIcon")  # noqa: ARG005
    monkeypatch.setattr(accessibility, "_pid_of_window", lambda h: 4256)  # noqa: ARG005

    def fake_activate(hwnd, **kw):  # noqa: ARG001
        seen.append(kw.get("poke"))
        return _ALREADY

    monkeypatch.setattr(accessibility, "activate", fake_activate)
    monkeypatch.setattr(desktop_agent, "_trace", lambda *a, **k: None)

    desktop_agent._maybe_wake_accessibility(1234)
    desktop_agent._maybe_wake_accessibility(1234, force=True)

    assert seen == [True, False], f"首次 poke / 稳态不 poke，实际 {seen}"


# --------------------------------------------------------------------------
# 按 F9 那一刻：树刚被救活就不能复用陈旧 leaf（provider 重建后旧指针会读失败）
# --------------------------------------------------------------------------

def _stub_hover_for_capture(monkeypatch, *, revived: bool):
    from ctypes import wintypes

    win_rect = wintypes.RECT(0, 0, 1920, 1080)
    counts = {"describe": 0, "retry": 0}
    state = {"f9": 0, "n": 0}

    class _Overlay:
        hwnd = 1

        def show_rect(self, *a):  # noqa: ANN002
            pass

        def hide(self):  # noqa: D102
            pass

        def pump(self):  # noqa: D102
            pass

        def destroy(self):  # noqa: D102
            pass

    def cursor():
        state["n"] += 1
        return (100 + state["n"] * 10, 200 + state["n"] * 10)

    def hotkey(vk):
        if vk != desktop_agent.VK_F9:
            return False
        state["f9"] += 1
        return state["f9"] >= 2  # 第二帧按下（第一帧先把 last_leaf 填上）

    def fake_hit(x, y, exclude, allow_scoped=True):  # noqa: ANN001, ARG001
        return win_rect, 1234, object(), False, [], object()

    monkeypatch.setattr(desktop_agent, "_HoverOverlay", lambda: _Overlay())
    monkeypatch.setattr(desktop_agent, "_cursor_pos", cursor)
    monkeypatch.setattr(desktop_agent, "_hotkey_pressed", hotkey)
    monkeypatch.setattr(desktop_agent, "_poll_click_hook_event", lambda: False)
    monkeypatch.setattr(desktop_agent, "_start_click_hook", lambda *a, **k: None)
    monkeypatch.setattr(desktop_agent, "_stop_click_hook", lambda: None)
    monkeypatch.setattr(desktop_agent, "_in_browser_content", lambda x, y: False)  # noqa: ARG005
    monkeypatch.setattr(desktop_agent, "_win32_root_at", lambda x, y: 1234)  # noqa: ARG005
    monkeypatch.setattr(desktop_agent, "_hover_hit", fake_hit)
    monkeypatch.setattr(desktop_agent, "_WAKE_INTERVAL", 999.0)  # 只留「按下捕获键」那条路径
    monkeypatch.setattr(
        desktop_agent, "_maybe_wake_accessibility",
        lambda root, **kw: revived,  # noqa: ARG005
    )
    monkeypatch.setattr(
        desktop_agent, "_describe_info",
        lambda *a, **k: counts.__setitem__("describe", counts["describe"] + 1) or {"fast": 1},
    )
    monkeypatch.setattr(
        desktop_agent, "capture_with_retry",
        lambda *a, **k: counts.__setitem__("retry", counts["retry"] + 1) or {"full": 1},
    )
    return counts


def test_capture_key_drops_stale_leaf_when_tree_revived(monkeypatch) -> None:
    """★ 树刚被救活 ⇒ provider 已重建，缓存的 leaf 可能失效，必须重新命中。"""
    counts = _stub_hover_for_capture(monkeypatch, revived=True)

    result = desktop_agent._hover_capture(desktop_agent.VK_F9, 0.6)

    assert counts["describe"] == 0, "救活后仍复用陈旧 leaf 会读失败（用户看到「读不到元素」）"
    assert counts["retry"] == 1
    assert result == {"full": 1}


def test_capture_key_keeps_fast_path_when_tree_alive(monkeypatch) -> None:
    """反向：没救活就走快路径复用——别为了修这个把 hover 的省时路径废掉。"""
    counts = _stub_hover_for_capture(monkeypatch, revived=False)

    result = desktop_agent._hover_capture(desktop_agent.VK_F9, 0.6)

    assert counts["describe"] == 1, "树是活的就该复用缓存 leaf（省一次 hit-test）"
    assert counts["retry"] == 0
    assert result == {"fast": 1}
