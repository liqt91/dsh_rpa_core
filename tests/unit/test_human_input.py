"""M107 S1：真人化鼠标注入的纯函数与轨迹契约。

全部用替身录事件，不碰真实光标（测试进程通常没有输入权限，见 tests/conftest.py
对合成输入的禁令）。
"""

from __future__ import annotations

import random

from rpa_core.executors import human_input as hi


class FakeMouse:
    """记录 SendInput 事件的后端替身。"""

    def __init__(
        self,
        cursor: tuple[int, int] = (100, 100),
        screen: tuple[int, int, int, int] = (0, -1080, 1920, 2160),
    ) -> None:
        self.cursor = cursor
        self.screen = screen
        self.events: list[dict[str, int]] = []

    def cursor_pos(self) -> tuple[int, int]:
        return self.cursor

    def virtual_screen(self) -> tuple[int, int, int, int]:
        return self.screen

    def send(self, events) -> int:  # noqa: ANN001 - 与 MouseAPI 协议一致
        self.events.extend(dict(item) for item in events)
        return len(events)


class Sleeper:
    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


# -- 坐标换算 ---------------------------------------------------------------


def test_normalize_absolute_uses_virtual_desktop_origin():
    """副屏在主屏上方（top=-1080）时，y 必须先减原点再归一化。"""
    screen = (0, -1080, 1920, 2160)
    # 虚拟桌面顶边 → 0
    assert hi.normalize_absolute(0, -1080, screen) == (0, 0)
    # 底边 → 65535
    assert hi.normalize_absolute(1919, 1079, screen) == (65535, 65535)
    # 主屏中心（y=0）应落在虚拟桌面 1/2 处
    nx, ny = hi.normalize_absolute(960, 0, screen)
    assert nx == round(960 * 65535 / 1919)
    assert ny == round(1080 * 65535 / 2159)


def test_normalize_absolute_handles_degenerate_screen():
    assert hi.normalize_absolute(10, 10, (0, 0, 1, 1)) == (0, 0)
    assert hi.normalize_absolute(10, 10, (0, 0, 0, 0)) == (0, 0)


def test_normalize_absolute_clamps_out_of_range():
    screen = (0, 0, 1920, 1080)
    assert hi.normalize_absolute(-500, -500, screen)[0] == 0
    assert hi.normalize_absolute(9999, 9999, screen)[0] == 65535


# -- 缓动 -------------------------------------------------------------------


def test_ease_out_cubic_anchors_and_monotonic():
    assert hi.ease_out_cubic(0.0) == 0.0
    assert hi.ease_out_cubic(1.0) == 1.0
    assert hi.ease_out_cubic(-1.0) == 0.0
    assert hi.ease_out_cubic(2.0) == 1.0
    samples = [hi.ease_out_cubic(i / 10) for i in range(11)]
    assert samples == sorted(samples)
    # ease-out：前半段走得比线性快
    assert samples[5] > 0.5


def test_bezier_endpoints_are_exact():
    p0, p1, p2, p3 = (0.0, 0.0), (10.0, 20.0), (30.0, -20.0), (40.0, 0.0)
    assert hi.bezier_point(p0, p1, p2, p3, 0.0) == p0
    assert hi.bezier_point(p0, p1, p2, p3, 1.0) == p3


# -- 轨迹规划 ---------------------------------------------------------------


def test_plan_move_short_distance_snaps():
    points, duration = hi.plan_move((10, 10), (12, 12), rng=random.Random(1))
    assert points == [(12, 12)]
    assert duration == 0.0


def test_plan_move_long_distance_has_many_steps_and_exact_end():
    points, duration = hi.plan_move((0, 0), (600, 400), rng=random.Random(7))
    assert len(points) >= hi.MIN_STEPS
    assert len(points) <= hi.MAX_STEPS
    assert points[-1] == (600, 400)
    assert duration > 0
    # 时长被夹在上下限之间（乘过随机系数 0.7~1.3）
    assert duration >= hi.MIN_DURATION_MS / 1000 * 0.7 - 1e-9
    assert duration <= hi.MAX_DURATION_MS / 1000 * 1.3 + 1e-9


def test_plan_move_progresses_towards_target():
    """每一步都应比上一步更靠近终点（不打转、不倒退）。"""
    points, _ = hi.plan_move((0, 0), (400, 0), rng=random.Random(3))
    distances = [abs(400 - x) for x, _ in points]
    assert distances == sorted(distances, reverse=True)


