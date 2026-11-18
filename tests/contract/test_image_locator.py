"""M75 S1 图像定位能力层的判据：纯逻辑用替身，任意平台可跑。

判据纪律（M73/用户 memory 教训）：坐标/置信度/命中数一律**等值断言**，不写 `>=`。
"""

from __future__ import annotations

import base64
from dataclasses import FrozenInstanceError

import pytest

from rpa_core.capture.image_locator import (
    ImageLocatorError,
    ImageMatch,
    locate_by_template,
    locate_by_text,
    scale_candidates,
)


def _shot(x: int = 100, y: int = 200, w: int = 800, h: int = 600) -> dict:
    """grab_window 形状的替身截图：PNG 字节是 1x1 占位（替身 matcher 不看内容）。"""
    png = base64.b64encode(b"fake-png").decode("ascii")
    return {
        "dataUrl": "data:image/png;base64," + png,
        "windowOrigin": [x, y],
        "imageSize": [w, h],
        "windowRect": [x, y, x + w, y + h],
    }


class _Matcher:
    def __init__(self, cx: int, cy: int, conf: float | None) -> None:
        self.cx, self.cy, self.conf = cx, cy, conf
        self.calls: list[tuple[bytes, bytes]] = []

    def match(self, screen: bytes, template: bytes) -> tuple[int, int, float | None]:
        self.calls.append((screen, template))
        return self.cx, self.cy, self.conf


class _Detector:
    def __init__(self, boxes: list[dict]) -> None:
        self.boxes_list = boxes

    def boxes(self, screen: bytes) -> list[dict]:
        return self.boxes_list


class _Resizer:
    """替身重采样：只记尺度，并把尺度写进模板字节供 matcher 辨认。"""

    def __init__(self) -> None:
        self.scales: list[float] = []

    def resize(self, template: bytes, scale: float) -> tuple[bytes, tuple[int, int]]:
        self.scales.append(scale)
        return template + f":{scale}".encode("ascii"), (
            int(20 * scale), int(10 * scale)
        )


class _ScaleMatcher:
    """只有期望尺度的模板给高分，其余低分——证明「择优而非按顺序第一个」。"""

    def __init__(self, best_scale: float) -> None:
        self.best = f":{best_scale}".encode("ascii")

    def match(self, screen: bytes, template: bytes) -> tuple[int, int, float | None]:
        return 300, 150, 0.95 if self.best in template else 0.40


# ---- 模板匹配 ----------------------------------------------------------------


def test_template_center_to_screen_absolute_rect_equals():
    """中心点 (300,150) + 窗口原点 (100,200) + 模板 20x10 ⇒ 屏幕绝对矩形精确。"""
    m = _Matcher(cx=300, cy=150, conf=0.95)
    got = locate_by_template(
        _shot(100, 200), b"tpl", (20, 10), min_confidence=0.8, matcher=m
    )
    # 中心 x=100+300=400，宽20 ⇒ left=400-10=390；y=200+150=350，高10 ⇒ top=350-5=345
    assert got.as_rect() == {"left": 390, "top": 345, "width": 20, "height": 10}
    assert got.confidence == 0.95
    assert got.count == 1


def test_template_confidence_below_threshold_is_zero_hit():
    """置信度 0.79 < 阈值 0.8 ⇒ count=0，不返回近似矩形（等值断言）。"""
    got = locate_by_template(
        _shot(), b"tpl", (20, 10), min_confidence=0.8,
        matcher=_Matcher(300, 150, 0.79),
    )
    assert got.count == 0
    assert got.as_rect() == {"left": 0, "top": 0, "width": 0, "height": 0}


def test_template_none_confidence_is_zero_hit():
    """置信度缺失（引擎退化）⇒ 零命中，不当成满分。"""
    got = locate_by_template(
        _shot(), b"tpl", (20, 10), min_confidence=0.0,
        matcher=_Matcher(300, 150, None),
    )
    assert got.count == 0


def test_template_odd_template_size_uses_floor_half():
    """奇数尺寸用 // 2（与中心还原口径一致），不是四舍五入。"""
    got = locate_by_template(
        _shot(0, 0), b"tpl", (21, 11), min_confidence=0.5,
        matcher=_Matcher(10, 10, 0.9),
    )
    # left=10-21//2=10-10=0, top=10-11//2=10-5=5
    assert got.as_rect() == {"left": 0, "top": 5, "width": 21, "height": 11}


def test_template_invalid_size_rejected():
    with pytest.raises(ImageLocatorError, match="positive"):
        locate_by_template(
            _shot(), b"tpl", (0, 10), matcher=_Matcher(1, 1, 0.9)
        )


def test_template_feeds_png_bytes_to_matcher():
    """截图 dataUrl 必须被解成 image/png 字节喂给 matcher（复用口径，不是原样 dataUrl）。"""
    m = _Matcher(1, 1, 0.9)
    locate_by_template(_shot(), b"tpl", (2, 2), matcher=m)
    assert m.calls[0][1] == b"tpl"
    assert m.calls[0][0]  # 非空 bytes（解出的 PNG）


