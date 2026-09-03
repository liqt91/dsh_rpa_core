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


def _win32_deepest_child(hwnd: int, x: int, y: int, max_depth: int = 10) -> int:
    """win32 子窗口下钻：WindowFromPoint 根下找包含点的最深子窗口。"""
    point = wintypes.POINT(x, y)
    for _ in range(max_depth):
        child = int(
            ctypes.windll.user32.ChildWindowFromPointEx(hwnd, point, 0) or 0
        )
        if not child or child == hwnd:
            break
        hwnd = child
    return hwnd


def _win32_root_at(x: int, y: int) -> int:
    """点下的 win32 根窗口（UIA 虚拟元素无 handle 时的兜底定位）。

    XAML（Terminal 标签页）/ 桌面 ListItem 等虚拟元素没有 win32 handle，
    ElementFromPoint 的钻取链会断；此时用 win32 窗口链定位根窗口，
    再在根窗口 UIA 树里做窗口作用域枚举。
    """
    hwnd = int(ctypes.windll.user32.WindowFromPoint(wintypes.POINT(x, y)) or 0)
    if not hwnd:
        return 0
    deepest = _win32_deepest_child(hwnd, x, y)
    return _root_window_handle(deepest) or deepest


# 网页 DOM 内容宿主（数千节点的无障碍树，属 bsk 页内捕获领域）：
# DFS 时跳过其子树，但浏览器 UI 骨架（TabStrip/Toolbar 等兄弟分支）照走
_DOM_HOST_MARKERS = ("Chrome_RenderWidgetHostHWND", "RootWebArea")


def _class_name_of(hwnd: int) -> str:
    if not hwnd:
        return ""
    buf = ctypes.create_unicode_buffer(256)
    ctypes.windll.user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _is_dom_host(info) -> bool:
    """UIA 节点是否网页 DOM 内容宿主（跳过其子树，避免展开数千节点）。"""
    try:
        cls = getattr(info, "class_name", "") or ""
        if any(marker in cls for marker in _DOM_HOST_MARKERS):
            return True
        aid = getattr(info, "automation_id", None) or ""
        if "RootWebArea" in aid:
            return True
        name = getattr(info, "name", "") or ""
        if "RootWebArea" in name:
            return True
    except Exception:
        pass
    return False


def _dfs_smallest_at(info, x: int, y: int, *, max_depth: int = 30,
                     max_children: int = 400):
    """含点优先 DFS：从 info 向下逐层只走包含点的分支，每层取面积最小者。

    与 _drill_to_leaf 同构但作用于任意子树根（窗口作用域），并跳过
    DOM 内容宿主子树（浏览器网页内容）。返回最深命中元素（info 层级对象）。
    """
    current = info
    for _ in range(max_depth):
        if _is_dom_host(current):
            break
        try:
            children = current.children()
        except Exception:
            break
        if not children or len(children) > max_children:
            break
        containing = []
        for child in children:
            if _is_dom_host(child):
                continue
            try:
                rect = child.rectangle
            except Exception:
                continue
            if _rect_contains(rect, x, y):
                containing.append((_rect_area(rect), child))
        if not containing:
            break
        containing.sort(key=lambda pair: pair[0])
        candidate = containing[0][1]
        # 防环：仅双方都有真实 handle 且相等才算同元素（虚拟元素 handle 全为 0，不比）
        cand_hwnd = int(getattr(candidate, "handle", 0) or 0)
        curr_hwnd = int(getattr(current, "handle", 0) or 0)
        if cand_hwnd and curr_hwnd and cand_hwnd == curr_hwnd:
            break
        current = candidate
    return current


def _window_scope_hit(root_hwnd: int, x: int, y: int):
    """窗口作用域命中：UIA 根下的含点优先 DFS（替代全树 descendants 枚举）。

    返回 (element_info, rect)；找不到返回 (None, None)。
    """
    if not root_hwnd:
        return None, None
    from pywinauto.uia_defines import IUIA
    from pywinauto.uia_element_info import UIAElementInfo

    try:
        root_element = IUIA().iuia.ElementFromHandle(root_hwnd)
        root_info = UIAElementInfo(root_element)
    except Exception:
        return None, None
    leaf = _dfs_smallest_at(root_info, x, y)
    try:
        rect = leaf.rectangle
    except Exception:
        return None, None
    if not _rect_contains(rect, x, y):
        return None, None
    return leaf, rect


def _rect_contains(rect, x: int, y: int) -> bool:
    return (rect.left <= x < rect.right and rect.top <= y < rect.bottom
            and (rect.right - rect.left) > 0 and (rect.bottom - rect.top) > 0)


def _rect_area(rect) -> int:
    return max(rect.right - rect.left, 1) * max(rect.bottom - rect.top, 1)


