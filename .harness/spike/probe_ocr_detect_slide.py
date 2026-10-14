"""M60 探针②：`ocr.detect` / `ocr.slideMatch` 的 ddddocr 真实 API 取证。

**只读探针**：不改产品代码、不改仓库状态；输出到同目录 `_probe_ocr_detect_slide.out`。

为什么必须先探：M60 S1 的教训是**设计稿 ≠ 事实**——方案稿写了 `charsetRange`，
实测 ddddocr 1.6.1 根本没有这个参数与方法（`inspect.signature` 实证），S2 被迫重设计。
本探针把 detect / slide_match / slide_comparison 的**签名、返回形状、耗时、序列化体积**
全部实测出来，manifest 的字段名与输出键**照实测写**，不照方案稿写。

用法：./.venv/Scripts/python.exe .harness/spike/probe_ocr_detect_slide.py
"""

from __future__ import annotations

import inspect
import io
import json
import sys
import time
from pathlib import Path

OUT = Path(__file__).with_suffix(".out")
_lines: list[str] = []


def emit(text: str = "") -> None:
    _lines.append(text)
    print(text, flush=True)


def png_bytes(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def font(size: int):
    from PIL import ImageFont

    for name in ("arial.ttf", "DejaVuSans.ttf", "consola.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def make_text_image(text: str, size: tuple[int, int] = (240, 70)) -> bytes:
    """白底黑字、字符间距拉开——给 detection() 制造多个独立目标。"""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    f = font(44)
    x = 12
    for ch in text:
        draw.text((x, 8), ch, fill="black", font=f)
        x += 52
    return png_bytes(img)


def make_slider_pair(width: int = 320, height: int = 160, gap_x: int = 190):
    """合成一对「滑块缺口图 + 滑块块图」。

    background：带纹理的底图，在 (gap_x, 60) 处挖一个 50x50 的方块缺口（涂深色）；
    target    ：该位置的原始纹理块（正是被挖走的那一块）。
    ddddocr 的 slide_match 应报出 target 的 x ≈ gap_x。
    """
    from PIL import Image, ImageDraw

    bg = Image.new("RGB", (width, height), (200, 210, 220))
    d = ImageDraw.Draw(bg)
    # 确定性纹理（不用随机，便于复现）
    for i in range(0, width, 7):
        d.line([(i, 0), (i, height)], fill=(180 + (i * 3) % 60, 190, 205), width=2)
    for j in range(0, height, 9):
        d.line([(0, j), (width, j)], fill=(190, 185 + (j * 5) % 50, 200), width=2)

    box = (gap_x, 60, gap_x + 50, 110)
    target_img = bg.crop(box)  # 先取原始纹理
    d.rectangle(box, fill=(40, 40, 45))  # 再挖缺口
    return png_bytes(target_img), png_bytes(bg), box


def main() -> int:
    emit("=== M60 探针② ocr.detect / ocr.slideMatch 的 ddddocr 真实 API ===")
    emit(f"python: {sys.version.split()[0]}")

    import ddddocr

    emit(f"ddddocr 包目录: {Path(ddddocr.__file__).parent}")

    emit()
    emit("---- 1. 构造签名（charset_range 到底在不在）----")
    sig = inspect.signature(ddddocr.DdddOcr.__init__)
    emit(f"DdddOcr.__init__{sig}")
    params = list(sig.parameters)
    for name in ("charset_range", "import_onnx_path", "det", "ocr", "old", "beta", "show_ad"):
        emit(f"  参数 {name!r:20} : {'有' if name in params else '**没有**'}")
    emit(
        f"  set_charset_range 方法 : "
        f"{'有' if hasattr(ddddocr.DdddOcr, 'set_charset_range') else '**没有**'}"
    )

    emit()
    emit("---- 2. 四个入口方法的签名与返回形状 ----")
    for meth in ("classification", "detection", "slide_match", "slide_comparison"):
        fn = getattr(ddddocr.DdddOcr, meth, None)
        emit(f"  {meth}{inspect.signature(fn) if fn else '  <不存在>'}")

    emit()
    emit("---- 3. detection()：真实返回形状 ----")
    text_bytes = make_text_image("5834")
    emit(f"测试图 bytes: {len(text_bytes)}（内容是四个分开的字符 5 8 3 4）")
    t0 = time.perf_counter()
    det = ddddocr.DdddOcr(det=True, show_ad=False)
    emit(f"   构造 DdddOcr(det=True)     : {time.perf_counter() - t0:6.2f}s")
    t0 = time.perf_counter()
    boxes = det.detection(text_bytes)
    dt = time.perf_counter() - t0
    emit(f"   detection() 首次           : {dt:6.2f}s")
    emit(f"   返回类型                    : {type(boxes).__name__}")
    emit(f"   返回内容                    : {boxes!r}")
    emit(f"   元素类型                    : {[type(b).__name__ for b in boxes][:6]}")
    emit(f"   序列化体积                  : {len(json.dumps(boxes))} bytes")
    t0 = time.perf_counter()
    boxes2 = det.detection(text_bytes)
    emit(f"   detection() 第二次         : {time.perf_counter() - t0:6.2f}s（缓存摊薄后）")
    emit(f"   两次结果一致                : {boxes2 == boxes}")

    emit()
    emit("---- 4. slide_match()：合成滑块对 ----")
    target_bytes, background_bytes, box = make_slider_pair(gap_x=190)
    emit(f"target bytes={len(target_bytes)}  background bytes={len(background_bytes)}")
    emit(f"合成时的**真值**缺口 bbox = {box}")
    slide = ddddocr.DdddOcr(det=False, ocr=False, show_ad=False)
    t0 = time.perf_counter()
    res_default = slide.slide_match(target_bytes, background_bytes)
    emit(f"   slide_match(target, bg)               : {time.perf_counter() - t0:6.2f}s")
    emit(f"   返回 = {res_default!r}")
    emit(f"   序列化体积 = {len(json.dumps(res_default))} bytes")

    t0 = time.perf_counter()
    res_simple = slide.slide_match(target_bytes, background_bytes, simple_target=True)
    emit(f"   slide_match(..., simple_target=True)  : {time.perf_counter() - t0:6.2f}s")
    emit(f"   返回 = {res_simple!r}")

    # 「底图里根本没有这块」时的返回形状——决定执行器要判什么才算「没找到」
    from PIL import Image as _Image

    plain = _Image.new("RGB", (320, 160), (120, 160, 200))
    t0 = time.perf_counter()
    res_none = slide.slide_match(target_bytes, png_bytes(plain))
    emit(f"   slide_match(纯色底图，必无处可匹配)   : {time.perf_counter() - t0:6.2f}s")
    emit(f"   返回 = {res_none!r}  ← 「找不到」的形态")

    emit()
    emit("---- 5. slide_comparison()：同一对图 ----")
    t0 = time.perf_counter()
    try:
        cmp_res = slide.slide_comparison(target_bytes, background_bytes)
        emit(f"   slide_comparison(target, bg)          : {time.perf_counter() - t0:6.2f}s")
        emit(f"   返回 = {cmp_res!r}")
    except Exception as exc:  # noqa: BLE001 - 就是要看它抛什么
        emit(f"   slide_comparison(target, bg)          : {time.perf_counter() - t0:6.2f}s")
        emit(f"   **抛异常** {type(exc).__module__}.{type(exc).__name__}: {str(exc)[:300]}")
        emit("   ⇒ 约束：slide_comparison 要求两张图**同尺寸**（内部走 cv2.absdiff）")

    emit()
    emit("---- 6. 边界：坏图 / 空图 会抛什么 ----")
    for label, payload in (("随机字节", b"\x00\x01\x02\x03" * 40), ("极小PNG", make_text_image("1", (12, 12)))):
        try:
            det.detection(payload)
            emit(f"   detection({label}) → 没抛异常")
        except Exception as exc:  # noqa: BLE001 - 就是要看异常类型
            emit(f"   detection({label}) → {type(exc).__module__}.{type(exc).__name__}: {exc}")
    try:
        slide.slide_match(b"\x00\x01" * 30, background_bytes)
        emit("   slide_match(坏target) → 没抛异常")
    except Exception as exc:  # noqa: BLE001
        emit(f"   slide_match(坏target) → {type(exc).__module__}.{type(exc).__name__}: {exc}")

    emit()
    emit("---- 7. set_ranges()：M60 S1 说「字符集收敛不存在」，实为**方法名找错** ----")
    emit("   S1 查的是 set_charset_range（不存在）；真名是 compat/v1 的 **set_ranges**。")
    rec = ddddocr.DdddOcr(show_ad=False)
    emit(f"   get_charset() 默认字符集长度 = {len(rec.get_charset())}")
    digit_bytes = make_text_image("5834")
    base_text = rec.classification(digit_bytes)
    emit(f"   不过滤          → {base_text!r}（内容真值 '5834'）")

    for label, spec in (
        ("str '0123456789'", "0123456789"),
        ("list ['5','8','3','4']", ["5", "8", "3", "4"]),
        ("list ['5','8','3']（缺 '4'）", ["5", "8", "3"]),
        ("int 10", 10),
        ("str 'abc'（与数字图无关）", "abc"),
        ("str ''（空）", ""),
    ):
        try:
            rec.set_ranges(spec)
            after = rec.classification(digit_bytes)
            emit(f"   set_ranges({label:26}) → 有效字符集 {len(rec.get_charset())} 个, 识别 = {after!r}")
        except Exception as exc:  # noqa: BLE001
            emit(f"   set_ranges({label:26}) → **抛** {type(exc).__name__}: {str(exc)[:110]}")

    emit()
    emit("   -- det 模式下调 set_ranges（源码有守卫，应当被拒）--")
    try:
        det.set_ranges("0123456789")
        emit("   det=True 上 set_ranges → 没抛")
    except Exception as exc:  # noqa: BLE001
        emit(f"   det=True 上 set_ranges → {type(exc).__name__}: {str(exc)[:110]}")

    emit()
    emit("---- 8. slide_match 的 confidence 到底是什么 ----")
    emit("   源码：confidence = float(max_val) = cv2.matchTemplate(TM_CCOEFF_NORMED) 的归一化相关系数")
    emit("   ⇒ **不是概率**，取值 [-1,1]；且**没有「找不到」分支**，恒返回最佳位置")
    emit(f"   纯色底图（退化）时 = {res_none.get('confidence')!r}")
    emit(f"   合成条纹底图时     = {res_default.get('confidence')!r}")
    emit("   ⇒ 两个值都「看起来很好」，但合成真值缺口是 x=190，返回却是 25 / 75")
    emit("   ⇒ **合成 fixture 不可信**：本节只做 API 形状取证；精度判据必须用真实滑块图，"
         "或只判「键齐全 + 坐标落在图内」")

    OUT.write_text("\n".join(_lines) + "\n", encoding="utf-8", newline="\n")
    print(f"\n[written] {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
