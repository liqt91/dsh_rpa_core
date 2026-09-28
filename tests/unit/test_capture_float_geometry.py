"""捕获浮窗避让几何（M41 S3）：纯函数 ``capture_float_origin``。

维护者 2026-09-28 报：「捕捉元素的桌面悬浮框，在鼠标即将移动到悬浮框的时候，
悬浮框移动到屏幕另一侧，避免挡住实际需要捕获的元素」。浮窗是**真实窗口**
（无 ``WS_EX_TRANSPARENT``、不吃点击穿透），压在鼠标路径上时会同时挡住视觉与
点击，所以判据落在「鼠标进入默认位置矩形外扩 ``AVOID_PAD`` 时，浮窗翻到左上角」。
"""

from __future__ import annotations

import pytest

from rpa_core.gui.capture_float import (
    _HEIGHT,
    _MARGIN,
    _WIDTH,
    AVOID_PAD,
    capture_float_origin,
)

AREA = (0, 0, 1920, 1080)
SIZE = (_WIDTH, _HEIGHT)
BOTTOM_RIGHT = (AREA[2] - _WIDTH - _MARGIN, AREA[3] - _HEIGHT - _MARGIN)
TOP_LEFT = (_MARGIN, _MARGIN)


def test_no_cursor_places_the_window_at_the_default_corner() -> None:
    assert capture_float_origin(AREA, SIZE, None) == BOTTOM_RIGHT


def test_far_away_cursor_keeps_the_default_corner() -> None:
    assert capture_float_origin(AREA, SIZE, (100, 100)) == BOTTOM_RIGHT


def test_cursor_on_the_window_flips_it_to_the_other_side() -> None:
    # 旧行为（永远贴右下角）在这里会红——这是判据的牙齿。
    inside = (BOTTOM_RIGHT[0] + SIZE[0] // 2, BOTTOM_RIGHT[1] + SIZE[1] // 2)
    assert capture_float_origin(AREA, SIZE, inside) == TOP_LEFT


@pytest.mark.parametrize("gap", [0, 1, AVOID_PAD // 2, AVOID_PAD])
def test_cursor_about_to_reach_the_window_moves_it_early(gap: int) -> None:
    # 「即将移动到」：还没压上、只差 ≤ AVOID_PAD 就要碰到——已经让开了。
    approaching = (BOTTOM_RIGHT[0] - gap, BOTTOM_RIGHT[1] - gap)
    assert capture_float_origin(AREA, SIZE, approaching) == TOP_LEFT


def test_cursor_beyond_the_pad_returns_to_the_default_corner() -> None:
    beyond = (
        BOTTOM_RIGHT[0] - AVOID_PAD - 1,
        BOTTOM_RIGHT[1] + SIZE[1] + AVOID_PAD + 1,
    )
    assert capture_float_origin(AREA, SIZE, beyond) == BOTTOM_RIGHT


def test_decision_does_not_depend_on_where_the_window_currently_is() -> None:
    """无状态：鼠标在左上（= 浮窗上一帧待的地方）时答案仍是右下 → 自动回位。"""
    for _ in range(3):
        assert capture_float_origin(AREA, SIZE, TOP_LEFT) == BOTTOM_RIGHT


def test_window_always_ends_up_on_the_opposite_side_of_the_cursor() -> None:
    assert capture_float_origin(AREA, SIZE, (10, 10)) == BOTTOM_RIGHT
    assert capture_float_origin(AREA, SIZE, (AREA[2] - 10, AREA[3] - 10)) == TOP_LEFT


def test_avoid_pad_covers_a_fast_flick_between_refresh_ticks() -> None:
    """避让节拍 40ms（``app.py``）：快速甩动鼠标也要在压上之前让开。

    取常见快速移动 ~1500 px/s 作下限 → 一帧 ≈ 60px；pad 小于它等于没避让。
    """
    assert AVOID_PAD >= 60
