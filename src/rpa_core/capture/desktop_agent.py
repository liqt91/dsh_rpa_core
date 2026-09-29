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
import threading
import time
from ctypes import wintypes

from rpa_core.capture._trace import trace as _trace

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


# 高亮框几何（M41）：框线**完全落在元素之外**，并给鼠标指针留出热区。
# 维护者 2026-09-28 报：「捕获元素的框跟影刀的一样，躲着鼠标，否则影响其他元素的捕捉」。
# 一手实测（.harness/spike/probe_m41_hover_overlay.py §7，13 个采样点，真机像素判定）：
#   框线带占元素**最外 3px**（旧实现）→ 6/13 个点位的指针 ±2px 内有红色框线像素（最近距离 0）；
#   外移 3px → 2/13；外移 5px → 0/13（最近 4px）；外移 8px → 0/13（最近 7px）。
# 取 5 = BORDER(3) + 指针热区(2)：框线带占元素外 [2,5)，元素内**任意位置**的指针
# ±2px 都碰不到它，同时框仍紧贴元素（不是「飘在外面」）。
OVERLAY_BORDER = 3
OVERLAY_OUTSET = 5


def overlay_bounds(
    left: int, top: int, right: int, bottom: int, *, outset: int = OVERLAY_OUTSET
) -> tuple[int, int, int, int]:
    """元素 rect → 高亮窗口 bounds（外扩 ``outset``）。

    「躲着鼠标」的全部实现就在这里：窗口 region 只保留 ``OVERLAY_BORDER`` 宽的边框带
    （见 ``_HoverOverlay.show_rect``），带的位置完全由 bounds 决定——bounds 外扩 5px
    后，带落在元素外 [2,5)，于是**元素内容与鼠标指针都不再被框线覆盖**。
    不做内缩：内缩会让框线吃掉元素最外 3px，指针贴边时正好压在框线上（旧行为）。
    """
    return left - outset, top - outset, right + outset, bottom + outset


class _HoverOverlay:
    """悬浮高亮框：无边框顶层点击穿透窗口，窗口 region 裁剪成 3px 边框。

    边框带落在**元素之外** [``OUTSET - BORDER``, ``OUTSET``)（见 ``overlay_bounds``）：
    既不吃掉元素内容，也不压住鼠标指针。
    """

    BORDER = OVERLAY_BORDER
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
        # 冷启动归因埋点：首次真正画框 = 用户看见红框的时刻（见 _trace.py）
        if not getattr(self, "_first_show_traced", False):
            self._first_show_traced = True
            _trace("agent", "overlay_first_show")
        # 先外扩再建 region：region 的挖空区因此 = 元素本身，3px 框线带完整落在
        # 元素之外（见 overlay_bounds）——这是「躲着鼠标」的落点。
        bounds = overlay_bounds(left, top, right, bottom)
        if bounds == getattr(self, "_last_bounds", None):
            return  # rect 未变：跳过 region 重建与 SetWindowPos（每帧 GDI 开销）
        self._last_bounds = bounds
        left, top, right, bottom = bounds
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
        gdi32.DeleteObject(inner)  # inner 不再使用，否则每帧泄漏一个 GDI 句柄
        win32gui.SetWindowRgn(self.hwnd, outer, True)  # outer 所有权移交窗口
        win32gui.SetWindowPos(
            self.hwnd, win32con.HWND_TOPMOST, left, top, width, height,
            win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW,
        )

    def pump(self) -> None:
        self._win32gui.PumpWaitingMessages()

    def hide(self) -> None:
        """隐藏高亮框（hybrid 模式鼠标进入浏览器内容区时让位给扩展页内高亮）。"""
        if self.hwnd:
            self._win32gui.ShowWindow(self.hwnd, self._win32con.SW_HIDE)
            self._last_bounds = None

    def destroy(self) -> None:
        if self.hwnd:
            self._win32gui.DestroyWindow(self.hwnd)
            self.hwnd = None


