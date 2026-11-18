"""M96 取证 3：以高完整性运行，复现「捕获微信搜索框」为什么落到 MMUIRenderSubWindowHW。

**必须**从 PowerShell（高完整性）里跑：低完整性进程读 Medium 进程的 UIA 树会拿到空树。

只读：只做 hit-test 与子树 dump，不点击、不输入。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    import win32gui

    hwnd = 0

    def cb(h, _):
        nonlocal hwnd
        if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h) == "微信":
            hwnd = h
            return False
        return True

    win32gui.EnumWindows(cb, None)
    if not hwnd:
        print("没找到微信窗口")
        return 1
    rect = win32gui.GetWindowRect(hwnd)
    print(f"wechat hwnd={hwnd} rect={rect}")

    from rpa_core.capture import accessibility, desktop_agent

    print("activate:", accessibility.activate(hwnd))

    # 1) 先按整窗 DFS 看能钻到哪一层（这就是 capture_in_window 干的事）
    path_infos: list = []
    leaf, leaf_rect = desktop_agent._window_scope_hit(hwnd, *(
        (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
    ), path_out=path_infos)
    print(f"\n[窗口作用域 DFS] leaf={_brief(leaf)} rect={leaf_rect}")
    print(f"  祖先链 {len(path_infos)} 级：")
    for i, item in enumerate(path_infos):
        print(f"    {i}. {_brief(item)}")

    # 2) 直接看顶层窗口的 children 长什么样（说明 DFS 起点是否为空）
    from pywinauto import Desktop

    win = Desktop(backend="uia").window(handle=hwnd)
    kids = win.children()
    print(f"\n[顶层 children] {len(kids)} 个")
    for k in kids[:10]:
        print(f"    - {_brief(k)}")

    # 3) 找 MMUIRenderSubWindowHW 并看它的 children
    target = None
    for k in kids:
        try:
            if "MMUIRender" in str(k.element_info.class_name):
                target = k
        except Exception:  # noqa: BLE001
            pass
    if target is not None:
        sub = target.children()
        print(f"\n[MMUIRenderSubWindowHW children] {len(sub)} 个")
        for i, s in enumerate(sub[:20]):
            print(f"    {i}. {_brief(s)}")
    else:
        print("\n[顶层里没有 MMUIRenderSubWindowHW]")
    return 0


def _brief(info) -> str:
    if info is None:
        return "None"
    try:
        e = info.element_info
        return (
            f"ct={e.control_type} name=[{e.name}] aid=[{e.automation_id}] "
            f"class=[{e.class_name}] hwnd={e.handle}"
        )
    except Exception as exc:  # noqa: BLE001
        return f"<{type(exc).__name__}>"


if __name__ == "__main__":
    raise SystemExit(main())
