"""M98 S3：微信窗口上 FindAll 与 TreeWalker 的能力对照（含唤醒后重建）。

M96 的结论是「RawViewWalker 能读 156 个后代，FindAll 返 0」。但保存后当场校验
是**命中过**的（trace 里有 bring_to_foreground），说明 FindAll 至少那一刻可用。
本探针要把「什么时候可用」测出来：

  [1] 冷态：FindAll 直接调用 / pywinauto descendants / RawViewWalker
  [2] 唤醒 gate 后等 1.5s，重建 root，再来一遍
  [3] 对照「另一条路」：从 raw walker 找到搜索框的父链，验证 path 逐级是否真存在
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


def find_hwnd() -> int:
    import win32gui

    hit = 0

    def cb(h, _):
        nonlocal hit
        if win32gui.GetWindowText(h) == "微信" and win32gui.IsWindowVisible(h):
            hit = h
            return False
        return True

    win32gui.EnumWindows(cb, None)
    return hit


def walkers(hwnd: int) -> tuple[int, list[str]]:
    from pywinauto.uia_defines import IUIA

    iuia = IUIA().iuia
    root = iuia.ElementFromHandle(hwnd)
    walker = iuia.RawViewWalker
    total = 0
    chain: list[str] = []

    def walk(el, depth: int) -> None:
        nonlocal total
        try:
            child = walker.GetFirstChildElement(el)
        except Exception:  # noqa: BLE001 - 树休眠时 walker 直接 NULL COM pointer
            return
        while child is not None:
            total += 1
            try:
                ct = child.CurrentControlType
                name = str(child.CurrentName or "")
                cls = str(child.CurrentClassName or "")
                aid = str(child.CurrentAutomationId or "")
            except Exception:  # noqa: BLE001
                try:
                    child = walker.GetNextSiblingElement(child)
                except Exception:  # noqa: BLE001
                    return
                continue
            if "搜索" in name:
                chain.append(f"depth={depth} ct={ct} name={name!r} class={cls!r} aid={aid!r}")
            if depth < 20:
                walk(child, depth + 1)
            try:
                child = walker.GetNextSiblingElement(child)
            except Exception:  # noqa: BLE001
                return

    walk(root, 0)
    return total, chain


def findall(hwnd: int) -> tuple[int, str]:
    from pywinauto.uia_defines import IUIA

    iuia = IUIA().iuia
    root = iuia.ElementFromHandle(hwnd)
    try:
        cond = iuia.CreateTrueCondition()
        found = root.FindAll(1, cond)  # TreeScope_Descendants = 1
        return int(found.Length), "ok"
    except Exception as exc:  # noqa: BLE001
        return -1, f"{type(exc).__name__}: {exc}"


def pywinauto_descendants(hwnd: int) -> tuple[int, str]:
    from rpa_core.executors.desktop import DesktopExecutor

    window = DesktopExecutor._window_by_handle(hwnd)
    if window is None:
        return -1, "_window_by_handle 返回 None"
    try:
        return len(window.descendants()), "ok"
    except Exception as exc:  # noqa: BLE001
        return -1, f"{type(exc).__name__}: {exc}"


def report(hwnd: int, label: str) -> None:
    total, chain = walkers(hwnd)
    print(f"  [{label}] RawViewWalker 后代 = {total}；名字含「搜索」= {len(chain)}")
    for line in chain[:4]:
        print("      ", line)
    count, note = findall(hwnd)
    print(f"  [{label}] IUIA FindAll(descendants) = {count}  ({note})")
    count2, note2 = pywinauto_descendants(hwnd)
    print(f"  [{label}] pywinauto descendants   = {count2}  ({note2})")


def main() -> int:
    hwnd = find_hwnd()
    if not hwnd:
        print("没找到可见的微信主窗口")
        return 1
    print(f"hwnd={hwnd}")
    print("[1] 冷态")
    report(hwnd, "cold")

    from rpa_core.capture import accessibility

    print("\n[2] 唤醒 gate")
    try:
        result = accessibility.activate(hwnd, poke=True)
        print("  activate:", result)
    except Exception as exc:  # noqa: BLE001
        print("  activate 抛:", type(exc).__name__, exc)
    time.sleep(1.5)
    report(hwnd, "warm")
    time.sleep(2.0)
    report(hwnd, "warm+2s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
