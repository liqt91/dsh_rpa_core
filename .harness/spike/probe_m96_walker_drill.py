"""M96 取证 7：TreeWalker 兜底下钻可行性。

pywinauto 的 children() 走 FindAll，微信 provider 对它返回空；PowerShell 侧
RawViewWalker 能读到 51 个节点。这里用 comtypes 的 TreeWalker 手工下钻，
证明「含点最小面积」在这条链上也成立（目标：导航栏「微信」按钮）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    import win32gui
    from pywinauto.uia_defines import IUIA
    from pywinauto.uia_element_info import UIAElementInfo

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
        print("没找到微信")
        return 1

    iuia = IUIA().iuia
    walker = iuia.RawViewWalker
    element = iuia.ElementFromHandle(hwnd)
    info = UIAElementInfo(element)
    print(f"root: ct={info.control_type} name=[{info.name}] rect={info.rectangle}")

    for point in ((262, 192), (262, 238), (424, 137)):
        print(f"\n=== 目标点 {point} ===")
        hit = _drill(walker, info, *point)
        if hit is None:
            print("  没命中")
            continue
        print(f"  命中 ct={hit.control_type} name=[{hit.name}] aid=[{hit.automation_id}] "
              f"class=[{hit.class_name}] rect={hit.rectangle}")
    return 0


def _walker_children(walker, info) -> list:
    """用 TreeWalker 取直接子节点（微信 provider 唯一支持的那条路）。"""
    from pywinauto.uia_element_info import UIAElementInfo

    out = []
    try:
        child = walker.GetFirstChildElement(info._element)
    except Exception as exc:  # noqa: BLE001
        print(f"    <walker first child 失败 {type(exc).__name__}>")
        return out
    guard = 0
    while child is not None and guard < 400:
        guard += 1
        try:
            out.append(UIAElementInfo(child))
        except Exception as exc:  # noqa: BLE001
            print(f"    <包装失败 {type(exc).__name__}>")
        try:
            child = walker.GetNextSiblingElement(child)
        except Exception:  # noqa: BLE001
            break
    return out


def _drill(walker, root_info, x: int, y: int, max_depth: int = 10):
    current = root_info
    for depth in range(max_depth):
        kids = _walker_children(walker, current)
        if not kids:
            return current
        containing = []
        for kid in kids:
            try:
                rect = kid.rectangle
            except Exception:  # noqa: BLE001
                continue
            if rect.left <= x < rect.right and rect.top <= y < rect.bottom:
                containing.append(((rect.right - rect.left) * (rect.bottom - rect.top), kid))
        if not containing:
            return current
        containing.sort(key=lambda p: p[0])
        current = containing[0][1]
        print(f"  L{depth + 1}: {current.control_type} name=[{current.name}] "
              f"class=[{current.class_name}] {current.rectangle}")
    return current


if __name__ == "__main__":
    raise SystemExit(main())
