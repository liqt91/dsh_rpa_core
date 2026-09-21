"""桌面点击参数契约（M30 S3：`simulateHuman` / `clickPosition` 从「声明了不生效」变成真语义）。

背景：`desktop.click` 与 `desktop.win32.click` 的 manifest 一直声明着 `simulateHuman`（默认 true）
与 `clickPosition`（center/random），GUI 也一直渲染这两个控件，但实现是一条道走到底：

- 不论 `simulateHuman` 是什么，永远调 `click_input()`（真实鼠标，会移动光标）；
- 不论 `clickPosition` 是什么，永远点在元素中心（`random` 从未被读过）；
- 双击写的是 `click_kwargs["click_count"] = 2`——pywinauto 的 `click_input()` **没有**这个参数
  （基础包装类与 `controls/common_controls` 的包装类都没有），**双击在这两个后端上一直是
  `TypeError`**。这是真 bug，不是漂移：M29 的口径是「参数有没有被读」，读错的形状查不出来。

S3 把规则做成真语义，口径是「**能退让就退让，互斥就报错**」，退让必须留证据：

| 输入 | 路径 | 理由 |
|---|---|---|
| `simulateHuman=true`（默认） | `click_input()` | 真实鼠标路径，能表达按钮/双击/辅助键/落点 |
| `false` + 普通左键单击 | `invoke()` | 最短路径，不移动鼠标 |
| `false` + 双击/非左键/辅助键 | `click_input()` | `invoke()` 表达不了，退让原因写进 `note` |
| `clickPosition=random` | `click_input(coords=...)` | 坐标只有真实鼠标路径有 |
| 上面两条同时给 | **`INVALID_INPUT`** | 互斥，静默按中心点就是假生效 |
| 元素不收 `coords` / 取不到矩形 | `click_input()` 中心 | 能力不足时退让，原因写进 `note` |

真机边界：桌面 E2E 会抢前台，AGENTS 已定「按需启用」。这里用纯函数与伪元素覆盖决策逻辑，
**不冒充真机结论**（真实控件上 `invoke()` 与 `click_input()` 的效果差异仍待需要时按任务单复验）。
"""

from __future__ import annotations

import ast
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from rpa_core.catalog import load_catalog
from rpa_core.executors.base import (
    CLICK_POSITION_BAND,
    MODIFIER_VIRTUAL_KEYS,
    modifier_key_sequences,
    plan_click,
    plan_click_for_element,
    random_point_in_rect,
)
from rpa_core.model.command import CommandInvocation

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(ROOT / "commands")


DESKTOP_ONLY = pytest.mark.skipif(
    sys.platform != "win32", reason="DesktopExecutor 在非 win32 上是 PLATFORM_UNSUPPORTED 占位"
)
BOTH_BACKENDS = pytest.mark.parametrize(
    "backend,command",
    [("uia", "desktop.click"), ("win32", "desktop.win32.click")],
)


def _seq(*values: float):
    """注入式随机源：按顺序吐值，用完后重复最后一个。"""
    remaining = list(values)
    state = {"last": values[-1] if values else 0.0}

    def roll() -> float:
        if remaining:
            state["last"] = remaining.pop(0)
        return state["last"]

    return roll


# ------------------------------------------------------------- 测试替身（伪元素，非真机）


class _FakeRect:
    def __init__(self, width: int, height: int) -> None:
        self._width, self._height = width, height

    def width(self) -> int:
        return self._width

    def height(self) -> int:
        return self._height


class _FakeElement:
    """只实现点击路径用到的那几个 pywinauto 能力，用来探测决策分支。"""

    def __init__(
        self,
        width: int = 120,
        height: int = 40,
        *,
        coords: bool = True,
        invoke: bool = True,
        click_input: bool = True,
    ) -> None:
        self.width, self.height = width, height
        self.calls: list[tuple[str, dict[str, Any]]] = []

        if click_input:

            def _record(button: str, double: bool, coords_value: Any) -> None:
                self.calls.append(
                    ("click_input", {"button": button, "double": double, "coords": coords_value})
                )

            if coords:
                # 基础包装类：`click_input(button, double, wheel_dist, where, pressed, coords)`
                def _with_coords(
                    button="left", double=False, wheel_dist=0, where=None, pressed=None, coords=None
                ):
                    _record(button, double, coords)
            else:
                # `controls/common_controls` 的列表/树/表格包装类重写过，**没有 coords**
                def _without_coords(
                    button="left", double=False, wheel_dist=0, where=None, pressed=None
                ):
                    _record(button, double, None)

            self.click_input = _with_coords if coords else _without_coords  # type: ignore[assignment]

        if invoke:

            def _invoke() -> None:
                self.calls.append(("invoke", {}))

            self.invoke = _invoke  # type: ignore[assignment]

    def rectangle(self) -> _FakeRect:
        return _FakeRect(self.width, self.height)


