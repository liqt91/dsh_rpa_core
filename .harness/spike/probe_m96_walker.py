"""M96 取证 5：用 TreeWalker（Navigate）枚举微信子树，和 FindAll 对照。

PowerShell 的 RawViewWalker 能读到 156 个后代，而 FindAll 在同一个窗口上返回 0，
怀疑微信的 UIA provider 只实现了节点导航、没实现 FindAll。这里逐层走一遍，
并打印能定位到「搜索」的节点（只读，不点击）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    import win32gui
    from pywinauto.uia_defines import IUIA

    hwnd = 0

    def cb(h, _):
        nonlocal hwnd
        if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h) == "微信":
            hwnd = h
            return False
        return True

    win32gui.EnumWindows(cb, None)
    if not hwnd:
        print("没找到微信")
        return 1
    iuia = IUIA().iuia
    root = iuia.ElementFromHandle(hwnd)

    walker = iuia.RawViewWalker
    total = 0
    hits: list[str] = []

    def walk(el, depth: int, max_depth: int = 10) -> None:
        nonlocal total
        if depth > max_depth:
            return
        child = walker.GetFirstChildElement(el)
        while child is not None:
            total += 1
            try:
                ct = child.CurrentControlType
                name = child.CurrentName
                aid = child.CurrentAutomationId
                cls = child.CurrentClassName
                rect = child.CurrentBoundingRectangle
                box = f"{rect.left},{rect.top} {rect.right - rect.left}x{rect.bottom - rect.top}"
            except Exception as exc:  # noqa: BLE001
                child = walker.GetNextSiblingElement(child)
                continue
            pad = "  " * depth
            print(f"{pad}- ct={ct} name=[{name}] aid=[{aid}] class=[{cls}] {box}")
            if name and "搜索" in str(name):
                hits.append(f"{cls}/{name} @ {box}")
            walk(child, depth + 1, max_depth)
            child = walker.GetNextSiblingElement(child)

    walk(root, 0)
    print(f"\n总节点 {total}；name 含「搜索」的 {len(hits)} 个：")
    for h in hits:
        print("   ", h)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
