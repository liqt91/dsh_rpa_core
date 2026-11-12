"""浮窗冒烟：真开窗验四件事（前三件都对应用户报障，第四件是同类复用的回归）。

1. **不是空白**：``win32gui.CreateFont`` 不存在 ⇒ WM_PAINT 抛异常 ⇒ 只剩白底。
   判据是**暗像素计数**（白底没有暗像素，画上三行字必然有）。
2. **躲鼠标的定时器真的在跑**：``win32gui.SetTimer`` 同样不存在 ⇒ 40ms 定时器从没
   建立。这里把窗摆到鼠标正下方，等 300ms 看它是否自己翻到另一角（不动用户的鼠标）。
3. **提示形态**（2026-11-12 改版）：环境没就绪这类消息只在浮窗里说。纯告知不显示
   按钮；带关闭回调时按钮文案是「关闭」。
4. **第二个窗口归第二个实例管**：窗口类按类名在进程内只注册一次，WNDPROC 若绑死
   第一个实例，第二次开浮窗就再也不刷新（窗开着、内容不动、日志无异常）。这里的
   判据是「第二个窗升级成捕获形态后按钮文案变成『取消捕获』」。
5. **每行字各占一条墨迹带**（2026-11-12 补）：``DrawText`` 要的是
   ``(left, top, right, bottom)`` 不是 ``(left, top, width, height)``，按后者传会让
   第二行起 ``bottom < top``，GDI 把几行字做垂直居中叠进同一片区域。判据是
   **行带结构**（连续暗像素的 y 区间）而不是暗像素总数——叠在一起时总数照样很足，
   这正是上半年的判据漏掉的形态。同时查右边界是否被按钮切掉。

跑法（会闪一下窗，约 3s）：
    .venv/Scripts/python.exe .harness/demo/ppx_probe/_float_probe.py
"""

from __future__ import annotations

import time

from rpa_core.capture_kit import engine
from rpa_core.capture_kit import float_window as _fw


class _Ready:
    desktop_offline = False
    extension_offline = False
    extension_unresponsive = False


def _texts():
    session = _Ready()
    return (
        engine.gesture_hint(session),
        engine.web_status(session)[0],
        engine.desktop_status(session)[0],
    )


# 按钮左边界：文字区到这儿就该收住（多留 4px 给抗锯齿）
_BTN_LEFT = _fw._WIDTH - _fw._BTN_W - 16
_INK_X1 = _BTN_LEFT - 4
# 无按钮（纯告知）时文字区宽 _WIDTH-28，左起 14 ⇒ 右边界留 4px 余量
_INK_X1_NO_BTN = 14 + (_fw._WIDTH - 28) - 4


def _ink_bands(image, x0: int = 8, x1: int = _INK_X1) -> list[tuple[int, int]]:
    """把左侧文字区切成「墨迹带」：连续有暗像素的 y 区间，一条 = 一行字。

    为什么不用暗像素总数判「有没有画上字」：几行字叠在一起时总数照样很足（实测
    叠成一坨仍有 2000+ 暗像素），只有**行与行之间留白**才证明它们没压在一起。
    """
    gray = image.convert("L")
    px = gray.load()
    bands: list[tuple[int, int]] = []
    start = None
    for y in range(4, gray.height - 4):  # 去掉 WS_BORDER 的上下边线
        hit = any(px[x, y] < 130 for x in range(x0, x1))
        if hit and start is None:
            start = y
        elif not hit and start is not None:
            bands.append((start, y - 1))
            start = None
    if start is not None:
        bands.append((start, gray.height - 5))
    return bands


def _ink_right(image, x0: int = 8, x1: int = _INK_X1) -> int:
    """文字区里最靠右的暗像素 x（判「有没有被按钮一刀切」用）。"""
    gray = image.convert("L")
    px = gray.load()
    best = 0
    for y in range(4, gray.height - 4):
        for x in range(x0, x1):
            if px[x, y] < 130:
                best = max(best, x)
    return best


