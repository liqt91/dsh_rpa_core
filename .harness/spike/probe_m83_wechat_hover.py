"""M83 真机探针：走 GUI **实际那条路**——hover + image + F9，在微信窗口上抓模板。

与 M81 的区别：那条走 ``--point``（CLI 单点），这条走 ``hover``（PPX「按图捕获」
真正用的模式：浮层跟随鼠标、热键触发）。2026-11-17 维护者报障是「桌面腿的进程根本
没起来」，所以必须验 hover 这条腿，而不是再验一遍 point。

跑法（会移动鼠标到微信窗口并模拟按一次 F9，跑完还原鼠标位置）::

    ./.venv/Scripts/python.exe .harness/spike/probe_m83_wechat_hover.py

★ 本机（2026-11-17）实测**鼠标注入被拦**：``SetCursorPos`` 返回 0 却不移动，
``mouse_event`` 只能挪十几像素。这种情况下本脚本返回 SKIP（2）而不是 FAIL——
注入不进去不等于功能坏了。桌面腿能不能起来由 ``probe_m83_child_env.py`` 判定
（那条不需要鼠标）。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

_WECHAT_EXE_TOKENS = ("weixin.exe", "wechat.exe", "wxwork.exe")


def _process_path(pid: int) -> str:
    import win32api
    import win32con
    import win32process

    try:
        handle = win32api.OpenProcess(
            win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ, False, pid
        )
    except Exception:  # noqa: BLE001
        return ""
    try:
        return str(win32process.GetModuleFileNameEx(handle, 0))
    except Exception:  # noqa: BLE001
        return ""
    finally:
        try:
            win32api.CloseHandle(handle)
        except Exception:  # noqa: BLE001
            pass


def find_wechat_window() -> tuple[int, tuple[int, int, int, int]] | None:
    import win32gui
    import win32process

    best: tuple[int, tuple[int, int, int, int]] | None = None
    best_area = 0

    def cb(hwnd, _):
        nonlocal best, best_area
        if not win32gui.IsWindowVisible(hwnd):
            return True
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        exe = _process_path(pid).lower()
        if not any(token in exe for token in _WECHAT_EXE_TOKENS):
            return True
        rect = win32gui.GetWindowRect(hwnd)
        width, height = rect[2] - rect[0], rect[3] - rect[1]
        if width < 200 or height < 200:
            return True
        if width * height > best_area:
            best_area = width * height
            best = (hwnd, rect)
        return True

    win32gui.EnumWindows(cb, None)
    return best


def main() -> int:
    found = find_wechat_window()
    if found is None:
        print("SKIP：没找到微信窗口（先打开微信再跑）")
        return 2
    hwnd, (left, top, right, bottom) = found
    width, height = right - left, bottom - top
    # 落在窗口内、避开边缘 resize 区；这一带是标题/搜索区，视觉上有纹理。
    x = left + int(width * 0.55)
    y = top + int(height * 0.09)
    print(f"微信 hwnd={hwnd} rect=({left},{top},{right},{bottom}) 目标点=({x},{y})")

    import win32api

    origin = win32api.GetCursorPos()
    from rpa_core.capture.desktop import DesktopCaptureSession

    session = DesktopCaptureSession(
        hover=True, hybrid=True, image=True, timeout_seconds=25
    )
    fails: list[str] = []
    try:
        time.sleep(1.2)  # 等 agent 建好 overlay
        try:
            win32api.SetCursorPos((x, y))
        except Exception as exc:  # noqa: BLE001 - 注入被拦是环境问题，不是功能红
            print(f"SKIP：鼠标注入不可用（{exc}）—— 这条只能人工点")
            return 2
        if win32api.GetCursorPos() != (x, y):
            print("SKIP：SetCursorPos 被拦（光标没动）—— 这条只能人工点")
            return 2
        time.sleep(0.9)  # 让 hover 命中（30/100ms 节流 + UIA 首次冷启动）
        for _ in range(6):
            win32api.SetCursorPos((x, y))
            time.sleep(0.12)
        # 模拟 F9（agent 的默认热键）
        win32api.keybd_event(0x78, 0, 0, 0)
        time.sleep(0.05)
        win32api.keybd_event(0x78, 0, 2, 0)
        payload = session.pick(timeout_seconds=20)
    finally:
        session.close()
        try:
            win32api.SetCursorPos(origin)
        except Exception:  # noqa: BLE001
            pass

    if payload.get("error"):
        fails.append(f"捕获失败：{payload['error']} / {payload.get('stderrTail')}")
    if payload.get("timeout"):
        fails.append("超时：F9 没触发捕获（热键没送到，或 hover 没在跑）")
    locator = (payload.get("selector") or {}).get("locator") or {}
    print("kind:", payload.get("kind"), "| driver:", locator.get("driver"))
    print(
        "templateSchema:", locator.get("templateSchema"),
        "| templateSize:", locator.get("templateSize"),
        "| captureScale:", locator.get("captureScale"),
        "| sourceRect:", locator.get("sourceRect"),
    )
    if locator.get("driver") != "image":
        fails.append(f"不是图像模板：driver={locator.get('driver')}")
    if locator.get("templateSchema") != 2:
        fails.append(f"模板 schema 不是 2：{locator.get('templateSchema')}")
    if not locator.get("imageTemplate"):
        fails.append("没有 imageTemplate")
    rect = locator.get("sourceRect")
    if isinstance(rect, list) and len(rect) == 4:
        inside = (
            rect[0] <= x <= rect[0] + rect[2] and rect[1] <= y <= rect[1] + rect[3]
        )
        if not inside:
            fails.append(f"sourceRect {rect} 不包含目标点 ({x},{y})")

    if fails:
        print("\nFAIL")
        for item in fails:
            print(" -", item)
        return 1
    print("\nPASS：hover + image + F9 在微信窗口上抓到 V2 模板")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
