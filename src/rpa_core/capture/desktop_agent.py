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
import os
import subprocess
import sys
import time
import traceback
from ctypes import wintypes

from rpa_core.capture import accessibility
from rpa_core.capture._trace import trace as _trace
from rpa_core.capture.foreground_window import is_browser_content
from rpa_core.capture.image_proposal import (
    DEFAULT_RADIUS,
    clip_template_rect,
    make_image_proposal,
)
from rpa_core.procenv import child_env

VK_F9 = 0x78
VK_ESCAPE = 0x1B

if sys.platform == "win32":
    # 64 位陷阱：句柄 API 不设 restype/argtypes 会按 32 位截断（DefWindowProcW 教训）
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateEventW.argtypes = [
        wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR,
    ]
    kernel32.CreateEventW.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.ResetEvent.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

VK_CONTROL = 0x11
VK_LBUTTON = 0x01
GA_ROOT = 2
POLL_INTERVAL = 0.05

# 无障碍唤醒：默认**关**。写第三方进程内存是显式选择，宿主必须传
# --wake-accessibility 才会打开（微信这类 Qt 应用不写就永远只有外壳）。
_WAKE_ACCESSIBILITY = False
# ★ 唤醒不是「一次成功就永久有效」：微信这类应用会在若干秒后自己把 gate 关回去
# （2026-11-17 真机：唤醒后能框到控件几秒，随后只剩外框，trace 里 before=0）。
# 所以这里不能「每进程只试一次」，而是**节流重试**——只要还在悬停这个 Qt 窗口，
# 就每秒兜一次；`activate` 本身幂等（gate 已是 1 就只补发 WM_GETOBJECT）。
_WAKE_INTERVAL = 1.0
_WAKE_LAST: dict[int, float] = {}
# 不可恢复的失败：dll 里找不到 gate（版本不支持）。反复试只会刷日志。
_WAKE_HOPELESS_REASONS = frozenset({"no_dll", "no_gate"})
_WAKE_HOPELESS: set[int] = set()
# pid -> 上一次是否「树活着」；只在状态翻转时写 trace，避免每秒刷屏。
_WAKE_STATE: dict[int, bool] = {}


def _maybe_wake_accessibility(hwnd: int, *, force: bool = False) -> bool:
    """目标像是「无障碍树休眠」的 Qt 窗口时，把它的 QAccessible gate 打开。

    只在 --wake-accessibility 下生效。判据见 ``accessibility.looks_dormant``：
    类名是 Qt 窗口壳。真正要不要写由 ``activate`` 内部再看 gate 当前值——已经是 1
    就只补发 WM_GETOBJECT。

    ``force=True`` 用于「用户刚按下捕获键」这种必须确认真实的时刻，跳过节流；
    悬停循环里用默认节流值，避免每帧都开进程句柄。

    返回 **True 仅当 gate 是被这次调用从 0 写成 1**——也就是「树刚刚被救活」。
    调用方据此立刻重做一次命中测试：否则用户鼠标停着不动时，红框会停在整窗外框上
    （hover 只在 ``moved`` 时才 hit-test，见 ``_hover_capture``）。

    ★ trace 里目标进程记作 ``target_pid``，**不能叫 ``pid``**：``_trace`` 自己会写
    一条本进程 pid，同名 extra 会把它覆盖掉。2026-11-18 排查「谁在唤醒」时就因此
    把微信的 pid 当成了 agent 的 pid，误判成「另一个进程唤醒的」。
    """
    if not _WAKE_ACCESSIBILITY or not hwnd:
        return False
    try:
        class_name = _class_name_of(hwnd)
    except Exception:  # noqa: BLE001 - 取类名失败不该影响捕获
        return False
    if not accessibility.looks_dormant(class_name, 1):
        return False
    pid = accessibility._pid_of_window(hwnd)
    if not pid or pid in _WAKE_HOPELESS:
        return False
    now = time.monotonic()
    if not force and now - _WAKE_LAST.get(pid, 0.0) < _WAKE_INTERVAL:
        return False
    _WAKE_LAST[pid] = now
    try:
        # 稳态（上次已知树是活的）只读字节确认，不再每秒让目标重建一次 provider 树；
        # 只有 gate 真被写过才 poke（`activate` 内部保证）。
        result = accessibility.activate(hwnd, poke=_WAKE_STATE.get(pid) is not True)
    except Exception as exc:  # noqa: BLE001 - 唤醒是增强路径，绝不能打断捕获
        _trace("agent", "accessibility_wake_error", error=type(exc).__name__,
               detail=str(exc)[:200])
        return False
    if not result.ok:
        if result.reason in _WAKE_HOPELESS_REASONS:
            _WAKE_HOPELESS.add(pid)
        # 失败要让用户看得见：否则「没有控件」会被误读成「这个应用没有控件」。
        # 但只报一次——每秒一条会把日志埋掉。
        if _WAKE_STATE.get(pid) is not False:
            _WAKE_STATE[pid] = False
            _trace(
                "agent", "accessibility_wake",
                ok=False, reason=result.reason, target_pid=result.pid,
                rva=result.rva, before=result.before, after=result.after,
                cls=class_name, poked=result.poked,
            )
            _trace("agent", "accessibility_wake_failed", reason=result.reason)
        return False
    # 成功：`activated` 说明 gate 是**从 0 被写回 1**——这就是「树曾经失效过」的
    # 硬证据，必须每次都记（否则「能框几秒又只剩外框」在日志里查不到）。
    # `already_active` 只在状态翻转（上次不是活的）时记一次。
    state_changed = _WAKE_STATE.get(pid) is not True
    _WAKE_STATE[pid] = True
    if result.reason == "activated" or state_changed:
        _trace(
            "agent", "accessibility_wake",
            ok=True, reason=result.reason, target_pid=result.pid,
            rva=result.rva, before=result.before, after=result.after,
            cls=class_name, poked=result.poked,
        )
    return result.reason == "activated"


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
                     max_children: int = 400, path_out: list | None = None):
    """含点优先 DFS：从 info 向下逐层只走包含点的分支，每层取面积最小者。

    与 _drill_to_leaf 同构但作用于任意子树根（窗口作用域），并跳过
    DOM 内容宿主子树（浏览器网页内容）。返回最深命中元素（info 层级对象）。

    ``path_out``（D1）：非 None 时把**沿途经过的每一级容器**依次 append 进去
    （起点 info 也算一级，**不含最终命中的目标本身**）。默认 None ⇒ 行为零变化。
    """
    current = info
    if path_out is not None:
        path_out.append(current)
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
        if path_out is not None:
            path_out.append(current)
    # 最后一跳 candidate 就是目标本身，不入祖先链
    if path_out is not None and path_out:
        path_out.pop()
    return current


