"""诊断：层级形态对话框的控件几何 + 文字实测宽度（只看不改，不进门禁）。

用途：定位用户截图里的「标签缺字」「行文字被硬切」「顶部文字贴边」到底是
文字比控件宽，还是控件互相覆盖，还是窗口客户区比布局宽度小。
"""

from __future__ import annotations

import ctypes
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from rpa_core.capture_kit import ElementEditDialog  # noqa: E402
from rpa_core.capture_kit.selector_tree import levels_from_path  # noqa: E402


class _SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


def text_width(hdc: int, hfont: int, text: str) -> int:
    gdi32 = ctypes.WinDLL("gdi32")
    gdi32.GetTextExtentPoint32W.argtypes = [
        ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int, ctypes.POINTER(_SIZE),
    ]
    import win32gui

    old = win32gui.SelectObject(hdc, hfont)
    size = _SIZE()
    gdi32.GetTextExtentPoint32W(
        ctypes.c_void_p(int(hdc)), text, len(text), ctypes.byref(size)
    )
    win32gui.SelectObject(hdc, old)
    return int(size.cx)


def main() -> int:
    import win32gui

    rows = int(sys.argv[1]) if len(sys.argv) > 1 else 11
    source = [
        {"tag": "body", "classes": ["page"], "inCss": False},
        {"tag": "div", "classes": ["main-content", "with-sidebar"], "inCss": False},
        {"tag": "div", "id": "mfContainer", "classes": ["feeds-container"], "inCss": False},
        {"tag": "div", "classes": ["feeds-content"], "inCss": False},
        {"tag": "div", "classes": ["search-area", "search-area-fixed"], "inCss": False},
        {"tag": "div", "classes": ["input-box", "search-box"], "inCss": False},
        {"tag": "div", "classes": ["wendian-wrapper", "wendian-wrapper-v2"], "inCss": False},
        {"tag": "div", "classes": ["textarea-container", "textarea-container-flex"], "inCss": False},
        {"tag": "div", "classes": ["single-line-row", "xhs-single-line"], "inCss": False},
        {"tag": "div", "classes": ["textarea-wrapper"], "inCss": False},
        {"tag": "textarea", "id": "search-input-in-feeds", "classes": [], "inCss": True},
    ]
    # 用户截图那种 10 层：不带 body、最后一行就是目标 textarea，不触发滚动条
    levels = levels_from_path(source[-rows:])
    print("levels:", len(levels))
    dialog = ElementEditDialog()
    dialog.show(
        title="捕获到元素",
        name="元素4",
        levels=levels,
        info="网页元素 · 命中 1 个 · 存为资产（不绑定节点）",
    )
    deadline = time.time() + 6
    while not dialog._hwnd and time.time() < deadline:
        time.sleep(0.05)
    hwnd = int(dialog._hwnd or 0)
    if not hwnd:
        print("FAIL: 窗口没建起来")
        return 1
    time.sleep(0.9)

    wl, wt, wr, wb = win32gui.GetWindowRect(hwnd)
    cl, ct, cr, cb = win32gui.GetClientRect(hwnd)
    print(f"window={wr - wl}x{wb - wt} at ({wl},{wt})  client={cr - cl}x{cb - ct}")
    print(f"base_h={dialog._base_h} shift={dialog._shift} shot_h={dialog._shot_h}")

    hdc = win32gui.GetDC(hwnd)
    hfont = dialog._hfont
    items: list[tuple] = []

    def each(handle, acc):
        cls = win32gui.GetClassName(handle)
        text = win32gui.GetWindowText(handle)
        left, top, right, bottom = win32gui.GetWindowRect(handle)
        acc.append(
            (
                cls,
                text,
                left - wl,
                top - wt,
                right - left,
                bottom - top,
                text_width(hdc, hfont, text) if text else 0,
            )
        )

    win32gui.EnumChildWindows(hwnd, each, items)
    win32gui.ReleaseDC(hwnd, hdc)
    for cls, text, x, y, w, h, tw in items:
        flag = "  <-- 文字超出" if tw > w else ""
        print(f"{cls:9} x={x:4} y={y:4} w={w:4} h={h:3} tw={tw:4} {text[:46]!r}{flag}")

    out = ROOT / ".harness" / "spike" / f"out_dialog_geom_{rows}.png"
    from PIL import Image
    import win32ui

    src = win32ui.CreateDCFromHandle(win32gui.GetWindowDC(hwnd))
    mem = src.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(src, wr - wl, wb - wt)
    mem.SelectObject(bitmap)
    # 位图初值是未定义的：先填白，否则非客户区（标题栏/边框）会是绿条和黑块
    mem.FillSolidRect((0, 0, wr - wl, wb - wt), 0xFFFFFF)
    win32gui.SendMessage(hwnd, 0x0317, mem.GetSafeHdc(), 0x1 | 0x4 | 0x8 | 0x10 | 0x20)
    info = bitmap.GetInfo()
    image = Image.frombuffer(
        "RGB", (info["bmWidth"], info["bmHeight"]),
        bitmap.GetBitmapBits(True), "raw", "BGRX", 0, 1,
    )
    image.save(out)
    # 再来一张含标题栏的（WM_PRINT 不抓非客户区）
    mem.FillSolidRect((0, 0, wr - wl, wb - wt), 0xFFFFFF)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.PrintWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
    user32.PrintWindow(ctypes.c_void_p(hwnd), ctypes.c_void_p(int(mem.GetSafeHdc())), 2)
    image_full = Image.frombuffer(
        "RGB", (info["bmWidth"], info["bmHeight"]),
        bitmap.GetBitmapBits(True), "raw", "BGRX", 0, 1,
    )
    image_full.save(out.with_name(f"out_dialog_geom_full_{rows}.png"))
    mem.DeleteDC()
    src.DeleteDC()
    print("saved", out)
    dialog.close()
    time.sleep(0.3)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
