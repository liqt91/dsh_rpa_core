"""M74 S3/S4 桌面活体校验的契约判据。

用替身 window_factory/finder/api/bring_front，在任意平台证明四件事：
跨窗口 Z 序命中即停、回传统一形状、三 mode 置前成对、错误三态结构化。
真机 UIA 行为另有 e2e，不在这里依赖。
"""

from __future__ import annotations

from rpa_core.capture.desktop_verification import DesktopElementVerifier
from rpa_core.capture.verification import verify_element


class FakeAPI:
    def __init__(self, rows):
        self._rows = rows

    def enumerate_top_windows(self):
        return list(self._rows)

    def foreground(self):
        return 0

    def set_foreground(self, hwnd):
        return True


def _row(hwnd, proc="chrome.exe"):
    return (hwnd, "Chrome_WidgetWin_1", f"w{hwnd}", proc, True)


class FakeMatch:
    def __init__(self, box=(10, 20, 110, 80)):
        self._box = box  # left, top, right, bottom

    def rectangle(self):
        class R:
            left, top, right, bottom = self._box

        return R()


def _harness(rows, hits: dict[int, list], front_calls: list[int]):
    """组装：给定 Z 序窗口 + 每窗口命中表 + 置前记录罐。"""
    api = FakeAPI(rows)
    probed: list[int] = []

    def window_factory(hwnd):
        return f"window-{hwnd}"

    def finder(window, locator):
        hwnd = int(str(window).rsplit("-", 1)[-1])
        probed.append(hwnd)
        return hits.get(hwnd, [])

    def bring_front(hwnd):
        front_calls.append(hwnd)

    verifier = DesktopElementVerifier(
        window_factory=window_factory,
        finder=finder,
        api=api,
        bring_front=bring_front,
    )
    return verifier, probed


def _element(locator=None):
    return {"kind": "desktop", "selector": {"locator": locator or {"backend": "uia", "name": "x"}}}


# ---- S3：跨窗口搜索 + 命中即停 ----------------------------------------------


def test_verify_searches_zorder_and_stops_at_first_hit():
    front: list[int] = []
    verifier, probed = _harness(
        [_row(1), _row(2), _row(3)],
        {2: [FakeMatch()], 3: [FakeMatch()]},
        front,
    )
    result = verifier.verify({"backend": "uia"})
    assert result["count"] == 1
    assert result["windowHandle"] == 2          # 更靠前的命中窗口
    assert probed == [1, 2]                       # 命中即停，3 没被查
    assert front == [2]                           # flash 置前的是命中窗口


def test_verify_finds_element_inside_taskbar_shell_window():
    """M95：开始图标右侧的搜索按钮宿主是 Shell_TrayWnd（标题为空）。

    默认 Z 序链会滤掉它；桌面校验必须单独放行，否则元素保存成功但永远
    count=0。这里用替身钉住「壳窗口进链 + 命中返回它的 hwnd」。
    """
    front: list[int] = []
    rows = [
        (90, "Chrome_WidgetWin_1", "Edge", "msedge.exe", True),
        (91, "Shell_TrayWnd", "", "explorer.exe", True),
    ]
    verifier, probed = _harness(rows, {91: [FakeMatch((48, 0, 96, 40))]}, front)
    result = verifier.verify({"backend": "uia"})
    assert result["count"] == 1
    assert result["windowHandle"] == 91
    assert probed == [90, 91]


def test_verify_rect_matches_web_shape():
    front: list[int] = []
    verifier, _ = _harness([_row(1)], {1: [FakeMatch((5, 7, 55, 47))]}, front)
    result = verifier.verify({"backend": "uia"})
    assert result["rect"] == {"left": 5, "top": 7, "width": 50, "height": 40}


def test_verify_no_hit_is_count_zero_not_error():
    front: list[int] = []
    verifier, probed = _harness([_row(1), _row(2)], {}, front)
    result = verifier.verify({"backend": "uia"})
    assert result == {"count": 0}
    assert probed == [1, 2]  # 全查过才判没有
    assert front == []


def test_verify_preferred_hwnd_searched_first():
    front: list[int] = []
    verifier, probed = _harness([_row(1), _row(2), _row(3)], {3: [FakeMatch()]}, front)
    result = verifier.verify({"backend": "uia"})  # 先不带 preferred
    assert result["windowHandle"] == 3
    assert probed == [1, 2, 3]

    # 带 preferred=3 时它插队到链首
    probed.clear()
    verifier_pref = DesktopElementVerifier(
        window_factory=lambda h: f"window-{h}",
        finder=lambda w, loc: probed.append(int(str(w)[-1])) or [],
        api=FakeAPI([_row(1), _row(2), _row(3)]),
        bring_front=lambda h: None,
        expected_hwnd=3,
    )
    verifier_pref.verify({"backend": "uia"})
    assert probed[0] == 3


