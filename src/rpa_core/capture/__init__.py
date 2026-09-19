from .desktop import DesktopCaptureSession, desktop_capture_available
from .extension import ExtensionCaptureSession, capture_click_label
from .hybrid import HybridCaptureSession

__all__ = [
    "DesktopCaptureSession",
    "ExtensionCaptureSession",
    "HybridCaptureSession",
    "capture_click_label",
    "desktop_capture_available",
]
