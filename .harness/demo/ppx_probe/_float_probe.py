"""浮窗冒烟：真开一次窗，验两件事（都对应用户报障）。

1. **不是空白**：``win32gui.CreateFont`` 不存在 ⇒ WM_PAINT 抛异常 ⇒ 只剩白底。
   判据是**暗像素计数**（白底没有暗像素，画上三行字必然有）。
2. **躲鼠标的定时器真的在跑**：``win32gui.SetTimer`` 同样不存在 ⇒ 40ms 定时器从没
   建立。这里把窗摆到鼠标正下方，等 300ms 看它是否自己翻到另一角（不动用户的鼠标）。

跑法（会闪一下窗，约 1.5s）：
    .venv/Scripts/python.exe .harness/demo/ppx_probe/_float_probe.py
"""

from __future__ import annotations

import ctypes
import time

from rpa_core.capture_kit import float_window as _fw
from rpa_core.capture_kit import engine


class _Ready:
    desktop_offline = False
    extension_offline = False
    extension_unresponsive = False


def _texts():
    session = _Ready()
    return (
        engine.gesture_hint(session),
        engine.web_status(session)[0],
        engine.desktop_status(session)[0],
    )


def main() -> int:
    float_win = _fw.CaptureFloat()
    gesture, web, desktop = _texts()
    float_win.show(gesture, web, desktop, True, True)
    time.sleep(1.0)
    hwnd = float_win._hwnd
    if not hwnd:
        print("FAIL 窗没起来（hwnd=None）")
        return 1

    import win32api
    import win32con
    import win32gui
    from PIL import ImageGrab

    rect = win32gui.GetWindowRect(hwnd)
    image = ImageGrab.grab(bbox=rect)
    image.save(".harness/demo/ppx_probe/_float_shot.png")
    gray = image.convert("L").load()
    dark = sum(
        1
        for y in range(image.height)
        for x in range(image.width)
        if gray[x, y] < 120
    )
    print("rect", rect, "size", image.size, "dark_px", dark)

    # 把窗摆到鼠标正下方（不移动鼠标），等定时器把它翻走
    cx, cy = win32api.GetCursorPos()
    width, height = rect[2] - rect[0], rect[3] - rect[1]
    win32gui.SetWindowPos(
        hwnd, win32con.HWND_TOPMOST, cx - width // 2, cy - height // 2, width, height,
        win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW,
    )
    time.sleep(0.4)
    after = win32gui.GetWindowRect(hwnd)
    still_over_cursor = (
        after[0] - 16 <= cx <= after[2] + 16 and after[1] - 16 <= cy <= after[3] + 16
    )
    print("cursor", (cx, cy), "after", after, "still_over_cursor", still_over_cursor)

    float_win.hide()
    time.sleep(0.3)

    failed = []
    if dark < 200:
        failed.append("浮窗没有文字（暗像素过少）")
    if still_over_cursor:
        failed.append("躲鼠标没生效（定时器没跑）")
    if failed:
        print("FAIL", "；".join(failed))
        return 1
    print("PASS 浮窗有文字、且会躲鼠标")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
