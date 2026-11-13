"""元素编辑框冒烟：真开窗验证五件事（都对应「web 弹窗时代」做不到或做错的点）。

1. **控件齐**：名称 EDIT、只读 meta EDIT、错误 STATIC、保存/取消按钮全在，
   且保存是默认按钮（Enter 的落点）。
2. **中文名保存**：SetWindowText 填中文名 + 点保存 ⇒ ``on_save`` 原样收到中文
   （原生 EDIT 的 IME 不需要 web 端那套 isComposing 守卫）。
3. **错误回显留框**：``on_save`` 返回错误文案 ⇒ 框**不关**、错误行显示原文、
   ``on_close`` **不**触发（假「保存失败」不能静默吞掉）。
4. **Enter 保存**：PostMessage 一个真实 WM_KEYDOWN(Return) 进队列，消息泵里的
   IsDialogMessage 必须把它变成「点保存」——这是用户最自然的提交手势。
5. **Esc 取消**：同样走真实按键路径 ⇒ ``on_close(False)``、框销毁；
   顺带验证与 CaptureFloat **两个窗口类共存**（同一张 hwnd 分派表）。

跑法（会开窗，约 3s）：
    .venv/Scripts/python.exe .harness/demo/ppx_probe/_edit_probe.py
"""

from __future__ import annotations

import sys
import time

sys.path.insert(0, "src")

import win32con
import win32gui

from rpa_core.capture_kit import CaptureFloat, ElementEditDialog, selector_tree
from rpa_core.capture_kit.edit_dialog import (
    _ID_ERROR,
    _ID_LEVEL_BASE,
    _ID_META,
    _ID_NAME,
    _ID_PREVIEW,
    _ID_SELECTOR,
)


def _wait_hwnd(dialog: ElementEditDialog, timeout: float = 3.0) -> int | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if dialog._hwnd:
            return dialog._hwnd
        time.sleep(0.05)
    return None


def _control_text(hwnd: int, cid: int) -> str:
    control = win32gui.GetDlgItem(hwnd, cid)
    return win32gui.GetWindowText(control) if control else ""


def _client_checks(hwnd: int, failed: list[str]) -> None:
    """按钮必须**完整**落在客户区里（2026-11-13「保存按钮显示不全」的回归判据）。

    早先把外框高当客户区高布局：WS_CAPTION 标题栏 + 边框吃掉 ~27px，
    排在底部的按钮被裁掉一半——像素截图能看出来，但控件矩形断言更准、
    还不依赖截图环境。
    """
    save_btn = win32gui.GetDlgItem(hwnd, win32con.IDOK)
    cancel_btn = win32gui.GetDlgItem(hwnd, win32con.IDCANCEL)
    _, _, client_w, client_h = win32gui.GetClientRect(hwnd)
    for btn, label in ((save_btn, "保存"), (cancel_btn, "取消")):
        left, top, right, bottom = win32gui.GetWindowRect(btn)
        # 转成客户区坐标再比
        cx1, cy1 = win32gui.ScreenToClient(hwnd, (left, top))
        cx2, cy2 = win32gui.ScreenToClient(hwnd, (right, bottom))
        if cx1 < 0 or cy1 < 0 or cx2 > client_w or cy2 > client_h:
            failed.append(
                f"{label}按钮越出客户区：({cx1},{cy1})-({cx2},{cy2}) vs 客户区 {client_w}x{client_h}"
            )


