"""元素编辑框的 F10 全局校验热键。

现场形状：鼠标必须悬停在页面上才能把下拉菜单悬出来，这时焦点在浏览器、编辑框
被盖住，「校验元素」按钮根本点不到。所以按键由一个 watch 线程读
``GetAsyncKeyState``，再 PostMessage 回窗口线程起校验。

这里钉住三件最容易写错的事：
① 按住不放不能连开校验（只认上升沿）；
② 已经在校验时按 F10 不能再起一轮（按钮上的「取消」语义不能被热键借用）；
③ 关框必须把 watch 线程停掉，否则框没了以后还在往销毁的句柄打消息。
"""

from __future__ import annotations

import sys
import types

import pytest

pytest.importorskip("win32con")
import win32con  # noqa: E402

from rpa_core.capture_kit import edit_dialog as edition  # noqa: E402


def _dialog() -> edition.ElementEditDialog:
    dlg = edition.ElementEditDialog()
    dlg._name_edit = 1
    return dlg


def _fake_gui(**extra):
    """只提供被测代码用到的那几个 win32gui 成员。"""
    members = {
        "PostMessage": lambda hwnd, msg, wparam, lparam: None,
        "PostQuitMessage": lambda code: None,
        "DeleteObject": lambda obj: None,
        "SendMessage": lambda *a: 0,
        "SetWindowText": lambda *a: None,
        "ShowWindow": lambda *a: None,
        "SetForegroundWindow": lambda *a: None,
        "InvalidateRect": lambda *a: None,
        "GetDlgItem": lambda *a: 0,
        "GetWindowText": lambda *a: "",
    }
    members.update(extra)
    return types.SimpleNamespace(**members)


class _Stop:
    """假 Event：``wait`` 返回 n 次 False 让循环跑 n 圈，然后 True 收工。"""

    def __init__(self, rounds: int) -> None:
        self.rounds = rounds
        self.calls = 0

    def wait(self, _timeout: float) -> bool:
        self.calls += 1
        return self.calls > self.rounds


def test_hotkey_message_starts_check_only_when_idle(monkeypatch):
    dlg = _dialog()
    started: list[int] = []
    monkeypatch.setattr(dlg, "_start_check", lambda hwnd: started.append(hwnd))

    dlg._checking = False
    assert dlg._wnd_proc(7, edition._WM_APP_HOTKEY, 0, 0) == 0
    assert started == [7], "F10 到窗口线程后没起校验"

    dlg._checking = True
    dlg._wnd_proc(7, edition._WM_APP_HOTKEY, 0, 0)
    assert started == [7], "校验中按 F10 又起了一轮（热键不能借用按钮的取消语义）"


def test_watch_posts_only_on_rising_edge(monkeypatch):
    posts: list[int] = []
    monkeypatch.setitem(
        sys.modules,
        "win32gui",
        _fake_gui(PostMessage=lambda hwnd, msg, wparam, lparam: posts.append(msg)),
    )
    dlg = _dialog()
    # 首帧读一次当基线，之后每圈读一次：False → True(按) → True(按住) → True(按住)
    # → False(松) → True(再按) → True(按住)
    states = iter([False, True, True, True, False, True, True])
    monkeypatch.setattr(dlg, "_f10_down", lambda: next(states))

    dlg._watch_hotkey(11, _Stop(6))

    assert posts == [edition._WM_APP_HOTKEY, edition._WM_APP_HOTKEY], (
        f"按住不放该只算一次，实际发了 {len(posts)} 次消息"
    )


def test_destroy_stops_watch_thread(monkeypatch):
    dlg = _dialog()
    monkeypatch.setattr(dlg, "_f10_down", lambda: False)
    dlg._start_hotkey_watch(1)
    stop = dlg._hotkey_stop
    assert stop is not None and not stop.is_set()

    monkeypatch.setitem(sys.modules, "win32gui", _fake_gui())
    edition._win32._INSTANCES[99] = dlg
    try:
        dlg._wnd_proc(99, win32con.WM_DESTROY, 0, 0)
    finally:
        edition._win32._INSTANCES.pop(99, None)

    assert stop.is_set(), "关框后 watch 线程没停，还在读按键"
    assert dlg._hotkey_stop is None
