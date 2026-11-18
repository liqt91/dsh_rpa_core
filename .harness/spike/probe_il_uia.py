"""One-shot diagnostic: report own integrity level and try a UIA hit-test.

Used to answer a single question -- does a Python child started from an
elevated PowerShell inherit the parent's integrity level? (In this repo the
Bash-launched interpreter is Low, so every cross-process UIA call returns
E_ACCESSDENIED and the whole capture path looks broken for the wrong reason.)
"""

import ctypes
from ctypes import wintypes

adv = ctypes.WinDLL("advapi32", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
adv.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
adv.GetTokenInformation.argtypes = [
    wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
]

tok = wintypes.HANDLE()
if adv.OpenProcessToken(k32.GetCurrentProcess(), 0x0008, ctypes.byref(tok)):
    size = wintypes.DWORD(0)
    adv.GetTokenInformation(tok, 25, None, 0, ctypes.byref(size))
    buf = ctypes.create_string_buffer(size.value)
    if adv.GetTokenInformation(tok, 25, buf, size.value, ctypes.byref(size)):
        sid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
        cnt = ctypes.cast(ctypes.c_void_p(sid + 1), ctypes.POINTER(ctypes.c_ubyte))[0]
        rid = ctypes.cast(ctypes.c_void_p(sid + 8 + 4 * (cnt - 1)),
                          ctypes.POINTER(ctypes.c_uint32))[0]
        names = {0x1000: "Low", 0x2000: "Medium", 0x3000: "High", 0x4000: "System"}
        print(f"integrity=0x{rid:X} ({names.get(rid, '?')})")
    else:
        print("GetTokenInformation failed")
else:
    print("OpenProcessToken failed")

u = ctypes.windll.user32
u.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
u.FindWindowW.restype = wintypes.HWND
u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]

hwnd = u.FindWindowW("Qt51514QWindowIcon", "微信")
print("FindWindow ->", hex(hwnd) if hwnd else 0)
if hwnd:
    r = wintypes.RECT()
    u.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(r))
    x, y = (r.left + r.right) // 2, (r.top + r.bottom) // 2
    print("point", x, y)
    try:
        import comtypes

        try:
            comtypes.CoInitialize()
        except Exception:
            pass
        from pywinauto.uia_defines import IUIA

        el = IUIA().iuia.ElementFromPoint(wintypes.POINT(x, y))
        print("hit class=", el.CurrentClassName, "name=", el.CurrentName)
    except Exception as exc:  # noqa: BLE001
        print("UIA EXC", type(exc).__name__, exc)
