"""一次性探针：桌面层级形态 + 简式形态的布局（含新增的校验按钮与截图区）。"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from PIL import Image  # noqa: E402

from rpa_core.capture_kit import ElementEditDialog  # noqa: E402
from rpa_core.capture_kit.desktop_editing import nodes_from_descriptor  # noqa: E402

_PRINT_CHILDREN = 0x1 | 0x4 | 0x8 | 0x10 | 0x20

DESKTOP_DOC = {
    "kind": "desktop",
    "selector": {
        "locator": {
            "backend": "uia",
            "name": "发送",
            "controlType": "Button",
            "path": [{"controlType": "Pane", "className": "Chrome_WidgetWin_1"}],
        }
    },
    "metadata": {
        "windowTitle": "微信",
        "className": "WeChatMainWndForPC",
        "windowHandle": 1234,
    },
}


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


def shoot(dialog: ElementEditDialog, name: str) -> None:
    deadline = time.time() + 6
    while not dialog._hwnd and time.time() < deadline:
        time.sleep(0.05)
    if not dialog._hwnd:
        print("FAIL: 窗口没建起来", name)
        return
    time.sleep(0.8)
    out = ROOT / ".harness" / "spike" / name
    grab(int(dialog._hwnd)).save(out)
    print("saved", out)
    dialog.close()
    time.sleep(0.35)


def main() -> int:
    desktop = ElementEditDialog()
    desktop.show(
        title="元素详情 · 发送",
        name="发送",
        info="桌面元素 · 命中 1 个 · 存为资产",
        nodes=nodes_from_descriptor(DESKTOP_DOC),
        descriptor=DESKTOP_DOC,
    )
    shoot(desktop, "out_edit_desktop.png")

    simple = ElementEditDialog()
    simple.show(
        title="元素详情 · 旧资产",
        name="旧资产",
        selector='{"locator": {"backend": "uia", "controlType": "Button"}}',
        meta='{"kind": "desktop"}',
        info="桌面元素 · 存为资产",
    )
    shoot(simple, "out_edit_simple.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
