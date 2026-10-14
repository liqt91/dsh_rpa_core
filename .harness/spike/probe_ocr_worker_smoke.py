"""M60 探针③：三条 ocr 命令的 worker 端到端冒烟（只读，不写仓库状态）。

直接按 stdin/stdout 一行一 JSON 的协议喂请求，验证：
- `ocr.recognize`（含 `charsetRange` 真的收敛）；
- `ocr.detect`（输出形状 `{x,y,width,height}` + `count`）；
- `ocr.slideMatch`（键齐全、坐标落在图内、`minConfidence` 能拦住）。

用法：./.venv/Scripts/python.exe .harness/spike/probe_ocr_worker_smoke.py
"""

from __future__ import annotations

import base64
import io
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).with_suffix(".out")


def png(text: str, size: tuple[int, int] = (240, 70)) -> str:
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    font = None
    for name in ("arial.ttf", "DejaVuSans.ttf", "consola.ttf"):
        try:
            font = ImageFont.truetype(name, 44)
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()
    x = 12
    for ch in text:
        draw.text((x, 8), ch, fill="black", font=font)
        x += 52
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def slider_pair(width: int = 320, height: int = 160, gap_x: int = 190) -> tuple[str, str, int]:
    from PIL import Image, ImageDraw

    bg = Image.new("RGB", (width, height), (200, 210, 220))
    d = ImageDraw.Draw(bg)
    for i in range(0, width, 7):
        d.line([(i, 0), (i, height)], fill=(180 + (i * 3) % 60, 190, 205), width=2)
    for j in range(0, height, 9):
        d.line([(0, j), (width, j)], fill=(190, 185 + (j * 5) % 50, 200), width=2)
    box = (gap_x, 60, gap_x + 50, 110)
    piece = bg.crop(box)
    d.rectangle(box, fill=(40, 40, 45))

    def enc(im) -> str:
        b = io.BytesIO()
        im.save(b, format="PNG")
        return base64.b64encode(b.getvalue()).decode("ascii")

    return enc(piece), enc(bg), gap_x


def main() -> int:
    lines: list[str] = []

    def emit(text: str = "") -> None:
        lines.append(text)
        print(text, flush=True)

    digit_b64 = png("5834")
    tmp_dir = ROOT / ".harness" / "spike" / "_m60b_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    img_path = tmp_dir / "digits.png"
    img_path.write_bytes(base64.b64decode(digit_b64))

    piece_b64, bg_b64, truth_x = slider_pair()

    requests = [
        {"id": 1, "commandId": "ocr.recognize", "inputs": {"imageBase64": digit_b64}},
        {
            "id": 2,
            "commandId": "ocr.recognize",
            "inputs": {"imageBase64": digit_b64, "charsetRange": "0123456789"},
        },
        {
            "id": 3,
            "commandId": "ocr.recognize",
            "inputs": {"imageBase64": digit_b64, "charsetRange": "583"},
        },
        {
            "id": 4,
            "commandId": "ocr.recognize",
            "inputs": {"imageBase64": digit_b64, "charsetRange": "abc"},
        },
        {"id": 5, "commandId": "ocr.recognize", "inputs": {"imagePath": str(img_path)}},
        {
            "id": 6,
            "commandId": "ocr.recognize",
            "inputs": {"imagePath": str(img_path), "imageBase64": digit_b64},
        },
        {"id": 7, "commandId": "ocr.detect", "inputs": {"imageBase64": digit_b64}},
        {
            "id": 8,
            "commandId": "ocr.slideMatch",
            "inputs": {"templateImageBase64": piece_b64, "backgroundImageBase64": bg_b64},
        },
        {
            "id": 9,
            "commandId": "ocr.slideMatch",
            "inputs": {
                "templateImageBase64": piece_b64,
                "backgroundImageBase64": bg_b64,
                "simpleTarget": True,
            },
        },
        {
            "id": 10,
            "commandId": "ocr.slideMatch",
            "inputs": {
                "templateImageBase64": piece_b64,
                "backgroundImageBase64": bg_b64,
                "minConfidence": 0.999,
            },
        },
        {
            "id": 11,
            "commandId": "ocr.slideMatch",
            "inputs": {"templateImageBase64": piece_b64},
        },
        {"id": 12, "commandId": "ocr.nope", "inputs": {}},
    ]
    payload = "\n".join(json.dumps(r, ensure_ascii=False) for r in requests) + "\n"

    emit("=== M60 探针③ worker 端到端冒烟 ===")
    proc = subprocess.run(
        [sys.executable, "-m", "rpa_core.workers.ocr_worker"],
        input=payload.encode("utf-8"),
        capture_output=True,
        cwd=ROOT,
    )
    emit(f"exit={proc.returncode}  stderr={(proc.stderr.decode('utf-8', errors='replace')[:200] or '<空>')}")
    emit(f"合成滑块真值缺口 x = {truth_x}（返回的是**中心**，应为 ~{truth_x + 25}）")
    emit()
    for line in proc.stdout.decode("utf-8").splitlines():
        if not line.strip():
            continue
        data = json.loads(line)
        emit(json.dumps(data, ensure_ascii=False))
        emit(f"   ↳ 序列化体积 {len(json.dumps(data, ensure_ascii=False))} bytes")

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"\n[written] {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
