"""元素编辑框：win32 原生对话框（M68 后半场 + M69 影刀式层级编辑）。

两种形态（``show(levels=...)`` 决定）：

- **层级形态**（browser 且有 ``selector.path``，影刀式）：左栏层级勾选
  （勾掉 = 该层不参与定位）、右栏选中层的属性行（勾选 / 精确·包含 / 值可改），
  底部实时预览重组后的 css。重组规则的唯一权威是
  ``capture_kit.selector_tree.compose``——控件只改状态，不自己拼字符串。
- **简式形态**（desktop 或旧资产无 path）：名称 + 可编辑定位 + 只读信息区。

实现要点（全是从浮窗/前一版搬来的教训）：

- 窗口类注册 / 实例分派 / 字体 / 定时器 / 外框反推全部走 ``capture_kit._win32``；
- **键盘行为不手写**：消息泵喂 ``IsDialogMessage``，Enter 触发默认按钮（保存）、
  Esc 转 IDCANCEL、Tab 在控件间轮换，EDIT 的中文 IME 组合态回车不会被当成命令；
- 外框尺寸由 ``_win32.adjust_window_size`` 从客户区反推（标题栏会吃掉底排按钮）；
- ``SetFocus`` 不在建窗期同步做（本机实测卡 ~1.5s），窗口可见后 PostMessage 再设；
- 非 Windows 直接不可用（``available is False``，show 为 no-op），宿主回退页面形态。
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from typing import Any

from . import _win32, selector_tree

_CLASS = "RpaElementEditDialog"
# 私有消息：窗口显示后再设焦点（同步 SetFocus 在本机实测会卡 ~1.5s）
_WM_APP_FOCUS = 0x8000 + 2

# 简式形态客户区
_SIMPLE_W = 560
_SIMPLE_H = 452
# 层级形态客户区（左层级 + 右属性，信息密度高，要更宽）
_TREE_W = 780
_TREE_H = 556

# 固定控件 id：保存/取消沿对话框惯例（IsDialogMessage 会把 Enter/Esc 映到这两个 id）
_ID_NAME = 201
_ID_META = 202
_ID_ERROR = 203
_ID_SELECTOR = 204
_ID_PREVIEW = 205
_ID_INFO = 206
# 动态控件 id 起点（层级勾选 / 属性行）
_ID_LEVEL_BASE = 1000
_ID_ATTR_BASE = 2000

_MATCH_MODES = ("精确", "包含")


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
        self._on_save: Callable[[str, str], str | None] | None = None
        self._on_close: Callable[[bool], None] | None = None
        self._closed = False
        # 探针可观察的最近一次保存错误（界面文案以外的事实源）
        self.last_error: str | None = None
        # 层级形态状态
        self._levels: list[dict[str, Any]] = []
        self._selected = 0
        self._attr_controls: list[int] = []
        self._control_map: dict[int, tuple] = {}

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
        save_label: str = "保存",
        on_save: Callable[[str, str], str | None] | None = None,
        on_close: Callable[[bool], None] | None = None,
    ) -> None:
        """弹出编辑框。``on_save(name, selector)`` 返回 None = 成功关框；返回字符串 =
        错误回显留框。``levels`` 给出时走影刀式层级形态（selector 参数忽略，以
        重组预览为准）；否则走简式形态（selector 是可编辑文本）。
        """
        if not self.available:
            return
        self._title = title
        self._name = name
        self._selector = selector
        self._meta = meta
        self._info = info
        self._levels = levels or []
        self._selected = 0
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

        tree_mode = bool(self._levels)
        client_w = _TREE_W if tree_mode else _SIMPLE_W
        client_h = _TREE_H if tree_mode else _SIMPLE_H
        _win32.register_class(_CLASS)
        ex_style = win32con.WS_EX_TOPMOST | win32con.WS_EX_DLGMODALFRAME
        style = win32con.WS_POPUP | win32con.WS_CAPTION | win32con.WS_SYSMENU
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
        if tree_mode:
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
        # 层级勾选：点哪层，右栏就编辑哪层（勾选与选中同一击，与影刀的 tree 一致）
        for index, level in enumerate(self._levels):
            style = win32con.BS_AUTOCHECKBOX
            box = control(
                "BUTTON", selector_tree.summary(level), style,
                16, 70 + index * 32, 280, 26, _ID_LEVEL_BASE + index,
            )
            win32gui.SendMessage(
                box, win32con.BM_SETCHECK, 1 if level["included"] else 0, 0
            )
            self._control_map[_ID_LEVEL_BASE + index] = ("level", index)
            # 目标级（链尾）不许勾掉：锁勾选
            if index == len(self._levels) - 1:
                win32gui.EnableWindow(box, False)
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
            self._on_control(hwnd, cid, notif)
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
        if role == "level" and notif == 0:  # BN_CLICKED
            index = meta[1]
            box = win32gui.GetDlgItem(hwnd, cid)
            self._levels[index]["included"] = bool(
                win32gui.SendMessage(box, win32con.BM_GETCHECK, 0, 0)
            )
            self._selected = index
            self._rebuild_attr_pane(hwnd)
            self._refresh_preview()
            return
        if role == "check" and notif == 0:
            box = win32gui.GetDlgItem(hwnd, cid)
            meta[2]["on"] = bool(win32gui.SendMessage(box, win32con.BM_GETCHECK, 0, 0))
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
        if self._levels:
            selector = selector_tree.compose(self._levels)
            if not selector:
                self._show_error("组合选择器为空：至少保留目标层的一个特征")
                return
        else:
            selector = win32gui.GetWindowText(self._selector_edit)
        if self._on_save is not None:
            error = self._on_save(name, selector)
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
