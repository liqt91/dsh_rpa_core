"""元素编辑框：win32 原生对话框（M68 后半场 + M69 影刀式层级编辑）。

两种形态（``show(levels=...)`` 决定）：

- **层级形态**（browser 且有 ``selector.path``，影刀式）：左栏层级勾选
  （勾掉 = 该层不参与定位）、右栏选中层的属性行（勾选 / 精确·包含 / 值可改），
  底部实时预览重组后的 css。重组规则的唯一权威是
  ``capture_kit.selector_tree.compose``——控件只改状态，不自己拼字符串。
- **桌面层级形态**（desktop，M91）：左栏节点勾选（窗口只读、容器可勾掉）、
  右栏属性行（勾选 / 精确·包含·正则 / 值可改），底部实时预览重组后的完整
  selector JSON（保留 candidates）。重组规则唯一权威是
  ``capture_kit.desktop_editing.compose_selector``——控件只改状态，不自己拼 JSON。
- **简式形态**（旧资产无层级模型）：名称 + 可编辑定位 + 只读信息区。

实现要点（全是从浮窗/前一版搬来的教训）：

- 窗口类注册 / 实例分派 / 字体 / 定时器 / 外框反推全部走 ``capture_kit._win32``；
- **键盘行为不手写**：消息泵喂 ``IsDialogMessage``，Enter 触发默认按钮（保存）、
  Esc 转 IDCANCEL、Tab 在控件间轮换，EDIT 的中文 IME 组合态回车不会被当成命令；
- 外框尺寸由 ``_win32.adjust_window_size`` 从客户区反推（标题栏会吃掉底排按钮）；
- ``SetFocus`` 不在建窗期同步做（本机实测卡 ~1.5s），窗口可见后 PostMessage 再设；
- 非 Windows 直接不可用（``available is False``，show 为 no-op），宿主回退页面形态。
"""

from __future__ import annotations

import ctypes
import json
import sys
import threading
from collections.abc import Callable
from typing import Any

from . import _win32, desktop_editing, selector_tree
from .edit_check import EditCheckResult, check_draft, check_structure
from .shot_view import ShotView
from .toast import CheckToast

_CLASS = "RpaElementEditDialog"
# 私有消息：窗口显示后再设焦点（同步 SetFocus 在本机实测会卡 ~1.5s）
_WM_APP_FOCUS = 0x8000 + 2
# 校验线程跑完 → 主线程收尾（只当信号，结果在 _pending_result 里）
_WM_APP_CHECK = 0x8000 + 3
# 热键线程读到 F10 上升沿 → 窗口线程起校验（同样只当信号）
_WM_APP_HOTKEY = 0x8000 + 4

# 简式形态客户区
_SIMPLE_W = 560
_SIMPLE_H = 452
# 层级形态客户区（左层级 + 右属性 + 信息行 + 截图预览）
_TREE_W = 780
# 不含截图区的固定高度（按钮行下沿 + 边距）：截图区按屏幕余量伸缩
_TREE_BASE_H = 552
_TREE_H = 850

# 桌面层级形态客户区（左节点 + 右属性 + 多行组合预览 + 截图预览）
_DESKTOP_W = 940
_DESKTOP_BASE_H = 614
_DESKTOP_H = 912

# 截图预览区（底部一大块）：等比缩放，留白处给提示语。
# ★ 高度不是常量：小屏（1366×768）上按 280 硬排会把「保存/取消」顶到屏幕外，
# 所以按工作区余量收缩；余量太小就整个不建（见 ``_fit_shot_height``）。
_SHOT_MARGIN = 16
_SHOT_H = 280
_SHOT_MIN = 120
_SHOT_GAP = 18

# 结果条配色（与保存错误行的红区分：成功绿、警告琥珀、失败红）
_COLOR_OK = (22, 132, 74)
_COLOR_WARN = (176, 106, 0)
_COLOR_ERROR = (192, 0, 0)
_COLOR_PLAIN = (96, 96, 96)
# 热键提示那行灰字（比正文浅一档，别跟校验结果抢注意力）
_COLOR_FAINT = (128, 128, 128)
# 状态条：左侧留白 + 结果色文字（含圆点前缀），交给 STATIC 自己画——父窗口自绘
# 在 WM_PRINT 下 HDC 原点与客户区不一致，圆点会错位（2026-11-25 真机实测）。
_STATUS_INSET = 16
_COLOR_TEXT = (38, 42, 48)

# A 档皮肤：STATIC 一律透明底（原生默认在某些主题下给灰块），按钮用 BS_FLAT
# 去 Win95 立体感。两者都不改控件行为（Tab/Enter/Esc 仍走 IsDialogMessage）。
_BS_FLAT = 0x8000
_BS_AUTOCHECKBOX = 0x8
_BS_DEFPUSHBUTTON = 0x1

# 层级行 tooltip（全文在悬停时显示，控件本身仍按固定宽度省略）。win32con 没这两个常量。
_TTS_ALWAYSTIP = 0x01
_TTS_NOPREFIX = 0x80
_TTM_ADDTOOL = 0x0400 + 50
_TTM_SETMAXTIPWIDTH = 0x0400 + 104
_TTF_SUBCLASS = 0x0010
_TTF_CENTERTIP = 0x0004


class _TOOLINFO(ctypes.Structure):
    """TTM_ADDTOOL 要的 TOOLINFOW（pywin32 没封装，只能自己按布局声明）。"""

    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("uFlags", ctypes.c_uint),
        ("hwnd", ctypes.c_void_p),
        ("uId", ctypes.c_size_t),
        ("rect", _win32._RECT),
        ("hinst", ctypes.c_void_p),
        ("lpszText", ctypes.c_void_p),
        ("lParam", ctypes.c_ssize_t),
    ]
# 固定控件 id：保存/取消沿对话框惯例（IsDialogMessage 会把 Enter/Esc 映到这两个 id）
_ID_NAME = 201
_ID_META = 202
_ID_ERROR = 203
_ID_SELECTOR = 204
_ID_PREVIEW = 205
_ID_INFO = 206
# 「校验元素」（影刀 §5 主动校验）：显式按钮，别做成边打字边跑
_ID_CHECK = 210
# 按钮右边那行灰字提示（F10 是全局热键，框里不写一句就没人知道）
_ID_HOTKEY_HINT = 211
# 全局校验热键 F10。现场是「鼠标悬停在页面上把下拉菜单悬出来」，这时焦点在
# 浏览器、编辑框被盖住，「校验元素」按钮根本点不到。所以用 watch 线程读
# GetAsyncKeyState（与捕获 agent 同一套机制：只观察、不拦也不改输入流），
# 上升沿 PostMessage 回窗口线程 —— 校验本身仍只在窗口线程里起。
_VK_F10 = 0x79
_HOTKEY_POLL_S = 0.04
# 提示文案放在按钮右边：不写一句，用户不会知道有全局热键这回事
_HOTKEY_HINT_TEXT = "悬停到页面上按 F10 也能校验"
# 动态控件 id 起点（网页层级勾选 / 属性行）
_ID_LEVEL_BASE = 1000
# 层级行左端的小复选框（与行文字分开：点文字只看属性，点方框才切勾选）
_ID_LEVEL_CHECK_BASE = 1100
_ID_ATTR_BASE = 2000
# 桌面层级勾选 / 桌面属性行（与网页层级分开，免得两种形态互相抢 cid）
_ID_NODE_BASE = 3000
_ID_DESKTOP_ATTR_BASE = 4000
# 层级区一屏放得下的行数（y 70 起、行高 32、预览标签在 402）；全链层级超出时
# 窗口右侧给 WS_VSCROLL 滚动条，checkbox 按偏移重排（M69：目标→根的完整链）。
_LEVEL_ROWS = 10
_NODE_ROWS = 10
# 滚动只滚左栏这一块（客户坐标 l/t/r/b）：`ScrollWindowEx` 会连带移动与区域相交
# 的子窗口，右栏属性控件从 x=312（网页）/ 420（桌面）起，不能被卷进来。
_LEVEL_AREA = (0, 64, 302, 70 + _LEVEL_ROWS * 32)
_NODE_AREA = (0, 64, 410, 70 + _NODE_ROWS * 32)
# 拖动滚动块时把「重排子控件」合并到 ~40fps：一条消息 19ms 全量重排会把
# 窗口线程堵死（M108 实测 100 条 1916ms、界面冻结 646ms）。
_SCROLL_THROTTLE = 0.025

_MATCH_MODES = ("精确", "包含")
_DESKTOP_MODES = ("精确", "包含", "正则")
# 标题栏 + 边框 + 任务栏留出来的余量（估大一点，宁可窄不可挤出屏幕）
_WINDOW_CHROME = 96


