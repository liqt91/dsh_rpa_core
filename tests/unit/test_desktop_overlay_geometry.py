"""桌面捕获高亮框的几何判据（M41）。

维护者 2026-09-28：「捕获元素的框跟影刀的一样，躲着鼠标，否则影响其他元素的捕捉。」

一手归因（``.harness/spike/probe_m41_hover_overlay.py``，真机 A/B）：

- §3：overlay **不参与** UIA 命中——同一批 13 个采样点，overlay 显示前后
  ``_element_from_point`` 结果差异 0/13。所以「影响捕捉」不是功能面（不会捕错元素），
  而是**视觉面**：指针被框线压住，用户看不清指到哪、进而影响继续选下一个元素。
- §7：框线带占元素**最外 3px**（旧实现）→ 6/13 个点位的指针 ±2px 内有红色框线像素
  （最近距离 0）；外移 3px → 2/13；**外移 5px → 0/13**（最近 4px）。

本文件钉住的就是 §7 那个几何：框线带整条落在元素**之外**，且内沿距元素边界 ≥ 指针热区
（2px）。纯函数、不需要真机。
"""

from __future__ import annotations

import pytest

from rpa_core.capture.desktop_agent import (
    OVERLAY_BORDER,
    OVERLAY_OUTSET,
    _HoverOverlay,
    overlay_bounds,
)

# 与 probe_m41_hover_overlay.py §7 的实测判据同源：指针点 ±2px 内不得有框线像素
CURSOR_HOTSPOT = 2

# 元素尺寸取样：普通控件 / 16px 图标（最坏情况）/ 整窗口
ELEMENT_RECTS = ((100, 100, 140, 124), (0, 0, 16, 16), (10, 10, 1000, 700))
# 屏幕左上角（position:fixed 下元素滚出视口时 left/top 可为负）
NEGATIVE_RECTS = ((-40, -30, 120, 90),)


def _overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    """半开区间相交。"""
    return a[0] < b[1] and b[0] < a[1]


def _frame_bands(left: int, top: int, right: int, bottom: int) -> dict[str, tuple[int, int]]:
    """按 ``_HoverOverlay.show_rect`` 的 region 规则，算出四条边框带的屏幕坐标区间。

    region 是「整窗减去内缩 ``BORDER`` 的挖空区」，所以带落在窗口四条边上。
    """
    win_left, win_top, win_right, win_bottom = overlay_bounds(left, top, right, bottom)
    return {
        "left": (win_left, win_left + OVERLAY_BORDER),
        "right": (win_right - OVERLAY_BORDER, win_right),
        "top": (win_top, win_top + OVERLAY_BORDER),
        "bottom": (win_bottom - OVERLAY_BORDER, win_bottom),
    }


def _legacy_frame_bands(
    left: int, top: int, right: int, bottom: int
) -> dict[str, tuple[int, int]]:
    """旧实现（内缩）的框线带：bounds 就是元素 rect 本身。"""
    return {
        "left": (left, left + OVERLAY_BORDER),
        "right": (right - OVERLAY_BORDER, right),
        "top": (top, top + OVERLAY_BORDER),
        "bottom": (bottom - OVERLAY_BORDER, bottom),
    }


def test_outset_is_border_plus_cursor_hotspot() -> None:
    """外移量 = 框线宽 + 指针热区——这是 §7 实测出的最小值（5 = 3 + 2）。"""
    assert _HoverOverlay.BORDER == OVERLAY_BORDER
    assert OVERLAY_OUTSET == OVERLAY_BORDER + CURSOR_HOTSPOT


@pytest.mark.parametrize("rect", ELEMENT_RECTS + NEGATIVE_RECTS)
def test_frame_band_lies_entirely_outside_the_element(rect) -> None:
    """四条框线带都必须整条落在元素之外（外沿 ≥ OUTSET，内沿 ≥ 热区）。"""
    left, top, right, bottom = rect
    bands = _frame_bands(left, top, right, bottom)

    assert bands["left"][1] <= left - CURSOR_HOTSPOT
    assert bands["top"][1] <= top - CURSOR_HOTSPOT
    assert bands["right"][0] >= right + CURSOR_HOTSPOT
    assert bands["bottom"][0] >= bottom + CURSOR_HOTSPOT

    # 外沿既不贴元素、也不飘太远：正好 OUTSET
    assert bands["left"][0] == left - OVERLAY_OUTSET
    assert bands["right"][1] == right + OVERLAY_OUTSET


@pytest.mark.parametrize("rect", ELEMENT_RECTS)
def test_cursor_hotspot_never_touches_frame_band(rect) -> None:
    """元素内**任意位置**的指针，其 ±2px 热区都碰不到框线带（§7 判据的解析形式）。"""
    left, top, right, bottom = rect
    bands = _frame_bands(left, top, right, bottom)

    for x in range(left, right + 1):
        hotspot = (x - CURSOR_HOTSPOT, x + CURSOR_HOTSPOT)
        assert not _overlaps(hotspot, bands["left"]), f"x={x} 压到左边框"
        assert not _overlaps(hotspot, bands["right"]), f"x={x} 压到右边框"
    for y in range(top, bottom + 1):
        hotspot = (y - CURSOR_HOTSPOT, y + CURSOR_HOTSPOT)
        assert not _overlaps(hotspot, bands["top"]), f"y={y} 压到上边框"
        assert not _overlaps(hotspot, bands["bottom"]), f"y={y} 压到下边框"


def test_legacy_geometry_would_touch_the_cursor() -> None:
    """旧几何**确实**会压住指针——证明上面的判据不是空转（否则门禁是假绿灯）。

    与 §7 实测对照：旧实现下 6/13 个采样点覆盖，其中「指针贴元素左边」必然命中。
    """
    left, top, right, bottom = 100, 100, 140, 124
    bands = _legacy_frame_bands(left, top, right, bottom)
    hotspot = (left - CURSOR_HOTSPOT, left + CURSOR_HOTSPOT)  # 指针贴着元素左边界
    assert _overlaps(hotspot, bands["left"])


def test_show_rect_uses_overlay_bounds() -> None:
    """接线：``show_rect`` 必须真的走 ``overlay_bounds``（否则外扩只是摆设）。

    ``show_rect`` 本体是 win32 调用（`CreateRectRgn` / `SetWindowPos`），offscreen 测不到；
    但它的**几何来源**可以钉住：region 的挖空区完全由 bounds 决定，而 bounds 只应来自
    ``overlay_bounds``。读源码断言这条接线（同 M40 的
    ``test_run_gui_wires_sliced_refresh_on_workbench`` 口径）——少了它，把
    ``bounds = overlay_bounds(...)`` 换回 ``bounds = (left, top, right, bottom)``
    这种「纯函数都对、执行器没用上」的注入不会被任何用例发现。
    """
    import inspect

    source = inspect.getsource(_HoverOverlay.show_rect)
    assert "overlay_bounds(" in source
    assert "self.BORDER" in source  # region 的挖空区按框线宽内缩
    assert "RGN_DIFF" in source  # 挖空 = 只留边框带
