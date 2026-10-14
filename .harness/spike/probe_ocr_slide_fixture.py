"""为 `tests/contract/test_ocr_contract.py` 挑夹具：**先实测，再写期望**。

只读探针：不写仓库文件，产物打到 stdout（调用方重定向到文件再读）。
目的是回答三件事：

1. 「纹理背景 + 原位裁剪块图」的相关度是否够高（能否当**正**样本）；
2. 「挖了洞的背景 + 同一块图」的相关度是否够低（能否当**负**样本）；
3. 命中位置的 ``x`` 到底是**中心**还是**左边界**——用已知裁剪框 (140,30,175,65)
   反推：中心应是 157/47，左边界应是 140/30。这是文档口径的唯一硬判据来源。

同时把 `ocr.detect` 在合成数字图上的框数一并量出来（判据只取「>=1」与形状）。
"""

from __future__ import annotations

import base64
import io
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from rpa_core.workers import ocr_worker  # noqa: E402

CROP = (140, 30, 175, 65)


def textured(width: int = 200, height: int = 100):
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (width, height), (205, 215, 225))
    d = ImageDraw.Draw(img)
    for i in range(0, width, 6):
        d.line([(i, 0), (i, height)], fill=(180 + (i * 7) % 60, 195, 210), width=2)
    for j in range(0, height, 8):
        d.line([(0, j), (width, j)], fill=(195, 190 + (j * 9) % 50, 205), width=2)
    return img


def png(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def main() -> int:
    from PIL import Image

    plain = textured()
    template = plain.crop(CROP)
    holed = textured()
    from PIL import ImageDraw

    ImageDraw.Draw(holed).rectangle(CROP, fill=(35, 35, 40))

    random.seed(7)
    noise = Image.new("RGB", (20, 20))
    px = noise.load()
    for y in range(20):
        for x in range(20):
            px[x, y] = (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))

    print(f"# 背景 {plain.size}, 块图 {template.size}, 裁剪框 {CROP} -> 中心应为 "
          f"{(CROP[0] + CROP[2]) // 2}/{(CROP[1] + CROP[3]) // 2}, 左边界为 {CROP[0]}/{CROP[1]}")

    scenarios = {
        "positive: plain bg + in-place crop": (template, plain),
        "negative: holed bg + same crop": (template, holed),
        "negative: noise block + holed bg": (noise, holed),
    }
    for label, (tpl, bg) in scenarios.items():
        print(f"\n## {label}")
        for simple in (False, True):
            out = ocr_worker.slide_match(
                png(tpl), png(bg), simple_target=simple, min_confidence=0.0
            )
            print(f"   simpleTarget={simple!s:<5} -> {out}")

    print("\n## minConfidence 拦截（negative: noise + holed, simple_target=False）")
    for thr in (0.5, 0.9):
        try:
            out = ocr_worker.slide_match(
                png(noise), png(holed), simple_target=False, min_confidence=thr
            )
            print(f"   minConfidence={thr} -> 未拦截(意外): {out}")
        except ocr_worker._OcrNoMatch as exc:
            print(f"   minConfidence={thr} -> 已拦截: {exc}")

    print("\n## detect 在合成数字图上的框数（判据只取 >=1 与形状）")
    digits = Image.new("RGB", (240, 70), "white")
    draw = ImageDraw.Draw(digits)
    x = 12
    for char in "5834":
        draw.rectangle([x, 10, x + 34, 58], outline=(0, 0, 0), width=4)
        draw.ellipse([x + 6, 20, x + 28, 48], outline=(0, 0, 0), width=4)
        x += 52
    got = ocr_worker.detect(png(digits))
    print(f"   size={digits.size} count={got['count']} boxes={got['boxes']}")

    print("\n## 夹具 base64 长度（写进测试时的体积参考）")
    for label, blob in (
        ("template", png(template)),
        ("plain", png(plain)),
        ("holed", png(holed)),
        ("noise", png(noise)),
        ("digits", png(digits)),
    ):
        print(f"   {label:<9} {len(base64.b64encode(blob))} chars")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