# ---- S4：三 mode 置前行为成对 ------------------------------------------------


def test_only_flash_brings_hit_window_to_front():
    front: list[int] = []
    verifier, _ = _harness([_row(9)], {9: [FakeMatch()]}, front)

    verifier.verify({"backend": "uia"})       # flash
    assert front == [9]
    front.clear()

    verifier.preview({"backend": "uia"})      # preview 不抢焦点
    assert front == []

    verifier.clear_preview()                  # clear 不抢焦点
    assert front == []


def test_front_target_is_hit_window_not_highest_zorder():
    """命中窗口在 Z 序第二位：置前必须是它，不是链首。"""
    front: list[int] = []
    verifier, _ = _harness([_row(1), _row(2)], {2: [FakeMatch()]}, front)
    verifier.verify({"backend": "uia"})
    assert front == [2] and front != [1]


def test_front_failure_is_swallowed():
    """置前抛异常（前台锁定）不冒泡：count 照常。"""
    def boom(hwnd):
        raise RuntimeError("foreground denied")

    verifier = DesktopElementVerifier(
        window_factory=lambda h: f"w{h}",
        finder=lambda w, loc: [FakeMatch()],
        api=FakeAPI([_row(1)]),
        bring_front=boom,
    )
    assert verifier.verify({"backend": "uia"})["count"] == 1


# ---- 结构化错误三态 ----------------------------------------------------------


def test_finder_exception_is_structured_error():
    def boom_finder(window, locator):
        raise RuntimeError("COM exploded")

    verifier = DesktopElementVerifier(
        window_factory=lambda h: f"w{h}",
        finder=boom_finder,
        api=FakeAPI([_row(1)]),
        bring_front=lambda h: None,
    )
    assert verifier.verify({"backend": "uia"}) == {"error": "find-failed"}


def test_clear_mode_does_not_touch_finder_or_front():
    front: list[int] = []
    verifier, probed = _harness([_row(1)], {1: [FakeMatch()]}, front)
    assert verifier.clear_preview() == {"count": 0}
    assert probed == [] and front == []


def test_unsupported_mode_is_error():
    verifier, _ = _harness([_row(1)], {}, [])
    assert verifier._run({"backend": "uia"}, "blink") == {"error": "unsupported-mode"}


# ---- 门面接线：统一入口把 desktop 分派到桌面 verifier --------------------------

def test_facade_dispatches_desktop_kind_to_desktop_verifier(monkeypatch):
    """用户实际走统一入口 verify_element，证明 desktop kind 不被误当 browser。"""
    import rpa_core.capture.desktop_verification as dv
    import rpa_core.capture.verification as vf

    front: list[int] = []
    real = DesktopElementVerifier(
        window_factory=lambda h: f"w{h}",
        finder=lambda w, loc: [FakeMatch()] if str(w) == "w7" else [],
        api=FakeAPI([_row(7)]),
        bring_front=lambda h: front.append(h),
    )
    monkeypatch.setattr(dv, "DesktopElementVerifier", lambda **kw: real)
    monkeypatch.setattr(vf, "_default_desktop_verifier", lambda **kw: real)

    result = verify_element(_element(), mode="flash")
    assert result.count == 1
    assert result.windowHandle == 7
    assert front == [7]


def test_default_finder_normalizes_raw_dict_before_executor(monkeypatch):
    """真机接线闸：元素资产存 dict，执行器 _find 吃 DesktopLocator。

    M79 真机曾因漏掉这一步，在所有窗口抛 AttributeError 并最终误报 find-failed；
    注入替身 finder 的纯契约测试看不见它，所以这里直接钉生产 finder。
    """
    import rpa_core.capture.desktop_verification as dv
    from rpa_core.executors.desktop import DesktopExecutor
    from rpa_core.model.desktop import DesktopLocator

    seen: list[object] = []

    def fake_find(window, locator):
        seen.append(locator)
        return []

    monkeypatch.setattr(DesktopExecutor, "_find", fake_find)
    dv._default_finder(object(), {"backend": "uia", "controlType": "Edit"})
    assert len(seen) == 1
    assert isinstance(seen[0], DesktopLocator)
    assert seen[0].control_type == "Edit"


# ---- M100：查之前先唤醒（顺序 + 幂等 + 失败不阻断） ----------------------------


class _WakeOK:
    ok = True
    reason = "activated"
    poked = True


def _qt_row(hwnd, proc="Weixin.exe", title="微信"):
    return (hwnd, "Qt51514QWindowIcon", title, proc, True)


