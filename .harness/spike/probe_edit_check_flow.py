"""一次性探针：验证「点校验 → 藏框 → toast → 回来 → 结果条」整条编排。

活体查询换成 0.9s 的假实现：这里只证明编排顺序，不碰真机 UIA（那部分由单测覆盖）。
产物：``out_edit_toast.png`` / ``out_edit_after.png``。
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

import rpa_core.capture_kit.edit_dialog as edition  # noqa: E402
from rpa_core.capture_kit import ElementEditDialog  # noqa: E402
from rpa_core.capture_kit.selector_tree import levels_from_path  # noqa: E402

_PRINT_CHILDREN = 0x1 | 0x4 | 0x8 | 0x10 | 0x20


def grab(hwnd: int) -> Image.Image:
    import win32gui
    import win32ui

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width, height = right - left, bottom - top
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
    image = Image.new("RGB", (600, 360), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 30, 560, 90), outline=(200, 200, 200), width=2)
    draw.rectangle((40, 120, 560, 330), fill=(244, 244, 248))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return {
        "dataUrl": "data:image/png;base64,"
        + base64.b64encode(buffer.getvalue()).decode("ascii"),
        "box": {"x": 38, "y": 28, "width": 524, "height": 64},
    }


def main() -> int:
    calls = []

    def fake_check(descriptor, selector, **_kwargs):
        calls.append(selector)
        time.sleep(0.9)
        return edition.EditCheckResult(
            success=True, count=1, message="找到 1 个元素", shot=fake_shot()
        )

    edition.check_draft = fake_check  # worker 线程里调的就是这个模块级名字

    levels = levels_from_path(
        [
            {"tag": "div", "id": "app", "classes": ["layout"], "inCss": True},
            {"tag": "input", "id": "kw", "classes": ["search-input"], "inCss": True},
        ]
    )
    dialog = ElementEditDialog()
    dialog.show(title="元素编辑器", name="输入框", levels=levels, info="网页元素 · 命中 2 个")
    deadline = time.time() + 6
    while not dialog._hwnd and time.time() < deadline:
        time.sleep(0.05)
    hwnd = int(dialog._hwnd or 0)
    if not hwnd:
        print("FAIL: 窗口没建起来")
        return 1
    time.sleep(0.8)

    import win32con
    import win32gui

    win32gui.SendMessage(hwnd, win32con.WM_COMMAND, 210, 0)  # _ID_CHECK
    time.sleep(0.25)
    hidden = not win32gui.IsWindowVisible(hwnd)
    toast = dialog._toast
    toast_hwnd = int(toast._hwnd or 0) if toast else 0
    print("编辑框已隐藏 =", hidden, "| toast hwnd =", toast_hwnd)
    if toast_hwnd:
        out = ROOT / ".harness" / "spike" / "out_edit_toast.png"
        grab(toast_hwnd).save(out)
        print("saved", out)

    time.sleep(1.2)
    back = win32gui.IsWindowVisible(hwnd)
    text = win32gui.GetWindowText(dialog._result_static or 0)
    print("编辑框已回来 =", bool(back), "| 结果条 =", repr(text), "| 调用次数 =", len(calls))
    out = ROOT / ".harness" / "spike" / "out_edit_after.png"
    grab(hwnd).save(out)
    print("saved", out)

    ok = hidden and toast_hwnd and back and text == "找到 1 个元素" and len(calls) == 1
    print("VERDICT", "PASS" if ok else "FAIL")
    dialog.close()
    time.sleep(0.3)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