# ------------------------------------------------------------ 1. 路径选择矩阵（纯函数）


def _plan(**overrides: Any):
    """默认是「能力齐备的元素 + 默认参数」；每个用例只覆盖自己要测的那一项。"""
    kwargs: dict[str, Any] = {
        "simulate_human": True,
        "click_position": "center",
        "plain_left_single": True,
        "has_input_click": True,
        "has_invoke": True,
        "accepts_coords": True,
        "rect_size": (100, 60),
    }
    kwargs.update(overrides)
    return plan_click(**kwargs)


def test_default_plan_is_real_mouse_at_the_center():
    plan = _plan()

    assert plan.path == "input"
    # center 不传 coords，让 click_input 自己去点中心——传一个算出来的中心点反而会在
    # 元素移动 / 高 DPI 缩放下错位。
    assert plan.coords is None
    assert plan.note == ""


@pytest.mark.parametrize(
    "case,plain_left_single,has_invoke,expected_path,expect_note",
    [
        ("普通左键单击", True, True, "invoke", False),
        ("元素没有 invoke()", True, False, "input", True),
    ],
)
def test_simulate_human_false_uses_the_shortest_path(
    case, plain_left_single, has_invoke, expected_path, expect_note
):
    plan = _plan(
        simulate_human=False,
        plain_left_single=plain_left_single,
        has_invoke=has_invoke,
    )

    assert plan.path == expected_path, case
    assert bool(plan.note) is expect_note, case


@pytest.mark.parametrize(
    "case,overrides",
    [
        ("双击", {"click_type": "double"}),
        ("右键", {"button": "right"}),
        ("带辅助键", {"modifiers": ["Ctrl"]}),
        ("带辅助键（组合）", {"modifiers": ["Ctrl", "Shift"]}),
    ],
)
def test_invoke_is_never_used_for_gestures_it_cannot_express(case, overrides):
    """`invoke()` 只能表达普通左键单击；其余形状必须退回真实鼠标路径并留下原因。"""
    call: dict[str, Any] = {"click_type": "single", "button": "left"}
    call.update(overrides)

    plan = plan_click_for_element(
        _FakeElement(), simulate_human=False, click_position="center", **call
    )

    assert plan.path == "input", case
    assert "invoke()" in plan.note, case


def test_random_position_goes_through_the_real_mouse_path():
    plan = _plan(click_position="random", roll=_seq(0.0, 1.0))

    assert plan.path == "input"
    # 偏中心带 [15%, 85%]：0.0 → 下沿、1.0 → 上沿（用 (边长-1) 换算，见 random_point_in_rect）
    assert plan.coords == (15, 50)


@pytest.mark.parametrize(
    "case,overrides",
    [
        ("元素的 click_input() 不收 coords（包装类）", {"accepts_coords": False}),
        ("取不到元素矩形", {"rect_size": None}),
    ],
)
def test_random_position_falls_back_to_center_but_says_so(case, overrides):
    plan = _plan(click_position="random", roll=_seq(0.5, 0.5), **overrides)

    assert plan.path == "input", case
    assert plan.coords is None, case
    assert "退回中心" in plan.note, case


def test_invoke_path_and_random_position_are_mutually_exclusive():
    """互斥要**显式报错**，不能静默挑一条路走——那正是「声明了不生效」的另一种形态。"""
    with pytest.raises(ValueError) as excinfo:
        _plan(simulate_human=False, click_position="random")

    message = str(excinfo.value)
    assert "互斥" in message
    assert "simulateHuman" in message
    assert "clickPosition" in message


def test_element_without_any_click_capability_fails():
    with pytest.raises(ValueError):
        _plan(has_input_click=False, has_invoke=False)


def test_unknown_click_position_is_rejected_instead_of_coerced():
    """扩展侧对未知值退 center（`clickPositionMode`），桌面侧**不猜**：manifest 是 enum，
    能走到这里说明输入已经越过了 schema，静默退中心会把它藏起来。"""
    with pytest.raises(ValueError):
        _plan(click_position="edge")


