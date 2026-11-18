"""M95 取证探针 2：用 pywinauto 读当前 Shell_TrayWnd 下的 Button 属性。

确认元素2 存的 automationId=4103 / name=搜索 是否还在；若 UIA 树为空，
再量一下本进程完整性级别，排除 Low 进程跨进程查询被拒的假阴性。
"""
from __future__ import annotations

import time


def integrity_level() -> str:
    import ctypes
    from ctypes import wintypes

    class TOKEN_ELEVATION_TYPE(ctypes.Structure):
        _fields_ = [("ElevationType", ctypes.c_int)]

    kernel = ctypes.windll.kernel32
    token = wintypes.HANDLE()
    if not kernel.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        return "unknown"
    try:
        info = TOKEN_ELEVATION_TYPE()
        size = wintypes.DWORD()
        if kernel.GetTokenInformation(token, 18, ctypes.byref(info), ctypes.sizeof(info), ctypes.byref(size)):
            return {1: "default", 2: "full", 3: "limited"}.get(info.ElevationType, "?")
    finally:
        kernel.CloseHandle(token)
    return "unknown"


def main() -> None:
    print(f"integrity={integrity_level()}")
    from pywinauto import Desktop

    desktop = Desktop(backend="uia")
    t0 = time.perf_counter()
    trays = desktop.windows(class_name="Shell_TrayWnd")
    print(f"Shell_TrayWnd: {len(trays)} 个  用时 {time.perf_counter() - t0:.2f}s")
    for tray in trays:
        print(f"  hwnd={tray.handle} rect={tray.rectangle()}")
        t0 = time.perf_counter()
        buttons = tray.descendants(control_type="Button")
        print(f"  Button 数={len(buttons)}  用时 {time.perf_counter() - t0:.2f}s")
        for button in buttons[:40]:
            info = button.element_info
            print(
                f"    aid={info.automation_id!r} name={info.name!r} "
                f"class={info.class_name!r} rect={button.rectangle()}"
            )


if __name__ == "__main__":
    main()