def test_scale_candidates_are_deterministic_and_do_not_widen_at_one():
    """M80：尺度候选等值；比例为 1 时不凭空多跑，比例非 1 才补 1.0 与 ±10%。"""
    assert scale_candidates(None) == (1.0,)
    assert scale_candidates(1.0) == (1.0,)
    assert scale_candidates(1.25) == (1.25, 1.0, 1.125, 1.375)


def test_template_cross_dpi_picks_best_scale_and_current_rect():
    """捕获 100% / 当前 125%：命中 1.25 尺度，矩形和 usedScale 都按当前屏幕算。"""
    resizer = _Resizer()
    got = locate_by_template(
        _shot(100, 200), b"tpl", (20, 10),
        min_confidence=0.8,
        matcher=_ScaleMatcher(1.25),
        capture_scale=1.0,
        current_scale=1.25,
        resampler=resizer,
    )
    assert resizer.scales == [1.25, 1.125, 1.375]  # 1.0 原尺寸无需重采样
    assert got.used_scale == 1.25
    assert got.confidence == 0.95
    assert got.as_rect() == {"left": 388, "top": 344, "width": 25, "height": 12}


def test_template_all_scales_below_threshold_is_zero_hit():
    """多尺度不是放宽阈值：所有候选都低于 minConfidence 仍 count=0。"""
    got = locate_by_template(
        _shot(), b"tpl", (20, 10),
        min_confidence=0.9,
        matcher=_Matcher(300, 150, 0.89),
        capture_scale=1.0,
        current_scale=1.25,
        resampler=_Resizer(),
    )
    assert got.count == 0
    assert got.as_rect() == {"left": 0, "top": 0, "width": 0, "height": 0}


# ---- 文本定位 ----------------------------------------------------------------


def test_text_hit_maps_to_screen_absolute_rect():
    """关键词命中框 + 窗口原点 ⇒ 屏幕绝对矩形，等值。"""
    det = _Detector([
        {"x": 50, "y": 60, "width": 30, "height": 20, "text": "登录", "confidence": 0.9}
    ])
    got = locate_by_text(_shot(100, 200), "登录", detector=det)
    assert got.as_rect() == {"left": 150, "top": 260, "width": 30, "height": 20}
    assert got.count == 1


def test_text_case_insensitive_substring():
    det = _Detector([{"x": 5, "y": 6, "width": 7, "height": 8, "text": "SignIn Now"}])
    got = locate_by_text(_shot(0, 0), "signin", detector=det)
    assert got.count == 1 and got.as_rect()["left"] == 5


def test_text_no_text_field_is_not_a_hit():
    """ddddocr detect 只出框不出字 ⇒ 没 text 的框不算命中（宁可零命中不假命中）。"""
    det = _Detector([{"x": 5, "y": 6, "width": 7, "height": 8}])  # 无 text
    got = locate_by_text(_shot(), "登录", detector=det)
    assert got.count == 0


def test_text_multi_hit_reports_count_gt_one():
    """多命中要报出来（歧义交给上层），不静默选一个当唯一。"""
    det = _Detector([
        {"x": 10, "y": 10, "width": 5, "height": 5, "text": "OK"},
        {"x": 20, "y": 20, "width": 5, "height": 5, "text": "OK"},
    ])
    got = locate_by_text(_shot(0, 0), "OK", detector=det)
    assert got.count == 2
    assert got.as_rect() == {"left": 10, "top": 10, "width": 5, "height": 5}  # 第一个框


def test_text_confidence_filter_is_equal_threshold():
    """置信度过滤：0.49 < 0.5 被拒、0.50 保留（等值边界）。"""
    det = _Detector([
        {"x": 1, "y": 1, "width": 2, "height": 2, "text": "Go", "confidence": 0.49},
        {"x": 3, "y": 3, "width": 2, "height": 2, "text": "Go", "confidence": 0.50},
    ])
    got = locate_by_text(_shot(0, 0), "Go", detector=det, min_confidence=0.5)
    assert got.count == 1 and got.as_rect()["left"] == 3


def test_empty_keyword_rejected():
    with pytest.raises(ImageLocatorError, match="keyword"):
        locate_by_text(_shot(), "  ", detector=_Detector([]))


# ---- 截图失败结构化 ----------------------------------------------------------


def test_no_screenshot_raises_structured_error():
    with pytest.raises(ImageLocatorError, match="unavailable"):
        locate_by_template(None, b"tpl", (2, 2), matcher=_Matcher(1, 1, 0.9))


def test_bad_window_origin_raises():
    bad = _shot()
    bad["windowOrigin"] = [1, 2, 3]
    with pytest.raises(ImageLocatorError, match="windowOrigin"):
        locate_by_template(bad, b"tpl", (2, 2), matcher=_Matcher(1, 1, 0.9))


def test_result_is_immutable_value():
    """ImageMatch 是 frozen dataclass — 结果不可被下游悄悄改坐标。"""
    m = ImageMatch(1, 2, 3, 4, 0.9, 1)
    with pytest.raises(FrozenInstanceError):
        m.left = 99  # type: ignore[misc]