def test_plan_move_is_randomized():
    a, _ = hi.plan_move((0, 0), (300, 300), rng=random.Random(1))
    b, _ = hi.plan_move((0, 0), (300, 300), rng=random.Random(2))
    assert a != b


# -- 注入 -------------------------------------------------------------------


def test_move_smooth_sends_normalized_points_and_sleeps():
    api = FakeMouse(cursor=(0, 0), screen=(0, 0, 1920, 1080))
    sleeper = Sleeper()
    plan = ([(100, 100), (200, 200)], 0.2)
    evidence = hi.move_smooth((0, 0), (200, 200), api=api, sleep=sleeper, plan=plan)

    assert evidence["points"] == 2
    assert evidence["durationMs"] == 200.0
    assert len(api.events) == 2
    for event in api.events:
        flags = event["flags"]
        assert flags & hi.MOUSEEVENTF_MOVE
        assert flags & hi.MOUSEEVENTF_ABSOLUTE
        assert flags & hi.MOUSEEVENTF_VIRTUALDESK
    assert api.events[-1]["dx"] == hi.normalize_absolute(200, 200, (0, 0, 1920, 1080))[0]
    assert sleeper.calls == [0.1, 0.1]


def test_move_to_zero_duration_is_single_jump():
    api = FakeMouse(cursor=(5, 5), screen=(0, 0, 1920, 1080))
    evidence = hi.move_to(800, 600, duration_ms=0, api=api)
    assert evidence["points"] == 1
    assert len(api.events) == 1
    assert api.events[0]["dx"] == hi.normalize_absolute(800, 600, (0, 0, 1920, 1080))[0]


def test_move_to_uses_cursor_as_start():
    api = FakeMouse(cursor=(50, 60), screen=(0, 0, 1920, 1080))
    evidence = hi.move_to(400, 300, api=api, sleep=Sleeper(), rng=random.Random(5))
    assert evidence["from"] == [50, 60]
    assert evidence["to"] == [400, 300]
    assert evidence["points"] > 1


def test_send_button_maps_flag_and_extra_info():
    api = FakeMouse()
    hi.send_button(hi.MOUSEEVENTF_LEFTDOWN, api=api, extra_info=0x1234)
    assert api.events[0]["flags"] == hi.MOUSEEVENTF_LEFTDOWN
    assert api.events[0]["data"] == 0x1234


def test_send_button_without_extra_info_omits_data():
    api = FakeMouse()
    hi.send_button(hi.MOUSEEVENTF_LEFTUP, api=api)
    assert "data" not in api.events[0]


def test_click_at_double_right_presses_and_releases_pairs():
    api = FakeMouse(cursor=(0, 0), screen=(0, 0, 1920, 1080))
    evidence = hi.click_at(
        10, 10, button="right", click_type="double", duration_ms=0, api=api
    )
    flags = [event["flags"] for event in api.events[1:]]
    assert flags == [
        hi.MOUSEEVENTF_RIGHTDOWN,
        hi.MOUSEEVENTF_RIGHTUP,
        hi.MOUSEEVENTF_RIGHTDOWN,
        hi.MOUSEEVENTF_RIGHTUP,
    ]
    assert evidence["repeats"] == 2
    assert evidence["clickType"] == "double"


def test_click_at_left_single_has_one_pair():
    api = FakeMouse(cursor=(0, 0), screen=(0, 0, 1920, 1080))
    hi.click_at(10, 10, duration_ms=0, api=api)
    flags = [event["flags"] for event in api.events[1:]]
    assert flags == [hi.MOUSEEVENTF_LEFTDOWN, hi.MOUSEEVENTF_LEFTUP]


# -- 到位后的犹豫 -----------------------------------------------------------


def test_settle_uses_random_delay_within_range():
    sleeper = Sleeper()
    value = hi.settle(rng=random.Random(1), sleep=sleeper)
    assert hi.SETTLE_MIN_MS <= value <= hi.SETTLE_MAX_MS
    assert sleeper.calls == [value / 1000.0]


def test_settle_zero_range_skips_sleep():
    sleeper = Sleeper()
    assert hi.settle(min_ms=0, max_ms=0, sleep=sleeper) == 0.0
    assert sleeper.calls == []


def test_settle_swaps_reversed_range():
    sleeper = Sleeper()
    value = hi.settle(min_ms=100, max_ms=50, rng=random.Random(2), sleep=sleeper)
    assert 50 <= value <= 100
    assert len(sleeper.calls) == 1
