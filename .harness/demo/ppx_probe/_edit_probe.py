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

from rpa_core.capture_kit import CaptureFloat, ElementEditDialog
from rpa_core.capture_kit.edit_dialog import _ID_ERROR, _ID_META, _ID_NAME


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


def _probe() -> list[str]:
    failed: list[str] = []
    saved_names: list[str] = []
    closes: list[bool] = []

    # 第一轮：错误回显留框 → 改正后保存成功
    attempts = iter(["这个名字被占了", None])  # 第一次保存报错，第二次放行

    def on_save(name: str) -> str | None:
        saved_names.append(name)
        return next(attempts)

    dialog = ElementEditDialog()
    dialog.show(
        title="捕获到元素",
        name="元素1",
        meta="browser · #search-input\n命中 1 个 · 第 3 步",
        on_save=on_save,
        on_close=closes.append,
    )
    hwnd = _wait_hwnd(dialog)
    if not hwnd:
        return ["编辑框没建起来"]

    # 1. 控件齐 + 默认按钮
    for cid, label in ((_ID_NAME, "名称 EDIT"), (_ID_META, "meta EDIT"), (_ID_ERROR, "错误行")):
        if not win32gui.GetDlgItem(hwnd, cid):
            failed.append(f"缺控件：{label}")
    save_btn = win32gui.GetDlgItem(hwnd, win32con.IDOK)
    if not save_btn or win32gui.GetWindowText(save_btn) != "保存":
        failed.append(f"保存按钮文案={win32gui.GetWindowText(save_btn)!r}")
    style = win32gui.GetWindowLong(save_btn, win32con.GWL_STYLE)
    if not (style & win32con.BS_DEFPUSHBUTTON):
        failed.append("保存不是默认按钮（Enter 会落空）")
    if _control_text(hwnd, _ID_NAME) != "元素1":
        failed.append(f"建议名没预填：{_control_text(hwnd, _ID_NAME)!r}")

    # 2. 中文名保存 → 第一次返回错误 → 框留着、错误行红字、on_close 没动
    win32gui.SetWindowText(win32gui.GetDlgItem(hwnd, _ID_NAME), "搜索框")
    win32gui.SendMessage(save_btn, win32con.BM_CLICK, 0, 0)
    time.sleep(0.3)
    if saved_names != ["搜索框"]:
        failed.append(f"on_save 收到的名字不对：{saved_names}")
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
    if saved_names != ["搜索框", "搜索框"]:
        failed.append(f"Enter 没触发第二次保存：{saved_names}")
    if dialog.last_error != "这个名字被占了":
        failed.append(f"last_error 没留痕：{dialog.last_error!r}")

    # 3. 空名不走 on_save，本地就拦下
    dialog2 = ElementEditDialog()
    calls: list[str] = []
    dialog2.show(title="t", name="", meta="", on_save=calls.append, on_close=closes.append)
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
