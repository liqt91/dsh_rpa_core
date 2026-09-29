# -*- coding: utf-8 -*-
"""最小实验：desktop_click_hook 的回调是否真的收到鼠标事件。"""
import ctypes
import subprocess
import sys
import time
from ctypes import wintypes

user32 = ctypes.windll.user32

child = subprocess.Popen(
    [sys.executable, "-m", "rpa_core.capture.desktop_click_hook",
     "--event", "Local\\rpa-debug-e2", "--parent-pid", str(__import__("os").getpid()),
     "--timeout", "12"],
)
time.sleep(4.0)  # 等子进程装好钩子

# 注入：移动 + 无 Ctrl 的普通点击 + 有 Ctrl 的点击（各一次）
user32.SetCursorPos(600, 400)
time.sleep(0.2)
for dx in range(20):  # 一串移动
    user32.SetCursorPos(600 + dx, 400)
    time.sleep(0.01)
user32.keybd_event(0x11, 0, 0, 0)
time.sleep(0.05)
user32.mouse_event(0x0002, 0, 0, 0, 0)  # LDown（Ctrl 按住）
time.sleep(0.05)
user32.mouse_event(0x0004, 0, 0, 0, 0)  # LUp
time.sleep(0.05)
user32.keybd_event(0x11, 0, 2, 0)
time.sleep(1.5)
child.terminate()
child.wait(timeout=5)
print("child rc =", child.returncode)
