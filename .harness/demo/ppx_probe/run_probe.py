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
    ./.venv/Scripts/python.exe run_probe.py --reuse    # 端口上已有前端时复用它

Ctrl-C 退出；窗口关掉后脚本随之结束。

**端口 5173 是硬约束。** 启动前会先探一次：空着才开新前端；被占着就报出占用者 PID 并退出
（除非 `--reuse`）。原因不是洁癖 —— `ppx dev` 起不了 vite 时**不报错退出**，只写一行
`[失败] 端口 5173 已被占用` 然后照样弹窗（连的是那个旧前端），脚本只看「http 通不通」就会
误报「已启动」。（真实来源：上一次 `ppx dev` 被强杀，vite 成了孤儿；关窗是干净的。）
"""

from __future__ import annotations

import ctypes
import socket
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


def someone_serving(port: int = PORT, timeout: float = 0.6) -> bool:
    """端口上有没有人在听。用 connect 判而不 bind：

    `bind` 会被 TIME_WAIT 的残留连接干扰（这台机器上关掉 vite 之后 5173 常留几条 TIME_WAIT），
    而 TIME_WAIT **不代表**有人服务。connect 成功才等价于「有 listener」。

    ★ 两个地址都要探。`vite` 不指定 host 时绑的是 **`::1`**（本轮实测验出来的：`urlopen
    (localhost)` 通、`connect(127.0.0.1)` 拒），只探 IPv4 会把「端口被占」判成「空着」，
    于是又起一个；而窗口里加载的是 `localhost`，可能正好落到别人那个前端上。
    """
    for family, address in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as probe:
                probe.settimeout(timeout)
                if probe.connect_ex((address, port)) == 0:
                    return True
        except OSError:
            continue
    return False


def listening_pid(port: int = PORT) -> str:
    """谁在听这个端口（只用于报错文案；取不到就返回 "?"，不影响判断）。

    ★ `text=True` 单独用会炸：netstat 在这台机器上吐 **GBK**，而这里的解释器默认按 UTF-8 解码
    （`UnicodeDecodeError: 0xbb`），`run()` 内部的 reader 线程挂掉后 `stdout` 直接变 `None`，
    接着 `.splitlines()` 报 `AttributeError` —— 报错路径本身把报错逻辑搞崩一次（本轮实测）。
    所以显式指定 `mbcs`（Windows 的 ANSI 码页）并兜住 `None`。
    """
    try:
        done = subprocess.run(
            # 不带 `-p tcp`：那会把 IPv6 的监听行整片漏掉，绑在 `::1` 上的前端就查不到 PID
            ["netstat", "-ano"],
            capture_output=True,
            encoding="mbcs",
            errors="replace",
            timeout=10,
        )
    except Exception:
        return "?"
    if not done.stdout:
        return "?"
    for line in done.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0].upper() == "TCP" and parts[3].upper() == "LISTENING":
            if parts[1].endswith(f":{port}"):
                return parts[4]
    return "?"


def log_failure() -> str:
    """ppx dev 起不来时**不退出**，只在日志里留一行 `[失败] …`，窗口照样弹出来。

    ★ 这是本轮实测踩到的：5173 被上次留下的 vite 占着，`ppx dev` 报 `[失败] 端口 5173 已被占用`
    却继续开窗（连的是那个旧前端），而脚本只看「http 通不通」⇒ 打印「已启动 / 已就绪」，
    假绿整整一小时。所以判据必须**同时**看日志里的失败行，不能只看端口通。
    """
    try:
        text = LOG.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    for line in text.splitlines():
        if line.startswith("[失败]"):
            return line.strip()
    return ""


def wait_vite(proc: subprocess.Popen | None = None, timeout: float = 60.0) -> str:
    """等前端就绪。返回空串 = 就绪，否则返回「为什么没就绪」。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        failed = log_failure()
        if failed:
            return failed
        if proc is not None and proc.poll() is not None:
            return f"ppx dev 已退出（退出码 {proc.returncode}）"
        try:
            # ★ 必须用 localhost：`socket.create_connection` 会按 getaddrinfo 逐个地址试，
            #   而 `127.0.0.1` 是字面量、只打 IPv4 —— 前端绑在 ::1 时会一路等到超时，
            #   窗口却已经弹出来了（本轮实测：reuse 路径 rc=1 但窗口在）。
            with urllib.request.urlopen(f"http://localhost:{PORT}/", timeout=2):
                return ""
        except Exception:
            time.sleep(0.5)
    return f"前端 {timeout:.0f}s 内没就绪"


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
    reuse = "--reuse" in sys.argv[1:]
    ppx = HERE / ".venv" / "Scripts" / "ppx.exe"
    if not ppx.is_file():
        print(f"找不到 {ppx}，先在 ppx_probe 目录建 venv 并装 ppx-py", flush=True)
        return 1

    args = [str(ppx), "dev"]
    # 启动前先确认端口是空的：ppx dev 起不了 vite 时**不报错退出**，只写一行
    # `[失败] 端口 5173 已被占用` 然后照样开窗（连的可能是上一个没收干净的前端）。
    if someone_serving():
        pid = listening_pid()
        if reuse:
            print(f"端口 {PORT} 上已有前端（PID {pid}），按 --reuse 复用它", flush=True)
            args.append("--skip-frontend")
        else:
            print(f"端口 {PORT} 已被占用（PID {pid}）——多半是上次被强杀时留下的 vite。", flush=True)
            print(f"  先关掉：taskkill /F /PID {pid}", flush=True)
            print("  确认那个前端可用就复用：run_probe.py --reuse", flush=True)
            return 1

    # 既有的同名窗口一律不认：`wait_window` 只是按标题找窗，找不到也不知道是**谁的**。
    # 残留窗口会让脚本对着上次的窗口「就绪」一整天（本轮实测：对一个旧窗口看了一小时）。
    if user32.FindWindowW(None, TITLE):
        print(f"已经有一个「{TITLE}」窗口 —— 多半是上次残留的，先关掉它再启动", flush=True)
        return 1

    areas = work_areas()
    print("屏幕工作区：" + "；".join(str(box) for box in areas), flush=True)

    log = LOG.open("w", encoding="utf-8")
    proc = subprocess.Popen(args, cwd=APP, stdout=log, stderr=subprocess.STDOUT)
    print(f"ppx dev 已拉起 (pid {proc.pid})，日志 {LOG.name}", flush=True)

    try:
        reason = wait_vite(proc)
        if reason:
            print(f"前端没就绪：{reason}", flush=True)
            print("日志：", LOG, flush=True)
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
        # 关窗这条路径**不用**我们收端口：ppx dev 自己会把 vite 收掉（负向验证过 ——
        # 把清理整段拿掉，关窗后 5173 照样腾空）。它只在**被强杀**时才留下孤儿 vite，
        # 那种情况这里也来不及跑；兜底就是上面启动前的那次预检。
        if proc.poll() is None:
            proc.terminate()


if __name__ == "__main__":
    sys.exit(main())
