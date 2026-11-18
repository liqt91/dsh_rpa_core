"""M96 取证 6：两件事各测一遍（高完整性运行，只读/只画临时框）。

A. ``flash_rect`` 连续两次调用，第二次是否还画得出来（怀疑 RegisterClass 重名）。
B. 鼠标落在微信「搜索框」那一带时，捕获链实际拿到哪一层元素——对比
   ``_window_scope_hit``（窗口作用域 DFS）与 ``ElementFromPoint``（屏幕级）。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


def part_a() -> None:
    from rpa_core.capture.desktop_highlight import flash_rect

    rect = {"left": 300, "top": 200, "width": 120, "height": 40}
    for index in (1, 2, 3):
        started = time.perf_counter()
        ok = flash_rect(rect, duration=0.15)
        print(f"[A] 第 {index} 次 flash_rect -> {ok} ({time.perf_counter() - started:.2f}s)")


def part_b() -> None:
    import win32gui

    hwnd = 0

    def cb(h, _):
        nonlocal hwnd
        if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h) == "微信" \
                and win32gui.GetClassName(h).startswith("Qt"):
            hwnd = h
            return False
        return True

    win32gui.EnumWindows(cb, None)
    if not hwnd:
        print("[B] 没找到微信窗口")
        return
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    print(f"[B] 微信窗口 {hwnd} rect={(left, top, right, bottom)}")

    from rpa_core.capture import desktop_agent

    # 左侧会话列表顶部（搜索框大致位置）：窗口左上角往内 200x60 处
    points = [(left + 200, top + 55), (left + 200, top + 90), (left + 60, top + 110)]
    for x, y in points:
        print(f"\n[B] 点 ({x},{y})")
        try:
            info = desktop_agent._element_from_point(x, y)
            print(f"    ElementFromPoint -> {_brief(info)}")
        except Exception as exc:  # noqa: BLE001
            print(f"    ElementFromPoint 抛 {type(exc).__name__}: {exc}")
        try:
            path_infos: list = []
            leaf, rect = desktop_agent._window_scope_hit(hwnd, x, y, path_out=path_infos)
            print(f"    窗口作用域 DFS -> {_brief(leaf)} rect={rect}")
            print(f"    祖先链 {len(path_infos)} 级")
        except Exception as exc:  # noqa: BLE001
            print(f"    窗口作用域 DFS 抛 {type(exc).__name__}: {exc}")
        try:
            out = desktop_agent.capture_at(x, y, hwnd)
            loc = (out.get("selector") or {}).get("locator") or {}
            print(f"    capture_at -> locator={loc} verifyCount={out.get('verifyCount')}")
        except Exception as exc:  # noqa: BLE001
            print(f"    capture_at 抛 {type(exc).__name__}: {exc}")


def _brief(info) -> str:
    if info is None:
        return "None"
    try:
        e = getattr(info, "element_info", info)
        return (
            f"ct={e.control_type} name=[{e.name}] aid=[{e.automation_id}] "
            f"class=[{e.class_name}]"
        )
    except Exception as exc:  # noqa: BLE001
        return f"<{type(exc).__name__}>"


if __name__ == "__main__":
    part_a()
    part_b()
