import sys

from .base import CommandExecutor
from .browser import PlaywrightExecutor
from .desktop import DesktopExecutor
from .python_worker import PythonWorkerExecutor
from .registry import ExecutorRegistry

__all__ = [
    "CommandExecutor",
    "DesktopExecutor",
    "ExecutorRegistry",
    "PlaywrightExecutor",
    "PythonWorkerExecutor",
]

# Win32DesktopExecutor仅在Windows上可用
if sys.platform == "win32":
    from .desktop_win32 import Win32DesktopExecutor
    __all__.append("Win32DesktopExecutor")
else:
    # 在非Windows平台上提供一个占位符，避免导入错误
    Win32DesktopExecutor = None
