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