def test_wake_happens_before_each_window_is_queried():
    """★ 顺序判据：唤醒必须在该窗口**第一次** UIA 查询之前。

    微信 4.x 的树只在进程首次被查询之前建得起来；把唤醒挪到 probe 之后
    （或整轮查完再补）等于没做——真机上完全看不出差别，只有顺序能证明。
    """
    order: list[tuple[str, int]] = []
    verifier = DesktopElementVerifier(
        window_factory=lambda h: f"w{h}",
        finder=lambda w, loc: order.append(("find", int(str(w)[1:]))) or [],
        api=FakeAPI([_row(1), _row(2)]),
        bring_front=lambda h: None,
        waker=lambda h: order.append(("wake", h)),
        tree_count=lambda w: 0,
    )
    verifier.verify({"backend": "uia"})
    assert order == [("wake", 1), ("find", 1), ("wake", 2), ("find", 2)], (
        "每个窗口都要「先唤醒、后查询」，且只唤醒一次"
    )


def test_wake_is_not_repeated_when_first_window_hits():
    """命中即停：后面窗口既不该查、也不该被唤醒。"""
    calls: list[str] = []
    verifier = DesktopElementVerifier(
        window_factory=lambda h: f"w{h}",
        finder=lambda w, loc: [FakeMatch()] if str(w) == "w1" else [],
        api=FakeAPI([_row(1), _row(2)]),
        bring_front=lambda h: None,
        waker=lambda h: calls.append(f"wake{h}"),
    )
    assert verifier.verify({"backend": "uia"})["count"] == 1
    assert calls == ["wake1"]


def test_wake_exception_does_not_break_verification():
    """唤醒是增强路径：waker 抛异常时校验照常进行。"""
    def boom(hwnd):
        raise RuntimeError("no access")

    verifier = DesktopElementVerifier(
        window_factory=lambda h: f"w{h}",
        finder=lambda w, loc: [FakeMatch()],
        api=FakeAPI([_row(1)]),
        bring_front=lambda h: None,
        waker=boom,
    )
    assert verifier.verify({"backend": "uia"})["count"] == 1


# ---- M100：命中 0 时的树诊断（区分「选择器不匹配」与「对方没有树」） ----------


def test_count_zero_reports_tree_missing_for_dormant_qt_shell():
    verifier = DesktopElementVerifier(
        window_factory=lambda h: f"w{h}",
        finder=lambda w, loc: [],
        api=FakeAPI([_qt_row(7)]),
        bring_front=lambda h: None,
        waker=lambda h: None,          # 唤醒没成功（不是候选 / 已定死）
        tree_count=lambda w: 1,        # 只有 Qt 外壳，一个后代都没有
    )
    assert verifier.verify({"backend": "uia"}) == {"count": 0, "hint": "tree-missing"}


def test_count_zero_has_no_hint_when_tree_is_alive():
    """树是活的（12 个后代）：就是选择器没匹配上，不能报「树没建」。"""
    verifier = DesktopElementVerifier(
        window_factory=lambda h: f"w{h}",
        finder=lambda w, loc: [],
        api=FakeAPI([_qt_row(7)]),
        bring_front=lambda h: None,
        waker=lambda h: _WakeOK(),
        tree_count=lambda w: 12,
    )
    assert verifier.verify({"backend": "uia"}) == {"count": 0}


def test_native_window_never_gets_tree_hint():
    """原生窗口（记事本/Chromium）本来就可能只有一层，不能拿它报「树没建」。"""
    verifier = DesktopElementVerifier(
        window_factory=lambda h: f"w{h}",
        finder=lambda w, loc: [],
        api=FakeAPI([(1, "Notepad", "无标题 - 记事本", "notepad.exe", True)]),
        bring_front=lambda h: None,
        waker=lambda h: None,
        tree_count=lambda w: 0,
    )
    assert verifier.verify({"backend": "uia"}) == {"count": 0}


def test_preview_skips_tree_diagnosis():
    """preview 边打字边跑，不做额外 UIA 查询；也就不会带 hint。"""
    counted: list[int] = []
    verifier = DesktopElementVerifier(
        window_factory=lambda h: f"w{h}",
        finder=lambda w, loc: [],
        api=FakeAPI([_qt_row(7)]),
        bring_front=lambda h: None,
        waker=lambda h: None,
        tree_count=lambda w: counted.append(1) or 0,
    )
    assert verifier.preview({"backend": "uia"}) == {"count": 0}
    assert counted == [], "preview 不该为诊断再查一遍树"


def test_hint_survives_unified_result_shape():
    """诊断要能穿过统一入口到前端：to_dict 必须透传 hint。"""
    verifier = DesktopElementVerifier(
        window_factory=lambda h: f"w{h}",
        finder=lambda w, loc: [],
        api=FakeAPI([_qt_row(7)]),
        bring_front=lambda h: None,
        waker=lambda h: None,
        tree_count=lambda w: 1,
    )
    result = verify_element(_element(), mode="flash", desktop_verifier=verifier)
    assert result.error is None
    assert result.to_dict() == {"count": 0, "hint": "tree-missing"}