def _probe() -> list[str]:
    failed: list[str] = []
    saved: list[tuple[str, str]] = []
    closes: list[bool] = []

    # 第一轮：错误回显留框 → 改正后保存成功
    attempts = iter(["这个名字被占了", None])  # 第一次保存报错，第二次放行

    def on_save(name: str, selector: str) -> str | None:
        saved.append((name, selector))
        return next(attempts)

    dialog = ElementEditDialog()
    dialog.show(
        title="捕获到元素",
        name="元素1",
        selector="#search-input",
        meta="browser\n命中 1 个 · 第 3 步",
        on_save=on_save,
        on_close=closes.append,
    )
    hwnd = _wait_hwnd(dialog)
    if not hwnd:
        return ["编辑框没建起来"]

    # 1. 控件齐 + 默认按钮 + 按钮完整落在客户区（不裁）
    for cid, label in ((_ID_NAME, "名称 EDIT"), (_ID_SELECTOR, "选择器 EDIT"),
                       (_ID_META, "meta EDIT"), (_ID_ERROR, "错误行")):
        if not win32gui.GetDlgItem(hwnd, cid):
            failed.append(f"缺控件：{label}")
    save_btn = win32gui.GetDlgItem(hwnd, win32con.IDOK)
    if not save_btn or win32gui.GetWindowText(save_btn) != "保存":
        failed.append(f"保存按钮文案={win32gui.GetWindowText(save_btn)!r}")
    style = win32gui.GetWindowLong(save_btn, win32con.GWL_STYLE)
    if not (style & win32con.BS_DEFPUSHBUTTON):
        failed.append("保存不是默认按钮（Enter 会落空）")
    _client_checks(hwnd, failed)
    if _control_text(hwnd, _ID_NAME) != "元素1":
        failed.append(f"建议名没预填：{_control_text(hwnd, _ID_NAME)!r}")
    if _control_text(hwnd, _ID_SELECTOR) != "#search-input":
        failed.append(f"选择器没预填：{_control_text(hwnd, _ID_SELECTOR)!r}")

    # 2. 中文名 + 改过的选择器保存 → 第一次返回错误 → 框留着、错误行红字
    win32gui.SetWindowText(win32gui.GetDlgItem(hwnd, _ID_NAME), "搜索框")
    win32gui.SetWindowText(win32gui.GetDlgItem(hwnd, _ID_SELECTOR), "#q")
    win32gui.SendMessage(save_btn, win32con.BM_CLICK, 0, 0)
    time.sleep(0.3)
    if saved != [("搜索框", "#q")]:
        failed.append(f"on_save 收到的名字/选择器不对：{saved}")
    if not win32gui.IsWindow(hwnd):
        failed.append("保存报错后框被关了（错误被吞）")
    if _control_text(hwnd, _ID_ERROR) != "这个名字被占了":
        failed.append(f"错误行没回显：{_control_text(hwnd, _ID_ERROR)!r}")
    if closes:
        failed.append(f"保存报错却触发了 on_close：{closes}")

    # 4. Enter 键真实路径保存（这次 on_save 放行 → 关框 + on_close(True)）
    win32gui.PostMessage(hwnd, win32con.WM_KEYDOWN, win32con.VK_RETURN, 0)
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and not closes:
        time.sleep(0.05)
    if closes != [True]:
        failed.append(f"Enter 保存后 on_close 不对：{closes}")
    if saved != [("搜索框", "#q"), ("搜索框", "#q")]:
        failed.append(f"Enter 没触发第二次保存：{saved}")
    if dialog.last_error != "这个名字被占了":
        failed.append(f"last_error 没留痕：{dialog.last_error!r}")

    # 3. 空名不走 on_save，本地就拦下
    dialog2 = ElementEditDialog()
    calls: list[tuple[str, str]] = []
    dialog2.show(title="t", name="", meta="", on_save=lambda n, s: calls.append((n, s)),
                 on_close=closes.append)
    hwnd2 = _wait_hwnd(dialog2)
    if not hwnd2:
        failed.append("第二个编辑框没建起来")
    else:
        win32gui.SendMessage(
            win32gui.GetDlgItem(hwnd2, win32con.IDOK), win32con.BM_CLICK, 0, 0
        )
        time.sleep(0.3)
        if calls:
            failed.append(f"空名也调了 on_save：{calls}")
        if _control_text(hwnd2, _ID_ERROR) != "名字不能为空":
            failed.append(f"空名没本地拦截：{_control_text(hwnd2, _ID_ERROR)!r}")

        # 5. Esc 真实按键路径 → on_close(False)；同时开个浮窗验证两类共存
        float_win = CaptureFloat()
        float_win.show_notice("共存", "浮窗与编辑框同时存在")
        win32gui.PostMessage(hwnd2, win32con.WM_KEYDOWN, win32con.VK_ESCAPE, 0)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and len(closes) < 2:
            time.sleep(0.05)
        if closes != [True, False]:
            failed.append(f"Esc 取消后 on_close 不对：{closes}")
        if win32gui.IsWindow(hwnd2):
            failed.append("Esc 后编辑框还活着")
        float_win.hide()
        time.sleep(0.3)

    # 6. 层级形态（影刀式）：初始预览=主 css；勾掉层级 ⇒ 预览重组；目标级锁勾选；
    #    保存拿到的是重组结果而不是手写文本（M69 的核心链路）。
    saved_tree: list[tuple[str, str]] = []
    levels = selector_tree.levels_from_path([
        {"tag": "div", "id": "app", "classes": ["page"], "nthOfType": None,
         "fragment": "#app"},
        {"tag": "li", "id": None, "classes": ["item"], "nthOfType": 2,
         "fragment": "li.item:nth-of-type(2)"},
    ])
    dialog3 = ElementEditDialog()
    dialog3.show(
        title="捕获到元素", name="元素1", levels=levels, info="网页元素 · 命中 1 个",
        on_save=lambda n, s: saved_tree.append((n, s)) or None,
        on_close=closes.append,
    )
    hwnd3 = _wait_hwnd(dialog3)
    if not hwnd3:
        failed.append("层级形态编辑框没建起来")
    else:
        _client_checks(hwnd3, failed)
        if _control_text(hwnd3, _ID_PREVIEW) != "#app > li.item:nth-of-type(2)":
            failed.append(f"初始预览不对：{_control_text(hwnd3, _ID_PREVIEW)!r}")
        if not win32gui.GetDlgItem(hwnd3, _ID_LEVEL_BASE):
            failed.append("层级 checkbox 没建")
        box1 = win32gui.GetDlgItem(hwnd3, _ID_LEVEL_BASE + 1)
        if win32gui.IsWindowEnabled(box1):
            failed.append("目标级 checkbox 没禁用（勾掉目标级就没有定位了）")
        # 模拟用户勾掉第一层：控件先变状态，再通知父窗（与真实点击同路径）
        box0 = win32gui.GetDlgItem(hwnd3, _ID_LEVEL_BASE)
        win32gui.SendMessage(box0, win32con.BM_SETCHECK, 0, 0)
        win32gui.SendMessage(hwnd3, win32con.WM_COMMAND, _ID_LEVEL_BASE, 0)
        time.sleep(0.3)
        if _control_text(hwnd3, _ID_PREVIEW) != "li.item:nth-of-type(2)":
            failed.append(f"勾掉层级后预览没重组：{_control_text(hwnd3, _ID_PREVIEW)!r}")
        win32gui.SendMessage(
            win32gui.GetDlgItem(hwnd3, win32con.IDOK), win32con.BM_CLICK, 0, 0
        )
        time.sleep(0.3)
        if saved_tree != [("元素1", "li.item:nth-of-type(2)")]:
            failed.append(f"层级形态保存结果不对：{saved_tree}")
        if closes != [True, False, True]:
            failed.append(f"层级形态保存后 on_close 不对：{closes}")
    return failed


def main() -> int:
    failed = _probe()
    if failed:
        print("FAIL", "；".join(failed))
        return 1
    print("PASS 编辑框控件齐、中文名保存、错误留框、Enter 提交、Esc 取消、与浮窗共存")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
