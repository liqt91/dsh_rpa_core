"""一键启动 PPX 原型窗口；窗口完全跑到所有屏幕之外时把它救回来。

**这个脚本不再假设只有一块屏幕。** 曾经的版本用 `SPI_GETWORKAREA`（只返回主屏）判「窗口是否
可见」，在这台接了两块屏的机器上把「窗口在副屏」误判成「窗口在屏幕外」，于是每秒把用户的
窗口从副屏拽回主屏 —— 用户把窗口拖过去，它就自己跳回来。

实测布局（必须按多屏算，不能取主屏）：主屏 work `(0,0,1920,1040)`，副屏在主屏**上方**
work `(0,-1080,1920,-40)`，虚拟桌面 `1920x2160`、原点 `(0,-1080)`。PPX 把窗口居中到副屏时
算出 `y ≈ -1080 + (1080-777)/2 = -929`，与实测 `-932 / -959` 一致 —— **它没错，是判据错**。

现在的规则：
- 窗口与**任一块**屏幕工作区的交集 ≥ 窗口面积 50% ⇒ 视为可见，**一个字都不动**；
- 否则救回到「与它交集最大的那块屏幕」中央（完全无交集则主屏），尺寸夹进该屏工作区。
- `--primary` 显式要求放主屏（用户说「我要它在主屏」时才用）。

用法：
    ./.venv/Scripts/python.exe run_probe.py            # 不干预多屏布局
    ./.venv/Scripts/python.exe run_probe.py --primary  # 强制放主屏中央

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


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("rcMonitor", wt.RECT),
        ("rcWork", wt.RECT),
        ("dwFlags", wt.DWORD),
    ]


MONITORENUMPROC = ctypes.WINFUNCTYPE(
    wt.BOOL, wt.HMONITOR, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM
)

Box = tuple[int, int, int, int]  # left, top, right, bottom


def work_areas() -> list[Box]:
    """所有显示器的**工作区**（去掉任务栏），主屏排第一。"""
    found: list[tuple[Box, bool]] = []

    def collect(hmon, _hdc, _lprc, _data) -> bool:
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            work = info.rcWork
            box = (work.left, work.top, work.right, work.bottom)
            found.append((box, bool(info.dwFlags & 1)))  # MONITORINFOF_PRIMARY
        return True

    callback = MONITORENUMPROC(collect)  # 必须留引用，否则回调被 GC 掉
    user32.EnumDisplayMonitors(None, None, callback, 0)

    if not found:
        rect = wt.RECT()
        if user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
            return [(rect.left, rect.top, rect.right, rect.bottom)]
        return [(0, 0, 1920, 1040)]

    found.sort(key=lambda item: not item[1])  # 主屏在前
    return [box for box, _primary in found]


def primary_box() -> Box:
    return work_areas()[0]


def overlap(a: Box, b: Box) -> int:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0


def visible_on_any_screen(rect: Box, areas: list[Box], need: float = 0.35) -> bool:
    """判断「用户能不能看见 / 能不能抓回来」。宁可少动，不要抢用户的窗口。

    两条任一成立就算可见：
    1. **合并**在所有屏幕上的可见面积 ≥ `need`。必须合并算：窗口跨两块屏时，
       每块屏单独看都只有一半，按单屏判会把「跨屏摆着」误判成「跑飞了」。
    2. 窗口顶部能抓：高 40px 的标题条与某块工作区横向重叠 ≥ 100px、纵向也真的露出
       一块（哪怕只有 40px），用户就能用鼠标把它拖回来。
    """
    area = max(1, (rect[2] - rect[0]) * (rect[3] - rect[1]))
    covered = sum(overlap(rect, screen) for screen in areas)
    if covered >= area * need:
        return True

    title_bar = (rect[0], rect[1], rect[2], min(rect[3], rect[1] + 40))
    for screen in areas:
        shared = overlap(title_bar, screen)
        if shared > 0:
            width = min(title_bar[2], screen[2]) - max(title_bar[0], screen[0])
            if width >= 100:
                return True
    return False


def best_screen(rect: Box, areas: list[Box]) -> Box:
    """与窗口交集最大的那块屏；完全没有交集时给主屏。"""
    scored = [(overlap(rect, screen), screen) for screen in areas]
    return max(scored, key=lambda item: item[0])[1]


def center_in(rect: Box, screen: Box) -> Box:
    """把窗口尺寸夹进屏幕，然后居中。"""
    width = min(rect[2] - rect[0], screen[2] - screen[0])
    height = min(rect[3] - rect[1], screen[3] - screen[1])
    x = screen[0] + max(0, (screen[2] - screen[0] - width) // 2)
    y = screen[1] + max(0, (screen[3] - screen[1] - height) // 2)
    return (x, y, x + width, y + height)


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


def current_rect(hwnd: int) -> Box | None:
    rect = wt.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return (rect.left, rect.top, rect.right, rect.bottom)


def apply(hwnd: int, target: Box) -> None:
    left, top, right, bottom = target
    user32.MoveWindow(hwnd, left, top, right - left, bottom - top, True)
    user32.SetForegroundWindow(hwnd)


def rescue(hwnd: int, force_primary: bool = False, tries: int = 4) -> str:
    """窗口完全看不见时才动它；已经可见就原样返回。"""
    areas = work_areas()
    moved = False
    report = "取窗口矩形失败"
    for _ in range(tries):
        rect = current_rect(hwnd)
        if rect is None:
            return "取窗口矩形失败，跳过位置修正"

        if not force_primary and visible_on_any_screen(rect, areas):
            if moved:
                return report + "（复查已可见）"
            return f"窗口可见于 {describe(rect, areas)}，不动它"

        screen = primary_box() if force_primary else best_screen(rect, areas)
        target = center_in(rect, screen)
        apply(hwnd, target)
        moved = True
        report = (
            f"窗口原本在 ({rect[0]},{rect[1]}) 不完整落在任何屏幕内，"
            f"已移到 {'主屏' if force_primary else '最近屏幕'} 中央 {target}"
        )
        time.sleep(0.6)
        if force_primary:
            break
    return report


def describe(rect: Box, areas: list[Box]) -> str:
    for index, screen in enumerate(areas):
        if overlap(rect, screen) > 0:
            name = "主屏" if index == 0 else f"副屏{index}"
            return f"{name} ({rect[0]},{rect[1]})-({rect[2]},{rect[3]})"
    return f"({rect[0]},{rect[1]})"


def watch(hwnd: int, proc: subprocess.Popen, force_primary: bool, interval: float = 1.0) -> int:
    """只在窗口完全看不见时出手。

    注意：不要在这里「定期归位」。用户会手动把窗口拖到任何一块屏，动它就是抢他的窗口。
    """
    last_report = ""
    while proc.poll() is None:
        time.sleep(interval)
        current = user32.FindWindowW(None, TITLE)
        if not current:
            continue
        if force_primary:
            continue  # 显式要求主屏时，启动那一次已经放过，不再纠缠
        rect = current_rect(current)
        if rect is None:
            continue
        if visible_on_any_screen(rect, work_areas()):
            continue
        report = rescue(current)
        if report != last_report:
            print(f"[救窗] {report}", flush=True)
            last_report = report
    return 0


def main() -> int:
    force_primary = "--primary" in sys.argv[1:]
    ppx = HERE / ".venv" / "Scripts" / "ppx.exe"
    if not ppx.is_file():
        print(f"找不到 {ppx}，先在 ppx_probe 目录建 venv 并装 ppx-py", flush=True)
        return 1

    areas = work_areas()
    print("屏幕工作区：" + "；".join(str(box) for box in areas), flush=True)

    log = LOG.open("w", encoding="utf-8")
    proc = subprocess.Popen([str(ppx), "dev"], cwd=APP, stdout=log, stderr=subprocess.STDOUT)
    print(f"ppx dev 已启动 (pid {proc.pid})，日志 {LOG.name}", flush=True)

    try:
        if not wait_vite():
            print("前端 60s 内没就绪，看日志：", LOG, flush=True)
            return 1
        hwnd = wait_window()
        if not hwnd:
            print("窗口 40s 内没出现（前端起来了但没有窗口），看日志：", LOG, flush=True)
            return 1
        time.sleep(2.0)  # 等 pywebview 自己那次定位落地
        print(rescue(hwnd, force_primary=force_primary), flush=True)
        print("窗口已就绪；拖到任意一块屏幕都行，关窗或 Ctrl-C 结束", flush=True)
        return watch(hwnd, proc, force_primary)
    except KeyboardInterrupt:
        print("\n收到 Ctrl-C，关闭窗口", flush=True)
        return 0
    finally:
        log.close()
        if proc.poll() is None:
            proc.terminate()


if __name__ == "__main__":
    sys.exit(main())