def _element_from_point(x: int, y: int):
    # 冷启动归因埋点：首次 UIA 调用 = pywinauto/comtypes 懒导入 + IUIA 接口生成
    # + ElementFromPoint 的合计成本（2026-09-29，见 _trace.py docstring）
    first = not getattr(_element_from_point, "_traced", False)
    if first:
        _element_from_point._traced = True  # type: ignore[attr-defined]
        _trace("agent", "uia_first_call_start")
    from pywinauto.uia_defines import IUIA

    point = wintypes.POINT(x, y)
    element = IUIA().iuia.ElementFromPoint(point)
    from pywinauto.uia_element_info import UIAElementInfo

    if first:
        _trace("agent", "uia_first_call_done")
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


# 网页 DOM 内容宿主（数千节点的无障碍树，属页内 DOM 捕获领域）：
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
    （如浏览器 Document 数千子节点）超过 max_children 即停（属页内 DOM 捕获领域）。
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
    """hover 命中：返回 (rect, root_hwnd, element_info, scoped_ran)。

    element_info 供捕获瞬间复用（避免重复 hit-test）。其余语义见 DFS 兜底注释。
    """
    rect = None
    root = 0
    scoped_ran = False
    leaf = None
    try:
        info = _element_from_point(x, y)
        hwnd = int(info.handle or 0)
        if hwnd != exclude_hwnd:
            # 虚拟元素（handle=None：桌面 ListItem / XAML 文本 / 浏览器无障碍树）
            # 同样可用——ElementFromPoint 返回的 rect 本来就有效
            fine = _drill_to_leaf(info, x, y)
            rect = fine.rectangle
            # root 定位：有 handle 用 UIA 祖先链；无 handle（虚拟元素）必须走
            # win32 窗口链，否则 hover 缓存的 last_root 会陈旧（指向别的窗口）
            root = _root_window_handle(hwnd) if hwnd else _win32_root_at(x, y)
    except Exception:
        pass
    # 仅快路径完全失败（无 rect）才走 DFS 兜底
    if rect is None and allow_scoped:
        scoped_ran = True
        if not root:
            root = _win32_root_at(x, y)
        if root:
            scoped_leaf, scoped_rect = _window_scope_hit(root, x, y)
            if scoped_rect is not None:
                rect = scoped_rect
                leaf = scoped_leaf
    if rect is None:
        # 全部失败时退化到窗口矩形（至少框住目标窗口）
        if root:
            win_rect = wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(root, ctypes.byref(win_rect))
            rect = win_rect
    return rect, root, leaf, scoped_ran


# hybrid 模式：这些窗口类是浏览器**网页内容区**（让位给 content-script 扩展的页内捕获）
_BROWSER_CONTENT_CLASSES = {"Chrome_RenderWidgetHostHWND"}


def _window_class_at(x: int, y: int) -> str:
    hwnd = int(ctypes.windll.user32.WindowFromPoint(wintypes.POINT(x, y)) or 0)
    return _class_name_of(hwnd)


# ---- Ctrl+Click 穿透拦截（WH_MOUSE_LL）--------------------------------------
# 维护者 2026-09-29 报障：「捕获时 Ctrl+Click 会触发实际的 click」。此前 agent 只是
# 轮询 GetAsyncKeyState **观察**手势（观察不改输入流），点会原样穿透到目标应用——
# 影刀式捕获手势必须吞掉这一次点击。低级鼠标钩子返回 1 = 事件不派发。两个例外/配套：
# ① 浏览器内容区**不吞**（hybrid 让位语义）：那里的 Ctrl+Click 是扩展页内捕获的手势，
#   页面必须收到真点击；② 吞掉的点击同时置 capture_click 兜底触发位——被低级钩子
#   吞掉的事件在 GetAsyncKeyState 里是否仍可见，官方口径不明确，两路都接才稳。
WH_MOUSE_LL = 14
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202


class _MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


if sys.platform == "win32":
    _MOUSEHOOKPROC = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
    )
else:   # 非 Windows 仅 import/单测替身用，永不安装
    _MOUSEHOOKPROC = None

# 回调必须持有模块级引用：ctypes 回调被 GC 会直接进程崩溃
_suppress_state = {
    "handle": None,
    "callback": None,
    "respect_browser_content": False,
    "swallow_pair": False,   # DOWN 被吞后配对的 UP 也吞，否则应用收到孤儿 UP
    "capture_click": False,  # 兜底触发位（见上）
}


