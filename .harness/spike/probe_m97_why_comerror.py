"""M97 取证 B：单点测 ElementFromPoint 的确切错误（含 HRESULT）与前台状态。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    import ctypes

    import win32gui
    import win32process

    found = 0

    def cb(h, _):
        nonlocal found
        try:
            if (
                win32gui.IsWindowVisible(h)
                and win32gui.GetWindowText(h) == "微信"
                and win32gui.GetClassName(h).startswith("Qt")
            ):
                found = h
                return False
        except Exception:  # noqa: BLE001
            return True
        return True

    win32gui.EnumWindows(cb, None)
    if not found:
        print("没找到微信")
        return 1
    left, top, right, bottom = win32gui.GetWindowRect(found)
    fg = int(ctypes.windll.user32.GetForegroundWindow())
    _, pid = win32process.GetWindowThreadProcessId(found)
    print(f"wechat hwnd={found} rect=({left},{top},{right},{bottom}) pid={pid}")
    print(f"foreground hwnd={fg} cls={win32gui.GetClassName(fg)} "
          f"title={win32gui.GetWindowText(fg)!r}")

    from rpa_core.capture import desktop_agent

    points = [(left + 120, top + 55), (left + 200, top + 60), (left + 60, top + 300)]
    for x, y in points:
        hwnd_at = int(ctypes.windll.user32.WindowFromPoint(ctypes.wintypes.POINT(x, y)) or 0)
        print(f"\n点 ({x},{y}) window_from_point={hwnd_at} "
              f"cls={win32gui.GetClassName(hwnd_at)} root={desktop_agent._win32_root_at(x, y)}")
        try:
            info = desktop_agent._element_from_point(x, y)
            if info is None:
                print("  ElementFromPoint -> None")
            else:
                box = info.rectangle
                print(f"  -> ct={info.control_type} name=[{info.name}] "
                      f"aid={info.automation_id} handle={getattr(info, 'handle', None)} "
                      f"rect=({box.left},{box.top}) {box.right - box.left}x{box.bottom - box.top}")
        except Exception as exc:  # noqa: BLE001
            hr = getattr(exc, "hresult", None)
            print(f"  抛 {type(exc).__name__} hresult={hr!r}: {exc}")

    # 直接看 IUIA 的 ElementFromPoint（绕过 pywinauto 的包装层）
    try:
        from pywinauto.uia_defines import IUIA

        iuia = IUIA().iuia
        x, y = points[0]
        el = iuia.ElementFromPoint(ctypes.wintypes.POINT(x, y))
        print("\n裸 IUIA.ElementFromPoint ->", None if el is None else (
            el.CurrentControlType, el.CurrentName, el.CurrentNativeWindowHandle))
    except Exception as exc:  # noqa: BLE001
        print("\n裸 IUIA.ElementFromPoint 抛", type(exc).__name__,
              getattr(exc, "hresult", None), exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
