"""一键启动 PPX 原型窗口，并把窗口钳制到主屏工作区内。

为什么需要这个脚本：PPX 只给 webview 传 width/height，不传 x/y（`ppx_py/application.py`
里 `create_window` 没有 position 参数，也不记忆上次位置），位置由 pywebview/WinForms
自己居中。实测出现过 `GetWindowRect().top = -932` —— 窗口整个在屏幕上方外侧，
进程正常、日志正常、屏幕上什么都没有。

用法：
    ./.venv/Scripts/python.exe run_probe.py

Ctrl-C 退出；窗口关掉后脚本随之结束。
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import time
import urllib.request
from ctypes import wintypes as wt
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE / "app"
TITLE = "RPA Probe"
PORT = 5173
LOG = HERE / "_ppx_run.log"

user32 = ctypes.WinDLL("user32", use_last_error=True)
# 不设 restype 的话 hwnd 会按 c_int 处理，超过 2^31 就被截断
user32.FindWindowW.restype = wt.HWND
user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.MoveWindow.argtypes = [
    wt.HWND,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wt.BOOL,
]
user32.SetForegroundWindow.argtypes = [wt.HWND]


def screen_box() -> tuple[int, int, int, int]:
    """主屏工作区（去掉任务栏）的左、上、右、下。"""
    rect = wt.RECT()
    if not user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):  # SPI_GETWORKAREA
        return (0, 0, 1920, 1040)
    return (rect.left, rect.top, rect.right, rect.bottom)


def wait_vite(timeout: float = 60.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=2):
                return True
        except Exception:
            time.sleep(0.5)
    return False


def wait_window(timeout: float = 40.0) -> int:
    deadline = time.time() + timeout
    while time.time() < deadline:
        hwnd = user32.FindWindowW(None, TITLE)
        if hwnd:
            return int(hwnd)
        time.sleep(0.5)
    return 0


def clamp_into_screen(hwnd: int, tries: int = 8) -> str:
    """反复确保窗口完整可见，返回最后一句人话描述。

    只做一次不够：pywebview 在 `created` 之后还会再定位一次，早移动会被覆盖。
    实测第一次 MoveWindow 到 (100,80) 后窗口又跑回 top=-959。
    """
    last = "未取到窗口矩形"
    moved = False
    for _ in range(tries):
        rect = wt.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return "取窗口矩形失败，跳过位置修正"

        left, top, right, bottom = screen_box()
        width, height = rect.right - rect.left, rect.bottom - rect.top
        fully_visible = (
            rect.left >= left
            and rect.top >= top
            and rect.right <= right
            and rect.bottom <= bottom
        )
        if fully_visible:
            if moved:
                return last + "（复查已可见）"
            return f"窗口位置正常 ({rect.left},{rect.top}) {width}x{height}"

        height = min(height, bottom - top)
        width = min(width, right - left)
        x = left + max(0, (right - left - width) // 2)
        y = top + max(0, (bottom - top - height) // 2)
        user32.MoveWindow(hwnd, x, y, width, height, True)
        user32.SetForegroundWindow(hwnd)
        moved = True
        last = (
            f"窗口原本在 ({rect.left},{rect.top})，已移到 ({x},{y}) {width}x{height}"
            f"（工作区 {left},{top}-{right},{bottom}）"
        )
        time.sleep(0.6)
    return last


def watch(hwnd: int, proc: subprocess.Popen, interval: float = 1.0) -> int:
    """守着窗口位置：只要出界就拉回，直到 ppx 进程退出。

    单次 clamp 不够。实测窗口被拉回 (320,131) 之后过一会儿又自己跳到 (593,-901)
    ——顶部正好在屏幕上方的窗口高度处。PPX 只给 webview 传 width/height、不传坐标
    （`ppx_py/application.py`），位置完全由下层自己算，触发条件未定位。
    与其追根因，不如守住可见性。
    """
    last_report = ""
    while proc.poll() is None:
        time.sleep(interval)
        current = user32.FindWindowW(None, TITLE)
        if not current:
            continue
        rect = wt.RECT()
        if not user32.GetWindowRect(current, ctypes.byref(rect)):
            continue
        left, top, right, bottom = screen_box()
        visible = (
            rect.left >= left
            and rect.top >= top
            and rect.right <= right
            and rect.bottom <= bottom
        )
        if visible:
            continue
        report = clamp_into_screen(current)
        if report != last_report:
            print(f"[守窗] {report}", flush=True)
            last_report = report
    return 0


def main() -> int:
    ppx = HERE / ".venv" / "Scripts" / "ppx.exe"
    if not ppx.is_file():
        print(f"找不到 {ppx}，先在 ppx_probe 目录建 venv 并装 ppx-py", flush=True)
        return 1

    log = LOG.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [str(ppx), "dev"], cwd=APP, stdout=log, stderr=subprocess.STDOUT
    )
    print(f"ppx dev 已启动 (pid {proc.pid})，日志 {LOG.name}", flush=True)

    try:
        if not wait_vite():
            print("前端 60s 内没就绪，看日志：", LOG, flush=True)
            return 1
        hwnd = wait_window()
        if not hwnd:
            print("窗口 40s 内没出现（前端起来了但没有窗口），看日志：", LOG, flush=True)
            return 1
        time.sleep(2.0)  # 等 pywebview 自己那一次定位落地，否则会被覆盖
        print(clamp_into_screen(hwnd), flush=True)
        print("窗口已就绪，关掉窗口或 Ctrl-C 结束", flush=True)
        return watch(hwnd, proc)
    except KeyboardInterrupt:
        print("\n收到 Ctrl-C，关闭窗口", flush=True)
        return 0
    finally:
        log.close()
        if proc.poll() is None:
            proc.terminate()


if __name__ == "__main__":
    sys.exit(main())
