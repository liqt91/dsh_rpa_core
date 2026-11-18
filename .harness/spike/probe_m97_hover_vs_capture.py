"""M97 取证：hover 能框到微信搜索框、捕获却回整窗——到底断在哪一环。

只读：只做 hit-test / 子树遍历，不点击、不输入。

用 RawViewWalker 找到「搜索」元素的矩形中心当坐标，然后逐层打印：
  A) 该元素的 NativeWindowHandle 与 ``_root_window_handle()``
  B) ``_element_from_point`` 在同一个点拿到什么
  C) ``capture_in_window``（窗口作用域 DFS）拿到什么
  D) ``_finer_element_in_same_window``（M96 兜底）拿到什么，以及它在哪一步回 None
  E) ``capture_at(x, y, root)`` 的最终 metadata
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


def find_wechat() -> int:
    import win32gui

    found = 0

    def cb(h, _):
        nonlocal found
        try:
            if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h) == "微信":
                found = h
                return False
        except Exception:  # noqa: BLE001
            return True
        return True

    win32gui.EnumWindows(cb, None)
    return found


def walk_collect(root) -> list:
    """RawViewWalker 全量遍历，返回 [(depth, element)]。"""
    from pywinauto.uia_defines import IUIA

    iuia = IUIA().iuia
    walker = iuia.RawViewWalker
    out: list = []

    def walk(el, depth: int) -> None:
        if depth > 12:
            return
        child = walker.GetFirstChildElement(el)
        while child is not None:
            out.append((depth, child))
            try:
                walk(child, depth + 1)
            except Exception:  # noqa: BLE001
                pass
            try:
                child = walker.GetNextSiblingElement(child)
            except Exception:  # noqa: BLE001
                break

    walk(root, 0)
    return out


def brief(el) -> str:
    try:
        ct = el.CurrentControlType
        name = el.CurrentName
        aid = el.CurrentAutomationId
        cls = el.CurrentClassName
        rect = el.CurrentBoundingRectangle
        native = el.CurrentNativeWindowHandle
        return (
            f"ct={ct} name=[{name}] aid=[{aid}] class=[{cls}] "
            f"native={native} rect=({rect.left},{rect.top}) "
            f"{rect.right - rect.left}x{rect.bottom - rect.top}"
        )
    except Exception as exc:  # noqa: BLE001
        return f"<读不到: {type(exc).__name__}: {exc}>"


def main() -> int:
    import win32gui
    from pywinauto.uia_defines import IUIA

    hwnd = find_wechat()
    if not hwnd:
        print("没找到微信窗口")
        return 1
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    print(f"微信 hwnd={hwnd} rect={(left, top, right, bottom)} "
          f"{right - left}x{bottom - top}")

    from rpa_core.capture import accessibility, desktop_agent

    wake = accessibility.activate(hwnd)
    force = desktop_agent._maybe_wake_accessibility(hwnd, force=True)
    print(f"activate={wake} wake_gate={force}")

    iuia = IUIA().iuia
    root_el = iuia.ElementFromHandle(hwnd)
    nodes = walk_collect(root_el)
    print(f"RawViewWalker 后代 {len(nodes)} 个")

    # 目标：name 含「搜索」的元素（微信 4.x 搜索框），退而求其次找 Edit
    target = None
    target_label = ""
    for depth, el in nodes:
        try:
            name = str(el.CurrentName or "")
        except Exception:  # noqa: BLE001
            continue
        if name and "搜索" in name:
            target, target_label = el, f"name 含搜索 (depth={depth})"
            print(f"  [候选] {target_label}: {brief(el)}")
            break
    if target is None:
        for depth, el in nodes:
            try:
                if el.CurrentControlType == 50004:  # UIA_EditControlTypeId
                    target, target_label = el, f"Edit (depth={depth})"
                    print(f"  [候选] {target_label}: {brief(el)}")
                    break
            except Exception:  # noqa: BLE001
                continue
    if target is None:
        print("树里没找到搜索框/Edit；打印全部节点供人工挑点：")
        for depth, el in nodes[:60]:
            print("   " + "  " * depth + brief(el))
        return 2

    box = target.CurrentBoundingRectangle
    cx = (box.left + box.right) // 2
    cy = (box.top + box.bottom) // 2
    target_rect = (box.left, box.top, box.right - box.left, box.bottom - box.top)
    print(f"\n目标 {target_label} 中心点=({cx},{cy}) rect={target_rect}")

    # A) 元素自带 handle 与它算出来的根窗口
    try:
        native = int(target.CurrentNativeWindowHandle or 0)
    except Exception:  # noqa: BLE001
        native = -1
    root_of_native = (
        desktop_agent._root_window_handle(native) if native > 0 else "n/a"
    )
    print(f"\n[A] NativeWindowHandle={native} _root_window_handle={root_of_native}"
          f"  期望 root={hwnd}")

    # B) 屏幕级 ElementFromPoint
    print("\n[B] _element_from_point")
    try:
        info = desktop_agent._element_from_point(cx, cy)
        print("    ->", brief(info))
        try:
            print("    handle=", int(info.handle or 0),
                  " root_of_handle=", desktop_agent._root_window_handle(int(info.handle or 0)))
        except Exception as exc:  # noqa: BLE001
            print("    读 handle 失败:", exc)
    except Exception as exc:  # noqa: BLE001
        print("    抛", type(exc).__name__, exc)

    # C) 窗口作用域 DFS（capture_in_window 主体）
    print("\n[C] capture_in_window（窗口作用域 DFS）")
    try:
        scoped = desktop_agent.capture_in_window(hwnd, cx, cy)
        if scoped is None:
            print("    -> None（DFS 一步都迈不出去）")
        else:
            print("    ->", scoped.get("metadata"))
            print("    fills_window =",
                  desktop_agent._rect_fills_window(
                      scoped.get("metadata", {}).get("rect"), hwnd))
    except Exception as exc:  # noqa: BLE001
        print("    抛", type(exc).__name__, exc)

    # D) M96 兜底逐步判定
    print("\n[D] _finer_element_in_same_window 逐步")
    finer = desktop_agent._finer_element_in_same_window(cx, cy, hwnd)
    print("    结果 =", "None" if finer is None else finer.get("metadata"))

    # E) 最终 capture_at
    print("\n[E] capture_at(x, y, root)")
    try:
        captured = desktop_agent.capture_at(cx, cy, hwnd)
        meta = captured.get("metadata", {})
        print("    kind=", captured.get("kind"), "verifyCount=", captured.get("verifyCount"))
        print("    metadata=", meta)
        rect = meta.get("rect")
        print("    rect=", rect, " fills_window=",
              desktop_agent._rect_fills_window(rect, hwnd))
        print("    目标 rect 应为=", target_rect)
        print("    selector.locator=",
              captured.get("selector", {}).get("locator"))
    except Exception as exc:  # noqa: BLE001
        print("    抛", type(exc).__name__, exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
