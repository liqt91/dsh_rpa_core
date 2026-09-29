# -*- coding: utf-8 -*-
"""独立验证命名事件链路：parent CreateEventW -> child OpenEventW+SetEvent -> parent wait。"""
import ctypes
import os
import subprocess
import sys
from ctypes import wintypes

kernel32 = ctypes.windll.kernel32
kernel32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD

name = "Local\\rpa-evtest-" + str(os.getpid())
h = kernel32.CreateEventW(None, True, False, name)
print("parent create:", bool(h), flush=True)

child_code = """
import ctypes
from ctypes import wintypes
k = ctypes.windll.kernel32
k.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
k.OpenEventW.restype = wintypes.HANDLE
k.SetEvent.argtypes = [wintypes.HANDLE]
e = k.OpenEventW(2, False, r"%s")
print("child open:", bool(e), flush=True)
print("child set:", bool(k.SetEvent(e)), flush=True)
""" % name

p = subprocess.Popen(
    [sys.executable, "-c", child_code],
    stdout=subprocess.PIPE, text=True,
)
out, _ = p.communicate(timeout=10)
print(out.strip())
print("parent wait(0):", kernel32.WaitForSingleObject(h, 0), "(0=signaled)")
