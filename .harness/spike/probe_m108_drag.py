"""M108 探针三：量化「拖动滚动条假死」。

拖动时 Windows 每移动一点就发一条 SB_THUMBTRACK。这里用两条线程模拟：
A 线程连发 100 条 SB_THUMBTRACK；B 线程每 5ms 发一条 WM_NULL 并量往返
（WM_NULL 要排在窗口线程队列里，等于测「界面还理不理人」）。

判定口径：A 总耗时 / 100 = 单条滞后；B 的 max RTT = 界面最长冻结时间。
"""

from __future__ import annotations

import json
import pathlib
import threading
import time

import win32con
import win32gui

from rpa_core.capture_kit import element_editing
from rpa_core.capture_kit.edit_dialog import ElementEditDialog

DOC = pathlib.Path("workflows/xiaohongshu/elements/小红书输入框1.json")
BURST = 100


def main() -> int:
    doc = json.loads(DOC.read_text(encoding="utf-8"))
    model = element_editing.build_edit_model(doc, name=doc.get("name", ""))
    levels = model["levels"]
    limit = max(0, len(levels) - 10)

    dlg = ElementEditDialog()
    dlg.show(title="drag probe", name="probe", levels=levels, selector=model["selector"])
    for _ in range(400):
        if dlg._hwnd and len(dlg._level_boxes) == len(levels):
            break
        time.sleep(0.01)
    time.sleep(0.9)
    hwnd = dlg._hwnd
    print("hwnd", hwnd, "levels", len(levels), "limit", limit)

    # 预热：首帧要建字体/首绘，别让它污染心跳基线
    win32gui.SendMessage(hwnd, win32con.WM_VSCROLL, win32con.SB_LINEDOWN, 0)
    win32gui.SendMessage(hwnd, win32con.WM_VSCROLL, win32con.SB_LINEUP, 0)
    time.sleep(0.4)

    rtts: list[float] = []
    stop = threading.Event()

    def heartbeat() -> None:
        while not stop.is_set():
            t0 = time.perf_counter()
            win32gui.SendMessage(hwnd, win32con.WM_NULL, 0, 0)
            rtts.append((time.perf_counter() - t0) * 1000)
            time.sleep(0.005)

    hb = threading.Thread(target=heartbeat, daemon=True)
    hb.start()
    time.sleep(0.2)

    # 模拟拖动：滚动块在 0..limit 之间来回扫，按鼠标 125Hz 的节奏（8ms/条）发
    layouts: list[float] = []
    orig_layout = ElementEditDialog._layout_levels

    def counted_layout(self, hwnd):
        t0 = time.perf_counter()
        try:
            return orig_layout(self, hwnd)
        finally:
            layouts.append((time.perf_counter() - t0) * 1000)

    ElementEditDialog._layout_levels = counted_layout

    seq = [p for _ in range(BURST // max(1, limit + 1) + 1) for p in range(limit + 1)][:BURST]
    t0 = time.perf_counter()
    for pos in seq:
        win32gui.SendMessage(
            hwnd, win32con.WM_VSCROLL, (pos << 16) | win32con.SB_THUMBTRACK, 0
        )
        time.sleep(0.008)
    burst = (time.perf_counter() - t0) * 1000
    stop.set()
    hb.join(timeout=1)
    ElementEditDialog._layout_levels = orig_layout

    rtts.sort()
    print(
        f"burst {BURST} msgs @8ms: total={burst:8.1f}ms (wall {BURST * 8}ms)"
        f"  layouts={len(layouts)}"
        + (f" max={max(layouts):5.2f}ms avg={sum(layouts) / len(layouts):5.2f}ms" if layouts else "")
    )
    print(
        f"UI rtt n={len(rtts)}  p50={rtts[len(rtts) // 2]:6.2f}ms"
        f"  p95={rtts[int(len(rtts) * 0.95) - 1]:7.2f}ms  max={rtts[-1]:7.2f}ms"
    )
    print("scroll now", dlg._level_scroll, "final pos", seq[-1])

    win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
    for _ in range(100):
        if dlg._closed:
            break
        time.sleep(0.01)
    print("closed", dlg._closed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
