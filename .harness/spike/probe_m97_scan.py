"""M97 取证 A：微信窗口内多点扫描 ElementFromPoint，找出「比整窗更细」的命中。

只读。目的：确认屏幕级命中在哪些坐标上真能拿到搜索框级别的元素，
以及那些元素的 NativeWindowHandle / 矩形 —— 这决定捕获链该在哪一层补救。

用法：python probe_m97_scan.py            # 扫默认区域（左侧顶部搜索框带）
      python probe_m97_scan.py 40 20 400 120   # 自定义相对窗口的 x0 y0 x1 y1
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
    return found


def main() -> int:
    import win32gui

    hwnd = find_wechat()
    if not hwnd:
        print("没找到微信窗口")
        return 1
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    win_w, win_h = right - left, bottom - top
    print(f"微信 hwnd={hwnd} rect={(left, top, right, bottom)} {win_w}x{win_h}")

    if len(sys.argv) >= 5:
        x0, y0, x1, y1 = (int(v) for v in sys.argv[1:5])
    else:
        x0, y0, x1, y1 = 70, 25, 340, 120

    from rpa_core.capture import desktop_agent

    print(f"扫描窗口内相对区域 x={x0}..{x1} y={y0}..{y1}")
    seen: dict[str, int] = {}
    for ry in range(y0, y1, 12):
        for rx in range(x0, x1, 24):
            x, y = left + rx, top + ry
            try:
                info = desktop_agent._element_from_point(x, y)
            except Exception as exc:  # noqa: BLE001
                print(f"({rx},{ry}) 抛 {type(exc).__name__}")
                continue
            if info is None:
                print(f"({rx},{ry}) None")
                continue
            try:
                box = info.rectangle
                w, h = box.right - box.left, box.bottom - box.top
                key = (
                    f"ct={info.control_type} name=[{info.name}] "
                    f"aid=[{info.automation_id}] rect={w}x{h}"
                )
                native = int(getattr(info, "handle", 0) or 0)
            except Exception as exc:  # noqa: BLE001
                print(f"({rx},{ry}) 读属性抛 {type(exc).__name__}: {exc}")
                continue
            seen[key] = seen.get(key, 0) + 1
            if w * h < win_w * win_h * 0.9:
                print(f"({rx},{ry}) ★ {key} native={native}")
            else:
                print(f"({rx},{ry})   整窗级 {key} native={native}")
    print("\n汇总（按出现次数）：")
    for key, count in sorted(seen.items(), key=lambda kv: -kv[1]):
        print(f"  {count:3d}  {key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