# ---------------------------------------------------------------- 2. 随机落点纯函数


def test_random_point_spans_the_band_and_stays_inside_the_element():
    assert random_point_in_rect((100, 60), roll=_seq(0.0, 0.0)) == (15, 9)
    assert random_point_in_rect((100, 60), roll=_seq(1.0, 1.0)) == (84, 50)


def test_random_point_is_not_a_constant():
    points = {random_point_in_rect((300, 200)) for _ in range(32)}
    assert len(points) > 1, "随机落点退化成了常量——「随机」又变成了假开关"

    low, high = CLICK_POSITION_BAND
    assert (low, high) == (0.15, 0.85)
    for x, y in points:
        assert 0 <= x <= 299 and 0 <= y <= 199
        assert low * 299 - 1 <= x <= high * 299 + 1
        assert low * 199 - 1 <= y <= high * 199 + 1


@pytest.mark.parametrize("size", [(1, 1), (2, 1), (1, 2)])
def test_random_point_survives_tiny_elements(size):
    x, y = random_point_in_rect(size)
    assert 0 <= x <= size[0] - 1
    assert 0 <= y <= size[1] - 1


@pytest.mark.parametrize("size", [(0, 10), (10, 0), (-1, -1), (0, 0)])
def test_random_point_rejects_degenerate_rect(size):
    with pytest.raises(ValueError):
        random_point_in_rect(size)


def test_random_point_tolerates_a_broken_random_source():
    """随机源给 NaN/Inf/非数值时收敛到带下沿，**不产生 NaN 坐标**（点一个 NaN 等于乱点）。"""
    assert random_point_in_rect((100, 60), roll=_seq(float("nan"), float("inf"))) == (15, 9)
    assert random_point_in_rect((100, 60), roll=_seq("bogus", None)) == (15, 9)


# ------------------------------------------------------- 2b. 辅助键的 send_keys 序列


def test_modifier_sequences_press_in_order_and_release_in_reverse():
    down, up = modifier_key_sequences(["Ctrl", "Shift"])

    assert down == ["{VK_CONTROL down}", "{VK_SHIFT down}"]
    assert up == ["{VK_SHIFT up}", "{VK_CONTROL up}"]


@pytest.mark.parametrize(
    "name,virtual_key",
    [("Alt", "VK_MENU"), ("Ctrl", "VK_CONTROL"), ("Shift", "VK_SHIFT"), ("Win", "VK_LWIN")],
)
def test_every_manifest_modifier_maps_to_a_virtual_key(name, virtual_key):
    """manifest 的枚举值必须一个不漏地有映射，否则勾了就静默丢掉。"""
    assert MODIFIER_VIRTUAL_KEYS[name] == virtual_key
    assert modifier_key_sequences([name])[0] == [f"{{{virtual_key} down}}"]


def test_modifier_names_are_normalized_and_unknown_ones_are_not_swallowed():
    """空白/大小写容错；未知名字**不静默丢弃**（丢了就是「勾了等于没勾」）。"""
    assert modifier_key_sequences([" ctrl "])[0] == ["{VK_CONTROL down}"]
    assert modifier_key_sequences(["Meta"])[0] == ["{Meta down}"]


def test_no_modifiers_produces_no_key_sequences():
    assert modifier_key_sequences([]) == ([], [])


# ---------------------------------------------------------- 3. 元素能力探测（伪元素）


def test_capability_probe_drives_the_plan():
    capable = _FakeElement()
    assert (
        plan_click_for_element(
            capable,
            simulate_human=False,
            click_position="center",
            click_type="single",
            button="left",
        ).path
        == "invoke"
    )

    wrapped = _FakeElement(coords=False)
    plan = plan_click_for_element(
        wrapped,
        simulate_human=True,
        click_position="random",
        click_type="single",
        button="left",
        roll=_seq(0.5, 0.5),
    )
    assert plan.path == "input"
    assert plan.coords is None
    assert "coords" in plan.note


def test_element_without_invoke_falls_back_for_shortest_path():
    plan = plan_click_for_element(
        _FakeElement(invoke=False),
        simulate_human=False,
        click_position="center",
        click_type="single",
        button="left",
    )

    assert plan.path == "input"
    assert "invoke()" in plan.note


def test_element_without_click_input_falls_back_to_invoke():
    plan = plan_click_for_element(
        _FakeElement(click_input=False),
        simulate_human=True,
        click_position="center",
        click_type="single",
        button="left",
    )

    assert plan.path == "invoke"
    assert "click_input()" in plan.note


