"""捕获期的显式图像 proposal（M77）：给自绘控件一条可入库的候选。

与 M75 的运行期图像定位共用同一份 locator 形状（``driver="image"`` +
``windowHandle`` + ``imageTemplate``/``templateSize``/``minConfidence``），但**不复用
定位结果**：这里只是把光标周围一小块屏幕截图存成模板，作为「用户显式选图像捕获」
时落盘的候选，命中数留给之后的校验/运行期去测。

铁律：本模块**只由显式开关调用**（``prefer_image=True``）。语义层有命中也不自动
改走这里，ADR 0003 的静默降级红线在 ``capture.image_guard`` 与调用方一起守住。
"""

from __future__ import annotations

import base64
import hashlib
import io
from collections.abc import Callable
from typing import Any

from rpa_core.capture.image_guard import TEMPLATE_SCHEMA

_PROTOCOL_HEAD = "data:image/png;base64,"

# 模板半径（像素）：模板边长 = 2 * DEFAULT_RADIUS。
# ★ 这是「红框」与「实际裁剪」共用的唯一数值——两处各写一个 16 就会漂移成
# 「框在这里、截在那里」，用户看到的高亮就骗人了（M84）。
DEFAULT_RADIUS = 16


def clip_template_rect(
    window_rect: tuple[int, int, int, int],
    point: tuple[int, int],
    *,
    radius: int = DEFAULT_RADIUS,
) -> tuple[int, int, int, int] | None:
    """光标周围的正方形模板区域（屏幕坐标，``(left, top, right, bottom)``）。

    **红框与模板裁剪的唯一几何权威**：hover 高亮画的就是这块，落盘的
    ``sourceRect`` 也是这块——同源之后不可能出现「框住按钮 A、截到旁边的 B」。

    窗口边缘处自动裁到窗口内（不越界截到隔壁窗口）；点不在窗口矩形内、
    半径非法、矩形退化 ⇒ ``None``（调用方据此不画框、不产 proposal）。
    """
    try:
        left, top, right, bottom = (int(v) for v in window_rect)
        px, py = (int(point[0]), int(point[1]))
        radius = int(radius)
    except (TypeError, ValueError, IndexError):
        return None
    if radius <= 0 or right <= left or bottom <= top:
        return None
    if px < left or py < top or px >= right or py >= bottom:
        return None
    return (
        max(left, px - radius),
        max(top, py - radius),
        min(right, px + radius),
        min(bottom, py + radius),
    )


def make_image_proposal(
    hwnd: int,
    point: tuple[int, int],
    *,
    radius: int = DEFAULT_RADIUS,
    min_confidence: float = 0.8,
    grab_window: Callable[[int], dict[str, Any] | None] | None = None,
) -> dict[str, Any] | None:
    """从窗口截图里裁出光标周围的正方形模板，产出显式 V2 image locator。

    ``grab_window`` 可注入替身，形状与 ``screen_shot.grab_window`` 完全一致
    （``dataUrl`` / ``windowOrigin`` / ``imageSize`` / ``dpi`` / ``scale``）。
    M80 起 ``dpi/scale`` 是必填 provenance：拿不到就不产 proposal，
    **不猜 96**——否则跨缩放时无法判断是模板还是匹配侧出了错。

    截图失败、点不在窗口内、参数非法都返回 ``None``——proposal 是增强，
    绝不能把捕获本身拖成失败。返回 dict 时 ``driver/backend`` 都是字面量
    ``"image"``（M75 守卫只认这个等值），``matchedCount`` 留给校验期实测。
    """
    if not isinstance(hwnd, int) or isinstance(hwnd, bool) or hwnd <= 0:
        return None
    try:
        px, py = (int(point[0]), int(point[1]))
        radius = int(radius)
        if radius <= 0:
            return None
        confidence = float(min_confidence)
    except (TypeError, ValueError, IndexError):
        return None

    if grab_window is None:
        from rpa_core.capture.screen_shot import grab_window as grab_window

    shot = grab_window(hwnd)
    if not isinstance(shot, dict):
        return None
    data_url = shot.get("dataUrl")
    origin = shot.get("windowOrigin")
    size = shot.get("imageSize")
    if not isinstance(data_url, str) or not data_url.startswith(_PROTOCOL_HEAD):
        return None
    if (
        not isinstance(origin, (list, tuple))
        or not isinstance(size, (list, tuple))
        or len(origin) != 2
        or len(size) != 2
    ):
        return None

    # M80：DPI provenance 是 V2 的硬字段。缺了就拒绝产出，而不是「按 1.0 先存着」。
    try:
        capture_dpi = int(shot["dpi"])
        capture_scale = float(shot["scale"])
    except (KeyError, TypeError, ValueError):
        return None
    if capture_dpi <= 0 or capture_scale <= 0:
        return None

    try:
        raw = base64.b64decode(data_url[len(_PROTOCOL_HEAD):], validate=True)
        from PIL import Image

        with Image.open(io.BytesIO(raw)) as image:
            w, h = image.size
            ox, oy = (int(origin[0]), int(origin[1]))
            # 几何走 clip_template_rect（与 hover 红框同一份）——图内坐标 = 屏幕点 − 窗口原点，
            # 因为 grab_window 截的就是窗口矩形本身（1:1，不缩放）。
            box = clip_template_rect((ox, oy, ox + w, oy + h), (px, py), radius=radius)
            if box is None:
                return None
            screen_left, screen_top, screen_right, screen_bottom = box
            left, top = screen_left - ox, screen_top - oy
            right, bottom = screen_right - ox, screen_bottom - oy
            if right <= left or bottom <= top:
                return None
            patch = image.crop((left, top, right, bottom)).convert("RGB")
            buffer = io.BytesIO()
            patch.save(buffer, format="PNG")
            patch_png = buffer.getvalue()
            template = base64.b64encode(patch_png).decode("ascii")
            template_size = [right - left, bottom - top]
            source_rect = [screen_left, screen_top, right - left, bottom - top]
    except Exception:  # noqa: BLE001 - 截图/裁剪失败吞掉，proposal 不挡捕获
        return None

    return {
        "driver": "image",
        "backend": "image",
        "windowHandle": hwnd,
        "imageTemplate": template,
        "templateSize": template_size,
        "templateSchema": TEMPLATE_SCHEMA,
        "templateHash": hashlib.sha256(patch_png).hexdigest(),
        "captureDpi": capture_dpi,
        "captureScale": capture_scale,
        "sourceRect": source_rect,
        "minConfidence": confidence,
        "proposal": True,
    }


__all__ = ["DEFAULT_RADIUS", "TEMPLATE_SCHEMA", "clip_template_rect", "make_image_proposal"]
