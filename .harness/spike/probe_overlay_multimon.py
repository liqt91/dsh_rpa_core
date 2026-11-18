"""Real-machine check: does the hover overlay actually paint on BOTH monitors?

The maintainer reports "image capture cannot box anything". `_image_box_at` is
pure geometry and returns a sane rect, so the suspect is the overlay itself --
a layered window that is only ever given `SetWindowPos`. On this machine the
secondary display lives at negative Y (0,-1080)-(1920,0), which is exactly the
kind of coordinate a window manager can quietly reject.

This probe shows a box on the primary screen and on the secondary screen, grabs
pixels around each, counts red ones, then destroys the overlay. A visible box
shows up as a few hundred red pixels; nothing drawn shows up as zero.

Read-only w.r.t. the product: creates and destroys one overlay window.
"""

from __future__ import annotations

import ctypes
import sys
import time

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2] / "src"))

from PIL import ImageGrab  # noqa: E402

from rpa_core.capture.desktop_agent import _HoverOverlay  # noqa: E402

BORDER = 3


def count_red(box: tuple[int, int, int, int]) -> int:
    img = ImageGrab.grab(bbox=box, all_screens=True).convert("RGB")
    n = 0
    for r, g, b in img.getdata():
        if r > 180 and g < 110 and b < 110:
            n += 1
    return n


def probe(overlay, left, top, label):
    right, bottom = left + 32, top + 32
    overlay.show_rect(left, top, right, bottom)
    time.sleep(0.5)
    pad = 12
    region = (left - pad, top - pad, right + pad, bottom + pad)
    red = count_red(region)
    print(f"{label:<12} rect=({left},{top},{right},{bottom}) red_pixels={red}")
    overlay.hide()
    time.sleep(0.3)
    after = count_red(region)
    print(f"{'':<12} after hide        red_pixels={after}")
    return red


def main() -> int:
    overlay = _HoverOverlay()
    try:
        a = probe(overlay, 500, 400, "primary")
        b = probe(overlay, 500, -700, "secondary")
    finally:
        overlay.destroy()
    print()
    print("primary  visible:", a > 50)
    print("secondary visible:", b > 50)
    return 0


if __name__ == "__main__":
    sys.exit(main())
