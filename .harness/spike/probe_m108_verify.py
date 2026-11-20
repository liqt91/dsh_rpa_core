"""M108 真机校验：位图平移后每一行的内容/位置/勾选是否仍然对得上。

滚动一格用的是 ScrollWindowEx（整段位图平移）+ 只补边缘一行，最容易出的
错是「位置对了但内容错行」「勾选跟着错位」。这里滚到底、再滚回顶，逐行核对
「第 i 个可见槽位的文字 == levels[scroll + i] 的摘要」。
"""

from __future__ import annotations

import json
import pathlib
import time

import win32con
import win32gui

from rpa_core.capture_kit import element_editing, selector_tree
from rpa_core.capture_kit.edit_dialog import ElementEditDialog

DOC = pathlib.Path("workflows/xiaohongshu/elements/小红书输入框1.json")


def visible_rows(dlg, top: int) -> list[tuple[int, int, str]]:
    """返回 [(index, 客户区 y, 文字)]，只取当前真的可见的行。"""
    rows = []
    for index, (row, box) in enumerate(
        zip(dlg._level_boxes, dlg._level_check_boxes, strict=True)
    ):
        if not win32gui.IsWindowVisible(row):
            continue
        rect = win32gui.GetWindowRect(row)
        rows.append((index, rect[1] - top, win32gui.GetWindowText(row)))
    return rows


def main() -> int:
    doc = json.loads(DOC.read_text(encoding="utf-8"))
    model = element_editing.build_edit_model(doc, name=doc.get("name", ""))
    levels = model["levels"]
    limit = max(0, len(levels) - 10)

    dlg = ElementEditDialog()
    dlg.show(title="verify probe", name="probe", levels=levels, selector=model["selector"])
    for _ in range(400):
        if dlg._hwnd and dlg._level_boxes:
            break
        time.sleep(0.01)
    time.sleep(1.0)
    hwnd = dlg._hwnd
    window_top = win32gui.GetWindowRect(hwnd)[1]
    print("hwnd", hwnd, "levels", len(levels), "limit", limit)

    def check(label: str) -> None:
        rows = visible_rows(dlg, window_top)
        expect = [i for i in range(dlg._level_scroll, min(dlg._level_scroll + 10, len(levels)))]
        got = [r[0] for r in rows]
        ys = [r[1] for r in rows]
        gaps = [b - a for a, b in zip(ys, ys[1:])]
        text_ok = all(text == selector_tree.summary(levels[i]) for i, _y, text in rows)
        print(
            f"{label}: scroll={dlg._level_scroll} rows={got} y0={ys[0] if ys else None} "
            f"gaps={set(gaps)} text_ok={text_ok}"
        )
        assert got == expect, f"{label}: 可见行错位，期望 {expect} 实际 {got}"
        assert set(gaps) <= {32}, f"{label}: 行距不是 32px：{gaps}"
        # GetWindowRect 是屏幕坐标（含标题栏），这里只核对相对行距与总高
        if ys:
            assert ys[-1] - ys[0] == 32 * (len(ys) - 1), f"{label}: 行没有连续排布 {ys}"
        checks = {
            i: win32gui.SendMessage(dlg._level_check_boxes[i], win32con.BM_GETCHECK, 0, 0)
            for i in expect
        }
        want = {i: 1 if levels[i]["included"] else 0 for i in expect}
        assert checks == want, f"{label}: 勾选与模型不符 {checks} != {want}"

    check("top")
    for _ in range(limit):
        win32gui.SendMessage(hwnd, win32con.WM_VSCROLL, win32con.SB_LINEDOWN, 0)
        time.sleep(0.05)
    check("bottom")
    for _ in range(limit):
        win32gui.SendMessage(hwnd, win32con.WM_VSCROLL, win32con.SB_LINEUP, 0)
        time.sleep(0.05)
    check("back-to-top")
    win32gui.SendMessage(hwnd, win32con.WM_MOUSEWHEEL, ((-120) & 0xFFFF) << 16, 0)
    time.sleep(0.1)
    check("wheel-down")
    win32gui.SendMessage(hwnd, win32con.WM_VSCROLL, (2 << 16) | win32con.SB_THUMBTRACK, 0)
    time.sleep(0.1)
    check("thumb-to-2")

    win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
    for _ in range(100):
        if dlg._closed:
            break
        time.sleep(0.01)
    print("closed", dlg._closed, "ALL ROWS OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
