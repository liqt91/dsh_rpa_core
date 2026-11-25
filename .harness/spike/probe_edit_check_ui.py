"""一次性探针：起编辑框（层级形态）→ 用 WM_PRINT 抓两张图（空态 / 有截图+结果）。

只为看布局，不进门禁。产物在 ``.harness/spike/out_edit_ui_*.png``。
"""

from __future__ import annotations

import base64
import io
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from PIL import Image, ImageDraw  # noqa: E402

from rpa_core.capture_kit import ElementEditDialog  # noqa: E402
from rpa_core.capture_kit.edit_check import EditCheckResult  # noqa: E402
from rpa_core.capture_kit.selector_tree import levels_from_path  # noqa: E402

_PRINT_CHILDREN = 0x1 | 0x4 | 0x8 | 0x10 | 0x20


def grab(hwnd: int) -> Image.Image:
    """WM_PRINT 抓窗口（含子控件）。屏幕抓取在这台机器上拿不到内容，别用。"""
    import win32gui
    import win32ui

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width, height = right - left, bottom - top
    win32gui.SendMessage(hwnd, 0x0317, 0, 0)  # 先让子控件失效重画一遍
    src = win32ui.CreateDCFromHandle(win32gui.GetWindowDC(hwnd))
    mem = src.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(src, width, height)
    mem.SelectObject(bitmap)
    win32gui.SendMessage(hwnd, 0x0317, mem.GetSafeHdc(), _PRINT_CHILDREN)
    info = bitmap.GetInfo()
    image = Image.frombuffer(
        "RGB", (info["bmWidth"], info["bmHeight"]),
        bitmap.GetBitmapBits(True), "raw", "BGRX", 0, 1,
    )
    mem.DeleteDC()
    src.DeleteDC()
    return image


def fake_shot() -> dict:
    """造一张「浏览器窗口 + 搜索框红框」的假截图。"""
    image = Image.new("RGB", (900, 520), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((60, 40, 840, 120), outline=(210, 210, 210), width=2)
    draw.text((90, 72), "搜索小红书", fill=(150, 150, 150))
    draw.rectangle((60, 160, 840, 470), fill=(248, 248, 250))
    for row in range(4):
        y = 180 + row * 70
        draw.rectangle((90, y, 810, y + 50), fill=(238, 238, 242))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return {
        "dataUrl": "data:image/png;base64,"
        + base64.b64encode(buffer.getvalue()).decode("ascii"),
        "box": {"x": 58, "y": 38, "width": 784, "height": 84},
    }


def save(image: Image.Image, name: str) -> None:
    out = ROOT / ".harness" / "spike" / name
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out)
    print("saved", out, image.size)


def main() -> int:
    levels = levels_from_path(
        [
            {"tag": "body", "classes": ["page"], "inCss": False},
            {"tag": "div", "id": "app", "classes": ["layout"], "inCss": False},
            {"tag": "div", "classes": ["search-wrap"], "inCss": True},
            {"tag": "input", "id": "kw", "classes": ["search-input"], "inCss": True},
        ]
    )
    dialog = ElementEditDialog()
    dialog.show(
        title="元素编辑器",
        name="输入框_search-input",
        levels=levels,
        info="网页元素 · 命中 2 个 · 存为资产（不绑定节点）",
    )
    deadline = time.time() + 6
    while not dialog._hwnd and time.time() < deadline:
        time.sleep(0.05)
    hwnd = dialog._hwnd
    if not hwnd:
        print("FAIL: 窗口没建起来")
        return 1
    time.sleep(0.8)
    import win32gui

    wl, wt, _, _ = win32gui.GetWindowRect(hwnd)
    print("shift", dialog._shift, "base_h", dialog._base_h)
    import win32api

    print("tooltip hwnd:", dialog._tooltip, "buffers:", len(dialog._tooltip_buffers))
    left, top, right, bottom = win32gui.GetWindowRect(dialog._level_boxes[-1])
    try:/n        win32api.SetCursorPos(((left + right) // 2, (top + bottom) // 2))
    except Exception:/n        pass
    time.sleep(1.5)
    visible: list[int] = []

    def collect(hwnd, _acc):
        if win32gui.GetClassName(hwnd) == "tooltips_class32" and win32gui.IsWindowVisible(hwnd):
            visible.append(hwnd)

    win32gui.EnumWindows(collect, None)
    print("visible tooltips on hover:", len(visible))
    save(grab(int(hwnd)), "out_edit_ui_empty.png")

    # 伪装一次「找到 1 个」：走 _render_result，截图区按需展开（A 档折叠）
    dialog._render_result(
        int(hwnd),
        EditCheckResult(success=True, count=1, message="找到 1 个元素", shot=fake_shot()),
    )
    time.sleep(0.6)
    save(grab(int(hwnd)), "out_edit_ui_shot.png")
    print("expanded:", dialog._shot_view is not None, "shot_h:", dialog._shot_h)

    dialog.close()
    time.sleep(0.3)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