def _work_area_height() -> int:
    """鼠标所在显示器的工作区高度（与 ``_place`` 同一取舍）。"""
    try:
        import win32api
        import win32con

        cursor = win32api.GetCursorPos()
        monitor = win32api.MonitorFromPoint(cursor, win32con.MONITOR_DEFAULTTONEAREST)
        left, top, right, bottom = win32api.GetMonitorInfo(monitor)["Work"]
        return int(bottom - top)
    except Exception:  # noqa: BLE001 - 拿不到就按 1080 保守算
        return 1080


def _fit_shot_height(base_h: int) -> int:
    """截图区高度：屏幕放得下就 280，放不下就缩，缩到 120 以下干脆不要。"""
    room = _work_area_height() - _WINDOW_CHROME - base_h - _SHOT_GAP
    if room < _SHOT_MIN:
        return 0
    return min(_SHOT_H, room)


def _visible_rows(count: int, desktop: bool) -> int:
    """窗口只给 ``min(实际层数, 满屏行数)`` 行的高度，不为不存在的层留白。"""
    rows = _NODE_ROWS if desktop else _LEVEL_ROWS
    return max(1, min(int(count or 0), rows))


def _content_shift(count: int, desktop: bool) -> int:
    """相对于满屏基准，下方控件整体上移的像素数（行高 32）。"""
    rows = _NODE_ROWS if desktop else _LEVEL_ROWS
    return (rows - _visible_rows(count, desktop)) * 32


def _base_height_for(count: int, desktop: bool) -> int:
    """按实际层数算出的客户区固定高度（不含截图区）。"""
    base = _DESKTOP_BASE_H if desktop else _TREE_BASE_H
    return base - _content_shift(count, desktop)


def _clamp_node_scroll(scroll: int, total: int) -> int:
    """把节点区偏移夹进 ``[0, total - _NODE_ROWS]``（不足一屏就是 0）。"""
    return max(0, min(int(scroll or 0), max(0, int(total) - _NODE_ROWS)))


def _clamp_level_scroll(scroll: int, total: int) -> int:
    """层级区与节点区同口径：偏移永远落在 ``[0, total - _LEVEL_ROWS]``。"""
    return max(0, min(int(scroll or 0), max(0, int(total) - _LEVEL_ROWS)))


def _set_vert_scroll_pos(hwnd, pos: int, *, redraw: bool = True) -> None:
    """只改滚动条位置（range/page 由建窗时定死），拖动期间可关掉重绘。"""
    import win32con
    import win32gui

    # pywin32 的 SCROLLINFO 按位tuple：(mask, min, max, page, pos[, trackPos])；
    # mask 只有 SIF_POS 时后面仍要占位，否则 TypeError。
    win32gui.SetScrollInfo(
        hwnd, win32con.SB_VERT,
        (win32con.SIF_POS, 0, 0, 0, int(pos)), bool(redraw),
    )


