"""编辑框里的截图预览（对应影刀编辑器的「预览」区）。

截图来自活体校验的 ``want_shot=True``：``shot_for_desktop`` / ``shot_for_browser``
回 ``{dataUrl, imageSize, box}``，其中 ``box`` 是**图内像素**的元素矩形（1:1，不含
缩放/滚动——桌面元素没有这三件事，见 ``screen_shot.box_in_window``）。

分两层，分开的理由是**可测性**：

- :func:`load_shot_bitmap` —— 纯函数：dataUrl → (top-down RGB bytes, w, h)，
  顺手按 ``box`` 把红框画进图里。不开窗口，测试直接断言像素。
- :class:`ShotView` —— 自绘子窗口，``WM_PAINT`` 里用 ``StretchDIBits`` 等比缩放。

画图用 ``StretchDIBits`` 而不是 PIL 的 ``ImageWin``：后者要拿 ``win32ui`` 的 DC
对象，而我们的 hdc 来自 ``win32gui.BeginPaint``（裸句柄），两者混用容易在 64 位下
拿到空 DC。``StretchDIBits`` 吃裸 hdc，还顺带把缩放做了，不用自己算重采样。
"""

from __future__ import annotations

import base64
import ctypes
import io
import sys
from typing import Any

from . import _win32

_CLASS = "RpaEditShotView"
# WM_PRINT：抓图/打印时窗口要自己把内容画进 wparam 这个 HDC
_WM_PRINT = 0x0317
_BOX_COLOR = (255, 59, 48)
_BOX_WIDTH = 3
_DIB_RGB_COLORS = 0
_SRCCOPY = 0x00CC0020


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32),
        ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32),
        ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16),
        ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32),
        ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


def _as_image(shot: dict[str, Any]) -> Any | None:
    data = shot.get("dataUrl")
    if not isinstance(data, str) or not data:
        return None
    payload = data.split(",", 1)[1] if data.startswith("data:") else data
    try:
        from PIL import Image

        return Image.open(io.BytesIO(base64.b64decode(payload))).convert("RGB")
    except Exception:  # noqa: BLE001 - 图坏了就当没有，绝不能把编辑框拖死
        return None


def load_shot_bitmap(
    shot: Any,
) -> tuple[bytes, int, int] | None:
    """校验回传的 ``shot`` → ``(top-down RGB bytes, width, height)``。

    有 ``box`` 就把元素框画进图里（红框）。**画不出框不算失败**——框只是辅助，
    没有它用户也还能看到页面长什么样。
    """
    if not isinstance(shot, dict):
        return None
    image = _as_image(shot)
    if image is None:
        return None
    box = shot.get("box")
    if isinstance(box, dict):
        try:
            from PIL import ImageDraw

            x = float(box["x"])
            y = float(box["y"])
            width = float(box["width"])
            height = float(box["height"])
            if width > 0 and height > 0:
                ImageDraw.Draw(image).rectangle(
                    (x, y, x + width, y + height),
                    outline=_BOX_COLOR,
                    width=_BOX_WIDTH,
                )
        except Exception:  # noqa: BLE001 - 框画不出来不影响预览
            pass
    width, height = image.size
    # ★ 必须是 BGR：``StretchDIBits`` 按 DIB 规则解释 24bpp 像素（字节序 B,G,R），
    # 传 RGB 会让红框变成蓝框。行优先 top-down 正好匹配下面 biHeight 取负数。
    return image.tobytes("raw", "BGR"), int(width), int(height)


