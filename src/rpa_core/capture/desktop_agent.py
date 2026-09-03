"""桌面元素捕获 agent（M10）：一次性子进程，UIA hit-test。

协议（与 python.worker 同构的进程隔离模式）：
- 启动：`python -m rpa_core.capture.desktop_agent [--hotkey F9] [--timeout 60]`
  `[--point X Y] [--hover]`
- 等待：轮询 GetAsyncKeyState 等待捕获热键（默认 F9），或 `--point` 测试模式立即命中；
  `--hover` 模式随鼠标移动实时高亮命中元素（悬浮框），热键或 Ctrl+Click 捕获
- 产出：stdout 打印一行 JSON 元素描述符（kind=desktop，selector.locator 可回验命中）
- 取消：父进程 terminate；超时输出 {"timeout": true}
"""

import argparse
import ctypes
import json
import sys
import time
from ctypes import wintypes

VK_F9 = 0x78
VK_ESCAPE = 0x1B
VK_CONTROL = 0x11
VK_LBUTTON = 0x01
GA_ROOT = 2
POLL_INTERVAL = 0.05


def _cursor_pos() -> tuple[int, int]:
    point = wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


def _hotkey_pressed(vk: int) -> bool:
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)


class _HoverOverlay:
    """悬浮高亮框：无边框顶层点击穿透窗口，窗口 region 裁剪成 3px 边框。"""

    BORDER = 3
    COLOR = (255, 59, 48)

    def __init__(self):
        import win32api
        import win32con
        import win32gui

        self._win32con = win32con
        self._win32gui = win32gui
        self._win32api = win32api
        hinst = win32gui.GetModuleHandle(None)
        wc = win32gui.WNDCLASS()
        wc.lpfnWndProc = {win32con.WM_PAINT: self._on_paint}
        wc.lpszClassName = "RpaCaptureHoverOverlay"
        wc.hInstance = hinst
        wc.hbrBackground = win32gui.GetStockObject(win32con.NULL_BRUSH)
        self._class = win32gui.RegisterClass(wc)
        ex_style = (
            win32con.WS_EX_TOPMOST | win32con.WS_EX_TRANSPARENT
            | win32con.WS_EX_LAYERED | win32con.WS_EX_TOOLWINDOW
            | win32con.WS_EX_NOACTIVATE
        )
        self.hwnd = win32gui.CreateWindowEx(
            ex_style, "RpaCaptureHoverOverlay", "", win32con.WS_POPUP,
            0, 0, 0, 0, 0, 0, hinst, None,
        )
        alpha = 200
        win32gui.SetLayeredWindowAttributes(
            self.hwnd, 0, alpha, win32con.LWA_ALPHA
        )

    def _on_paint(self, hwnd, msg, wparam, lparam):
        win32gui = self._win32gui
        win32api = self._win32api
        hdc, ps = win32gui.BeginPaint(hwnd)
        brush = win32gui.CreateSolidBrush(win32api.RGB(*self.COLOR))
        left, top, right, bottom = win32gui.GetClientRect(hwnd)
        win32gui.FillRect(hdc, (left, top, right, bottom), brush)
        win32gui.DeleteObject(brush)
        win32gui.EndPaint(hwnd, ps)
        return 0

    def show_rect(self, left: int, top: int, right: int, bottom: int) -> None:
        win32gui = self._win32gui
        win32con = self._win32con
        width = right - left
        height = bottom - top
        # region 函数走 gdi32（pywin32 不暴露 CreateRectRgn/CombineRgn）
        gdi32 = ctypes.windll.gdi32
        outer = gdi32.CreateRectRgn(0, 0, width, height)
        inner = gdi32.CreateRectRgn(
            self.BORDER, self.BORDER,
            max(width - self.BORDER, self.BORDER),
            max(height - self.BORDER, self.BORDER),
        )
        gdi32.CombineRgn(outer, outer, inner, win32con.RGN_DIFF)
        win32gui.SetWindowRgn(self.hwnd, outer, True)
        win32gui.SetWindowPos(
            self.hwnd, win32con.HWND_TOPMOST, left, top, width, height,
            win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW,
        )

    def pump(self) -> None:
        self._win32gui.PumpWaitingMessages()

    def destroy(self) -> None:
        if self.hwnd:
            self._win32gui.DestroyWindow(self.hwnd)
            self.hwnd = None


def _element_from_point(x: int, y: int):
    from pywinauto.uia_defines import IUIA

    point = wintypes.POINT(x, y)
    element = IUIA().iuia.ElementFromPoint(point)
    from pywinauto.uia_element_info import UIAElementInfo

    return UIAElementInfo(element)


def _uia_element_at(x: int, y: int):
    return _element_from_point(x, y)


def _root_window_handle(hwnd: int) -> int:
    if not hwnd:
        return 0
    return int(ctypes.windll.user32.GetAncestor(hwnd, GA_ROOT))


