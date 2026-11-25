"""编辑框几何：截图区必须让窗口始终塞进工作区。

小屏（1366×768）上把截图区按 280 硬排，「保存/取消」会被顶到屏幕外——用户点不到
就只能关框重来。这里用不变量把那条线钉住。
"""

from __future__ import annotations

import pytest

from rpa_core.capture_kit import edit_dialog as edition


@pytest.mark.parametrize("work", [768, 900, 1080, 1440, 2160])
def test_window_always_fits_work_area(monkeypatch, work):
    monkeypatch.setattr(edition, "_work_area_height", lambda: work)
    for base in (edition._TREE_BASE_H, edition._DESKTOP_BASE_H):
        shot = edition._fit_shot_height(base)
        total = base + (shot + edition._SHOT_GAP if shot else 0)
        assert total + edition._WINDOW_CHROME <= work, (base, shot, total, work)


def test_shot_is_dropped_when_there_is_no_room(monkeypatch):
    monkeypatch.setattr(edition, "_work_area_height", lambda: 768)
    # 768 屏连桌面形态的固定部分都快放不下，截图区必须整个不建
    assert edition._fit_shot_height(edition._DESKTOP_BASE_H) == 0
    assert edition._fit_shot_height(edition._TREE_BASE_H) == 0


def test_shot_keeps_full_height_on_roomy_screen(monkeypatch):
    monkeypatch.setattr(edition, "_work_area_height", lambda: 1080)
    assert edition._fit_shot_height(edition._TREE_BASE_H) == edition._SHOT_H
    assert edition._fit_shot_height(edition._DESKTOP_BASE_H) == edition._SHOT_H


def test_shot_shrinks_between_min_and_full(monkeypatch):
    """中等屏幕：给多少用多少，但不低于下限（低于下限就不要了）。"""
    monkeypatch.setattr(edition, "_work_area_height", lambda: 980)
    shot = edition._fit_shot_height(edition._DESKTOP_BASE_H)
    assert edition._SHOT_MIN <= shot < edition._SHOT_H
    expected = 980 - edition._WINDOW_CHROME - edition._DESKTOP_BASE_H - edition._SHOT_GAP
    assert shot == expected


def test_height_constants_stay_consistent():
    """总高常量必须等于「固定部分 + 截图区 + 间距」，否则改一个忘一个。"""
    assert edition._TREE_H == edition._TREE_BASE_H + edition._SHOT_H + edition._SHOT_GAP
    assert (
        edition._DESKTOP_H
        == edition._DESKTOP_BASE_H + edition._SHOT_H + edition._SHOT_GAP
    )


def test_window_height_tracks_visible_rows():
    """A 档：4 层只占 4 行的高度，不为不存在的 6 层留白（视觉「土」的主因）。"""
    assert edition._base_height_for(4, False) == edition._TREE_BASE_H - 6 * 32
    assert edition._base_height_for(3, True) == edition._DESKTOP_BASE_H - 7 * 32
    assert edition._visible_rows(4, False) == 4
    assert edition._content_shift(4, False) == 6 * 32


def test_row_count_beyond_page_keeps_full_height():
    """超过一屏的行靠滚动，窗口不再长高（否则 30 层会把保存按钮顶出屏幕）。"""
    assert edition._base_height_for(30, False) == edition._TREE_BASE_H
    assert edition._base_height_for(30, True) == edition._DESKTOP_BASE_H
    assert edition._base_height_for(0, False) == edition._TREE_BASE_H - 9 * 32


def test_clip_adds_ellipsis_only_when_too_wide():
    """行文字放不下时必须补省略号。

    真机事故（2026-11-25 用户截图）：BUTTON 不认 DT_END_ELLIPSIS，长 class 串
    被硬切在边框上，看着像被右边的灰块压掉一截。
    """
    measure = len  # 等宽模型：一个字符算一像素
    assert edition._clip_to_width("short", 10, measure) == "short"
    assert edition._clip_to_width("abcdefghij", 6, measure) == "abcde…"
    assert edition._clip_to_width("abc", 0, measure) == ""
    assert edition._clip_to_width("", 10, measure) == ""


def test_scrollbar_width_is_added_back_to_frame():
    """WS_VSCROLL 会从客户区吃掉滚动条宽度，外框必须补回来。

    AdjustWindowRectEx 明文不算 WS_VSCROLL/WS_HSCROLL；漏了这段，按客户区满宽
    摆的属性值框和「取消」按钮就被窗口右边缘裁掉（探针实测 client 763 vs 780）。
    """
    win32con = pytest.importorskip("win32con")
    win32api = pytest.importorskip("win32api")

    style = win32con.WS_POPUP | win32con.WS_CAPTION | win32con.WS_SYSMENU
    plain, _ = edition._win32.adjust_window_size(780, 552, style, 0)
    scrolled, _ = edition._win32.adjust_window_size(
        780, 552, style | win32con.WS_VSCROLL, 0
    )
    assert scrolled - plain == win32api.GetSystemMetrics(win32con.SM_CXVSCROLL)
