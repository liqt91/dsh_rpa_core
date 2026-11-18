"""M96 取证 2：直接按句柄读微信窗口的 UIA 子树（不靠 Desktop.windows() 枚举）。

微信顶层窗 handle 从 win32 EnumWindows 拿（Qt51514QWindowIcon / 标题「微信」），
再走 UIA ElementFromHandle 读子树。低完整性下如果 COM 被拒，会明确打印错误码。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))


def find_wechat_hwnd() -> int:
    import win32gui

    found = []

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        cls = win32gui.GetClassName(hwnd)
        if title == "微信" or "QWindowIcon" in cls and "微信" in title:
            found.append(hwnd)
        return True

    win32gui.EnumWindows(cb, None)
    return found[0] if found else 0


def main() -> int:
    hwnd = find_wechat_hwnd()
    print(f"wechat hwnd={hwnd}")
    if not hwnd:
        return 1

    # 唤醒 gate（与捕获同一条路），确保读到的是醒着的树
    from rpa_core.capture import accessibility

    try:
        ok = accessibility.activate(hwnd)
        print(f"activate -> {ok}")
    except Exception as exc:  # noqa: BLE001
        print(f"activate 失败 {type(exc).__name__}: {exc}")

    from pywinauto import Desktop

    try:
        win = Desktop(backend="uia").window(handle=hwnd)
        print(f"wrapper ok: {win}")
        info = win.element_info
        print(f"top: ct={info.control_type} name=[{info.name}] class=[{info.class_name}]")
    except Exception as exc:  # noqa: BLE001
        print(f"window(handle) 失败 {type(exc).__name__}: {exc}")
        return 2

    _dump(win, depth=0, max_depth=8)
    return 0


def _dump(node, *, depth: int, max_depth: int) -> None:
    pad = "  " * depth
    try:
        info = node.element_info
        ct = info.control_type
        name = info.name
        aid = info.automation_id
        cls = info.class_name
    except Exception as exc:  # noqa: BLE001
        print(f"{pad}<属性失败 {type(exc).__name__}>")
        return
    try:
        rect = node.rectangle()
        box = f"{rect.left},{rect.top} {rect.width()}x{rect.height()}"
    except Exception:  # noqa: BLE001
        box = "?"
    print(f"{pad}- {ct} name=[{name}] aid=[{aid}] class=[{cls}] {box}")
    if depth >= max_depth:
        return
    try:
        kids = node.children()
    except Exception as exc:  # noqa: BLE001
        print(f"{pad}  <children 失败 {type(exc).__name__}>")
        return
    if not kids:
        print(f"{pad}  <无子节点>")
        return
    if len(kids) > 40:
        print(f"{pad}  <共 {len(kids)} 个子节点，只列前 40>")
        kids = kids[:40]
    for kid in kids:
        _dump(kid, depth=depth + 1, max_depth=max_depth)


if __name__ == "__main__":
    raise SystemExit(main())
