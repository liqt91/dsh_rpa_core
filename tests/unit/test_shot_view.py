"""截图预览的像素契约。

重点锁一件事：``load_shot_bitmap`` 吐的是 **BGR** 字节序（``StretchDIBits`` 的
24bpp DIB 规则）。写成 RGB 时红框会在真机上显示成蓝框——这个 bug 只在看真机截图
时才发现，所以必须由测试守住。
"""

from __future__ import annotations

import base64
import io

import pytest
from PIL import Image

from rpa_core.capture_kit.shot_view import _fit, load_shot_bitmap


def _data_url(size=(40, 30), color=(10, 20, 30)) -> str:
    image = Image.new("RGB", size, color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _pixel(bits: bytes, width: int, x: int, y: int) -> tuple[int, int, int]:
    offset = (y * width + x) * 3
    return tuple(bits[offset:offset + 3])  # type: ignore[return-value]


def test_bitmap_keeps_size_and_bgr_channel_order():
    bits, width, height = load_shot_bitmap({"dataUrl": _data_url()})
    assert (width, height) == (40, 30)
    assert len(bits) == 40 * 30 * 3
    # 源色 (10,20,30) 在 BGR 下应是 (30,20,10)
    assert _pixel(bits, width, 0, 0) == (30, 20, 10)


def test_element_box_is_drawn_in_red():
    shot = {"dataUrl": _data_url(), "box": {"x": 5, "y": 5, "width": 10, "height": 10}}
    bits, width, _ = load_shot_bitmap(shot)
    # 红 (255,59,48) 在 BGR 下是 (48,59,255)
    assert _pixel(bits, width, 5, 5) == (48, 59, 255)


@pytest.mark.parametrize(
    "shot",
    [None, {}, {"dataUrl": ""}, {"dataUrl": "garbage"}, {"dataUrl": "data:image/png;base64,@@@"}],
)
def test_broken_shots_degrade_to_none(shot):
    assert load_shot_bitmap(shot) is None


def test_box_outside_image_is_ignored_not_fatal():
    """框画到图外不能把整张图弄丢——预览仍然要能显示。"""
    shot = {"dataUrl": _data_url(), "box": {"x": 900, "y": 900, "width": 50, "height": 50}}
    loaded = load_shot_bitmap(shot)
    assert loaded is not None and loaded[1] == 40


def test_fit_preserves_aspect_ratio():
    # 宽图：按宽度贴合，上下留白
    x, y, width, height = _fit((1920, 1080), (300, 200))
    assert (x, y, width, height) == (0, 16, 300, 168)
    # 方图放进宽框：按高度贴合，左右居中
    assert _fit((100, 100), (300, 200)) == (50, 0, 200, 200)