def _verify(window, criteria: dict, automation_id: str | None) -> int:

    matches = window.descendants(**criteria)
    if automation_id:
        matches = [
            match
            for match in matches
            if getattr(match.element_info, "automation_id", None) == automation_id
        ]
    return len(matches)


def capture_in_window(root_hwnd: int, x: int, y: int) -> dict | None:
    """窗口作用域 hit-test：在被测窗口 UIA 子树内找包含该点且面积最小的元素。

    免疫屏幕覆盖层（安全软件 InputSite 等）对 ElementFromPoint 的劫持。
    找不到包含点的后代时返回 None（调用方回退屏幕级 hit-test）。
    """
    from pywinauto import Desktop

    window = Desktop(backend="uia").window(handle=root_hwnd)
    best_wrapper = None
    best_area = None
    for wrapper in window.descendants():
        try:
            rect = wrapper.rectangle()
        except Exception:
            continue
        if rect.left <= x < rect.right and rect.top <= y < rect.bottom:
            area = max(rect.width(), 1) * max(rect.height(), 1)
            if best_area is None or area <= best_area:
                best_wrapper, best_area = wrapper, area
    if best_wrapper is None:
        return None
    return _describe_wrapper(best_wrapper, root_hwnd)


def _describe_wrapper(wrapper, root_hwnd: int) -> dict:
    info = wrapper.element_info
    control_type = info.control_type
    automation_id = info.automation_id or None
    name = info.name or None

    criteria: dict = {}
    locator: dict = {"backend": "uia"}
    if control_type:
        criteria["control_type"] = control_type
        locator["controlType"] = control_type
    if automation_id:
        locator["automationId"] = automation_id
    if name:
        criteria["title"] = name
        locator["name"] = name

    verify_count = 0
    if criteria:
        try:
            verify_count = _verify(wrapper.top_level_parent(), criteria, automation_id)
        except Exception:
            verify_count = 0
    if verify_count != 1 and "controlType" in locator and locator["controlType"] == "Pane":
        # 面板类泛化元素找不到唯一 locator 时仍返回（供人工确认），避免假唯一
        verify_count = verify_count if verify_count > 1 else 0

    window_title = ""
    if root_hwnd:
        title_buffer = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetWindowTextW(root_hwnd, title_buffer, 256)
        window_title = title_buffer.value

    return {
        "kind": "desktop",
        "selector": {"locator": locator},
        "verifyCount": verify_count,
        "metadata": {
            "windowHandle": root_hwnd,
            "windowTitle": window_title,
            "controlType": control_type,
            "automationId": automation_id,
            "name": name,
            "className": info.class_name or None,
        },
    }


def capture_at(x: int, y: int, scope_hwnd: int | None = None) -> dict:
    if scope_hwnd:
        scoped = capture_in_window(scope_hwnd, x, y)
        if scoped is not None:
            return scoped
    from pywinauto import Desktop

    info = _element_from_point(x, y)
    hwnd = int(info.handle or 0)
    root_hwnd = _root_window_handle(hwnd) or (scope_hwnd or 0)
    window = Desktop(backend="uia").window(handle=root_hwnd) if root_hwnd else None

    control_type = info.control_type
    automation_id = info.automation_id or None
    name = info.name or None
    class_name = info.class_name or None

    candidates: list[tuple[dict, dict, str | None]] = []
    if automation_id and control_type:
        criteria = {"control_type": control_type}
        locator = {"backend": "uia", "controlType": control_type, "automationId": automation_id}
        if name:
            criteria["title"] = name
            locator["name"] = name
        candidates.append((criteria, locator, automation_id))
    if name and control_type:
        candidates.append(
            ({"control_type": control_type, "title": name},
             {"backend": "uia", "controlType": control_type, "name": name},
             None)
        )
    if control_type:
        candidates.append(
            ({"control_type": control_type}, {"backend": "uia", "controlType": control_type}, None)
        )

    best_locator: dict = {}
    best_count = -1
    if window is not None:
        for criteria, locator, aid in candidates:
            try:
                count = _verify(window, criteria, aid)
            except Exception:
                continue
            if count == 1:
                best_locator, best_count = locator, count
                break
            if best_count < 0 or count < best_count:
                best_locator, best_count = locator, count
    if not best_locator and control_type:
        best_locator = {"backend": "uia", "controlType": control_type}
        best_count = 0

    window_title = ""
    if root_hwnd:
        title_buffer = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetWindowTextW(root_hwnd, title_buffer, 256)
        window_title = title_buffer.value

    return {
        "kind": "desktop",
        "selector": {"locator": best_locator},
        "verifyCount": max(best_count, 0),
        "metadata": {
            "point": [x, y],
            "windowHandle": root_hwnd,
            "windowTitle": window_title,
            "controlType": control_type,
            "automationId": automation_id,
            "name": name,
            "className": class_name,
        },
    }


