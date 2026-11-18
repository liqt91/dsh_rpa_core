"""窗口 DPI / 缩放取数的唯一权威（M80）。

本模块只回答一件事：某个窗口当前按多少 DPI 渲染，等效缩放比是多少。
不做坐标换算、不做模板匹配，也不决定何时使用图像层（那是 ``image_guard`` 的事）。

口径：
* 基准 DPI = 96（Windows 100% 缩放）；
* ``scale = dpi / 96``；
* 优先 ``GetDpiForWindow(hwnd)``（自动处理副屏/负坐标/进程 DPI 感知），失败再退
  ``GetDpiForSystem``；两者都拿不到就返回 ``None``，**不猜 96**。
* 非 Windows / 缺 Win32 依赖返回 ``None``，由调用方给结构化错误。

为什么不让 proposal / locator / verifier 各算各的：那会让「捕获时 DPI」与
「匹配时 DPI」来自两套口径，跨缩放时的误差无法归因。这里收敛成一个函数。
"""

from __future__ import annotations

import sys
from typing import Any

BASE_DPI = 96


def probe_window_dpi(hwnd: int) -> dict[str, Any]:
    """返回 ``{"dpi": int | None, "scale": float | None}``。

    句柄无效也允许退到系统 DPI：截图本身仍可能来自该窗口所在屏幕，
    但调用方若拿不到有效窗口矩形会在更早一步返回 ``None``。
    """
    if sys.platform != "win32":
        return {"dpi": None, "scale": None}

    try:
        import ctypes

        user32 = ctypes.windll.user32
    except Exception:  # noqa: BLE001 - 缺 ctypes/windll 的精简环境
        return {"dpi": None, "scale": None}

    dpi: int | None = None
    try:
        value = int(user32.GetDpiForWindow(int(hwnd)))
        if value > 0:
            dpi = value
    except Exception:  # noqa: BLE001 - 句柄失效/旧系统没有该 API
        dpi = None

    if dpi is None:
        try:
            value = int(user32.GetDpiForSystem())
            if value > 0:
                dpi = value
        except Exception:  # noqa: BLE001
            dpi = None

    if dpi is None:
        return {"dpi": None, "scale": None}
    return {"dpi": dpi, "scale": dpi / float(BASE_DPI)}


__all__ = ["BASE_DPI", "probe_window_dpi"]