def test_element_without_any_capability_raises():
    with pytest.raises(ValueError):
        plan_click_for_element(
            _FakeElement(click_input=False, invoke=False),
            simulate_human=True,
            click_position="center",
            click_type="single",
            button="left",
        )


def test_unreadable_rectangle_is_treated_as_unavailable():
    element = _FakeElement()
    element.rectangle = lambda: (_ for _ in ()).throw(RuntimeError("元素已消失"))

    plan = plan_click_for_element(
        element,
        simulate_human=True,
        click_position="random",
        click_type="single",
        button="left",
    )

    assert plan.path == "input"
    assert plan.coords is None


# ----------------------------------------------- 4. 执行器路径（打桩，非真机）


def _armed_desktop(element: Any, backend: str = "uia"):
    """装好会话与 `_find` 桩，把 `element` 原样交给点击分支。"""
    if backend == "uia":
        from rpa_core.executors.desktop import DesktopExecutor, _DesktopSession

        executor = DesktopExecutor()
        executor._sessions["s"] = _DesktopSession(
            process_id=1, window_handle=2, elements={"el": {"automationId": "nameInput"}}
        )
    else:
        from rpa_core.executors.desktop_win32 import Win32DesktopExecutor, _Win32Session

        executor = Win32DesktopExecutor()
        executor._sessions["s"] = _Win32Session(
            process_id=1,
            window_handle=2,
            # `DesktopLocator` 按 backend 校验身份字段：win32 侧认 title/className/controlId…
            elements={"el": {"backend": "win32", "title": "Demo"}},
        )

    executor._window_by_handle = lambda handle: object()  # type: ignore[method-assign]
    executor._find = lambda window, locator: [element]  # type: ignore[method-assign]
    return executor


def _click(executor: Any, command_id: str, **inputs: Any):
    invocation = CommandInvocation(
        command_id=command_id,
        command_version="1.0.0",
        run_id="run",
        step_id="step",
        inputs={"sessionId": "s", "elementId": "el", **inputs},
    )
    return asyncio.run(executor.execute(invocation, asyncio.Event()))


def _evidence(result) -> dict[str, Any]:
    assert result.status == "success", getattr(result, "error", None)
    return dict(result.effects[0].details)


@BOTH_BACKENDS
@DESKTOP_ONLY
def test_click_default_records_the_real_mouse_path(backend, command):
    element = _FakeElement()

    details = _evidence(_click(_armed_desktop(element, backend), command))

    assert details["operation"] == "click"
    assert details["path"] == "input"
    assert "note" not in details  # 默认路径没有需要解释的退让
    assert element.calls == [("click_input", {"button": "left", "double": False, "coords": None})]


@BOTH_BACKENDS
@DESKTOP_ONLY
def test_click_simulate_human_false_takes_the_shortest_path(backend, command):
    element = _FakeElement()

    details = _evidence(_click(_armed_desktop(element, backend), command, simulateHuman=False))

    assert details["path"] == "invoke"
    assert element.calls == [("invoke", {})]


@BOTH_BACKENDS
@DESKTOP_ONLY
def test_double_click_passes_double_without_click_count(backend, command):
    """回归锁：此前写 `click_count=2` 会 `TypeError`——双击在这两个后端上一直是坏的。"""
    element = _FakeElement()

    details = _evidence(_click(_armed_desktop(element, backend), command, clickType="double"))

    assert details["path"] == "input"
    assert element.calls == [("click_input", {"button": "left", "double": True, "coords": None})]


@BOTH_BACKENDS
@DESKTOP_ONLY
def test_click_random_position_passes_coords_inside_the_element(backend, command):
    element = _FakeElement(width=200, height=80)
    executor = _armed_desktop(element, backend)

    points: list[tuple[int, int]] = []
    for _ in range(6):
        details = _evidence(_click(executor, command, clickPosition="random"))
        coords = details["coords"]
        assert len(coords) == 2
        x, y = coords
        assert 0 <= x <= 199 and 0 <= y <= 79
        assert 0.15 * 199 - 1 <= x <= 0.85 * 199 + 1
        points.append((x, y))

    assert len(set(points)) > 1, "random 落点在执行器里退化成了恒定坐标"
    clicked = [call[1]["coords"] for call in element.calls]
    assert clicked == points