def _wrap_checks() -> list[str]:
    """折行的**纯函数**判据：真折了、每行不超宽、不超过窗内能放的行数。"""
    failed: list[str] = []
    avail = _fw._TEXT_W - 6
    detail = "浏览器还没连上（已装插件：edge）；先打开装好插件的浏览器再试"
    lines = _fw.CaptureFloat._wrap_px(detail, avail)
    if len(lines) < 2:
        failed.append(f"长中文说明没折行（{len(lines)} 行，{len(detail)} 字）")
    # 每行字数上限按**最小**字宽(8px)算：超了必然出界，不超则不误报
    over = [line for line in lines if len(line) > avail // 8]
    if over:
        failed.append(f"折行后仍有超宽行：{over[0]!r}（{len(over[0])} 字 > {avail // 8}）")
    if len(lines) > 3:
        failed.append(f"折行出了 {len(lines)} 行，渲染只画前 3 行，尾巴会消失")
    return failed


def _layout_checks(
    image, expect_lines: int, label: str, limit: int = _INK_X1
) -> list[str]:
    """渲染层判据：行数对得上（没叠）、右边界收在可画区域里（没被切）。"""
    failed: list[str] = []
    bands = _ink_bands(image, x1=limit)
    if len(bands) != expect_lines:
        failed.append(
            f"{label}应有 {expect_lines} 行各自成带，实测 {len(bands)} 条：{bands}"
            "（多条挤成一条 = 纵向叠字）"
        )
    right = _ink_right(image, x1=limit)
    if right >= limit - 1:
        failed.append(f"{label}文字右边界 {right} 顶到可画区右界 {limit}（被切）")
    return failed


def _shot(win, name: str):
    """按窗口矩形抓一张并存盘（人眼复核用）。"""
    import win32gui
    from PIL import ImageGrab

    image = ImageGrab.grab(bbox=win32gui.GetWindowRect(win._hwnd))
    image.save(f".harness/spike/{name}.png")
    return image


def _probe_notice() -> list[str]:
    """提示形态 + 同类名第二个窗口。"""
    import win32gui

    failed: list[str] = []
    detail = "浏览器还没连上（已装插件：edge）；先打开装好插件的浏览器再试"

    plain = _fw.CaptureFloat()
    plain.show_notice("还不能开始", detail)
    time.sleep(0.6)
    if not plain._hwnd:
        failed.append("提示窗没建起来")
    else:
        if plain._button_visible:
            failed.append("纯告知却显示了按钮")
        expect = 1 + len(_fw.CaptureFloat._wrap_px(detail, _fw._WIDTH - 28 - 6)[:3])
        failed.extend(
            _layout_checks(
                _shot(plain, "_notice_a"), expect, "纯告知提示", _INK_X1_NO_BTN
            )
        )

    second = _fw.CaptureFloat()
    second.show_notice("删不掉", "「搜索框」还被流程里 2 处引用着，先改掉引用再删",
                       on_close=second.hide)
    time.sleep(0.6)
    if not second._hwnd:
        failed.append("第二个提示窗没建起来")
    else:
        if win32gui.GetWindowText(second._button) != "关闭":
            failed.append(f"关闭按钮文案={win32gui.GetWindowText(second._button)!r}")
        failed.extend(_layout_checks(_shot(second, "_notice_c"), 2, "带关闭按钮的提示"))

    # 同一实例原地升级成捕获形态（环境检查完就接着抓，不该闪两个窗）
    second.show("移动鼠标框选：Ctrl+Click 或右键捕获，Esc 取消",
                "网页：就绪", "桌面：就绪", True, True, on_cancel=lambda: None)
    time.sleep(0.4)
    if second._mode != "capture":
        failed.append("升级后不是捕获形态")
    elif win32gui.GetWindowText(second._button) != "取消捕获":
        failed.append(
            f"第二个窗没被自己驱动（按钮文案={win32gui.GetWindowText(second._button)!r}）"
        )
    else:
        failed.extend(_layout_checks(_shot(second, "_notice_b"), 3, "升级后的捕获形态"))
    plain.hide()
    second.hide()
    time.sleep(0.3)
    return failed


def main() -> int:
    float_win = _fw.CaptureFloat()
    gesture, web, desktop = _texts()
    float_win.show(gesture, web, desktop, True, True)
    time.sleep(1.0)
    hwnd = float_win._hwnd
    if not hwnd:
        print("FAIL 窗没起来（hwnd=None）")
        return 1

    import win32api
    import win32con
    import win32gui
    from PIL import ImageGrab

    rect = win32gui.GetWindowRect(hwnd)
    image = ImageGrab.grab(bbox=rect)
    image.save(".harness/demo/ppx_probe/_float_shot.png")
    gray = image.convert("L").load()
    dark = sum(
        1
        for y in range(image.height)
        for x in range(image.width)
        if gray[x, y] < 120
    )
    print("rect", rect, "size", image.size, "dark_px", dark)

    # 把窗摆到鼠标正下方（不移动鼠标），等定时器把它翻走
    cx, cy = win32api.GetCursorPos()
    width, height = rect[2] - rect[0], rect[3] - rect[1]
    win32gui.SetWindowPos(
        hwnd, win32con.HWND_TOPMOST, cx - width // 2, cy - height // 2, width, height,
        win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW,
    )
    time.sleep(0.4)
    after = win32gui.GetWindowRect(hwnd)
    still_over_cursor = (
        after[0] - 16 <= cx <= after[2] + 16 and after[1] - 16 <= cy <= after[3] + 16
    )
    print("cursor", (cx, cy), "after", after, "still_over_cursor", still_over_cursor)

    float_win.hide()
    time.sleep(0.3)

    failed = []
    if dark < 200:
        failed.append("浮窗没有文字（暗像素过少）")
    if still_over_cursor:
        failed.append("躲鼠标没生效（定时器没跑）")
    failed.extend(_layout_checks(image, 3, "捕获形态（手势+网页+桌面）"))
    failed.extend(_wrap_checks())
    failed.extend(_probe_notice())
    if failed:
        print("FAIL", "；".join(failed))
        return 1
    print("PASS 浮窗有文字、会躲鼠标、逐行不叠字、提示形态与同类复用都正常")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