def capture_with_retry(x: int, y: int, scope_hwnd: int | None, attempts: int = 4) -> dict:
    """整体重试（重新 hit-test）：负载下首次 UIA 树枚举偶发不完整或被覆盖层劫持。"""
    best: dict | None = None
    for index in range(attempts):
        result = capture_at(x, y, scope_hwnd)
        if result.get("verifyCount") == 1:
            return result
        if best is None or result.get("verifyCount", 0) > best.get("verifyCount", 0):
            best = result
        if index < attempts - 1:
            time.sleep(0.4)
    return best if best is not None else {"timeout": True}


def _hover_capture(hotkey_vk: int, timeout: float) -> dict:
    """hover 模式：鼠标移动实时高亮命中元素；热键或 Ctrl+Click 捕获；Esc 取消。

    作用域取悬停元素的根窗口（免疫屏幕覆盖层劫持），而非前台窗口。
    """
    overlay = _HoverOverlay()
    deadline = time.monotonic() + timeout
    last_pos = (-1, -1)
    last_hit = 0.0
    last_root = 0
    hotkey_was = _hotkey_pressed(hotkey_vk)
    lbutton_was = _hotkey_pressed(VK_LBUTTON)
    try:
        while time.monotonic() < deadline:
            x, y = _cursor_pos()
            moved = abs(x - last_pos[0]) > 3 or abs(y - last_pos[1]) > 3
            if moved and time.monotonic() - last_hit > 0.06:
                last_hit = time.monotonic()
                last_pos = (x, y)
                try:
                    info = _element_from_point(x, y)
                    hwnd = int(info.handle or 0)
                    if hwnd and hwnd != overlay.hwnd:
                        rect = info.rectangle
                        overlay.show_rect(rect.left, rect.top, rect.right, rect.bottom)
                        last_root = _root_window_handle(hwnd)
                except Exception:
                    pass
            overlay.pump()

            hotkey_now = _hotkey_pressed(hotkey_vk)
            lbutton_now = _hotkey_pressed(VK_LBUTTON)
            escape_now = _hotkey_pressed(VK_ESCAPE)
            capture_triggered = (hotkey_now and not hotkey_was) or (
                lbutton_now and not lbutton_was and _hotkey_pressed(VK_CONTROL)
            )
            hotkey_was = hotkey_now
            lbutton_was = lbutton_now

            if escape_now:
                return {"cancelled": True}
            if capture_triggered:
                # 捕获瞬间以当前点重新命中取根窗口（hover 的 last_root 可能滞后/陈旧）
                scope = last_root or None
                try:
                    info = _element_from_point(x, y)
                    hwnd = int(info.handle or 0)
                    if hwnd and hwnd != overlay.hwnd:
                        scope = _root_window_handle(hwnd) or scope
                except Exception:
                    pass
                return capture_with_retry(x, y, scope)
            time.sleep(0.03)
        return {"timeout": True}
    finally:
        overlay.destroy()


def main() -> int:
    # 父进程按 UTF-8 读取 stdout：本地化编码（cp936）会让中文描述符破坏管道。
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="desktop-capture-agent")
    parser.add_argument("--hotkey", default="F9")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--point", nargs=2, type=int, metavar=("X", "Y"), default=None)
    parser.add_argument("--window-handle", type=int, default=None, dest="window_handle")
    parser.add_argument("--hover", action="store_true",
                        help="hover 模式：鼠标移动实时高亮，热键或 Ctrl+Click 捕获")
    args = parser.parse_args()

    if sys.platform != "win32":
        print(json.dumps({"error": "desktop capture requires Windows"}))
        return 1

    import pythoncom

    pythoncom.CoInitialize()
    try:
        keys = {"F9": 0x78, "F8": 0x77, "F10": 0x79}
        hotkey = keys.get(args.hotkey.upper(), VK_F9)
        if args.hover:
            result = _hover_capture(hotkey, args.timeout)
        elif args.point is not None:
            scope = args.window_handle
            if not scope:
                scope = int(ctypes.windll.user32.GetForegroundWindow()) or None
            result = capture_with_retry(args.point[0], args.point[1], scope)
        else:
            deadline = time.monotonic() + args.timeout
            pressed_before = _hotkey_pressed(hotkey)
            result = {"timeout": True}
            while time.monotonic() < deadline:
                pressed = _hotkey_pressed(hotkey)
                if pressed and not pressed_before:
                    x, y = _cursor_pos()
                    scope = int(ctypes.windll.user32.GetForegroundWindow()) or None
                    result = capture_with_retry(x, y, scope)
                    break
                pressed_before = pressed
                time.sleep(POLL_INTERVAL)
        print(json.dumps(result, ensure_ascii=False))
    finally:
        pythoncom.CoUninitialize()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