def _drill_to_leaf(info, x: int, y: int, *, max_depth: int = 12,
                   max_children: int = 200):
    """从 ElementFromPoint 结果向下钻取：逐层找包含该点且面积最小的子元素。

    UIA ElementFromPoint 常返回粗粒度容器（窗口/Pane）；小控件/小文字需要
    沿树向下钻到最深叶子。仅沿点路径钻取（O(深度×每层子数)），大容器
    （如浏览器 Document 数千子节点）超过 max_children 即停（那是 bsk 的领域）。
    鸭子类型：info 需有 .children()/.rectangle/.handle（便于单测替身）。
    """
    current = info
    for _ in range(max_depth):
        try:
            children = current.children()
        except Exception:
            break
        if not children or len(children) > max_children:
            break
        containing = []
        for child in children:
            try:
                rect = child.rectangle
            except Exception:
                continue
            if _rect_contains(rect, x, y):
                containing.append((_rect_area(rect), child))
        if not containing:
            break
        containing.sort(key=lambda pair: pair[0])
        candidate = containing[0][1]
        # 防环：仅双方都有真实 handle 且相等才算同元素（虚拟元素 handle 全为 0，不比）
        cand_hwnd = int(getattr(candidate, "handle", 0) or 0)
        curr_hwnd = int(getattr(current, "handle", 0) or 0)
        if cand_hwnd and curr_hwnd and cand_hwnd == curr_hwnd:
            break
        current = candidate
    return current


def _verify(window, criteria: dict, automation_id: str | None) -> int:

    matches = window.descendants(**criteria)
    if automation_id:
        matches = [
            match
            for match in matches
            if getattr(match.element_info, "automation_id", None) == automation_id
        ]
    return len(matches)


def _verify_in_root(root_hwnd: int, criteria: dict, automation_id: str | None) -> int:
    from pywinauto import Desktop

    window = Desktop(backend="uia").window(handle=root_hwnd)
    return _verify(window, criteria, automation_id)


def _describe_info(info, root_hwnd: int) -> dict:
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
            verify_count = _verify_in_root(root_hwnd, criteria, automation_id)
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


def capture_in_window(root_hwnd: int, x: int, y: int) -> dict | None:
    """窗口作用域 hit-test：含点优先 DFS 找最深命中（免疫覆盖层 + 不展开全树）。

    找不到包含点的后代时返回 None（调用方回退屏幕级 hit-test）。
    """
    leaf, _rect = _window_scope_hit(root_hwnd, x, y)
    if leaf is None:
        return None
    return _describe_info(leaf, root_hwnd)


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


def _hover_hit(x: int, y: int, exclude_hwnd: int, allow_scoped: bool = True):
    """hover 命中：返回 (rect, root_hwnd, scoped_ran)。

    快路径优先（ElementFromPoint + 向下钻取，正常控件/浏览器 UI 骨架都在这命中）；
    仅当快路径失败（无 handle 虚拟元素）或命中面积过大（疑似粗容器）时才走
    窗口作用域 DFS 兜底。allow_scoped=False 时跳过 DFS（hover 高频帧节流用，
    捕获瞬间必须 True 保证精度）。scoped_ran 表示本次是否真正执行了 DFS。
    """
    rect = None
    root = 0
    scoped_ran = False
    try:
        info = _element_from_point(x, y)
        hwnd = int(info.handle or 0)
        if hwnd != exclude_hwnd:
            # 虚拟元素（handle=None：桌面 ListItem / XAML 文本）同样可用——
            # ElementFromPoint 返回的 rect 本来就有效，handle 只用于根窗口定位
            fine = _drill_to_leaf(info, x, y)
            rect = fine.rectangle
            root = _root_window_handle(hwnd) if hwnd else 0
    except Exception:
        pass
    # 大矩形（>约 400x300）疑似粗容器命中（面板/文档/整窗），才走 DFS 兜底
    need_scoped = rect is None or _rect_area(rect) > 120_000
    if need_scoped and allow_scoped:
        scoped_ran = True
        if not root:
            root = _win32_root_at(x, y)
        if root:
            scoped_info, scoped_rect = _window_scope_hit(root, x, y)
            if scoped_rect is not None and (
                rect is None or _rect_area(scoped_rect) < _rect_area(rect)
            ):
                rect = scoped_rect
    if rect is None:
        # 全部失败时退化到窗口矩形（至少框住目标窗口）
        if root:
            win_rect = wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(root, ctypes.byref(win_rect))
            rect = win_rect
    return rect, root, scoped_ran


def _hover_capture(hotkey_vk: int, timeout: float) -> dict:
    """hover 模式：鼠标移动实时高亮命中元素；热键或 Ctrl+Click 捕获；Esc 取消。

    作用域取悬停点 win32 根窗口（免疫屏幕覆盖层劫持），而非前台窗口。
    """
    overlay = _HoverOverlay()
    deadline = time.monotonic() + timeout
    last_pos = (-1, -1)
    last_hit = 0.0
    last_scoped = 0.0  # DFS 节流：粗命中区域 150ms 一次（浏览器 UI 骨架变化慢）
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
                # 粗命中（大 rect）时才允许 DFS，且节流 150ms（DFS ~50ms 不能每帧跑）
                allow_scoped = time.monotonic() - last_scoped > 0.15
                rect, root, scoped_ran = _hover_hit(
                    x, y, overlay.hwnd, allow_scoped=allow_scoped
                )
                if scoped_ran:
                    last_scoped = time.monotonic()
                if rect is not None:
                    overlay.show_rect(rect.left, rect.top, rect.right, rect.bottom)
                if root:
                    last_root = root
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
                # 捕获瞬间以当前点重新命中取根窗口（allow_scoped=True 保证精度，
                # 免疫覆盖层 / XAML 虚拟元素无 handle 的情况）
                _rect, root, _ = _hover_hit(x, y, overlay.hwnd, allow_scoped=True)
                scope = root or last_root or None
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
