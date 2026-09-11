from .browser import BrowserCaptureSession
from .desktop import DesktopCaptureSession
from .extension import ExtensionCaptureSession
from .hybrid import HybridCaptureSession

__all__ = [
    "BrowserCaptureSession",
    "DesktopCaptureSession",
    "ExtensionCaptureSession",
    "HybridCaptureSession",
]
