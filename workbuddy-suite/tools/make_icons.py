"""生成三件套用到的图标（扩展 16/32/48/128、PPX 窗口 512 + ico）。

只依赖 Pillow。图标思路：深色圆角底 + 蓝色取景框 + 中心点 —— 在 16px 下也能看出
「瞄准一个元素」这件事，比放一个字母清晰得多。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

SUITE = Path(__file__).resolve().parents[1]
EXT_ICONS = SUITE / "extension" / "icons"
PPX_ASSETS = SUITE / "editor" / "ppx" / "assets"

BG_TOP = (24, 30, 41)
BG_BOTTOM = (12, 15, 20)
ACCENT = (79, 140, 255)
ACCENT_SOFT = (122, 92, 255)


def _rounded_mask(size: int, radius_ratio: float = 0.22) -> Image.Image:
    mask = Image.new("L", (size * 4, size * 4), 0)
    draw = ImageDraw.Draw(mask)
    radius = int(size * 4 * radius_ratio)
    draw.rounded_rectangle((0, 0, size * 4 - 1, size * 4 - 1), radius=radius, fill=255)
    return mask.resize((size, size), Image.LANCZOS)


def _gradient(size: int, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    canvas = Image.new("RGB", (1, size))
    for y in range(size):
        ratio = y / max(1, size - 1)
        canvas.putpixel(
            (0, y),
            tuple(int(top[i] + (bottom[i] - top[i]) * ratio) for i in range(3)),
        )
    return canvas.resize((size, size), Image.BICUBIC)


def _icon(size: int) -> Image.Image:
    scale = 4
    big = size * scale
    base = _gradient(big, BG_TOP, BG_BOTTOM).convert("RGBA")

    ring = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(ring)
    pad = int(big * 0.22)
    width = max(2 * scale, int(big * 0.055))
    # 取景框四角：比整圈更「瞄准」，也更抗小尺寸糊化
    arm = int((big - pad * 2) * 0.32)
    for x0, y0, x1, y1 in (
        (pad, pad, pad + arm, pad + width),
        (pad, pad, pad + width, pad + arm),
        (big - pad - arm, pad, big - pad, pad + width),
        (big - pad - width, pad, big - pad, pad + arm),
        (pad, big - pad - width, pad + arm, big - pad),
        (pad, big - pad - arm, pad + width, big - pad),
        (big - pad - arm, big - pad - width, big - pad, big - pad),
        (big - pad - width, big - pad - arm, big - pad, big - pad),
    ):
        draw.rectangle((x0, y0, x1, y1), fill=ACCENT + (255,))

    # 中心点 + 十字准线
    cx = cy = big // 2
    r = int(big * 0.075)
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 255, 255, 235))
    draw.line((cx - r * 2, cy, cx - r * 2, cy), fill=(255, 255, 255, 0))

    glow = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    gr = int(big * 0.30)
    radius = (cx - gr, cy - gr, cx + gr, cy + gr)
    gdraw.ellipse(radius, outline=ACCENT_SOFT + (70,), width=max(1, scale))

    composed = Image.alpha_composite(base, glow)
    composed = Image.alpha_composite(composed, ring)
    composed.putalpha(_rounded_mask(size * scale))
    return composed.resize((size, size), Image.LANCZOS)


def main() -> int:
    EXT_ICONS.mkdir(parents=True, exist_ok=True)
    PPX_ASSETS.mkdir(parents=True, exist_ok=True)
    for size in (16, 32, 48, 128):
        _icon(size).save(EXT_ICONS / f"icon-{size}.png")
    big = _icon(512)
    big.save(PPX_ASSETS / "logo.png")
    sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    _icon(256).save(PPX_ASSETS / "logo.ico", sizes=sizes)
    print(f"icons: 4 extension + 2 ppx under {SUITE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