class ShotView:
    """编辑框内的只读截图区：等比缩放居中，留白处显示提示语。"""

    def __init__(self) -> None:
        self._hwnd: int | None = None
        self._bits: bytes | None = None
        self._src_w = 0
        self._src_h = 0
        self._hint = "点「校验元素」后在这里看目标长什么样"
        self._hfont = 0
        self._header: _BITMAPINFOHEADER | None = None

    def create(self, parent: int, x: int, y: int, width: int, height: int) -> int:
        if sys.platform != "win32":
            return 0
        import win32con
        import win32gui

        _win32.register_class(_CLASS)
        self._hfont = _win32.make_font()
        hwnd = win32gui.CreateWindowEx(
            0,
            _CLASS,
            "",
            win32con.WS_CHILD | win32con.WS_VISIBLE,
            x,
            y,
            width,
            height,
            parent,
            0,
            win32gui.GetModuleHandle(None),
            None,
        )
        if not hwnd:
            return 0
        self._hwnd = hwnd
        _win32._INSTANCES[int(hwnd)] = self
        return int(hwnd)

    def set_shot(self, shot: Any) -> bool:
        """换一张截图；解码失败返回 False 并保留旧图（不清空，免得闪一下空白）。"""
        loaded = load_shot_bitmap(shot)
        if loaded is None:
            return False
        self._bits, self._src_w, self._src_h = loaded
        self._header = _BITMAPINFOHEADER(
            ctypes.sizeof(_BITMAPINFOHEADER),
            self._src_w,
            # 负数 = top-down，与 PIL 的 tobytes 行序一致
            -self._src_h,
            1,
            24,
            0,
            0,
            0,
            0,
            0,
            0,
        )
        self._invalidate()
        return True

    def set_hint(self, text: str) -> None:
        self._hint = text
        self._invalidate()

    def destroy(self) -> None:
        hwnd = self._hwnd
        self._hwnd = None
        if not hwnd:
            return
        import win32gui

        _win32._INSTANCES.pop(int(hwnd), None)
        try:
            win32gui.DestroyWindow(hwnd)
        except Exception:  # noqa: BLE001
            pass
        if self._hfont:
            try:
                win32gui.DeleteObject(self._hfont)
            except Exception:  # noqa: BLE001
                pass
            self._hfont = 0

    def _invalidate(self) -> None:
        if not self._hwnd:
            return
        import win32gui

        try:
            win32gui.InvalidateRect(self._hwnd, None, False)
        except Exception:  # noqa: BLE001
            pass

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        import win32con
        import win32gui

        if msg == win32con.WM_PAINT:
            hdc, paint = win32gui.BeginPaint(hwnd)
            try:
                self._render(hdc, hwnd)
            finally:
                win32gui.EndPaint(hwnd, paint)
            return 0
        if msg == _WM_PRINT:
            # 自绘窗口收到 WM_PRINT 会落到 DefWindowProc = 什么都不画，
            # 于是「抓一张编辑框」的图里这一块永远空白。wparam 就是要画的 HDC。
            self._render(wparam, hwnd)
            return 0
        if msg == win32con.WM_ERASEBKGND:
            return 1
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    def _render(self, hdc: Any, hwnd: int) -> None:
        import win32api
        import win32con
        import win32gui

        left, top, right, bottom = win32gui.GetClientRect(hwnd)
        client_w = max(right - left, 1)
        client_h = max(bottom - top, 1)
        brush = win32gui.CreateSolidBrush(win32api.RGB(246, 246, 246))
        win32gui.FillRect(hdc, (left, top, right, bottom), brush)
        win32gui.DeleteObject(brush)

        drawn = False
        if self._bits and self._header and self._src_w and self._src_h:
            dest = _fit((self._src_w, self._src_h), (client_w, client_h))
            gdi32 = ctypes.windll.gdi32
            gdi32.StretchDIBits.argtypes = [
                ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                ctypes.c_uint, ctypes.c_uint,
            ]
            gdi32.StretchDIBits.restype = ctypes.c_int
            result = gdi32.StretchDIBits(
                int(hdc),
                dest[0], dest[1], dest[2], dest[3],
                0, 0, self._src_w, self._src_h,
                self._bits,
                ctypes.byref(self._header),
                _DIB_RGB_COLORS,
                _SRCCOPY,
            )
            drawn = bool(result)
        if not drawn:
            if self._hfont:
                win32gui.SelectObject(hdc, self._hfont)
            win32gui.SetBkMode(hdc, win32con.TRANSPARENT)
            win32gui.SetTextColor(hdc, win32api.RGB(140, 140, 140))
            rect = (left + 12, top, right - 12, bottom)
            win32gui.DrawText(
                hdc, self._hint, -1, rect,
                win32con.DT_CENTER | win32con.DT_VCENTER
                | win32con.DT_SINGLELINE | win32con.DT_END_ELLIPSIS,
            )


def _fit(src: tuple[int, int], box: tuple[int, int]) -> tuple[int, int, int, int]:
    """等比缩放 + 居中：返回 ``(x, y, w, h)``（图不变形）。"""
    src_w, src_h = src
    box_w, box_h = max(box[0], 1), max(box[1], 1)
    scale = min(box_w / src_w, box_h / src_h)
    width = max(int(src_w * scale), 1)
    height = max(int(src_h * scale), 1)
    return ((box_w - width) // 2, (box_h - height) // 2, width, height)


__all__ = ["ShotView", "load_shot_bitmap"]