def _suppress_proc(n_code, w_param, l_param):
    try:
        if n_code >= 0 and w_param in (WM_LBUTTONDOWN, WM_LBUTTONUP):
            info = ctypes.cast(l_param, ctypes.POINTER(_MSLLHOOKSTRUCT)).contents
            ctrl = bool(ctypes.windll.user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)
            over_browser = (
                _suppress_state["respect_browser_content"]
                and _window_class_at(info.pt.x, info.pt.y) in _BROWSER_CONTENT_CLASSES
            )
            if ctrl and not over_browser:
                if w_param == WM_LBUTTONDOWN:
                    _suppress_state["swallow_pair"] = True
                    _suppress_state["capture_click"] = True
                    if not _suppress_state.get("decide_traced"):
                        _suppress_state["decide_traced"] = True
                        _trace("agent", "hook_down_decide", swallow=True)
                    return 1
                if _suppress_state["swallow_pair"]:
                    return 1
    except Exception:
        pass  # 钩子回调绝不能抛（异常会令钩子失效）
    return ctypes.windll.user32.CallNextHookEx(None, n_code, w_param, l_param)


def _install_click_suppressor(*, respect_browser_content: bool) -> None:
    if _MOUSEHOOKPROC is None:
        return
    _suppress_state["respect_browser_content"] = respect_browser_content
    _suppress_state["swallow_pair"] = False
    _suppress_state["capture_click"] = False
    callback = _MOUSEHOOKPROC(_suppress_proc)
    _suppress_state["callback"] = callback
    # 钩子必须装在**专职泵线程**上：低级钩子回调在安装线程泵消息时被调用，
    # 线程不泵（如 hover 首次 UIA 初始化阻塞数秒）系统会在超时后**旁路钩子**放行
    # 事件——2026-09-29 探针实测：主线程被 UIA 首调卡住时点击原样穿透。专职线程
    # 只做 GetMessageW 泵，钩子回调永远及时。
    ready = threading.Event()
    quit_flag = {"stop": False}

    def _hook_thread():
        user32 = ctypes.windll.user32
        h = int(
            user32.SetWindowsHookExW(WH_MOUSE_LL, callback, None, 0)
        )
        _suppress_state["handle"] = h
        ready.set()
        msg = wintypes.MSG()
        while not quit_flag["stop"]:
            # PeekMessage 轮询而非 GetMessage 阻塞：停机标志才能被及时看到
            if user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            else:
                time.sleep(0.005)

    thread = threading.Thread(target=_hook_thread, daemon=True)
    thread.start()
    _suppress_state["thread"] = thread
    _suppress_state["quit_flag"] = quit_flag
    ready.wait(timeout=5.0)
    _trace(
        "agent", "click_suppressor_installed",
        respect_browser_content=respect_browser_content,
        handle_ok=bool(_suppress_state["handle"]),
    )


def _uninstall_click_suppressor() -> None:
    quit_flag = _suppress_state.get("quit_flag")
    if quit_flag is not None:
        quit_flag["stop"] = True
    handle = _suppress_state["handle"]
    if handle:
        ctypes.windll.user32.UnhookWindowsHookEx(handle)
    thread = _suppress_state.get("thread")
    if thread is not None:
        thread.join(timeout=2.0)
    _suppress_state["handle"] = None
    _suppress_state["callback"] = None
    _suppress_state["thread"] = None
    _suppress_state["quit_flag"] = None
    _suppress_state["swallow_pair"] = False
    _suppress_state["capture_click"] = False


