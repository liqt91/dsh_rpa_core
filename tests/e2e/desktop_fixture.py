"""Windows 桌面 fixture 应用的公共装配（编译 / 启动 / 抢前台 / 清理）。

`testapps/desktop/Program.cs` 是 uia 与 win32 两个桌面后端共用的靶子程序：测试时用
.NET Framework 自带的 `csc.exe` 现场编译成 `RpaCoreDesktopDemo.exe`（仓库里不放二进制）。

这里只放**装配**（与「测什么」无关的那部分），三个消费方共用：
`test_uia_desktop.py`（UIA 全链路）、`test_desktop_pause_resume.py`（暂停/继续）。
复制一份的下场是「同一件事两处口径」——本仓已经在别的片上吃过这个亏。

运行环境要求：Windows + .NET Framework v4.0.30319 的 `csc.exe`。
两个测试文件都通过 `RPA_DESKTOP_E2E=1` 显式开启（真实桌面 E2E 会弹窗抢前台，
缺省不进默认门禁，见 `tests/conftest.py`）。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# .NET Framework 的 C# 编译器（随 Windows 自带，不需要 SDK）。
CSC = (
    Path(os.environ.get("WINDIR", r"C:\Windows"))
    / "Microsoft.NET"
    / "Framework64"
    / "v4.0.30319"
    / "csc.exe"
)

# 主窗口标题必须稳定：`desktop.attachWindow` 靠它精确匹配。
APP_TITLE = "RPA Core Desktop Demo"
DIALOG_TITLE = "RPA Core Desktop Dialog"


def fixture_unavailable_reason() -> str | None:
    """不适合跑桌面 fixture 的原因；可用时返回 None。"""
    if sys.platform != "win32":
        return "Windows desktop fixture requires Windows"
    if not CSC.exists():
        return "UIA fixture requires the .NET Framework compiler"
    return None


def compile_demo_app(target_dir: Path) -> Path:
    exe = target_dir / "RpaCoreDesktopDemo.exe"
    subprocess.run(
        [
            str(CSC),
            "/nologo",
            "/target:winexe",
            f"/out:{exe}",
            "/r:System.Windows.Forms.dll",
            "/r:System.Drawing.dll",
            str(ROOT / "testapps" / "desktop" / "Program.cs"),
        ],
        check=True,
        capture_output=True,
    )
    return exe


def wait_for_window(title: str, timeout: float = 15.0) -> int:
    """轮询等待窗口出现，返回 HWND。用 Win32 `FindWindowW`（毫秒级）探测，避免
    pywinauto UIA Desktop 首次初始化的长阻塞（实测可达 60s）拖垮轮询超时。"""
    find_window = ctypes.windll.user32.FindWindowW
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        hwnd = find_window(None, title)
        if hwnd:
            return int(hwnd)
        time.sleep(0.2)
    raise TimeoutError(f"window did not appear: {title}")


def warmup_uia(title: str) -> None:
    """触发一次 pywinauto UIA 初始化，只对目标窗口构造单窗口 UIAWrapper。

    `DesktopExecutor` 每个命令外层有 15s 的 asyncio 超时，而 pywinauto UIA backend
    在进程内首次初始化实测可达 ~60s（慢 provider / 首次 provider 连接）。若首启发生在
    executor 的命令里会被 15s 掐断 → TIMEOUT。此处提前把首启消耗掉，后续同进程内的
    UIA 调用走进程级缓存（实测 ~125ms）。

    与 executor 的 attach 路径一致：先用 Win32 `FindWindowW` 定位 HWND，再从单窗口构造
    UIAWrapper，避免 `Desktop(backend="uia").windows()` 全桌面枚举（慢 provider 会触发
    `RPC_E_SERVERCALL_RETRYLATER` 噪音）。
    """
    import pythoncom
    from pywinauto.controls.uiawrapper import UIAWrapper
    from pywinauto.uia_element_info import UIAElementInfo

    hwnd = ctypes.windll.user32.FindWindowW(None, title)
    if not hwnd:
        raise RuntimeError(f"window did not appear: {title}")
    pythoncom.CoInitialize()
    try:
        UIAWrapper(UIAElementInfo(hwnd))
    finally:
        pythoncom.CoUninitialize()


def client_center(title: str) -> tuple[int, int] | None:
    """靶子窗口客户区中心的屏幕坐标；窗口不在时返回 `None`。

    `desktop.drag` 的 `targetX`/`targetY` 是**绝对屏幕坐标**，而这个坐标写不死：靶子用
    `FormStartPosition.CenterScreen`，可「屏幕中心」取决于会话的虚拟屏布局——实测本机
    1920x1080 的远程会话把窗口放在了 y=-765（主显示器**上方**那块区域）里。照抄任何
    常量，要么拖拽终点落在窗口外、要么把真实鼠标甩到桌面上无关的位置。
    """
    user32 = ctypes.windll.user32
    hwnd = user32.FindWindowW(None, title)
    if not hwnd:
        return None
    rect = ctypes.wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    origin = ctypes.wintypes.POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        return None
    return (
        origin.x + (rect.right - rect.left) // 2,
        origin.y + (rect.bottom - rect.top) // 2,
    )


def force_foreground(title: str) -> None:
    """强制把目标窗口置前台（`AttachThreadInput` 绕过前台锁）。

    全量套件内 UIA 用例排在一堆真实 Chromium e2e 之后，前台焦点常被残留窗口占用；
    UIA `SetFocus` 只设置应用内键盘焦点，`type_keys` 的 SendInput 会落到前台窗口
    → 输入丢失（门禁内抖、单跑即过的根因）。运行前显式抢回前台。
    """
    user32 = ctypes.windll.user32
    hwnd = user32.FindWindowW(None, title)
    if not hwnd:
        return
    foreground = user32.GetForegroundWindow()
    current_tid = ctypes.windll.kernel32.GetCurrentThreadId()
    foreground_tid = user32.GetWindowThreadProcessId(foreground, None)
    target_tid = user32.GetWindowThreadProcessId(hwnd, None)
    user32.AttachThreadInput(current_tid, foreground_tid, True)
    user32.AttachThreadInput(current_tid, target_tid, True)
    try:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        user32.SetActiveWindow(hwnd)
        user32.SetFocus(hwnd)
    finally:
        user32.AttachThreadInput(current_tid, target_tid, False)
        user32.AttachThreadInput(current_tid, foreground_tid, False)


def kill_demo_apps() -> None:
    subprocess.run(
        ["taskkill", "/F", "/IM", "RpaCoreDesktopDemo.exe"],
        capture_output=True,
        check=False,
    )