class ElementEditDialog:
    """一次性使用的编辑框：show() 起线程弹窗，保存/取消后自行销毁。"""

    def __init__(self) -> None:
        self._hwnd = None
        self._thread: threading.Thread | None = None
        self._hfont = None
        self._font_owned = False
        self._name_edit = None
        self._selector_edit = None
        self._error_static = None
        self._preview_edit = None
        self._on_save: Callable[[str, str, dict | None], str | None] | None = None
        self._on_close: Callable[[bool], None] | None = None
        self._closed = False
        # 探针可观察的最近一次保存错误（界面文案以外的事实源）
        self.last_error: str | None = None
        # 层级形态状态
        self._levels: list[dict[str, Any]] = []
        self._selected = 0
        self._level_scroll = 0
        self._level_boxes: list[int] = []
        self._level_check_boxes: list[int] = []
        # 上一帧每行落到第几行 / 是否屏内。★ 两件事分开记：滚动一格时「行号」
        # 全变（要移动）、可见性只翻两行（要 ShowWindow），混成一个键会让十几
        # 个没变的控件白跑 SetWindowPos + SW_SHOW（实测各占 6.6ms / 4.4ms）。
        self._level_pos: dict[int, int] = {}
        self._level_vis: dict[int, bool] = {}
        # 上一帧画到哪个偏移（None = 还没画）：差值 ±1 行以内才能整段平移
        self._level_painted: int | None = None
        # 最近一次真正重排的时刻（monotonic 秒），用于拖动节流
        self._last_layout = 0.0
        self._attr_controls: list[int] = []
        self._control_map: dict[int, tuple] = {}
        # 桌面层级形态状态（nodes / descriptor 由内核编辑模型提供）
        self._nodes: list[dict[str, Any]] = []
        self._descriptor: dict[str, Any] | None = None
        self._selected_node = 0
        self._node_scroll = 0
        self._node_boxes: list[int] = []
        self._node_pos: dict[int, int] = {}
        self._node_vis: dict[int, bool] = {}
        self._node_painted: int | None = None
        self._desktop_attr_controls: list[int] = []
        self._desktop_control_map: dict[int, tuple] = {}
        # 校验（影刀 §5 主动校验）：结果条复用 info 行，截图区是独立自绘子窗口
        self._check_btn = None
        self._result_static = None
        self._result_color: tuple[int, int, int] | None = None
        self._hotkey_hint = None
        self._shot_view: ShotView | None = None
        self._toast: CheckToast | None = None
        # 截图区的实际几何（``_run`` 按屏幕余量算，_build_* 照着摆）
        self._shot_x = _SHOT_MARGIN
        self._shot_y = 0
        self._shot_w = 0
        self._shot_h = 0
        self._checking = False
        # worker 线程写、主线程读；消息只当信号，结果走这个槽（带序号，防旧结果覆盖新的）
        self._pending_result: tuple[int, EditCheckResult] | None = None
        self._check_seq = 0
        self._kind = ""
        # F10 watch 线程的停止信号（关框即置位，别让线程陪到进程退出）
        self._hotkey_stop: threading.Event | None = None

    @property
    def available(self) -> bool:
        return sys.platform == "win32"

    def show(
        self,
        *,
        title: str,
        name: str = "",
        selector: str = "",
        meta: str = "",
        info: str = "",
        levels: list[dict[str, Any]] | None = None,
        nodes: list[dict[str, Any]] | None = None,
        descriptor: dict[str, Any] | None = None,
        save_label: str = "保存",
        on_save: Callable[[str, str, dict | None], str | None] | None = None,
        on_close: Callable[[bool], None] | None = None,
    ) -> None:
        """弹出编辑框。``on_save(name, selector, edit_state)`` 返回 None = 成功关框；
        返回字符串 = 错误回显留框。``edit_state`` 是 ``{"levels": ...}`` 或
        ``{"nodes": ...}`` 的勾选快照，随定位文本一起落盘，供下次打开详情精确还原。
        ``levels`` 给出时走网页层级形态（selector 参数忽略，以重组预览为准）；
        ``nodes`` 给出时走桌面层级/属性形态（同样以重组预览为准，保存回写完整
        selector 字典）；否则走简式形态（selector 是可编辑文本）。
        """
        if not self.available:
            return
        self._title = title
        self._name = name
        self._selector = selector
        self._meta = meta
        self._info = info
        self._levels = levels or []
        self._nodes = nodes or []
        self._descriptor = descriptor
        # 校验要的 element kind：网页层级形态拿不到完整描述符，但确知自己是 browser
        if self._nodes:
            self._kind = "desktop"
        elif self._levels:
            self._kind = "browser"
        else:
            self._kind = str((descriptor or {}).get("kind") or "browser")
        # 校验状态每轮清零（同一个 dialog 实例可复用）
        self._checking = False
        self._pending_result = None
        self._check_seq = 0
        self._result_color = None
        self._result_static = None
        self._check_btn = None
        self._hotkey_hint = None
        self._shot_view = None
        self._tooltip = None
        self._tooltip_buffers = []
        self._toast = None
        self._hotkey_stop = None
        self._selected = 0
        self._selected_node = 0
        # 布局缓存按这次的数据重建（同一个 dialog 实例可复用）
        self._level_pos = {}
        self._level_vis = {}
        self._level_painted = None
        self._node_pos = {}
        self._node_vis = {}
        self._node_painted = None
        self._last_layout = 0.0
        self._save_label = save_label
        self._on_save = on_save
        self._on_close = on_close
        self._closed = False
        self._thread = threading.Thread(target=self._run, name="element-edit", daemon=True)
        self._thread.start()

    def close(self) -> None:
        """外部强制收框（宿主取消会话时用），按「未保存」处理。"""
        hwnd = self._hwnd
        if hwnd is None:
            return
        try:
            import win32con
            import win32gui

            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        except Exception:  # noqa: BLE001 - 窗已销毁也无妨
            pass

    # -- 窗口线程 ------------------------------------------------------------
    def _run(self) -> None:
        import win32con
        import win32gui

        desktop_mode = bool(self._nodes)
        tree_mode = bool(self._levels) and not desktop_mode
        client_w = _DESKTOP_W if desktop_mode else (_TREE_W if tree_mode else _SIMPLE_W)
        # A 档：窗口按**实际层数**收缩（4 层不再为不存在的 6 层留白）；截图区默认
        # 折叠，等校验真拿到截图才展开。展开走 _ensure_shot_view，不改校验逻辑。
        count = len(self._nodes) if desktop_mode else len(self._levels)
        layered = desktop_mode or tree_mode
        self._shift = _content_shift(count, desktop_mode) if layered else 0
        base_h = (
            _base_height_for(count, desktop_mode) if layered else _SIMPLE_H
        )
        if desktop_mode:
            self._shot_y = base_h
            self._shot_x = _SHOT_MARGIN
            self._shot_w = _DESKTOP_W - 2 * _SHOT_MARGIN
        elif tree_mode:
            self._shot_y = base_h
            self._shot_x = _SHOT_MARGIN
            self._shot_w = _TREE_W - 2 * _SHOT_MARGIN
        else:
            self._shot_y = base_h
            self._shot_x = self._shot_w = 0
        self._shot_h = 0
        self._base_h = base_h
        self._client_w = client_w
        client_h = base_h
        _win32.register_class(_CLASS)
        ex_style = win32con.WS_EX_TOPMOST | win32con.WS_EX_DLGMODALFRAME
        style = (
            win32con.WS_POPUP
            | win32con.WS_CAPTION
            | win32con.WS_SYSMENU
            # 子控件挪位会让父窗口那块区域失效；没有 WS_CLIPCHILDREN 时父窗口
            # 先拿白刷把子控件区域整个擦一遍再让它们重画，一次重排就翻倍。
            | win32con.WS_CLIPCHILDREN
        )
        if len(self._levels) > _LEVEL_ROWS or len(self._nodes) > _NODE_ROWS:
            # 全链层级超一屏：给窗口原生垂直滚动条（WM_VSCROLL 里重排 checkbox）
            style |= win32con.WS_VSCROLL
        # ★ 外框尺寸从客户区反推：把客户区尺寸当外框传，标题栏和边框会吃掉
        # 底部控件（保存按钮被裁一半的真机事故）。
        outer_w, outer_h = _win32.adjust_window_size(client_w, client_h, style, ex_style)
        hwnd = win32gui.CreateWindowEx(
            ex_style,
            _CLASS,
            self._title,
            style,
            0,
            0,
            outer_w,
            outer_h,
            0,
            0,
            win32gui.GetModuleHandle(None),
            None,
        )
        self._hwnd = hwnd
        self._style = style
        self._ex_style = ex_style
        _win32._INSTANCES[int(hwnd)] = self
        if desktop_mode:
            self._build_desktop_children(hwnd)
        elif tree_mode:
            self._build_tree_children(hwnd)
        else:
            self._build_simple_children(hwnd)
        self._place(hwnd, client_w, client_h)
        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:  # noqa: BLE001 - 抢前台是锦上添花，绝不能把窗口线程打死
            # 实测会拿「句柄无效(6)」：前台锁/桌面状态相关的环境性失败。
            # topmost 已保证可见，抢不到前台不该连累整个编辑框。
            pass
        # 焦点在窗口可见之后再设（同步 SetFocus 实测会卡 ~1.5s，藏在可见后用户无感）
        try:
            win32gui.PostMessage(hwnd, _WM_APP_FOCUS, 0, 0)
        except Exception:  # noqa: BLE001 - 设不了焦点也能用，顶多用户多点一下
            pass
        self._start_hotkey_watch(hwnd)
        _win32.pump_dialog(hwnd)
        self._stop_hotkey_watch()
        self._hwnd = None

    # -- F10 全局校验热键 ----------------------------------------------------
    def _f10_down(self) -> bool:
        """只读按键状态：不改输入流，也不影响别人收键。"""
        try:
            import win32api

            return bool(win32api.GetAsyncKeyState(_VK_F10) & 0x8000)
        except Exception:  # noqa: BLE001 - 读不到就当没按，绝不能把窗口线程打死
            return False

    def _start_hotkey_watch(self, hwnd: int) -> None:
        stop = threading.Event()
        self._hotkey_stop = stop
        threading.Thread(
            target=self._watch_hotkey,
            args=(hwnd, stop),
            name="element-check-hotkey",
            daemon=True,
        ).start()

    def _stop_hotkey_watch(self) -> None:
        stop = self._hotkey_stop
        self._hotkey_stop = None
        if stop is not None:
            stop.set()

    def _watch_hotkey(self, hwnd: int, stop: threading.Event) -> None:
        """等 F10 上升沿（按住不放只算一次），然后把活交给窗口线程。

        ★ 必须是上升沿：用户悬停页面时手指多半会多按几下，按住持续的按
        ``GetAsyncKeyState`` 会一直报按下，不判边沿就会连开好几轮校验。
        """
        was = self._f10_down()
        while not stop.wait(_HOTKEY_POLL_S):
            down = self._f10_down()
            if down and not was:
                try:
                    import win32gui

                    win32gui.PostMessage(hwnd, _WM_APP_HOTKEY, 0, 0)
                except Exception:  # noqa: BLE001 - 框已销毁就收工
                    return
            was = down

    def _make_font(self) -> int:
        import win32con
        import win32gui

        self._hfont = _win32.make_font()
        self._font_owned = bool(self._hfont)
        if not self._hfont:
            self._hfont = win32gui.GetStockObject(win32con.DEFAULT_GUI_FONT)
        return self._hfont

    def _control(self, hwnd, cls, text, style, x, y, w, h, cid):
        import win32con
        import win32gui

        # A 档皮肤：普通按钮去 Win95 立体边框；复选框保留系统画法（勾框要清晰）。
        if cls == "BUTTON" and not style & _BS_AUTOCHECKBOX:
            style |= _BS_FLAT
        handle = win32gui.CreateWindowEx(
            0, cls, text,
            style | win32con.WS_CHILD | win32con.WS_VISIBLE | win32con.WS_TABSTOP,
            x, y, w, h, hwnd, cid, win32gui.GetModuleHandle(None), None,
        )
        if handle and self._hfont:
            win32gui.SendMessage(handle, win32con.WM_SETFONT, self._hfont, True)
        return handle

    def _attach_tooltip(self, target: int, text: str) -> None:
        """悬停显示层级行全文；省略号仍由控件自己画，两者不冲突。

        ★ TOOLINFO.lpszText 是裸指针，指向的缓冲区必须活到窗口销毁；用实例列表
        持有，不能是局部变量。tooltip 是父窗口的 owned popup，随父窗口一起销毁。
        """
        import win32con
        import win32gui

        if not target or not text:
            return
        # ★ WinDLL 每次调用返回**新的**包装对象：argtypes 必须在这次调用里设，
        # 不能只在第一次建 tooltip 时设（第二次 SendMessageW 会把 byref 当整数）。
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.SendMessageW.argtypes = [
            ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_void_p,
        ]
        user32.SendMessageW.restype = ctypes.c_ssize_t
        if not getattr(self, "_tooltip", None):
            self._tooltip = win32gui.CreateWindowEx(
                0, "tooltips_class32", "",
                win32con.WS_POPUP | _TTS_ALWAYSTIP | _TTS_NOPREFIX,
                0, 0, 0, 0, self._hwnd, 0,
                win32gui.GetModuleHandle(None), None,
            )
            if not self._tooltip:
                return
            win32gui.ShowWindow(self._tooltip, win32con.SW_SHOW)
            user32.SendMessageW(
                ctypes.c_void_p(self._tooltip), _TTM_SETMAXTIPWIDTH,
                0, ctypes.c_void_p(420),
            )
        buffer = ctypes.create_unicode_buffer(text)
        self._tooltip_buffers.append(buffer)
        info = _TOOLINFO(
            ctypes.sizeof(_TOOLINFO), _TTF_SUBCLASS,
            ctypes.c_void_p(target), 0, _win32._RECT(0, 0, 0, 0),
            None, ctypes.c_void_p(ctypes.addressof(buffer)), 0,
        )
        user32.SendMessageW(
            ctypes.c_void_p(self._tooltip), _TTM_ADDTOOL,
            0, ctypes.byref(info),
        )

    # -- 简式形态（desktop / 无 path 旧资产） ------------------------------------
    def _build_simple_children(self, hwnd) -> None:
        import win32con
        import win32gui

        self._make_font()
        control = lambda *a: self._control(hwnd, *a)  # noqa: E731
        control("STATIC", "资产名称", 0, 16, 12, 74, 24, 200)
        self._name_edit = control(
            "EDIT", self._name,
            win32con.ES_AUTOHSCROLL | win32con.WS_BORDER,
            96, 10, 448, 26, _ID_NAME,
        )
        control("STATIC", "定位选择器", 0, 16, 46, 200, 20, 207)
        self._selector_edit = control(
            "EDIT", self._selector,
            win32con.ES_MULTILINE | win32con.ES_AUTOVSCROLL | win32con.WS_VSCROLL
            | win32con.WS_BORDER,
            16, 68, 528, 148, _ID_SELECTOR,
        )
        control("STATIC", "元素信息（只读）", 0, 16, 226, 200, 20, 208)
        control(
            "EDIT", self._meta,
            win32con.ES_MULTILINE | win32con.ES_READONLY | win32con.WS_VSCROLL
            | win32con.WS_BORDER,
            16, 248, 528, 122, _ID_META,
        )
        self._error_static = control(
            "STATIC", "", 0, 16 + _STATUS_INSET, 378,
            528 - _STATUS_INSET, 20, _ID_ERROR,
        )
        # 简式只有一行提示位，校验结果与保存错误复用（颜色由 _result_color 决定）
        self._result_static = self._error_static
        self._check_btn = control("BUTTON", "校验元素", 0, 232, 406, 100, 30, _ID_CHECK)
        self._hotkey_hint = control(
            "STATIC", _HOTKEY_HINT_TEXT, 0, 16, 412, 200, 20, _ID_HOTKEY_HINT,
        )
        control(
            "BUTTON", self._save_label, win32con.BS_DEFPUSHBUTTON,
            368, 406, 84, 30, win32con.IDOK,
        )
        control("BUTTON", "取消", 0, 460, 406, 84, 30, win32con.IDCANCEL)
        win32gui.SendMessage(self._name_edit, win32con.EM_SETSEL, 0, -1)

    # -- 层级形态（browser，影刀式） ---------------------------------------------
    def _build_tree_children(self, hwnd) -> None:
        import win32con
        import win32gui

        self._make_font()
        control = lambda *a: self._control(hwnd, *a)  # noqa: E731
        control("STATIC", "资产名称", 0, 16, 12, 74, 24, 200)
        self._name_edit = control(
            "EDIT", self._name,
            win32con.ES_AUTOHSCROLL | win32con.WS_BORDER,
            96, 10, 668, 26, _ID_NAME,
        )
        control("STATIC", "层级（勾选参与定位）", 0, 16, 46, 240, 20, 207)
        control("STATIC", "属性（勾选参与本层定位）", 0, 312, 46, 280, 20, 208)
        # 层级行 = 文字（只选中查看属性）+ 左端小方框（才切换勾选）。
        # 旧实现整行是 BS_AUTOCHECKBOX：点文字就切勾选，和目标级整行禁用一样反直觉。
        self._level_boxes = []
        self._level_check_boxes = []
        for index, level in enumerate(self._levels):
            # 行文字与小方框必须**分家**：旧实现行是 BS_CHECKBOX 且 x=16，
            # 与 BS_AUTOCHECKBOX 小方框完全重叠；行那个勾框从没设过状态（恒空），
            # 鼠标扫过时 Windows 重绘 hot 态，空框盖住小方框的勾看起来像“勾没了”。
            # 现在行是纯文字按钮（无勾框），从 x=44 起；只有 x=16 的 box 表示 included。
            row = control(
                "BUTTON", selector_tree.summary(level),
                win32con.BS_LEFT | win32con.BS_FLAT,  # 只选中看属性，不画勾框
                44, 70 + index * 32, 252, 26, _ID_LEVEL_BASE + index,
            )
            box = control(
                "BUTTON", "", win32con.BS_AUTOCHECKBOX,
                16, 70 + index * 32, 20, 26, _ID_LEVEL_CHECK_BASE + index,
            )
            win32gui.SendMessage(
                box, win32con.BM_SETCHECK, 1 if level["included"] else 0, 0
            )
            self._control_map[_ID_LEVEL_BASE + index] = ("level", index)
            self._control_map[_ID_LEVEL_CHECK_BASE + index] = ("level_check", index)
            self._level_boxes.append(row)
            self._level_check_boxes.append(box)
            self._attach_tooltip(row, selector_tree.summary(level))
        self._setup_level_scroll(hwnd)
        self._rebuild_attr_pane(hwnd)
        shift = self._shift
        control("STATIC", "组合选择器", 0, 16, 402 - shift, 200, 20, 209)
        self._preview_edit = control(
            "EDIT", "",
            win32con.ES_AUTOHSCROLL | win32con.ES_READONLY | win32con.WS_BORDER,
            16, 424 - shift, 748, 28, _ID_PREVIEW,
        )
        # 结果条复用 info 行：状态色只落在左侧圆点，文字不再整行染色。
        self._result_static = control(
            "STATIC", self._info, 0, 16 + _STATUS_INSET, 460 - shift,
            748 - _STATUS_INSET, 18, _ID_INFO,
        )
        self._error_static = control(
            "STATIC", "", 0, 16 + _STATUS_INSET, 484 - shift,
            748 - _STATUS_INSET, 18, _ID_ERROR,
        )
        self._check_btn = control(
            "BUTTON", "校验元素", 0, 452, 512 - shift, 120, 30, _ID_CHECK,
        )
        self._hotkey_hint = control(
            "STATIC", _HOTKEY_HINT_TEXT, 0, 16, 518 - shift, 420, 20, _ID_HOTKEY_HINT,
        )
        control(
            "BUTTON", self._save_label, win32con.BS_DEFPUSHBUTTON,
            580, 512 - shift, 88, 30, win32con.IDOK,
        )
        control("BUTTON", "取消", 0, 676, 512 - shift, 88, 30, win32con.IDCANCEL)
        # 截图区默认折叠：校验拿到图后由 _ensure_shot_view 展开（A 档）
        win32gui.SendMessage(self._name_edit, win32con.EM_SETSEL, 0, -1)
        self._refresh_preview()

    def _setup_level_scroll(self, hwnd) -> None:
        """层级超一屏时初始化滚动条并重排 checkbox（不超也要按 0 偏移排一次）。"""
        import win32con
        import win32gui

        self._level_scroll = 0
        self._level_pos = {}
        self._level_vis = {}
        self._level_painted = None
        if len(self._levels) > _LEVEL_ROWS:
            # pywin32 的 SCROLLINFO 是 (mask, min, max, page, pos[, trackPos]) 元组
            mask = win32con.SIF_RANGE | win32con.SIF_PAGE | win32con.SIF_POS
            win32gui.SetScrollInfo(
                hwnd, win32con.SB_VERT,
                (mask, 0, len(self._levels) - 1, _LEVEL_ROWS, 0), True,
            )
        self._layout_levels(hwnd)

    def _apply_layout(
        self,
        hwnd,
        moves: list[tuple[int, int, int, bool | None]],
    ) -> None:
        """提交一帧的「移动 + 显隐」；批量失败就退回逐个 SetWindowPos。"""
        import win32con
        import win32gui

        if not _win32.move_windows(moves):
            for handle, x, y, show in moves:
                flags = (
                    win32con.SWP_NOSIZE | win32con.SWP_NOZORDER
                    | win32con.SWP_NOACTIVATE
                )
                if show is True:
                    flags |= win32con.SWP_SHOWWINDOW
                elif show is False:
                    flags |= win32con.SWP_HIDEWINDOW
                win32gui.SetWindowPos(handle, 0, x, y, 0, 0, flags)

    def _layout_levels(self, hwnd) -> None:
        """按滚动偏移摆放层级行：整段平移 + 只补边缘，`O(滚动差)` 而不是 `O(层数)`。

        ★ 一次滚动**绝不重摆一屏**。实测（M108 真机）把 30 个控件挨个
        `SetWindowPos` 要 16ms，批量提交 7ms，而 `ScrollWindowEx` 把整块内容
        （含子控件，`SW_SCROLLCHILDREN`）位图平移只要 0.12ms——差 60 倍。
        滚动一格时只需把**滚进来的那一行**摆到边上、把**滚出去的那行**藏起来。

        区域裁在左栏（`_LEVEL_AREA`）：`ScrollWindowEx` 会连带移动与区域相交
        的子窗口，右栏属性控件从 x=312 起，绝不能被卷进来。

        真机取证：`.harness/spike/probe_m108_drag.py`（拖动 100 条消息的滞后与
        界面冻结时长）、`.harness/spike/probe_m108_verify.py`（滚到底/回顶后逐行
        核对文字、行距、勾选）。
        """
        import win32con
        import win32gui

        delta = 0 if self._level_painted is None else self._level_scroll - self._level_painted
        smooth = 0 < abs(delta) < _LEVEL_ROWS
        moves: list[tuple[int, int, int, bool | None]] = []
        if smooth:
            # 只 BitBlt + 移子控件，**不** SW_INVALIDATE：那会把整块区域
            # 交给父窗口擦白重画（30 个控件的背景全刷一遍）。滚出来的只有边上
            # 那一两行，单独失效那条就够。
            win32gui.ScrollWindowEx(
                hwnd, 0, -delta * 32, _LEVEL_AREA, None, None,
                win32con.SW_SCROLLCHILDREN,
            )
            rows = min(abs(delta), _LEVEL_ROWS)
            if delta > 0:  # 内容上移，底部露白
                edge_top = 70 + (_LEVEL_ROWS - rows) * 32
            else:
                edge_top = 70
            win32gui.InvalidateRect(
                hwnd,
                (_LEVEL_AREA[0], edge_top, _LEVEL_AREA[2], edge_top + rows * 32),
                True,
            )
            # 系统把屏内行整体挪了 -delta 行，缓存跟着走（否则边缘补位会错行）。
            for index in self._level_pos:
                self._level_pos[index] -= delta
        for index, (row, box) in enumerate(
            zip(self._level_boxes, self._level_check_boxes, strict=True)
        ):
            line = index - self._level_scroll
            visible = 0 <= line < _LEVEL_ROWS
            if smooth:
                # 平移已经把屏内行摆好，只处理进出视口的那几行
                if self._level_vis.get(index) is visible:
                    continue
            elif self._level_pos.get(index) == line and self._level_vis.get(index) is visible:
                continue
            self._level_pos[index] = line
            self._level_vis[index] = visible
            y = 70 + line * 32
            # 行文字与小方框分开摆放：box 在 x=16，row 从 x=44 起，互不覆盖
            moves.append((box, 16, y, visible))
            moves.append((row, 44, y, visible))
        self._apply_layout(hwnd, moves)
        self._level_painted = self._level_scroll
        # ★ 勾选态的唯一事实源是 self._levels，不是复选框上一次画出来的样子。
        # 滚动/重绘后 Windows 会重画子控件；这里按模型回写一遍（只在不一致时
        # 发 BM_SETCHECK），避免「组合里有这一层、方框却是空的」视觉错位。
        self._sync_level_checks(hwnd)

    def _sync_level_checks(self, hwnd) -> None:
        """把层级复选框画成模型里的 included（不动模型，只读回写）。"""
        import win32con
        import win32gui

        for index, box in enumerate(self._level_check_boxes):
            if index >= len(self._levels):
                break
            wanted = 1 if self._levels[index]["included"] else 0
            current = win32gui.SendMessage(box, win32con.BM_GETCHECK, 0, 0)
            if current != wanted:
                win32gui.SendMessage(box, win32con.BM_SETCHECK, wanted, 0)

    def _on_vscroll(self, hwnd, wparam) -> None:
        """滚动层级区：行/页/拖块三档都收（桌面节点与网页层级各走各的偏移）。

        ★ 拖动块（`SB_THUMBTRACK`）有两条硬规矩，缺一条真机就「卡死」
        （M108 实测：100 条拖动消息耗 1916ms、界面最长 646ms 不响应）：

        1. **不写滚动条位置**。`DefWindowProc` 的拖动循环自己维护滑块，这里
           再 `SetScrollInfo(SIF_POS)` 会把滑块钉到我们算的值上，和拖动循环
           来回拉扯（上一版只是把 `redraw` 关掉，位置照样写，所以仍卡）。
        2. **布局节流**。拖动时每移动一像素就一条 `SB_THUMBTRACK`，而一次全
           量重排（移动 2N 个子控件 + 改可见性）要 19ms，等于把窗口线程按在
           地上：消息越积越多，用户看到的就是假死。这里把重排合并到 ~40fps，
           松手（`SB_THUMBPOSITION`/`SB_ENDSCROLL`）立刻补齐并校正滑块。
        """
        import time

        import win32con

        code = wparam & 0xFFFF
        pos = (wparam >> 16) & 0xFFFF
        desktop = bool(self._nodes)
        before = self._node_scroll if desktop else self._level_scroll
        total = len(self._nodes) if desktop else len(self._levels)
        rows = _NODE_ROWS if desktop else _LEVEL_ROWS
        limit = max(0, total - rows)
        after = before
        if code == win32con.SB_LINEUP:
            after = max(0, before - 1)
        elif code == win32con.SB_LINEDOWN:
            after = min(limit, before + 1)
        elif code == win32con.SB_PAGEUP:
            after = max(0, before - rows)
        elif code == win32con.SB_PAGEDOWN:
            after = min(limit, before + rows)
        elif code in (win32con.SB_THUMBTRACK, win32con.SB_THUMBPOSITION):
            after = min(limit, max(0, pos))
        elif code != win32con.SB_ENDSCROLL:
            return
        if desktop:
            self._node_scroll = after
        else:
            self._level_scroll = after
        now = time.perf_counter()
        if code == win32con.SB_THUMBTRACK:
            # 只记偏移；滑块交给拖动循环，重排按节流合并（见 docstring）。
            if now - self._last_layout < _SCROLL_THROTTLE:
                return
        else:
            _set_vert_scroll_pos(hwnd, after, redraw=True)
        self._last_layout = now
        if desktop:
            self._layout_nodes(hwnd)
        else:
            self._layout_levels(hwnd)

    def _on_mouse_wheel(self, hwnd, wparam) -> None:
        """滚轮滚层级/节点区：原生框没有内嵌滚动控件，WM_MOUSEWHEEL 要自己接。

        没接这条时用户只能去拖窗口最右侧的滚动条；多层级页面滚不动，
        拖一次又容易和上面的滑块重绘打架。一格 120，滚 3 行（与常见列表一致）。
        """
        import time

        delta = (((int(wparam) >> 16) & 0xFFFF) + 0x8000) % 0x10000 - 0x8000
        if not delta:
            return
        notches = max(1, abs(delta) // 120)
        rows = 3 * notches * (1 if delta > 0 else -1)
        desktop = bool(self._nodes)
        clamp = _clamp_node_scroll if desktop else _clamp_level_scroll
        total = len(self._nodes) if desktop else len(self._levels)
        before = self._node_scroll if desktop else self._level_scroll
        after = clamp(before - rows, total)  # 正 delta = 向上滚 = 偏移变小
        if after == before:
            return
        if desktop:
            self._node_scroll = after
        else:
            self._level_scroll = after
        _set_vert_scroll_pos(hwnd, after)
        if desktop:
            self._layout_nodes(hwnd)
        else:
            self._layout_levels(hwnd)
        self._last_layout = time.perf_counter()

    def _rebuild_attr_pane(self, hwnd) -> None:
        """右栏属性行随选中层重建（行数随层级属性变，池化比重排省事）。"""
        import win32con
        import win32gui

        for handle in self._attr_controls:
            win32gui.DestroyWindow(handle)
        self._attr_controls = []
        # ★ 只清属性行（check/value/mode）。层级树控件（level 与 level_check）
        # 必须留着映射——漏掉 level_check 时，点完层级行再看属性，小方框就
        # 「点不动了」（WM_COMMAND 查不到 meta 直接 return），且没有任何报错。
        for cid in [
            cid
            for cid, meta in self._control_map.items()
            if meta[0] not in ("level", "level_check")
        ]:
            self._control_map.pop(cid, None)
        level = self._levels[self._selected]
        # ★ 层级勾选是唯一门禁（M109）：这一层没勾上时，属性区只读——字段值
        # 保留（重新勾上就能继续改），但它们不会进选择器，也不许在这里改。
        level_on = bool(level["included"])
        rows: list[tuple[str, str, bool, dict]] = [("id", "id", False, level["id"])]
        rows += [
            ("class", "class", True, cls) for cls in level["classes"]
        ]
        rows.append(("index", "index", False, level["nth"]))
        y = 70
        for row_index, (kind, label, has_mode, state) in enumerate(rows):
            base = _ID_ATTR_BASE + row_index * 4
            box = self._control(
                hwnd, "BUTTON", "", win32con.BS_AUTOCHECKBOX,
                312, y, 20, 26, base,
            )
            win32gui.SendMessage(box, win32con.BM_SETCHECK, 1 if state["on"] else 0, 0)
            win32gui.EnableWindow(box, level_on)
            self._control(hwnd, "STATIC", label, 0, 338, y + 3, 52, 20, base + 1)
            if has_mode:
                combo = self._control(
                    hwnd, "COMBOBOX", "",
                    win32con.CBS_DROPDOWNLIST | win32con.WS_VSCROLL,
                    396, y, 76, 120, base + 2,
                )
                for mode in _MATCH_MODES:
                    win32gui.SendMessage(combo, win32con.CB_ADDSTRING, 0, mode)
                win32gui.SendMessage(
                    combo, win32con.CB_SETCURSEL,
                    _MATCH_MODES.index(state["mode"])
                    if state.get("mode") in _MATCH_MODES else 0, 0,
                )
                self._control_map[base + 2] = ("mode", kind, state)
                self._attr_controls.append(combo)
                win32gui.EnableWindow(combo, level_on)
            # 两栏统一列：有没有模式下拉，值框都从同一 x 起（参差是「土」的主因）
            value_x = 480
            value_w = 764 - value_x
            edit = self._control(
                hwnd, "EDIT", state["value"],
                win32con.ES_AUTOHSCROLL | win32con.WS_BORDER
                | (0 if level_on else win32con.ES_READONLY),
                value_x, y, value_w, 26, base + 3,
            )
            self._control_map[base] = ("check", kind, state)
            self._control_map[base + 3] = ("value", kind, state)
            self._attr_controls += [box, edit]
            y += 34

    def _refresh_preview(self) -> None:
        import win32gui

        if self._preview_edit:
            win32gui.SetWindowText(self._preview_edit, selector_tree.compose(self._levels))
        self._invalidate_check()

    def _invalidate_check(self) -> None:
        """选择器一改，上一轮校验结果就过期了——必须显式作废。

        留着「找到 1 个」而用户已经把某层取消勾选，等于用旧结论担保新选择器。
        """
        if self._checking or self._result_color is None:
            return
        self._set_result("已修改，点「校验元素」重新检查", _COLOR_PLAIN)

    # -- 桌面层级形态（desktop，影刀式节点/属性编辑） -----------------------------
    def _build_desktop_children(self, hwnd) -> None:
        import win32con
        import win32gui

        self._make_font()
        control = lambda *a: self._control(hwnd, *a)  # noqa: E731
        control("STATIC", "资产名称", 0, 16, 12, 74, 24, 200)
        self._name_edit = control(
            "EDIT", self._name,
            win32con.ES_AUTOHSCROLL | win32con.WS_BORDER,
            96, 10, 828, 26, _ID_NAME,
        )
        # 层数写进标题：节点区只放得下 ``_NODE_ROWS`` 行，微信这类应用动辄十几层，
        # 不报总数用户会以为层级抓漏了。
        self._selected_node, self._node_scroll = self._initial_node_view()
        control(
            "STATIC",
            f"层级（勾选参与定位）· 共 {len(self._nodes)} 层，可滚动",
            0, 16, 46, 380, 20, 207,
        )
        control("STATIC", "属性（勾选参与本层定位）", 0, 420, 46, 460, 20, 208)
        self._node_boxes = []
        for index, node in enumerate(self._nodes):
            label = f"{node.get('type') or '节点'} · {node.get('name') or '(无名)'}"
            box = control(
                "BUTTON", label, win32con.BS_AUTOCHECKBOX,
                16, 70 + index * 32, 380, 26, _ID_NODE_BASE + index,
            )
            win32gui.SendMessage(
                box, win32con.BM_SETCHECK, 1 if node.get("selected") else 0, 0
            )
            self._desktop_control_map[_ID_NODE_BASE + index] = ("node", index)
            self._node_boxes.append(box)
            self._attach_tooltip(box, label)
        self._setup_node_scroll(hwnd)
        self._rebuild_desktop_attr_pane(hwnd)
        shift = self._shift
        control(
            "STATIC", "组合选择器（完整 JSON，保留捕获候选）",
            0, 16, 402 - shift, 500, 20, 209,
        )
        self._preview_edit = control(
            "EDIT", "",
            win32con.ES_MULTILINE | win32con.ES_READONLY | win32con.ES_AUTOVSCROLL
            | win32con.WS_VSCROLL | win32con.WS_BORDER,
            16, 424 - shift, 908, 96, _ID_PREVIEW,
        )
        self._result_static = control(
            "STATIC", self._info, 0, 16 + _STATUS_INSET, 526 - shift,
            908 - _STATUS_INSET, 18, _ID_INFO,
        )
        self._error_static = control(
            "STATIC", "", 0, 16 + _STATUS_INSET, 548 - shift,
            908 - _STATUS_INSET, 18, _ID_ERROR,
        )
        self._check_btn = control(
            "BUTTON", "校验元素", 0, 620, 574 - shift, 120, 30, _ID_CHECK,
        )
        self._hotkey_hint = control(
            "STATIC", _HOTKEY_HINT_TEXT, 0, 16, 580 - shift, 580, 20, _ID_HOTKEY_HINT,
        )
        control(
            "BUTTON", self._save_label, win32con.BS_DEFPUSHBUTTON,
            748, 574 - shift, 84, 30, win32con.IDOK,
        )
        control("BUTTON", "取消", 0, 840, 574 - shift, 84, 30, win32con.IDCANCEL)
        # 截图区默认折叠：校验拿到图后由 _ensure_shot_view 展开（A 档）
        win32gui.SendMessage(self._name_edit, win32con.EM_SETSEL, 0, -1)
        self._refresh_desktop_preview()

    def _setup_node_scroll(self, hwnd) -> None:
        import win32con
        import win32gui

        # 初始偏移由 ``_initial_node_view`` 决定（目标节点那条必须是可见的），
        # 这里只负责把它夹回合法范围并同步滚动条位置。
        self._node_scroll = _clamp_node_scroll(self._node_scroll, len(self._nodes))
        self._node_pos = {}
        self._node_vis = {}
        self._node_painted = None
        if len(self._nodes) > _NODE_ROWS:
            mask = win32con.SIF_RANGE | win32con.SIF_PAGE | win32con.SIF_POS
            win32gui.SetScrollInfo(
                hwnd, win32con.SB_VERT,
                (mask, 0, len(self._nodes) - 1, _NODE_ROWS, self._node_scroll), True,
            )
        self._layout_nodes(hwnd)

    def _initial_node_view(self) -> tuple[int, int]:
        """默认把「目标节点」摆进可视区，返回 ``(选中行, 滚动偏移)``。

        节点区只放得下 ``_NODE_ROWS`` 行，而微信这类应用的层级有十几层，且
        **前缀全是无名容器**（``Group`` / ``Custom`` / ``mmui::XView``）。默认
        停在最顶上时，用户看到的是一屏 ``(无名)``，目标那行被挤到窗口外——
        看起来就像"层级没抓全"。选中行也跟着走，属性面板才显示目标自己的属性。
        """
        if not self._nodes:
            return 0, 0
        target = len(self._nodes) - 1
        for index in range(len(self._nodes) - 1, -1, -1):
            if self._nodes[index].get("selected"):
                target = index
                break
        scroll = _clamp_node_scroll(target - _NODE_ROWS + 1, len(self._nodes))
        return target, scroll

    def _layout_nodes(self, hwnd) -> None:
        """节点区与层级区同口径：整段平移 + 补边缘（见 `_layout_levels`）。"""
        import win32con
        import win32gui

        delta = 0 if self._node_painted is None else self._node_scroll - self._node_painted
        smooth = 0 < abs(delta) < _NODE_ROWS
        moves: list[tuple[int, int, int, bool | None]] = []
        if smooth:
            win32gui.ScrollWindowEx(
                hwnd, 0, -delta * 32, _NODE_AREA, None, None,
                win32con.SW_SCROLLCHILDREN | win32con.SW_INVALIDATE,
            )
            for index in self._node_pos:
                self._node_pos[index] -= delta
        for index, box in enumerate(self._node_boxes):
            line = index - self._node_scroll
            visible = 0 <= line < _NODE_ROWS
            if smooth:
                if self._node_vis.get(index) is visible:
                    continue
            elif self._node_pos.get(index) == line and self._node_vis.get(index) is visible:
                continue
            self._node_pos[index] = line
            self._node_vis[index] = visible
            moves.append((box, 16, 70 + line * 32, visible))
        self._apply_layout(hwnd, moves)
        self._node_painted = self._node_scroll

    def _rebuild_desktop_attr_pane(self, hwnd) -> None:
        import win32con
        import win32gui

        for handle in self._desktop_attr_controls:
            win32gui.DestroyWindow(handle)
        self._desktop_attr_controls = []
        for cid in [
            cid for cid, meta in self._desktop_control_map.items() if meta[0] != "node"
        ]:
            self._desktop_control_map.pop(cid, None)
        node = self._nodes[self._selected_node]
        readonly_node = bool(node.get("readonly"))
        # ★ 层级勾选是唯一门禁：节点没勾上时它的属性一律不生效，属性区只读
        # （勾选值保留，重新勾上这层即可继续编辑）。不做「勾属性回勾层」的
        # 反向联动——那会让用户取消掉的层又出现在选择器里。
        node_on = bool(node.get("selected"))
        y = 70
        for row_index, row in enumerate(node.get("attributes") or []):
            base = _ID_DESKTOP_ATTR_BASE + row_index * 4
            readonly = readonly_node or bool(row.get("readonly"))
            editable = not readonly and node_on
            box = self._control(
                hwnd, "BUTTON", "", win32con.BS_AUTOCHECKBOX,
                420, y, 20, 26, base,
            )
            win32gui.SendMessage(
                box, win32con.BM_SETCHECK, 1 if row.get("required") else 0, 0
            )
            win32gui.EnableWindow(box, editable)
            label = f"{row.get('label') or row.get('field') or ''}"
            if readonly:
                label += "（只读）"
            elif not node_on:
                label += "（该层未参与定位）"
            # 标签列放宽，容纳「（该层未参与定位）」这类状态后缀，不再被截半截
            self._control(hwnd, "STATIC", label, 0, 448, y + 3, 150, 20, base + 1)
            combo = self._control(
                hwnd, "COMBOBOX", "",
                win32con.CBS_DROPDOWNLIST | win32con.WS_VSCROLL,
                606, y, 84, 120, base + 2,
            )
            for mode in _DESKTOP_MODES:
                win32gui.SendMessage(combo, win32con.CB_ADDSTRING, 0, mode)
            win32gui.SendMessage(
                combo, win32con.CB_SETCURSEL,
                _DESKTOP_MODES.index(row.get("operator"))
                if row.get("operator") in _DESKTOP_MODES else 0, 0,
            )
            # 运行期只有目标级 name/automationId 吃 matchMode；其余列不能装作可改。
            win32gui.EnableWindow(
                combo, editable and row.get("field") in desktop_editing.FUZZY_FIELDS
            )
            edit = self._control(
                hwnd, "EDIT", str(row.get("value") or ""),
                win32con.ES_AUTOHSCROLL | win32con.WS_BORDER
                | (0 if editable else win32con.ES_READONLY),
                698, y, 226, 26, base + 3,
            )
            self._desktop_control_map[base] = ("attr_check", row_index)
            self._desktop_control_map[base + 2] = ("attr_mode", row_index)
            self._desktop_control_map[base + 3] = ("attr_value", row_index)
            self._desktop_attr_controls += [box, combo, edit]
            y += 34

    def _refresh_desktop_preview(self) -> None:
        import json

        import win32gui

        if not self._preview_edit:
            return
        try:
            selector = desktop_editing.compose_selector(self._descriptor or {}, self._nodes)
            text = json.dumps(selector, ensure_ascii=False, indent=2)
        except (TypeError, ValueError) as exc:
            text = f"（当前勾选无法组合：{exc}）"
        win32gui.SetWindowText(self._preview_edit, text)
        self._invalidate_check()

    def _on_desktop_control(self, hwnd, cid: int, notif: int) -> None:
        import win32con
        import win32gui

        meta = self._desktop_control_map.get(cid)
        if meta is None:
            return
        role, index = meta
        if role == "node" and notif == 0:  # BN_CLICKED
            box = win32gui.GetDlgItem(hwnd, cid)
            checked = bool(win32gui.SendMessage(box, win32con.BM_GETCHECK, 0, 0))
            node = self._nodes[index]
            if node.get("readonly"):
                win32gui.SendMessage(box, win32con.BM_SETCHECK, 0, 0)
                checked = False
            elif index == len(self._nodes) - 1 and not checked:
                # 目标级（链尾）必须参与定位：勾掉就回到勾选，绝不让它空着保存。
                win32gui.SendMessage(box, win32con.BM_SETCHECK, 1, 0)
                checked = True
            node["selected"] = checked
            self._selected_node = index
            self._rebuild_desktop_attr_pane(hwnd)
            self._refresh_desktop_preview()
            return
        row = (self._nodes[self._selected_node].get("attributes") or [])[index]
        if role == "attr_check" and notif == 0:
            box = win32gui.GetDlgItem(hwnd, cid)
            row["required"] = bool(win32gui.SendMessage(box, win32con.BM_GETCHECK, 0, 0))
            # ★ 只改「这一层用哪些字段」，不改「这一层参不参与」（M109）：
            # 勾属性绝不把已取消的层拉回来，否则选择器里又会冒出这一层。
            # 层未勾上时属性框本来就是禁用的，这里只是双保险。
            self._refresh_desktop_preview()
        elif role == "attr_value" and notif == win32con.EN_CHANGE:
            edit = win32gui.GetDlgItem(hwnd, cid)
            row["value"] = win32gui.GetWindowText(edit)
            self._refresh_desktop_preview()
        elif role == "attr_mode" and notif == win32con.CBN_SELCHANGE:
            combo = win32gui.GetDlgItem(hwnd, cid)
            row["operator"] = _DESKTOP_MODES[
                win32gui.SendMessage(combo, win32con.CB_GETCURSEL, 0, 0)
            ]
            self._refresh_desktop_preview()

    # -- 消息 -----------------------------------------------------------------
    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        import win32con
        import win32gui

        if msg == win32con.WM_COMMAND:
            cid = wparam & 0xFFFF
            notif = (wparam >> 16) & 0xFFFF
            if cid == win32con.IDOK and notif == 0:
                self._try_save()
                return 0
            if cid == win32con.IDCANCEL and notif == 0:
                self._close(saved=False)
                return 0
            if cid == _ID_CHECK and notif == 0:
                self._start_check(hwnd)
                return 0
            if self._nodes:
                self._on_desktop_control(hwnd, cid, notif)
            else:
                self._on_control(hwnd, cid, notif)
            return 0
        if msg == win32con.WM_VSCROLL:
            self._on_vscroll(hwnd, wparam)
            return 0
        if msg == win32con.WM_MOUSEWHEEL:
            self._on_mouse_wheel(hwnd, wparam)
            return 0
        if msg == _WM_APP_FOCUS:
            try:
                win32gui.SetFocus(self._name_edit)
            except Exception:  # noqa: BLE001 - 焦点没设上不影响功能
                pass
            return 0
        if msg == _WM_APP_CHECK:
            self._finish_check(hwnd)
            return 0
        if msg == _WM_APP_HOTKEY:
            # 已经在校验就不再起（连按 F10 不该把这一轮顶掉，取消仍走按钮）
            if not self._checking:
                self._start_check(hwnd)
            return 0
        if msg == win32con.WM_CTLCOLORSTATIC:
            import win32api

            target = int(lparam)
            # ★ 所有 STATIC 一律透明底：原生默认主题会在标签后面画灰块（截图里
            # 「资产名称」那种），是「土」的直接来源。白刷只作背景兜底。
            win32gui.SetBkMode(wparam, win32con.TRANSPARENT)
            win32gui.SetTextColor(wparam, win32api.RGB(*_COLOR_TEXT))
            brush = win32gui.GetStockObject(win32con.WHITE_BRUSH)
            # 结果条整行染色（含 ● 前缀）：原来灰字看不出校验成功
            if target == int(self._result_static or 0) and self._result_color:
                win32gui.SetTextColor(wparam, win32api.RGB(*self._result_color))
                return brush
            if target == int(self._error_static or 0):
                # 保存错误仍整行红（简式与结果条共用控件，_show_error 会清 result_color）
                win32gui.SetTextColor(wparam, win32api.RGB(*_COLOR_ERROR))
                return brush
            if target == int(self._hotkey_hint or 0):
                win32gui.SetTextColor(wparam, win32api.RGB(*_COLOR_FAINT))
                return brush
            return brush
        if msg == win32con.WM_CLOSE:
            self._close(saved=False)
            return 0
        if msg == win32con.WM_DESTROY:
            self._stop_hotkey_watch()
            _win32._INSTANCES.pop(int(hwnd), None)
            if self._font_owned and self._hfont is not None:
                try:
                    win32gui.DeleteObject(self._hfont)
                except Exception:  # noqa: BLE001 - 句柄已释放也无妨
                    pass
            self._hfont = None
            self._font_owned = False
            win32gui.PostQuitMessage(0)
            return 0
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    def _on_control(self, hwnd, cid: int, notif: int) -> None:
        """层级/属性控件的状态回写 + 预览刷新。"""
        import win32con
        import win32gui

        meta = self._control_map.get(cid)
        if meta is None:
            return
        role = meta[0]
        if role == "level" and notif == 0:  # BN_CLICKED：只选中看属性，不改勾选
            self._selected = meta[1]
            self._rebuild_attr_pane(hwnd)
            return
        if role == "level_check" and notif == 0:  # 点左端小方框才切 included
            index = meta[1]
            box = win32gui.GetDlgItem(hwnd, cid)
            wanted = bool(win32gui.SendMessage(box, win32con.BM_GETCHECK, 0, 0))
            included = selector_tree.level_included_after_toggle(
                len(self._levels), index, wanted
            )
            if included != wanted:  # 目标级点掉也弹回，但控件仍可点、可查看属性
                win32gui.SendMessage(box, win32con.BM_SETCHECK, 1, 0)
            self._levels[index]["included"] = included
            if index == self._selected:
                # 属性区能否编辑跟着这层走：取消勾选后整区只读，重新勾上再放开
                self._rebuild_attr_pane(hwnd)
            self._refresh_preview()
            return
        if role == "check" and notif == 0:
            box = win32gui.GetDlgItem(hwnd, cid)
            meta[2]["on"] = bool(win32gui.SendMessage(box, win32con.BM_GETCHECK, 0, 0))
            # ★ 同桌面：属性勾选只管字段，不管层级参与（M109）。层被取消后
            # 这些属性即便还勾着也不会进选择器，`compose` 只收 included 的层。
            self._refresh_preview()
            return
        if role == "value" and notif == win32con.EN_CHANGE:
            edit = win32gui.GetDlgItem(hwnd, cid)
            meta[2]["value"] = win32gui.GetWindowText(edit)
            self._refresh_preview()
            return
        if role == "mode" and notif == win32con.CBN_SELCHANGE:
            combo = win32gui.GetDlgItem(hwnd, cid)
            meta[2]["mode"] = _MATCH_MODES[
                win32gui.SendMessage(combo, win32con.CB_GETCURSEL, 0, 0)
            ]
            self._refresh_preview()

    # -- 校验（影刀 §5 主动校验） ------------------------------------------------
    def _current_selector(self) -> Any:
        """当前编辑态的定位：桌面给 selector 字典，网页/简式给文本。"""
        if self._nodes:
            return desktop_editing.compose_selector(
                self._descriptor or {}, self._nodes
            )
        if self._levels:
            return selector_tree.compose(self._levels)
        if self._selector_edit:
            import win32gui

            return win32gui.GetWindowText(self._selector_edit)
        return ""

    def _set_result(self, text: str, color: tuple[int, int, int]) -> None:
        """写结果条（复用 info 行）；● 前缀和颜色都由 STATIC 自己画。"""
        import win32gui

        self._result_color = color
        if self._result_static:
            win32gui.SetWindowText(self._result_static, "●  " + text)
            win32gui.InvalidateRect(self._result_static, None, True)

    def _restore_ui(self, hwnd) -> None:
        """收起 toast、按钮复位、把编辑框放回来（取消与完成共用）。"""
        import win32con
        import win32gui

        if self._toast is not None:
            self._toast.close()
            self._toast = None
        if self._check_btn:
            try:
                win32gui.SetWindowText(self._check_btn, "校验元素")
            except Exception:  # noqa: BLE001 - 控件没了就别管
                pass
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
            win32gui.SetForegroundWindow(hwnd)
        except Exception:  # noqa: BLE001 - 抢不到前台不影响用
            pass

    def _cancel_check(self, hwnd) -> None:
        """中止等待：立刻回框，在跑的那轮结果作废（红框还会自己闪完，无害）。"""
        if not self._checking:
            return
        self._check_seq += 1
        self._checking = False
        self._restore_ui(hwnd)
        self._set_result("校验已取消", _COLOR_PLAIN)

    def _start_check(self, hwnd) -> None:
        """点「校验元素」：结构先本地判，再藏框 → toast → 后台定位。"""
        import win32con
        import win32gui

        if self._checking:
            self._cancel_check(hwnd)
            return
        try:
            selector = self._current_selector()
        except (TypeError, ValueError) as exc:
            self._show_error(f"当前勾选无法组合：{exc}")
            return
        # 结构不对就别藏框了：本地判完立刻回显，用户还能直接改
        structure_error = check_structure(self._kind, selector)
        if structure_error:
            self._show_error(structure_error)
            self._set_result(structure_error, _COLOR_ERROR)
            return

        self._check_seq += 1
        seq = self._check_seq
        self._checking = True
        self._pending_result = None
        self._show_error("")
        if self._check_btn:
            win32gui.SetWindowText(self._check_btn, "取消")
        self._set_result("正在校验元素…", _COLOR_PLAIN)
        # toast 必须在**有消息泵的线程**创建：worker 线程没有泵，窗口会白着不画
        self._toast = CheckToast()
        self._toast.show("正在校验元素")
        # 藏起来才能看见目标窗口被置前 + 红框（影刀同一交互）
        win32gui.ShowWindow(hwnd, win32con.SW_HIDE)
        threading.Thread(
            target=self._check_worker,
            args=(selector, seq),
            name="element-check",
            daemon=True,
        ).start()

    def _check_worker(self, selector: Any, seq: int) -> None:
        """后台定位：活体查询是阻塞的（桌面 UIA 几十~几百毫秒），绝不进消息循环。"""
        try:
            result = check_draft(
                self._descriptor or {}, selector, kind=self._kind
            )
        except Exception as exc:  # noqa: BLE001 - 线程里抛出去没人接，只能自己兜
            result = EditCheckResult(
                success=False,
                error_code="verify-exception",
                message=f"校验出错：{type(exc).__name__}",
            )
        self._pending_result = (seq, result)
        hwnd = self._hwnd
        if not hwnd:
            return
        try:
            import win32gui

            win32gui.PostMessage(hwnd, _WM_APP_CHECK, 0, 0)
        except Exception:  # noqa: BLE001 - 框已关就没人收，结果自然丢弃
            pass

    def _finish_check(self, hwnd) -> None:
        """主线程收尾：旧轮次（被取消/被顶掉）的结果直接丢。"""
        payload = self._pending_result
        self._pending_result = None
        if payload is None:
            return
        seq, result = payload
        if seq != self._check_seq:
            return
        self._checking = False
        self._restore_ui(hwnd)
        self._render_result(hwnd, result)

    def _ensure_shot_view(self, hwnd, shot: Any) -> None:
        """校验拿到截图才展开预览区：默认紧凑，不为「可能有的图」先留 280px。"""
        import win32con
        import win32gui

        if self._shot_view is None:
            self._shot_h = _fit_shot_height(self._base_h)
            if self._shot_h <= 0:
                return
            view = ShotView()
            if not view.create(hwnd, self._shot_x, self._shot_y, self._shot_w, self._shot_h):
                return
            self._shot_view = view
            # 外框加高「截图高度 + 间距」，顶部保持原位（不重新居中，免得框跳走）
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            win32gui.SetWindowPos(
                hwnd, 0, 0, 0,
                right - left, (bottom - top) + self._shot_h + _SHOT_GAP,
                win32con.SWP_NOMOVE | win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE,
            )
        self._shot_view.set_shot(shot)

    def _render_result(self, hwnd, result: EditCheckResult) -> None:
        if result.error_code and result.error_code != "tree-missing":
            color = _COLOR_ERROR
        elif result.success:
            color = _COLOR_OK
        else:
            color = _COLOR_WARN
        self._set_result(result.message, color)
        if result.shot:
            self._ensure_shot_view(hwnd, result.shot)

    # -- 保存 / 关闭 ------------------------------------------------------------
    def _try_save(self) -> None:
        import win32gui

        name = win32gui.GetWindowText(self._name_edit).strip()
        if not name:
            self._show_error("名字不能为空")
            return
        if self._nodes:
            try:
                composed = desktop_editing.compose_selector(self._descriptor or {}, self._nodes)
                selector = json.dumps(composed, ensure_ascii=False, indent=2)
            except (TypeError, ValueError) as exc:
                self._show_error(str(exc))
                return
        elif self._levels:
            selector = selector_tree.compose(self._levels)
            if not selector:
                self._show_error("组合选择器为空：至少保留目标层的一个特征")
                return
        else:
            selector = win32gui.GetWindowText(self._selector_edit)
        # 勾选快照必须和定位文本同一次交出去：只存 css / locator 的话，用户
        # 取消过的层级/class 重开详情就没了（2026-11-19 现场）。
        edit_state: dict[str, Any] | None = None
        if self._nodes:
            edit_state = {"nodes": self._nodes}
        elif self._levels:
            edit_state = {"levels": self._levels}
        if self._on_save is not None:
            error = self._on_save(name, selector, edit_state)
        else:
            error = None
        if error:
            self._show_error(str(error))
            return
        self._close(saved=True)

    def _show_error(self, text: str) -> None:
        import win32gui

        self.last_error = text
        if text:
            # 保存错误优先：别让上一轮校验的绿字把错误染成绿色（简式两行合一）
            self._result_color = None
        win32gui.SetWindowText(self._error_static, text)

    def _close(self, saved: bool) -> None:
        if self._closed:
            return
        self._closed = True
        import win32gui

        on_close = self._on_close
        self._on_close = None
        # 校验还在跑就作废：worker 回来时句柄已失效，不能再去 PostMessage
        self._check_seq += 1
        self._checking = False
        if self._toast is not None:
            self._toast.close()
            self._toast = None
        if self._shot_view is not None:
            self._shot_view.destroy()
            self._shot_view = None
        hwnd = self._hwnd
        if hwnd is not None:
            win32gui.DestroyWindow(hwnd)
        if on_close is not None:
            on_close(saved)

    # -- 摆放 ------------------------------------------------------------------
    def _place(self, hwnd, client_w: int, client_h: int) -> None:
        """居中到**鼠标所在**的显示器（多屏机器上框出现在用户正看着的屏）。"""
        import win32api
        import win32con
        import win32gui

        try:
            cursor = win32api.GetCursorPos()
            monitor = win32api.MonitorFromPoint(cursor, win32con.MONITOR_DEFAULTTONEAREST)
            left, top, right, bottom = win32api.GetMonitorInfo(monitor)["Work"]
        except Exception:  # noqa: BLE001 - 拿不到就退主屏
            left = top = 0
            right = win32api.GetSystemMetrics(win32con.SM_CXSCREEN)
            bottom = win32api.GetSystemMetrics(win32con.SM_CYSCREEN)
        outer_w = client_w + 40  # 反推后的外框略大于客户区，居中按外框算
        outer_h = client_h + 60
        x = max(left, left + (right - left - outer_w) // 2)
        y = max(top, top + (bottom - top - outer_h) // 2)
        win32gui.SetWindowPos(
            hwnd, win32con.HWND_TOPMOST, x, y, 0, 0,
            win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
        )