@BOTH_BACKENDS
@DESKTOP_ONLY
def test_click_random_on_wrapper_element_records_the_fallback(backend, command):
    """包装类的 `click_input()` 不收 `coords`：退让到中心，但**必须**把退让写进证据。"""
    element = _FakeElement(coords=False)

    details = _evidence(_click(_armed_desktop(element, backend), command, clickPosition="random"))

    assert details["path"] == "input"
    assert "coords" not in details
    assert "note" in details
    assert element.calls == [("click_input", {"button": "left", "double": False, "coords": None})]


@BOTH_BACKENDS
@DESKTOP_ONLY
def test_mutually_exclusive_click_inputs_fail_with_invalid_input(backend, command):
    element = _FakeElement()

    result = _click(
        _armed_desktop(element, backend), command, simulateHuman=False, clickPosition="random"
    )

    assert result.status == "error"
    assert result.error.code == "INVALID_INPUT"
    assert element.calls == []  # 静默挑一条路走 = 骗用户点了别的地方


@BOTH_BACKENDS
@DESKTOP_ONLY
def test_shortest_path_with_a_gesture_invoke_cannot_express_stays_on_the_mouse(backend, command):
    element = _FakeElement()

    details = _evidence(
        _click(_armed_desktop(element, backend), command, simulateHuman=False, button="right")
    )

    assert details["path"] == "input"
    assert "note" in details
    assert element.calls == [("click_input", {"button": "right", "double": False, "coords": None})]


@BOTH_BACKENDS
@DESKTOP_ONLY
def test_modifiers_are_held_around_the_real_mouse_click(monkeypatch, backend, command):
    """辅助键必须**按下 → 点击 → 抬起**成对出现，且用 pywinauto 真有的 API。

    回归锁：此前写 `pywinauto.keyboard.key_down(...)`——**这个函数不存在**（pywinauto 0.6.9
    的 `keyboard` 模块只有 `send_keys`/`parse_keys`/`KeyAction` 家族），带辅助键的点击一直
    `AttributeError → EXECUTOR_FAILED`。所以这里同时钉住「调的是 `send_keys`」这件事。

    打桩 `pywinauto.keyboard.send_keys`：否则这条测试会真的按住 Ctrl（测试不该动用户的键盘）。
    """
    from pywinauto import keyboard as pywinauto_keyboard

    assert not hasattr(pywinauto_keyboard, "key_down"), (
        "pywinauto 又有 key_down 了？确认 API 变了再改实现与这条断言"
    )

    events: list[str] = []
    monkeypatch.setattr(
        pywinauto_keyboard, "send_keys", lambda sequence: events.append(sequence)
    )

    element = _FakeElement()

    details = _evidence(
        _click(_armed_desktop(element, backend), command, modifiers=["Ctrl", "Shift"])
    )

    assert details["path"] == "input"
    assert events == ["{VK_CONTROL down}", "{VK_SHIFT down}", "{VK_SHIFT up}", "{VK_CONTROL up}"]
    assert element.calls == [("click_input", {"button": "left", "double": False, "coords": None})]


@BOTH_BACKENDS
@DESKTOP_ONLY
def test_modifiers_are_released_even_if_the_click_explodes(backend, command):
    """点击炸了也不能把 Ctrl 永久留住：抬起必须在 `finally` 里。"""
    from pywinauto import keyboard as pywinauto_keyboard

    events: list[str] = []
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        pywinauto_keyboard, "send_keys", lambda sequence: events.append(sequence)
    )
    try:
        element = _FakeElement()

        def boom(**kwargs):
            raise RuntimeError("点击目标已消失")

        element.click_input = boom  # type: ignore[method-assign]

        result = _click(_armed_desktop(element, backend), command, modifiers=["Ctrl"])
    finally:
        monkeypatch.undo()

    assert events == ["{VK_CONTROL down}", "{VK_CONTROL up}"]
    assert result.status == "error"


@BOTH_BACKENDS
@DESKTOP_ONLY
def test_wait_budget_is_not_swallowed_by_the_click_parameters(backend, command):
    """`timeoutMs` 抬高的是等待预算；`clickPosition` 不该顺手把它变成固定等待。"""
    element = _FakeElement()
    started = time.perf_counter()
    result = _click(
        _armed_desktop(element, backend), command, timeoutMs=120, clickPosition="random"
    )
    elapsed = time.perf_counter() - started

    assert result.status == "success"
    assert elapsed < 1.0  # 元素一次就找到了，预算不该变成 sleep
    assert element.calls[0][1]["coords"] is not None


# ------------------------------------------------------------ 5. manifest 面（声明要诚实）