def _hover_capture(hotkey_vk: int, timeout: float, hybrid: bool = False) -> dict:
    """hover 模式：鼠标移动实时高亮命中元素；热键或 Ctrl+Click 捕获；Esc 取消。

    作用域取悬停点 win32 根窗口（免疫屏幕覆盖层劫持），而非前台窗口。
    hybrid=True（混合捕获）：鼠标进入浏览器网页内容区时抑制高亮且忽略捕获手势——
    让位给浏览器扩展的页内捕获（没装扩展时该区域 UIA 捕获本来就不可用，行为不变）。
    """
    overlay = _HoverOverlay()
    _trace("agent", "overlay_created", hybrid=hybrid)
    _install_click_suppressor(respect_browser_content=hybrid)
    deadline = time.monotonic() + timeout
    last_pos = (-1, -1)
    last_hit = 0.0
    last_scoped = 0.0  # DFS 节流：粗命中区域 150ms 一次（浏览器 UI 骨架变化慢）
    last_root = 0
    last_leaf = None  # hover 最后命中的元素（捕获瞬间复用，省一次 hit-test）
    last_rect = None
    hotkey_was = _hotkey_pressed(hotkey_vk)
    lbutton_was = _hotkey_pressed(VK_LBUTTON)
    try:
        while time.monotonic() < deadline:
            x, y = _cursor_pos()
            in_browser_content = hybrid and _window_class_at(x, y) in _BROWSER_CONTENT_CLASSES
            moved = abs(x - last_pos[0]) > 3 or abs(y - last_pos[1]) > 3
            if in_browser_content:
                overlay.hide()
                last_leaf = None
                last_rect = None
            elif moved and time.monotonic() - last_hit > 0.03:
                last_hit = time.monotonic()
                last_pos = (x, y)
                # 粗命中（大 rect）时才允许 DFS，且节流 150ms（DFS ~50ms 不能每帧跑）
                allow_scoped = time.monotonic() - last_scoped > 0.15
                rect, root, leaf, scoped_ran = _hover_hit(
                    x, y, overlay.hwnd, allow_scoped=allow_scoped
                )
                if scoped_ran:
                    last_scoped = time.monotonic()
                if rect is not None:
                    overlay.show_rect(rect.left, rect.top, rect.right, rect.bottom)
                    last_rect = rect
                if root:
                    last_root = root
                if leaf is not None:
                    last_leaf = leaf
            overlay.pump()

            hotkey_now = _hotkey_pressed(hotkey_vk)
            lbutton_now = _hotkey_pressed(VK_LBUTTON)
            escape_now = _hotkey_pressed(VK_ESCAPE)
            # 吞掉的 Ctrl+Click 置位的兜底触发（见 _suppress_state 注释）：读了即清
            hook_click = _suppress_state["capture_click"]
            _suppress_state["capture_click"] = False
            capture_triggered = (hotkey_now and not hotkey_was) or (
                lbutton_now and not lbutton_was and _hotkey_pressed(VK_CONTROL)
            ) or hook_click
            hotkey_was = hotkey_now
            lbutton_was = lbutton_now

            if escape_now:
                return {"cancelled": True}
            if capture_triggered:
                if in_browser_content:
                    # 浏览器内容区：让位给扩展的页内捕获（它有 Ctrl+Click 手势）
                    continue
                # 捕获瞬间：鼠标仍在 hover 最后命中的 rect 内 → 直接复用该元素
                # （省一次 hit-test + DFS，捕获延迟从数百 ms 降到 describe 一次）
                if last_leaf is not None and last_rect is not None and _rect_contains(
                    last_rect, x, y
                ):
                    return _describe_info(last_leaf, last_root or 0)
                # 否则（hover 没跟上/鼠标刚快速移入）走全量 DFS 保证精度
                _rect, root, _leaf, _ = _hover_hit(x, y, overlay.hwnd, allow_scoped=True)
                scope = root or last_root or None
                return capture_with_retry(x, y, scope)
            time.sleep(0.015)
        return {"timeout": True}
    finally:
        _uninstall_click_suppressor()
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
    parser.add_argument("--hybrid", action="store_true",
                        help="混合捕获：浏览器网页内容区让位给扩展页内捕获（仅配合 --hover）")
    args = parser.parse_args()
    mode = "hover" if args.hover else ("point" if args.point is not None else "hotkey")
    _trace("agent", "main_enter", mode=mode)

    if sys.platform != "win32":
        print(json.dumps({"error": "desktop capture requires Windows"}))
        return 1

    import pythoncom

    _trace("agent", "pythoncom_imported")
    pythoncom.CoInitialize()
    _trace("agent", "com_initialized")
    try:
        keys = {"F9": 0x78, "F8": 0x77, "F10": 0x79}
        hotkey = keys.get(args.hotkey.upper(), VK_F9)
        if args.hover:
            result = _hover_capture(hotkey, args.timeout, hybrid=args.hybrid)
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
        _trace(
            "agent", "result_emitted",
            outcome=(
                "cancelled" if result.get("cancelled")
                else "timeout" if result.get("timeout")
                else "descriptor"
            ),
        )
        print(json.dumps(result, ensure_ascii=False))
    finally:
        pythoncom.CoUninitialize()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
