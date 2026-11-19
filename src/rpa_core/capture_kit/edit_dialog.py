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

import json
import sys
import threading
from collections.abc import Callable
from typing import Any

from . import _win32, desktop_editing, selector_tree

_CLASS = "RpaElementEditDialog"
# 私有消息：窗口显示后再设焦点（同步 SetFocus 在本机实测会卡 ~1.5s）
_WM_APP_FOCUS = 0x8000 + 2

# 简式形态客户区
_SIMPLE_W = 560
_SIMPLE_H = 452
# 层级形态客户区（左层级 + 右属性，信息密度高，要更宽）
_TREE_W = 780
_TREE_H = 556

# 桌面层级形态客户区（左节点 + 右属性 + 多行组合预览）
_DESKTOP_W = 940
_DESKTOP_H = 620
# 固定控件 id：保存/取消沿对话框惯例（IsDialogMessage 会把 Enter/Esc 映到这两个 id）
_ID_NAME = 201
_ID_META = 202
_ID_ERROR = 203
_ID_SELECTOR = 204
_ID_PREVIEW = 205
_ID_INFO = 206
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

_MATCH_MODES = ("精确", "包含")
_DESKTOP_MODES = ("精确", "包含", "正则")


def _clamp_node_scroll(scroll: int, total: int) -> int:
    """把节点区偏移夹进 ``[0, total - _NODE_ROWS]``（不足一屏就是 0）。"""
    return max(0, min(int(scroll or 0), max(0, int(total) - _NODE_ROWS)))


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
        self._attr_controls: list[int] = []
        self._control_map: dict[int, tuple] = {}
        # 桌面层级形态状态（nodes / descriptor 由内核编辑模型提供）
        self._nodes: list[dict[str, Any]] = []
        self._descriptor: dict[str, Any] | None = None
        self._selected_node = 0
        self._node_scroll = 0
        self._node_boxes: list[int] = []
        self._desktop_attr_controls: list[int] = []
        self._desktop_control_map: dict[int, tuple] = {}

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
        self._selected = 0
        self._selected_node = 0
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
        client_h = _DESKTOP_H if desktop_mode else (_TREE_H if tree_mode else _SIMPLE_H)
        _win32.register_class(_CLASS)
        ex_style = win32con.WS_EX_TOPMOST | win32con.WS_EX_DLGMODALFRAME
        style = win32con.WS_POPUP | win32con.WS_CAPTION | win32con.WS_SYSMENU
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
        _win32.pump_dialog(hwnd)
        self._hwnd = None

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

        handle = win32gui.CreateWindowEx(
            0, cls, text,
            style | win32con.WS_CHILD | win32con.WS_VISIBLE | win32con.WS_TABSTOP,
            x, y, w, h, hwnd, cid, win32gui.GetModuleHandle(None), None,
        )
        if handle and self._hfont:
            win32gui.SendMessage(handle, win32con.WM_SETFONT, self._hfont, True)
        return handle

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
        self._error_static = control("STATIC", "", 0, 16, 378, 528, 20, _ID_ERROR)
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
        self._setup_level_scroll(hwnd)
        self._rebuild_attr_pane(hwnd)
        control("STATIC", "组合选择器", 0, 16, 402, 200, 20, 209)
        self._preview_edit = control(
            "EDIT", "",
            win32con.ES_AUTOHSCROLL | win32con.ES_READONLY | win32con.WS_BORDER,
            16, 424, 748, 28, _ID_PREVIEW,
        )
        control("STATIC", self._info, 0, 16, 460, 748, 18, _ID_INFO)
        self._error_static = control("STATIC", "", 0, 16, 484, 748, 18, _ID_ERROR)
        control(
            "BUTTON", self._save_label, win32con.BS_DEFPUSHBUTTON,
            580, 512, 88, 30, win32con.IDOK,
        )
        control("BUTTON", "取消", 0, 676, 512, 88, 30, win32con.IDCANCEL)
        win32gui.SendMessage(self._name_edit, win32con.EM_SETSEL, 0, -1)
        self._refresh_preview()

    def _setup_level_scroll(self, hwnd) -> None:
        """层级超一屏时初始化滚动条并重排 checkbox（不超也要按 0 偏移排一次）。"""
        import win32con
        import win32gui

        self._level_scroll = 0
        if len(self._levels) > _LEVEL_ROWS:
            # pywin32 的 SCROLLINFO 是 (mask, min, max, page, pos[, trackPos]) 元组
            mask = win32con.SIF_RANGE | win32con.SIF_PAGE | win32con.SIF_POS
            win32gui.SetScrollInfo(
                hwnd, win32con.SB_VERT,
                (mask, 0, len(self._levels) - 1, _LEVEL_ROWS, 0), True,
            )
        self._layout_levels(hwnd)

    def _layout_levels(self, hwnd) -> None:
        """按滚动偏移摆放层级 checkbox：屏内显示、屏外隐藏。"""
        import win32con
        import win32gui

        for index, (row, box) in enumerate(
            zip(self._level_boxes, self._level_check_boxes, strict=True)
        ):
            line = index - self._level_scroll
            visible = 0 <= line < _LEVEL_ROWS
            y = 70 + line * 32
            # 行文字与小方框分开摆放：box 在 x=16，row 从 x=44 起，互不覆盖
            win32gui.SetWindowPos(
                box, 0, 16, y, 0, 0,
                win32con.SWP_NOSIZE | win32con.SWP_NOZORDER,
            )
            win32gui.SetWindowPos(
                row, 0, 44, y, 0, 0,
                win32con.SWP_NOSIZE | win32con.SWP_NOZORDER,
            )
            for handle in (row, box):
                win32gui.ShowWindow(
                    handle, win32con.SW_SHOW if visible else win32con.SW_HIDE
                )

    def _on_vscroll(self, hwnd, wparam) -> None:
        """滚动层级区：行/页/拖块三档都收（桌面节点与网页层级各走各的偏移）。"""
        import win32con
        import win32gui

        if self._nodes:
            code = wparam & 0xFFFF
            pos = (wparam >> 16) & 0xFFFF
            limit = max(0, len(self._nodes) - _NODE_ROWS)
            if code == win32con.SB_LINEUP:
                self._node_scroll = max(0, self._node_scroll - 1)
            elif code == win32con.SB_LINEDOWN:
                self._node_scroll = min(limit, self._node_scroll + 1)
            elif code == win32con.SB_PAGEUP:
                self._node_scroll = max(0, self._node_scroll - _NODE_ROWS)
            elif code == win32con.SB_PAGEDOWN:
                self._node_scroll = min(limit, self._node_scroll + _NODE_ROWS)
            elif code in (win32con.SB_THUMBTRACK, win32con.SB_THUMBPOSITION):
                self._node_scroll = min(limit, max(0, pos))
            win32gui.SetScrollInfo(
                hwnd, win32con.SB_VERT,
                (win32con.SIF_POS, 0, 0, 0, self._node_scroll), True,
            )
            self._layout_nodes(hwnd)
            return
        code = wparam & 0xFFFF
        pos = (wparam >> 16) & 0xFFFF
        limit = max(0, len(self._levels) - _LEVEL_ROWS)
        if code == win32con.SB_LINEUP:
            self._level_scroll = max(0, self._level_scroll - 1)
        elif code == win32con.SB_LINEDOWN:
            self._level_scroll = min(limit, self._level_scroll + 1)
        elif code == win32con.SB_PAGEUP:
            self._level_scroll = max(0, self._level_scroll - _LEVEL_ROWS)
        elif code == win32con.SB_PAGEDOWN:
            self._level_scroll = min(limit, self._level_scroll + _LEVEL_ROWS)
        elif code in (win32con.SB_THUMBTRACK, win32con.SB_THUMBPOSITION):
            self._level_scroll = min(limit, max(0, pos))
        # SCROLLINFO 元组按位填充：min/max 必须成对给（给了 mask=SIF_POS 也得占位）。
        win32gui.SetScrollInfo(
            hwnd, win32con.SB_VERT,
            (win32con.SIF_POS, 0, 0, 0, self._level_scroll), True,
        )
        self._layout_levels(hwnd)

    def _rebuild_attr_pane(self, hwnd) -> None:
        """右栏属性行随选中层重建（行数随层级属性变，池化比重排省事）。"""
        import win32con
        import win32gui

        for handle in self._attr_controls:
            win32gui.DestroyWindow(handle)
        self._attr_controls = []
        for cid in [cid for cid, meta in self._control_map.items() if meta[0] != "level"]:
            self._control_map.pop(cid, None)
        level = self._levels[self._selected]
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
            value_x = 480 if has_mode else 396
            value_w = 764 - value_x
            edit = self._control(
                hwnd, "EDIT", state["value"],
                win32con.ES_AUTOHSCROLL | win32con.WS_BORDER,
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
        self._setup_node_scroll(hwnd)
        self._rebuild_desktop_attr_pane(hwnd)
        control("STATIC", "组合选择器（完整 JSON，保留捕获候选）", 0, 16, 402, 500, 20, 209)
        self._preview_edit = control(
            "EDIT", "",
            win32con.ES_MULTILINE | win32con.ES_READONLY | win32con.ES_AUTOVSCROLL
            | win32con.WS_VSCROLL | win32con.WS_BORDER,
            16, 424, 908, 96, _ID_PREVIEW,
        )
        control("STATIC", self._info, 0, 16, 526, 908, 18, _ID_INFO)
        self._error_static = control("STATIC", "", 0, 16, 548, 908, 18, _ID_ERROR)
        control(
            "BUTTON", self._save_label, win32con.BS_DEFPUSHBUTTON,
            748, 574, 84, 30, win32con.IDOK,
        )
        control("BUTTON", "取消", 0, 840, 574, 84, 30, win32con.IDCANCEL)
        win32gui.SendMessage(self._name_edit, win32con.EM_SETSEL, 0, -1)
        self._refresh_desktop_preview()

    def _setup_node_scroll(self, hwnd) -> None:
        import win32con
        import win32gui

        # 初始偏移由 ``_initial_node_view`` 决定（目标节点那条必须是可见的），
        # 这里只负责把它夹回合法范围并同步滚动条位置。
        self._node_scroll = _clamp_node_scroll(self._node_scroll, len(self._nodes))
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
        import win32con
        import win32gui

        for index, box in enumerate(self._node_boxes):
            row = index - self._node_scroll
            visible = 0 <= row < _NODE_ROWS
            win32gui.SetWindowPos(
                box, 0, 16, 70 + row * 32, 0, 0,
                win32con.SWP_NOSIZE | win32con.SWP_NOZORDER,
            )
            win32gui.ShowWindow(box, win32con.SW_SHOW if visible else win32con.SW_HIDE)

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
        y = 70
        for row_index, row in enumerate(node.get("attributes") or []):
            base = _ID_DESKTOP_ATTR_BASE + row_index * 4
            readonly = readonly_node or bool(row.get("readonly"))
            box = self._control(
                hwnd, "BUTTON", "", win32con.BS_AUTOCHECKBOX,
                420, y, 20, 26, base,
            )
            win32gui.SendMessage(
                box, win32con.BM_SETCHECK, 1 if row.get("required") else 0, 0
            )
            win32gui.EnableWindow(box, not readonly)
            label = f"{row.get('label') or row.get('field') or ''}"
            if readonly:
                label += "（只读）"
            self._control(hwnd, "STATIC", label, 0, 448, y + 3, 96, 20, base + 1)
            combo = self._control(
                hwnd, "COMBOBOX", "",
                win32con.CBS_DROPDOWNLIST | win32con.WS_VSCROLL,
                552, y, 84, 120, base + 2,
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
                combo, not readonly and row.get("field") in desktop_editing.FUZZY_FIELDS
            )
            edit = self._control(
                hwnd, "EDIT", str(row.get("value") or ""),
                win32con.ES_AUTOHSCROLL | win32con.WS_BORDER
                | (win32con.ES_READONLY if readonly else 0),
                644, y, 280, 26, base + 3,
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
            # 与网页层级同一口径：勾上属性 ⇒ 所在节点参与定位。readonly 节点
            # （窗口信息行）的属性本身禁勾，不会被改到；目标级本来就恒参与。
            node = self._nodes[self._selected_node]
            selected = selector_tree.included_after_attr_check(
                node.get("selected", False), row["required"]
            )
            if selected != bool(node.get("selected")):
                node["selected"] = selected
                win32gui.SendMessage(
                    self._node_boxes[self._selected_node],
                    win32con.BM_SETCHECK, 1 if selected else 0, 0,
                )
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
            if self._nodes:
                self._on_desktop_control(hwnd, cid, notif)
            else:
                self._on_control(hwnd, cid, notif)
            return 0
        if msg == win32con.WM_VSCROLL:
            self._on_vscroll(hwnd, wparam)
            return 0
        if msg == _WM_APP_FOCUS:
            try:
                win32gui.SetFocus(self._name_edit)
            except Exception:  # noqa: BLE001 - 焦点没设上不影响功能
                pass
            return 0
        if msg == win32con.WM_CTLCOLORSTATIC and int(lparam) == int(self._error_static or 0):
            # 错误行画红：返回画刷前先把文字颜色设上
            import win32api

            win32gui.SetTextColor(wparam, win32api.RGB(192, 0, 0))
            win32gui.SetBkMode(wparam, win32con.TRANSPARENT)
            return win32gui.GetStockObject(win32con.WHITE_BRUSH)
        if msg == win32con.WM_CLOSE:
            self._close(saved=False)
            return 0
        if msg == win32con.WM_DESTROY:
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
            self._refresh_preview()
            return
        if role == "check" and notif == 0:
            box = win32gui.GetDlgItem(hwnd, cid)
            meta[2]["on"] = bool(win32gui.SendMessage(box, win32con.BM_GETCHECK, 0, 0))
            # 勾上本层属性 ⇒ 本层自动参与定位（否则属性勾了选择器不变）。
            # 取消属性不反取消层：层可能被别的属性或用户手动勾着。
            level = self._levels[self._selected]
            included = selector_tree.included_after_attr_check(
                level["included"], meta[2]["on"]
            )
            if included != level["included"]:
                level["included"] = included
                win32gui.SendMessage(
                    self._level_check_boxes[self._selected],
                    win32con.BM_SETCHECK, 1 if included else 0, 0,
                )
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
        win32gui.SetWindowText(self._error_static, text)

    def _close(self, saved: bool) -> None:
        if self._closed:
            return
        self._closed = True
        import win32gui

        on_close = self._on_close
        self._on_close = None
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