def _manifest(backend: str, name: str) -> dict:
    return json.loads((ROOT / "commands" / backend / f"{name}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("backend", ["desktop", "desktop_win32"])
def test_click_manifest_documents_the_real_contract(backend):
    properties = _manifest(backend, "click")["input_schema"]["properties"]

    simulate = properties["simulateHuman"]["description"]
    position = properties["clickPosition"]["description"]

    # `simulateHuman` 的说明必须讲清「只在普通左键单击时能走最短路径」，不能只说「模拟人工点击」
    assert "invoke()" in simulate
    assert "普通左键单击" in simulate
    # `clickPosition` 的说明必须讲清互斥语义与它的报错码
    assert "互斥" in position
    assert "INVALID_INPUT" in position

    assert properties["simulateHuman"]["default"] is True
    assert properties["clickPosition"]["default"] == "center"


@pytest.mark.parametrize("backend", ["desktop", "desktop_win32"])
def test_click_manifest_declares_invalid_input(backend):
    """实现会因为参数互斥返回 `INVALID_INPUT`，声明面必须跟上（门禁见 check_error_contract.py）。"""
    assert "INVALID_INPUT" in _manifest(backend, "click")["errors"]


def test_two_backends_agree_on_the_click_contract(catalog):
    """两后端的参数表与说明必须一致：同一份语义写在两份 manifest 里，是漂移的高发地。"""
    uia = _manifest("desktop", "click")
    win32 = _manifest("desktop_win32", "click")

    assert catalog["desktop.click"].input_schema == catalog["desktop.win32.click"].input_schema
    for field in ("simulateHuman", "clickPosition"):
        assert (
            uia["input_schema"]["properties"][field]["description"]
            == win32["input_schema"]["properties"][field]["description"]
        )
    assert uia["errors"] == win32["errors"]


@pytest.mark.parametrize("backend", ["desktop", "desktop_win32"])
def test_click_implementation_uses_the_shared_planner(backend):
    """反漂移：两后端都必须走同一个 `plan_click_for_element`，不许各写一套判断。"""
    module = "desktop.py" if backend == "desktop" else "desktop_win32.py"
    source = (ROOT / "src" / "rpa_core" / "executors" / module).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=module)

    assert "plan_click_for_element(" in source
    # 走 AST 而不是字符串包含：注释里为了解释这个 bug 提到了 `click_count=2`，字符串检查会误报。
    assert not any(
        isinstance(node, ast.keyword) and node.arg == "click_count" for node in ast.walk(tree)
    ), "`click_input()` 没有 click_count 参数，写了就是 TypeError"
    assert not any(
        isinstance(node, ast.Constant) and node.value == "click_count" for node in ast.walk(tree)
    ), "`click_kwargs[\"click_count\"]` 会原样转成关键字参数，同样是 TypeError"
    assert not any(
        isinstance(node, ast.Attribute) and node.attr in ("key_down", "key_up")
        for node in ast.walk(tree)
    ), "pywinauto 没有 key_down/key_up，辅助键要走 `click_with_modifiers`"


@BOTH_BACKENDS
@DESKTOP_ONLY
@pytest.mark.xfail(
    strict=True,
    reason="跨通道归一化尚未统一：执行器用 bool()，扩展侧用 simulateHumanEnabled()，见 M30 任务单",
)
def test_simulate_human_flag_normalization_matches_the_extension(backend, command):
    """`simulateHuman` 的归一化口径四端必须一致（扩展 `simulateHumanEnabled` 是参照）。

    现状：桌面/浏览器执行器都写 `bool(inputs.get("simulateHuman", True))`，于是
    `"simulateHuman": "false"`（手写或导入的工作流 JSON）被当成 **true**——用户显式写的
    「不要模拟人工」被静默反转；而 `null` 又被当成 false，与扩展侧（`null` → 开）相反。
    扩展侧的口径是 `String(raw ?? "").trim().toLowerCase() !== "false"`。

    这是 S3 期间挖到的**跨通道**差异，没在本片修（改了 browser.py 就要同步改
    `scripts/check_click_helpers.mjs` 的反漂移断言，属独立切片）。先钉住它：
    真去统一归一化时这条会由 xfail 变 xpass，strict 会让它立刻报红提醒摘掉标记。
    """
    element = _FakeElement()

    _click(_armed_desktop(element, backend), command, simulateHuman="false")

    assert [name for name, _ in element.calls] == ["invoke"]