def _window_scope_hit(root_hwnd: int, x: int, y: int, path_out: list | None = None):
    """窗口作用域命中：UIA 根下的含点优先 DFS（替代全树 descendants 枚举）。

    返回 (element_info, rect)；找不到返回 (None, None)。
    ``path_out``（D1）：祖先链输出（不含目标本身），见 ``_dfs_smallest_at``。
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
    leaf = _dfs_smallest_at(root_info, x, y, path_out=path_out)
    try:
        rect = leaf.rectangle
    except Exception:
        return None, None
    if not _rect_contains(rect, x, y):
        if path_out is not None:
            path_out.clear()
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


def _locator_step_for(info) -> dict | None:
    """把一个 UIA 层级元素转成 LocatorStep 文档（D1）。

    键集与 ``LOCATOR_STEP_KEYS`` 一致（controlType / automationId / name / className）；
    一个键都取不到时返回 None（该级无法描述，调用方跳过——不发明空 step）。

    **「全空级丢弃」的判据本体在 ``model.desktop.prune_locator_steps``**（M50 抽出共享）——
    本函数只负责从 UIA info **取值**，合法性判断交给那份唯一权威，避免 GUI 编辑器
    另立一套「一级长什么样」的口径。
    """
    from rpa_core.model.desktop import prune_locator_steps

    raw = {
        "controlType": getattr(info, "control_type", None),
        "automationId": getattr(info, "automation_id", None),
        "name": getattr(info, "name", None),
        "className": getattr(info, "class_name", None),
    }
    pruned = prune_locator_steps([raw])
    return pruned[0] if pruned else None


def _path_steps_from(chain: list, root_hwnd: int) -> list:
    """把祖先链元素列表转成 ``path``（D1），**去掉根窗口自身那一级**。

    两条生成分支（`_dfs_smallest_at` 的 DFS 起点、`_ancestor_chain` 的父链链尾）都会把
    根窗口写进链里。根窗口这一级**不携带任何收窄信息**，却是执行器唯一匹配不上的级：
    UIA 里窗口不属于自己的后代（实测 `window.descendants()` 中
    `control_type == "Window"` 的个数为 0），而 `_narrow_by_path` 规定「在后代里找这一级」
    ⇒ 留着它会让**每一条真实 path 在第一级就收窄为空**、桌面元素定位恒失败。

    故这里统一剥掉根窗口级；`path` 的语义随之定为「根窗口**之下**到目标的容器级」。
    传入的链若**只有**根窗口一级，剥完为空 ⇒ 返回 `[]`（调用方据此不写 `path` 字段，
    与「无 path = 不收窄」等价，行为正确）。
    """
    root = int(root_hwnd or 0)
    if root:
        chain = [
            item
            for item in chain
            if int(getattr(item, "handle", 0) or 0) != root
        ]
    return [step for step in (_locator_step_for(item) for item in chain) if step]


def _ancestor_chain(info, root_hwnd: int, *, max_depth: int = 32) -> list:
    """从 ``info`` 向上收集到根窗口的祖先链（**不含 info 本身**），根在前。

    与 ``_dfs_smallest_at(path_out=…)`` 的区别：这条用于屏幕级 ElementFromPoint
    路径（没有 DFS 下降过程，只有父链）。防死循环：深度上限 + handle 重复即停
    （虚拟元素 handle 全为 0，不参与重复判定）。
    """
    chain: list = []
    current = info
    seen: set[int] = set()
    root = int(root_hwnd or 0)
    for _ in range(max_depth):
        try:
            parent = current.parent
        except Exception:
            break
        if parent is None:
            break
        handle = int(getattr(parent, "handle", 0) or 0)
        if handle and handle in seen:
            break
        if handle:
            seen.add(handle)
        chain.append(parent)
        if root and handle == root:
            break
        current = parent
    chain.reverse()
    return chain


def _describe_info(
    info,
    root_hwnd: int,
    path_infos: list | None = None,
    *,
    image_point: tuple[int, int] | None = None,
    prefer_image: bool = False,
) -> dict:
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

    # D1：祖先链（不含目标本身、也不含根窗口自身——见 `_path_steps_from`）。
    # `steps` 提到外面：候选打分要注入同一条 path（见下方 M52 S3）。
    steps: list = []
    if path_infos:
        steps = _path_steps_from(path_infos, root_hwnd)
        if steps:
            locator["path"] = steps

    verify_count = 0
    if criteria:
        try:
            verify_count = _verify_in_root(root_hwnd, criteria, automation_id)
        except Exception:
            verify_count = 0
    if verify_count != 1 and "controlType" in locator and locator["controlType"] == "Pane":
        # 面板类泛化元素找不到唯一 locator 时仍返回（供人工确认），避免假唯一
        verify_count = verify_count if verify_count > 1 else 0

    # M52 S3：本函数是 **hover 快路径 / 窗口作用域** 这条**日常捕获**路径的出口
    # （`_hover_capture` 复用缓存元素时直接 return 它，压根不经 `capture_at`）——候选
    # 与分数必须在这里也落盘，否则确认框里永远看不到（2026-10-10 维护者实测的根因）。
    # 候选形状与 `capture_at` 同源（`_locator_candidates`）；实测走本函数既有的 root
    # 作用域口径（`_root_verify` → `_verify_in_root`），因为本函数没有 window 对象。
    scored = _evaluate_candidates(
        _locator_candidates(control_type, automation_id, name),
        _root_verify(root_hwnd),
        path_steps=steps,
    )
    image_rect: dict | None = None
    # M77：只有用户显式选了图像模式才产 proposal；不覆盖语义命中，也不冒充实测。
    if prefer_image and image_point is not None:
        image_record = _image_proposal_record(root_hwnd, image_point)
        if image_record is not None:
            scored.append(image_record)
            locator = dict(image_record["locator"])
            image_rect = _rect_from_source(image_record["locator"].get("sourceRect"))

    window_title = ""
    if root_hwnd:
        title_buffer = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetWindowTextW(root_hwnd, title_buffer, 256)
        window_title = title_buffer.value

    # 元素**屏幕矩形**（M47.12）：桌面预览的红框要它，且它与窗口坐标截屏的图同源
    # （都是绝对屏幕像素）⇒ 图内框 = 矩形 − 窗口原点，1:1 无换算。
    # 此前**只读不产**：``gui/element_panel._metadata_text`` 一直读 ``meta["rect"]``，
    # 于是那一行永远空着——补上之后既修了那个既有显示缺口，也给预览供了数据。
    rect_text: dict | None = image_rect
    if rect_text is None:
        rect_text = _info_rect(info)

    metadata = {
        "windowHandle": root_hwnd,
        "windowTitle": window_title,
        "controlType": control_type,
        "automationId": automation_id,
        "name": name,
        "className": info.class_name or None,
    }
    if rect_text is not None:
        metadata["rect"] = rect_text

    selector: dict = {"locator": locator}
    if scored:
        selector["candidates"] = scored

    return {
        "kind": "desktop",
        "selector": selector,
        "verifyCount": verify_count,
        "metadata": metadata,
    }


def capture_in_window(root_hwnd: int, x: int, y: int) -> dict | None:
    """窗口作用域 hit-test：含点优先 DFS 找最深命中（免疫覆盖层 + 不展开全树）。

    找不到包含点的后代时返回 None（调用方回退屏幕级 hit-test）。
    """
    path_infos: list = []
    leaf, _rect = _window_scope_hit(root_hwnd, x, y, path_out=path_infos)
    if leaf is None:
        return None
    return _describe_info(leaf, root_hwnd, path_infos)


def _window_rect(root_hwnd: int) -> tuple[int, int] | None:
    """根窗口的 ``(宽, 高)``；句柄无效/调用失败回 None（判据据此不做整窗推断）。"""
    box = wintypes.RECT()
    try:
        ok = ctypes.windll.user32.GetWindowRect(int(root_hwnd), ctypes.byref(box))
    except Exception:  # noqa: BLE001
        return None
    if not ok:
        return None
    return (max(int(box.right - box.left), 1), max(int(box.bottom - box.top), 1))


def _rect_fills_window(rect: dict | None, root_hwnd: int, *, threshold: float = 0.9) -> bool:
    """元素矩形是否几乎铺满根窗口 ⇒ 这一枪只拿到「整窗容器」，没钻到具体控件。

    取不到元素矩形时回 ``True``：宁可多跑一次屏幕级命中，也不要静默把整窗
    当成用户想捕获的控件（M96 真机：悬停框住搜索框、保存下来是 1024x865 整窗）。
    """
    if not root_hwnd:
        return False
    if not isinstance(rect, dict):
        return True
    size = _window_rect(int(root_hwnd))
    if size is None:
        return False
    win_w, win_h = size
    elem_w = max(int(rect.get("width") or 0), 0)
    elem_h = max(int(rect.get("height") or 0), 0)
    return elem_w * elem_h >= win_w * win_h * threshold


def _finer_element_in_same_window(x: int, y: int, root_hwnd: int) -> dict | None:
    """屏幕级命中里取「同一根窗口内、比整窗更细」的元素；不满足就回 None。

    为什么要这条兜底：窗口作用域 DFS 依赖 ``children()``（pywinauto 的 FindAll），
    而微信 4.x 的 UIA provider 对它返回空、只支持 TreeWalker 导航 ⇒ DFS 从根窗口
    一步都迈不出去。``ElementFromPoint`` 这类屏幕级命中却能给出更细的元素
    （真机上悬停红框正是这条路画出来的）。两条路必须收敛到同一个粒度，
    否则「框选看到搜索框、保存下来是整窗」。

    **只接受同一根窗口内的元素**：别的窗口（浮层/覆盖层）面积再小也不能要，
    那正是 DFS 优先设计要免疫的东西。★ 但「同一根窗口」的判法必须与 hover 快路径
    同口径：Qt 自绘控件（微信搜索框）的 UIA 元素 ``handle`` 常为 0，此时按
    ``_win32_root_at(x, y)`` 定位根窗口——直接拿 0 去比会**一票否决唯一可用的命中**
    （M97 真机：hover 框到搜索框、按 F9 保存成整窗）。
    """
    try:
        info = _element_from_point(x, y)
    except Exception:  # noqa: BLE001 - 屏幕级被 provider 拒是常态，兜底而已
        _trace("agent", "finer_unavailable", reason="element_from_point_raised")
        return None
    if info is None:
        _trace("agent", "finer_unavailable", reason="no_element")
        return None
    try:
        handle = int(info.handle or 0)
    except Exception:  # noqa: BLE001
        return None
    root_of_element = _root_window_handle(handle) if handle else _win32_root_at(x, y)
    if (root_of_element or 0) != int(root_hwnd):
        _trace(
            "agent", "finer_other_window",
            element_root=int(root_of_element or 0), expected=int(root_hwnd),
        )
        return None
    try:
        fine = _drill_to_leaf(info, x, y)
    except Exception:  # noqa: BLE001
        fine = info
    captured = _describe_info(fine, root_hwnd, _ancestor_chain(fine, root_hwnd))
    rect = captured.get("metadata", {}).get("rect")
    if _rect_fills_window(rect, int(root_hwnd)):
        # 屏幕级也只给到整窗（例如误命中未下钻的渲染宿主）：留痕，否则用户侧
        # 只有「保存下来还是整个窗口」这一条线索。
        _trace(
            "agent", "finer_still_fills_window",
            control_type=captured.get("metadata", {}).get("controlType"),
            rect=rect if isinstance(rect, dict) else None,
        )
        return None
    return captured


def _locator_candidates(
    control_type: str | None,
    automation_id: str | None,
    name: str | None,
) -> list[tuple[dict, dict, str | None]]:
    """由一个 UIA 元素的三个属性生成候选定位方案——**候选形状的唯一权威**。

    从「最具体」到「最宽泛」最多三层，属性缺失的层不生成（不发明空候选）：

    1. ``controlType + automationId (+ name)``（要 aid 与 ct 都有）
    2. ``controlType + name``（要 name 与 ct 都有）
    3. ``controlType``（只要 ct 有）

    **两条捕获路径共用本函数**：``capture_at``（屏幕级点捕获）与
    ``_describe_info``（hover 快路径 / 窗口作用域捕获）。此前候选只在 ``capture_at``
    里就地构造，hover 那条**日常捕获**路径压根没有候选——2026-10-10 维护者实测
    「重启了还是没有」的根因。同源之后再不会出现「集合取决于用户用哪种手势捕获」。
    """
    out: list[tuple[dict, dict, str | None]] = []
    if automation_id and control_type:
        criteria = {"control_type": control_type}
        locator = {
            "backend": "uia",
            "controlType": control_type,
            "automationId": automation_id,
        }
        if name:
            criteria["title"] = name
            locator["name"] = name
        out.append((criteria, locator, automation_id))
    if name and control_type:
        out.append(
            (
                {"control_type": control_type, "title": name},
                {"backend": "uia", "controlType": control_type, "name": name},
                None,
            )
        )
    if control_type:
        out.append(
            (
                {"control_type": control_type},
                {"backend": "uia", "controlType": control_type},
                None,
            )
        )
    return out


def _image_proposal_record(hwnd: int, point: tuple[int, int]) -> dict | None:
    """显式图像模式的 proposal 记录（不冒充实测命中）。

    ``matchedCount=0`` 是刻意的：模板只是从光标附近裁出来的候选，还没有跑过匹配；
    运行期/校验门面会真测。``kind="image"`` 让确认框能把它和 UIA 候选区分开。
    """
    locator = make_image_proposal(hwnd or 0, point)
    if locator is None:
        return None
    from rpa_core.model.desktop import locator_penalty

    return {
        "kind": "image",
        "locator": locator,
        "matchedCount": 0,
        "penalty": locator_penalty(locator),
        "proposed": True,
    }


def _info_rect(info) -> dict | None:
    """UIA 元素的屏幕矩形 → ``metadata["rect"]`` 的具名字段形状。

    **两条捕获路径共用本函数**（hover/窗口作用域的 ``_describe_info`` 与屏幕级
    ``capture_at``）：矩形形状一旦分叉，预览红框、整窗判定、元素面板显示就会
    各说各话。取不到/空矩形回 None（调用方按「没有矩形」处理，绝不编造）。
    """
    if info is None:
        return None
    try:
        box = info.rectangle
    except Exception:  # noqa: BLE001 - 取不到矩形不该让整个捕获失败
        return None
    if box is None:
        return None
    try:
        width = int(box.right - box.left)
        height = int(box.bottom - box.top)
        if width <= 0 or height <= 0:
            return None
        return {
            "left": int(box.left),
            "top": int(box.top),
            "width": width,
            "height": height,
        }
    except Exception:  # noqa: BLE001
        return None


def _rect_from_source(source) -> dict | None:
    """``sourceRect``（``[x, y, w, h]``）→ 预览用的 ``rect``（同语义的具名字段）。

    图像定位的主定位是那块模板，预览框就必须框这块——否则确认框/元素面板框住整个
    窗口，用户看不出「到底截了多大一块」（M84 维护者：「没有更细的粒度」）。
    """
    if not isinstance(source, (list, tuple)) or len(source) != 4:
        return None
    try:
        x, y, w, h = (int(v) for v in source)
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    return {"left": x, "top": y, "width": w, "height": h}


def _window_verify(window):
    """把 pywinauto 窗口对象包成 ``_evaluate_candidates`` 的实测接缝。

    ``window`` 为 None（无根窗口）⇒ 返回 None（调用方据此跳过实测，不产假数据）。
    """
    if window is None:
        return None
    return lambda criteria, automation_id: _verify(window, criteria, automation_id)


def _root_verify(root_hwnd: int | None):
    """把根窗口句柄包成实测接缝——``_describe_info`` 手上没有 window 对象，只有 hwnd。

    口径与 ``_verify_in_root`` 一致（root 作用域）；``root_hwnd`` 为 0/None ⇒ None。
    """
    if not root_hwnd:
        return None
    return lambda criteria, automation_id: _verify_in_root(
        root_hwnd, criteria, automation_id
    )


def _evaluate_candidates(
    candidates: list[tuple[dict, dict, str | None]],
    verify,
    path_steps: list | None = None,
) -> list[dict]:
    """实测每条候选的命中数并给出 penalty 分数（M52 S3，捕获期落盘用）。

    返回 ``[{"kind": "uia", "locator": <dict>, "matchedCount": <int>,
    "penalty": <int>}]``——**全部**评估过的定位方案都保留（含最终被选中的那条），
    供捕获确认框展示「系统评估过哪些方案、各自命中几个、稳定性分数多少」。

    ``verify(criteria, automation_id) -> int`` 是**实测接缝**：点捕获传
    ``_window_verify(window)``（有 pywinauto 窗口对象），hover / 窗口作用域捕获传
    ``_root_verify(root_hwnd)``（只有根窗口句柄）——两条路径打分口径一致，只是命中
    测试的实现不同。``verify`` 为 None（无法实测）⇒ 返回空表。

    ``path_steps``（D1 祖先链，可选）注入每条候选的 locator 并计入其 penalty——
    path 是同一元素的收窄条件，对每条方案都适用，这样「候选之一」与最终主定位
    完全一致（含 path），debug 时不会出现「主定位有 path、候选没有」的错位。

    某条实测异常 ⇒ 该条跳过。产出的 ``locator``/``matchedCount`` 正是
    ``choose_best_locator`` 择优时读的两个键。
    """
    from rpa_core.model.desktop import locator_penalty

    if verify is None:
        return []
    scored: list[dict] = []
    for criteria, locator, automation_id in candidates:
        try:
            count = verify(criteria, automation_id)
        except Exception:
            continue
        full = dict(locator)
        if path_steps:
            full["path"] = [dict(step) for step in path_steps]
        scored.append(
            {
                "kind": "uia",
                "locator": full,
                "matchedCount": int(count),
                "penalty": locator_penalty(full),
            }
        )
    return scored


def _choose_from_scored(scored: list[dict]) -> tuple[dict, int]:
    """从已实测打分的候选里择优：唯一性硬门 + penalty 最小化（共享引擎，M52 单一权威）。

    净函数（不碰 window / ``_verify``）：把 ``_evaluate_candidates`` 的产物交给
    ``model.desktop.choose_best_locator``；空列表 ⇒ ``({}, 0)``，调用方自行回退。
    """
    from rpa_core.model.desktop import choose_best_locator

    chosen = choose_best_locator(
        [
            {"locator": item["locator"], "count": item["matchedCount"]}
            for item in scored
        ]
    )
    if chosen is None:
        return {}, 0
    return chosen["locator"], chosen["count"]


def _pick_best_candidate(
    candidates: list[tuple[dict, dict, str | None]], window
) -> tuple[dict, int]:
    """实测候选后择优的薄封装——与 ``capture_at`` 走**同一条**「实测 → 打分 → 择优」路径。

    组合 ``_evaluate_candidates``（依赖 window/``_verify`` 的脏活）与
    ``_choose_from_scored``（共享引擎的纯择优），单测据它钉住这条链路确实接上了
    （不是残留的朴素「遇 count==1 即 break」循环）。
    """
    return _choose_from_scored(
        _evaluate_candidates(candidates, _window_verify(window))
    )


def capture_at(
    x: int,
    y: int,
    scope_hwnd: int | None = None,
    *,
    image_point: tuple[int, int] | None = None,
    prefer_image: bool = False,
) -> dict:
    # 显式图像模式不能在这里提前返回——否则窗口作用域捕获会绕过 image proposal。
    if scope_hwnd and not prefer_image:
        # 窗口作用域捕获最容易撞上「Qt 外壳」：先给休眠的无障碍树一次唤醒机会，
        # 否则这道门里只能看到 MMUIRenderSubWindowHW 一个节点。
        # force：用户已经按下捕获键，这一刻的树状态必须是真实的，不能因为悬停循环
        # 一秒前刚试过就跳过（gate 可能正好在这 1 秒内被目标关回去）。
        _maybe_wake_accessibility(int(scope_hwnd), force=True)
        scoped = capture_in_window(scope_hwnd, x, y)
        if scoped is not None and not _rect_fills_window(
            scoped.get("metadata", {}).get("rect"), int(scope_hwnd)
        ):
            return scoped
        # ★ 只拿到整窗容器（微信这类 provider 的 DFS 迈不出第一步）：再问一次屏幕级，
        #   同一窗口内更细的元素才采用。否则用户悬停看到搜索框、保存下来是整窗。
        finer = _finer_element_in_same_window(x, y, int(scope_hwnd))
        if finer is not None:
            return finer
        if scoped is not None:
            return scoped
    info = None
    if prefer_image:
        # ★ 自绘控件（微信等）上 UIA ElementFromPoint 可能直接 COM 拒绝访问。显式图像
        # 模式本来就不依赖语义树：这里失败只记 trace，继续用 scope_hwnd 产出 proposal。
        # 非图像模式保持原行为——该抛就抛，不静默降级。
        try:
            info = _element_from_point(x, y)
        except Exception as exc:  # noqa: BLE001 - 仅显式图像模式吞，语义模式绝不吞
            _trace("agent", "image_uia_unavailable", error=type(exc).__name__)
    else:
        info = _element_from_point(x, y)

    if info is None:
        hwnd = int(scope_hwnd or 0)
        root_hwnd = _root_window_handle(hwnd) or hwnd
        control_type = automation_id = name = class_name = None
        candidates = []
        steps = []
        scored = []
    else:
        hwnd = int(info.handle or 0)
        root_hwnd = _root_window_handle(hwnd) or (scope_hwnd or 0)
        control_type = info.control_type
        automation_id = info.automation_id or None
        name = info.name or None
        class_name = info.class_name or None

        # 候选形状的唯一权威在 `_locator_candidates`（与 hover / 窗口作用域路径共用）。
        candidates = _locator_candidates(control_type, automation_id, name)

        # D1：屏幕级路径没有 DFS 下降过程，祖先链走父链上溯（不含目标本身、不含根窗口自身）。
        # **先算 path 再评估候选**：path 是同一元素的收窄条件，对每条定位方案都适用，故一并
        # 注入候选 locator 并计入分数——这样「候选之一」与最终主定位逐字节一致。
        chain = _ancestor_chain(info, root_hwnd)
        steps = _path_steps_from(chain, root_hwnd)

        from pywinauto import Desktop

        window = Desktop(backend="uia").window(handle=root_hwnd) if root_hwnd else None
        # M52：候选先实测（命中数 + penalty），同一份测量既供择优、也随元素落盘。
        scored = _evaluate_candidates(candidates, _window_verify(window), path_steps=steps)

    best_locator, best_count = _choose_from_scored(scored)
    image_rect: dict | None = None
    # M77：显式图像模式把 proposal 设为主定位；语义候选仍留在 candidates 里供人核对。
    # matchedCount 保持 0——proposal 没跑过图像匹配，不冒充「实测命中」。
    if prefer_image:
        image_record = _image_proposal_record(
            root_hwnd or scope_hwnd or 0, image_point or (x, y)
        )
        if image_record is not None:
            scored.append(image_record)
            best_locator = dict(image_record["locator"])
            best_count = 0
            image_rect = _rect_from_source(image_record["locator"].get("sourceRect"))
    if not best_locator and control_type:
        best_locator = {"backend": "uia", "controlType": control_type}
        best_count = 0
        if steps:
            best_locator["path"] = [dict(step) for step in steps]

    # M52 S3：评估过的全部定位方案（含分数）随元素落盘——捕获确认框据此 debug 展示。
    # desktop 的 selector 是自由字典：``_candidate_errors`` 只校验 browser 的候选，
    # 这里的形状（每项带 ``locator`` 而非 ``selector`` 串）不会被拦。
    selector: dict = {"locator": best_locator}
    if scored:
        selector["candidates"] = scored

    window_title = ""
    if root_hwnd:
        title_buffer = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetWindowTextW(root_hwnd, title_buffer, 256)
        window_title = title_buffer.value

    metadata: dict = {
        "point": [x, y],
        "windowHandle": root_hwnd,
        "windowTitle": window_title,
        "controlType": control_type,
        "automationId": automation_id,
        "name": name,
        "className": class_name,
    }
    # 图像主定位时 rect 必须是那块模板（而不是整窗）——预览/确认框据此画框。
    if image_rect is not None:
        metadata["rect"] = image_rect
    else:
        # 语义模式也要写 rect：预览红框读它，``capture_with_retry`` 判「这一枪是不是
        # 只拿到整窗容器」也读它。此前只有图像模式写 ⇒ 语义捕获的 rect 恒缺，
        # 下游只能按「拿不到矩形 = 容器」保守处理（M97）。
        rect_text = _info_rect(info)
        if rect_text is not None:
            metadata["rect"] = rect_text

    return {
        "kind": "desktop",
        "selector": selector,
        "verifyCount": max(best_count, 0),
        "metadata": metadata,
    }


def _capture_rank(result: dict, scope_hwnd: int | None) -> tuple[int, int]:
    """重试择优的排序键：**先看粒度，再看命中数**。

    为什么粒度必须压过命中数：微信 4.x 的 provider 不支持 ``FindAll``，屏幕级兜底
    给出的搜索框 ``verifyCount`` 恒 0（实测不出），而整窗渲染宿主是 Pane 泛化、能
    「命中」好几次。只按命中数择优，会把用户框住的搜索框换成整个微信窗口——这正是
    M97 真机报障的第二形态。
    """
    rect = result.get("metadata", {}).get("rect")
    container = _rect_fills_window(rect, int(scope_hwnd or 0))
    return (0 if container else 1, int(result.get("verifyCount") or 0))


def capture_with_retry(
    x: int,
    y: int,
    scope_hwnd: int | None,
    attempts: int = 4,
    *,
    prefer_image: bool = False,
) -> dict:
    """整体重试（重新 hit-test）：负载下首次 UIA 树枚举偶发不完整或被覆盖层劫持。"""
    if prefer_image:
        # 图像模式的 ``verifyCount`` 恒 0（proposal 没跑过匹配，不冒充实测），
        # 因而下面的「等到 1 就返回」永不成立——按原样会白跑 4 轮（每轮一次 UIA
        # 命中 + 一张窗口截图，真机 200~900ms/轮）。一次就够。
        return capture_at(x, y, scope_hwnd, prefer_image=True)
    best: dict | None = None
    best_rank: tuple[int, int] = (0, 0)
    for index in range(attempts):
        result = capture_at(x, y, scope_hwnd, prefer_image=prefer_image)
        rank = _capture_rank(result, scope_hwnd)
        # 「唯一命中且已经钻到具体控件」才算够好；整窗容器命中一次不算（否则
        # 微信这类 provider 会让第一轮就带着整窗返回，兜底的细元素白算）。
        if rank[1] == 1 and rank[0] == 1:
            return result
        if best is None or rank > best_rank:
            best = result
            best_rank = rank
        if index < attempts - 1:
            time.sleep(0.4)
    return best if best is not None else {"timeout": True}


def _hover_hit(x: int, y: int, exclude_hwnd: int, allow_scoped: bool = True):
    """hover 命中：返回 (rect, root_hwnd, element_info, scoped_ran, path_infos, hit_info)。

    element_info 供捕获瞬间复用（避免重复 hit-test）。``path_infos``（D1）是该元素的
    祖先链（不含自身，root 在前），供捕获瞬间一并带出；取不到时为空列表。
    ``hit_info`` 是祖先链所描述的那个元素（快路径 = 下钻后的 fine，DFS 兜底 = leaf），
    供调用方保证「path 与元素同源」——两者必须一起缓存，否则 path 会挂到别的元素上。
    其余语义见 DFS 兜底注释。
    """
    rect = None
    root = 0
    scoped_ran = False
    leaf = None
    path_infos: list = []
    hit_info = None
    # ★ 唤醒必须在 hit-test **之前**：无障碍树休眠时（微信冷启动）`ElementFromPoint`
    #   可能直接抛 E_ACCESSDENIED，异常会跳进下面的 except，唤醒就永远轮不到——
    #   于是「树休眠 → 命中失败 → 不唤醒 → 一直休眠」形成死锁，用户只看到外框。
    #   实测 2026-11-17：唤醒后能框到控件几秒，随后微信把 gate 关回去，正是这个形态。
    probe_root = 0
    try:
        probe_root = _win32_root_at(x, y)
        if probe_root:
            _maybe_wake_accessibility(probe_root)
    except Exception:  # noqa: BLE001 - 唤醒失败不影响后续命中
        probe_root = 0
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
            root = _root_window_handle(hwnd) if hwnd else (probe_root or _win32_root_at(x, y))
            if root:
                _maybe_wake_accessibility(root)
                path_infos = _ancestor_chain(fine, root)
            hit_info = fine
    except Exception:
        pass
    # 仅快路径完全失败（无 rect）才走 DFS 兜底
    if rect is None and allow_scoped:
        scoped_ran = True
        if not root:
            root = probe_root or _win32_root_at(x, y)
        if root:
            path_infos = []
            scoped_leaf, scoped_rect = _window_scope_hit(root, x, y, path_out=path_infos)
            if scoped_rect is not None:
                rect = scoped_rect
                leaf = scoped_leaf
                hit_info = scoped_leaf
    if rect is None:
        # 全部失败时退化到窗口矩形（至少框住目标窗口）
        if root:
            win_rect = wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(root, ctypes.byref(win_rect))
            rect = win_rect
            # 退化取证：用户侧看到的是「只框到外框」，但没有任何原因线索。
            # 这条日志把「快路径与 DFS 都没命中」这件事留痕（权限被拒是最常见原因）。
            _trace("agent", "hover_degraded_to_window", hwnd=root)
    return rect, root, leaf, scoped_ran, path_infos, hit_info


# hybrid 模式：浏览器的**网页内容区**让位给 content-script 扩展的页内捕获。
# 「是不是真浏览器」的判据在 foreground_window.is_browser_content（类名 + 进程名）——
# 只看类名会把 Electron 定制客户端一起判死（M84：WorkBuddy 悬停无反应）。


def _window_class_at(x: int, y: int) -> str:
    hwnd = int(ctypes.windll.user32.WindowFromPoint(wintypes.POINT(x, y)) or 0)
    return _class_name_of(hwnd)


def _in_browser_content(x: int, y: int) -> bool:
    """点是否落在**真浏览器**的网页内容区（只有这类区域才让位给扩展）。

    ★ 判据必须是「类名 + 进程名」两个条件（M84 真机）：
    ``Chrome_RenderWidgetHostHWND`` 不只出现在浏览器里——Electron 定制客户端
    （WorkBuddy / 咚咚 / VS Code）的渲染子窗同样是这个类。只按类名判，会让这类
    应用整片被当成「网页内容区」：高亮被隐藏、捕获手势被忽略，用户侧就是
    **悬停在窗口上毫无反应、连窗口都认不出来**。判定权威在
    ``foreground_window.is_browser_content``，本函数只负责取句柄。
    """
    hwnd = int(ctypes.windll.user32.WindowFromPoint(wintypes.POINT(x, y)) or 0)
    return _browser_content_at(_class_name_of(hwnd), hwnd)


def _browser_content_at(class_name: str, hwnd: int) -> bool:
    """命中窗口（任意层级）是否属于真浏览器的网页内容区。

    进程名要按**顶层窗口**取：渲染子窗与顶层窗同进程，但顶层句柄在窗口重建时更稳。
    """
    if not hwnd:
        return False
    return is_browser_content(class_name, _root_window_handle(hwnd) or hwnd)


def _image_box_at(
    root_hwnd: int, x: int, y: int, *, radius: int = DEFAULT_RADIUS
) -> tuple[int, int, int, int] | None:
    """光标处那块模板的屏幕矩形（四边），与落盘模板**同一份几何**。

    图像模式的悬浮红框必须画这块（而不是语义元素的矩形）：自绘控件（微信）没有
    控件树，语义命中只能回到整窗——框住整个窗口的话，用户既看不到「到底截了多大
    一块」，也没法把光标对准某个按钮（M84 维护者实测：「没有更细的粒度」）。
    """
    if not root_hwnd:
        return None
    rect = wintypes.RECT()
    if not ctypes.windll.user32.GetWindowRect(root_hwnd, ctypes.byref(rect)):
        return None
    return clip_template_rect(
        (rect.left, rect.top, rect.right, rect.bottom), (x, y), radius=radius
    )


# ---- Ctrl+Click 穿透拦截（专职钩子子进程）------------------------------------
# 维护者 2026-09-29 报障：「捕获时 Ctrl+Click 会触发实际的 click」。此前 agent 只是
# 轮询 GetAsyncKeyState **观察**手势（观察不改输入流），点会原样穿透到目标应用——
# 影刀式捕获手势必须吞掉这一次点击。钩子放在**专职子进程**（desktop_click_hook）：
# ① 低级钩子回调需要 GIL，而本进程的 UIA 工作（comtypes 冷启动真机实测 6~16s，
#   沙箱 0.3s）又重又慢——回调一旦被拖住，系统原始输入线程串行等钩子，全系统鼠标
#   跟着卡（2026-09-30 维护者报「开启捕获后鼠标移动特别卡」）。零 UIA 依赖的子进程
#   里回调永远及时，鼠标输入与本进程的任何 Python 重活彻底解耦。
# ② 浏览器内容区**不吞**（hybrid 让位语义，子进程自行判定窗口类名）。
# ③ 吞掉的 DOWN 经命名事件 SetEvent 通知本进程作兜底触发（被吞事件在
#   GetAsyncKeyState 里是否可见官方口径不明，轮询与事件两路都接）。

_click_hook_state = {"proc": None, "event": None}


def _start_click_hook(respect_browser_content: bool) -> None:
    if sys.platform != "win32":
        return
    name = f"Local\\rpa-capture-click-{os.getpid()}-{time.monotonic_ns()}"
    handle = kernel32.CreateEventW(None, True, False, name)
    argv = [
        sys.executable, "-m", "rpa_core.capture.desktop_click_hook",
        "--event", name,
        "--parent-pid", str(os.getpid()),
        "--timeout", "600",
    ]
    if respect_browser_content:
        argv.append("--respect-browser-content")
    try:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=child_env(),
        )
    except OSError as exc:
        # 起不来不致命：捕获轮询仍在，只是 Ctrl+Click 会穿透（装钩子前的行为）
        _trace("agent", "click_hook_spawn_failed", error=str(exc))
        if handle:
            kernel32.CloseHandle(handle)
        return
    _click_hook_state["proc"] = proc
    _click_hook_state["event"] = handle
    _trace(
        "agent", "click_hook_spawned", pid=proc.pid,
        respect_browser_content=respect_browser_content,
        event_ok=bool(handle),
    )


def _poll_click_hook_event() -> bool:
    """读兜底触发事件（manual-reset：读到即复位）。"""
    handle = _click_hook_state["event"]
    if handle and kernel32.WaitForSingleObject(handle, 0) == 0:
        kernel32.ResetEvent(handle)
        _trace("agent", "hook_click_seen")
        return True
    return False


def _stop_click_hook() -> None:
    proc = _click_hook_state["proc"]
    if proc is not None:
        proc.terminate()
        _click_hook_state["proc"] = None
    handle = _click_hook_state["event"]
    if handle:
        kernel32.CloseHandle(handle)
        _click_hook_state["event"] = None


def _hover_capture(
    hotkey_vk: int,
    timeout: float,
    hybrid: bool = False,
    *,
    prefer_image: bool = False,
) -> dict:
    """hover 模式：鼠标移动实时高亮命中元素；热键或 Ctrl+Click 捕获；Esc 取消。

    作用域取悬停点 win32 根窗口（免疫屏幕覆盖层劫持），而非前台窗口。
    hybrid=True（混合捕获）：鼠标进入浏览器网页内容区时抑制高亮且忽略捕获手势——
    让位给浏览器扩展的页内捕获（没装扩展时该区域 UIA 捕获本来就不可用，行为不变）。
    """
    overlay = _HoverOverlay()
    _trace("agent", "overlay_created", hybrid=hybrid)
    _start_click_hook(hybrid)
    deadline = time.monotonic() + timeout
    last_pos = (-1, -1)
    last_hit = 0.0
    last_scoped = 0.0  # DFS 节流：粗命中区域 150ms 一次（浏览器 UI 骨架变化慢）
    last_root = 0
    last_rect = None
    last_box = None  # 图像模式：上一次画出的模板框（相同则不重画，免闪烁）
    last_image_trace = 0.0  # 图像模式诊断：每秒至多一条，见下面 image_box 埋点
    last_path: list = []  # D1：与 last_hit_info 同源的祖先链（必须一起更新）
    last_hit_info = None
    last_wake_check = 0.0  # 无障碍保活：与鼠标是否移动无关的定时检查
    force_hit = False  # 树刚被救活 → 立刻重做一次命中测试，不等用户再动鼠标
    hotkey_was = _hotkey_pressed(hotkey_vk)
    lbutton_was = _hotkey_pressed(VK_LBUTTON)
    try:
        while time.monotonic() < deadline:
            x, y = _cursor_pos()
            in_browser_content = (
                hybrid and not prefer_image and _in_browser_content(x, y)
            )
            moved = abs(x - last_pos[0]) > 3 or abs(y - last_pos[1]) > 3
            # ★ 无障碍保活：**不依赖鼠标移动**。目标（微信 4.x）会在若干秒后自己把
            #   QAccessible gate 关回去，而下面的命中测试只在 ``moved`` 时跑——鼠标一停，
            #   红框就永远停在整窗外框上，用户只能再动一下才恢复。这里每 ``_WAKE_INTERVAL``
            #   兜一次；若这次把 gate 从 0 写成 1（树刚被救活），立刻强制重做命中测试。
            if (
                not in_browser_content
                and not prefer_image
                and time.monotonic() - last_wake_check >= _WAKE_INTERVAL
            ):
                last_wake_check = time.monotonic()
                wake_root = _win32_root_at(x, y)
                if wake_root and _maybe_wake_accessibility(wake_root):
                    force_hit = True
            # 命中测试是每移动最大的开销（ElementFromPoint+下钻，全是跨进程 COM；
            # 真机 comtypes 慢一个量级 ⇒ 30ms 连发即「红框追着爬 + CPU 打满」）：
            # 光标仍在当前 rect 内（本次移动大概率命中的还是它）降到 100ms 一测，
            # 跨界（换元素）才全速 30ms。
            inside_last = last_rect is not None and _rect_contains(last_rect, x, y)
            if in_browser_content:
                overlay.hide()
                last_rect = None
                last_path = []
                last_hit_info = None
            elif prefer_image:
                # 图像模式不查控件树：只画「光标周围那块模板」。win32 取根窗口是微秒级，
                # 红框能跟着光标走；UIA 单次命中真机 200~900ms，框都追不上光标，更别说
                # 对准一个 32×32 的按钮（M84 维护者实测「没有更细的粒度」的根因）。
                if moved:
                    last_pos = (x, y)
                    root = _win32_root_at(x, y)
                    if root:
                        last_root = root
                    box = _image_box_at(last_root, x, y)
                    # 图像模式此前完全没有埋点，报障只能靠猜（2026-11-18 维护者报
                    # 「按图捕获框不住桌面/微信元素」）。每秒至多一条：窗口类名回答
                    # 「框的是谁」，box=None 回答「为什么没画」——两种情况修法完全不同。
                    if time.monotonic() - last_image_trace >= 1.0:
                        last_image_trace = time.monotonic()
                        try:
                            root_cls = _class_name_of(last_root)[:48]
                        except Exception:  # noqa: BLE001 - 诊断通道不得打断捕获
                            root_cls = ""
                        _trace(
                            "agent", "image_box", root=int(last_root), cls=root_cls,
                            box=list(box) if box is not None else None,
                            cursor=[x, y],
                        )
                    if box is not None and box != last_box:
                        overlay.show_rect(*box)
                        last_box = box
                        # ★ 不得写 ``last_rect``：那是**语义矩形**的槽（UIA 的 RECT
                        # 对象，下面 ``_rect_contains`` 按 .left/.right 读）。图像模式的
                        # box 是四元组，写进去下一帧就 AttributeError ——而这里是 hover
                        # 主循环，异常会打死整个 agent，父进程只看到「exited with code 1」，
                        # 用户侧就是「鼠标一动就报捕获进程起不来」（2026-11-17 报障）。
            elif (moved or force_hit) and (
                force_hit or time.monotonic() - last_hit > (0.10 if inside_last else 0.03)
            ):
                t_hit = time.perf_counter()
                last_hit = time.monotonic()
                last_pos = (x, y)
                force_hit = False
                # 粗命中（大 rect）时才允许 DFS，且节流 150ms（DFS ~50ms 不能每帧跑）
                allow_scoped = time.monotonic() - last_scoped > 0.15
                try:
                    rect, root, _leaf, scoped_ran, path_infos, hit_info = _hover_hit(
                        x, y, overlay.hwnd, allow_scoped=allow_scoped
                    )
                except Exception as exc:  # noqa: BLE001
                    # UIA 被系统拒绝（E_ACCESSDENIED，常见于受限令牌/沙箱）时，
                    # `_window_scope_hit` 会抛出来。这里是 hover 主循环：一帧命中失败
                    # 不该把整个 agent 打死（否则用户只看到红框消失、面板报崩溃）。
                    _trace("agent", "hover_hit_error", error=str(exc)[:200])
                    time.sleep(0.05)
                    continue
                hit_ms = (time.perf_counter() - t_hit) * 1000
                if hit_ms > 100:
                    # 慢命中取证：真机若仍卡，日志直接给出每次命中耗时
                    _trace("agent", "hover_hit_slow", ms=round(hit_ms, 1),
                           scoped=scoped_ran)
                if scoped_ran:
                    last_scoped = time.monotonic()
                if rect is not None:
                    overlay.show_rect(rect.left, rect.top, rect.right, rect.bottom)
                    last_rect = rect
                if root:
                    last_root = root
                # D1：path 与描述它的元素必须同源同时更新——否则 path 会挂到别的元素上。
                # ★ 「画红框的那个元素」是 ``hit_info``，**不是** DFS 的 leaf：快路径
                #   （ElementFromPoint 成功）压根不产 leaf。这里必须与 ``last_rect``
                #   同进同退——一次命中没有元素就把旧的清掉，否则会出现「矩形是这一处、
                #   元素还是上一次那一处」，捕获瞬间复用的正是元素（M97 真机：hover
                #   框着搜索框、按 F9 保存成整窗）。
                last_hit_info = hit_info
                last_path = list(path_infos) if hit_info is not None else []
            overlay.pump()

            hotkey_now = _hotkey_pressed(hotkey_vk)
            lbutton_now = _hotkey_pressed(VK_LBUTTON)
            escape_now = _hotkey_pressed(VK_ESCAPE)
            # 吞掉的 Ctrl+Click 经命名事件兜底触发（见 _start_click_hook 注释）：
            # manual-reset，读到即复位
            hook_click = _poll_click_hook_event()
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
                if prefer_image:
                    # 图像模式不跑 hover 的 UIA 命中（那一步只为拿语义元素，本模式不用），
                    # 直接按「光标位置 + win32 根窗口」出 descriptor：能取到语义元素就
                    # 顺带带上做候选，取不到就纯像素——自绘控件（微信）属于后者。
                    try:
                        scope = last_root or _win32_root_at(x, y) or None
                        return capture_with_retry(x, y, scope, prefer_image=True)
                    except Exception as exc:  # noqa: BLE001
                        _trace("agent", "capture_error", error=str(exc)[:300])
                        return {"error": f"读不到该位置的元素：{exc}"}
                # ★ 按下捕获键这一刻，树必须是真的活的：下面会**复用缓存的那个元素**，
                #   而它依赖目标进程的 provider 还在。保活有 1 秒窗口，可能刚好被跳过，
                #   此时读旧元素的属性会抛（用户看到「读不到该位置的元素」）。
                revived = False
                if not prefer_image:
                    try:
                        _root_now = int(last_root or _win32_root_at(x, y) or 0)
                        if _root_now:
                            revived = _maybe_wake_accessibility(_root_now, force=True)
                    except Exception:  # noqa: BLE001 - 捕获流程不能因唤醒失败中断
                        revived = False
                try:
                    # 捕获瞬间：鼠标仍在 hover 最后命中的 rect 内 → **直接复用画红框
                    # 的那个元素**（省一次 hit-test + DFS，捕获延迟从数百 ms 降到
                    # describe 一次）。
                    # ★ 复用对象必须是 ``last_hit_info``（快路径 = 下钻后的 fine，
                    #   DFS 兜底 = leaf），而不是只认 DFS 的 leaf：微信这类 provider 的
                    #   FindAll 返回空、DFS 一步都迈不出去，快路径**永远不产 leaf** ——
                    #   只认 leaf 就等于「日常这条路径永远不复用」，一路退到
                    #   capture_at 再走一遍 DFS，最后保存成整窗（M97 真机：框选明明
                    #   停在搜索框）。以「画红框的元素」为准则不会出现这种分叉。
                    if (
                        not revived
                        and last_hit_info is not None
                        and last_rect is not None
                        and _rect_contains(last_rect, x, y)
                    ):
                        # path 与元素同源（两者在命中那一轮一起缓存）⇒ 可以一起带出。
                        return _describe_info(
                            last_hit_info, last_root or 0, last_path or None,
                            image_point=(x, y),
                            prefer_image=prefer_image,
                        )
                    # 否则（hover 没跟上/鼠标刚快速移入）走全量 DFS 保证精度
                    _rect, root, _leaf, _, _path, _hit = _hover_hit(
                        x, y, overlay.hwnd, allow_scoped=True
                    )
                    scope = root or last_root or None
                    return capture_with_retry(x, y, scope, prefer_image=prefer_image)
                except Exception as exc:  # noqa: BLE001
                    # ★ 不能静默：UIA 被拒（E_ACCESSDENIED）/元素在按下瞬间消失时，
                    # 老实现要么让异常打死进程（面板只看到 stderr 尾巴），要么返回
                    # `timeout` 让会话继续干等 —— 用户侧就是「按 F9 毫无反应」。
                    # 这里把确切原因回传，宿主能直接显示「读不到这个位置的元素」。
                    _trace("agent", "capture_error", error=str(exc)[:300])
                    return {"error": f"读不到该位置的元素：{exc}"}
            time.sleep(0.015)
        return {"timeout": True}
    finally:
        _stop_click_hook()
        overlay.destroy()


def main() -> int:
    # 父进程按 UTF-8 读取 stdout：本地化编码（cp936）会让中文描述符破坏管道。
    # 非标准流（测试替身、重定向到 StringIO）没有 reconfigure，不能让它致命。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError, OSError):
            pass
    parser = argparse.ArgumentParser(prog="desktop-capture-agent")
    parser.add_argument("--hotkey", default="F9")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--point", nargs=2, type=int, metavar=("X", "Y"), default=None)
    parser.add_argument("--window-handle", type=int, default=None, dest="window_handle")
    parser.add_argument("--hover", action="store_true",
                        help="hover 模式：鼠标移动实时高亮，热键或 Ctrl+Click 捕获")
    parser.add_argument("--hybrid", action="store_true",
                        help="混合捕获：浏览器网页内容区让位给扩展页内捕获（仅配合 --hover）")
    parser.add_argument("--image", action="store_true",
                        help="显式图像捕获：裁光标周边模板作 image locator 候选")
    parser.add_argument("--wake-accessibility", action="store_true",
                        dest="wake_accessibility",
                        help="目标是无障碍树休眠的 Qt 应用（微信 4.x）时，打开其 "
                             "QAccessible gate（写目标进程一字节，可还原）")
    args = parser.parse_args()
    global _WAKE_ACCESSIBILITY
    _WAKE_ACCESSIBILITY = bool(args.wake_accessibility)
    mode = "hover" if args.hover else ("point" if args.point is not None else "hotkey")
    # image 必须进 trace：取证时「按图捕获」与「捕获元素」的子进程长得一模一样，
    # 只能靠「有没有 UIA 调用」反推（2026-11-17 排查 M85 时踩过）。
    _trace("agent", "main_enter", mode=mode, image=bool(args.image),
           wake_accessibility=_WAKE_ACCESSIBILITY)

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
            result = _hover_capture(
                hotkey, args.timeout, hybrid=args.hybrid, prefer_image=args.image
            )
        elif args.point is not None:
            scope = args.window_handle
            if not scope:
                scope = int(ctypes.windll.user32.GetForegroundWindow()) or None
            result = capture_with_retry(
                args.point[0], args.point[1], scope, prefer_image=args.image
            )
        else:
            deadline = time.monotonic() + args.timeout
            pressed_before = _hotkey_pressed(hotkey)
            result = {"timeout": True}
            while time.monotonic() < deadline:
                pressed = _hotkey_pressed(hotkey)
                if pressed and not pressed_before:
                    x, y = _cursor_pos()
                    scope = int(ctypes.windll.user32.GetForegroundWindow()) or None
                    result = capture_with_retry(x, y, scope, prefer_image=args.image)
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
    except Exception as exc:  # noqa: BLE001
        # 未处理异常 ⇒ 进程秒退，父进程只读得到「exited with code N」，浮窗上写着
        # 「捕获进程起不来」；用户会去重装插件/查环境，而真凶是本进程里的一个 bug
        # （2026-11-17：图像模式 hover 循环抛 AttributeError 就是这个形态）。
        # 把确切原因当**结构化结果**回传，父进程/浮窗能直接显示；完整栈仍写 stderr。
        traceback.print_exc()
        _trace("agent", "crashed", kind=type(exc).__name__, error=str(exc)[:300])
        _trace("agent", "result_emitted", outcome="error")
        reason = f"{type(exc).__name__}: {exc}".replace("\n", " ")
        print(json.dumps({"error": f"桌面捕获内部错误：{reason}"}, ensure_ascii=False))
    finally:
        pythoncom.CoUninitialize()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
