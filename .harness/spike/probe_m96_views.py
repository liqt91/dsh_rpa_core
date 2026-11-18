"""M96 取证 4：绕过 pywinauto，直接问 UIA「同一个窗口在 Control / Raw 两个视图下各有多少后代」。

高完整性运行（从 PowerShell 起）。只读。
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
    print(f"hwnd={hwnd}")
    if not hwnd:
        return 1

    iuia = IUIA().iuia
    root = iuia.ElementFromHandle(hwnd)
    print(f"ElementFromHandle -> ct={root.CurrentControlType} name=[{root.CurrentName}]")

    # TrueCondition = Raw View（能看到 IsControlElement=false 的节点）
    from comtypes.gen.UIAutomationClient import (
        CUIAutomation,  # noqa: F401  (确保类型库已加载)
    )
    import comtypes.gen.UIAutomationClient as uia_mod

    true_cond = iuia.CreateTrueCondition()
    for label, scope in (
        ("children", IUIA().tree_scope["children"]),
        ("descendants", IUIA().tree_scope["descendants"]),
    ):
        found = root.FindAll(scope, true_cond)
        print(f"\n[Raw TrueCondition / {label}] count={found.Length}")
        for i in range(min(found.Length, 12)):
            el = found.GetElement(i)
            print(f"    {i}. ct={el.CurrentControlType} name=[{el.CurrentName}] "
                  f"aid=[{el.CurrentAutomationId}] class=[{el.CurrentClassName}]")

    # Control View 对照
    cond = iuia.CreatePropertyCondition(
        uia_mod.UIA_IsControlElementPropertyId, True
    )
    found = root.FindAll(IUIA().tree_scope["descendants"], cond)
    print(f"\n[ControlView / descendants] count={found.Length}")
    for i in range(min(found.Length, 12)):
        el = found.GetElement(i)
        print(f"    {i}. ct={el.CurrentControlType} name=[{el.CurrentName}] "
              f"aid=[{el.CurrentAutomationId}] class=[{el.CurrentClassName}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
