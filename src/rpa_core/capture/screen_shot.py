"""桌面截图（M47.12：统一走「坐标截屏」，浏览器腿与桌面腿共用）。

## 为什么不用 PrintWindow

先试过 ``PrintWindow(hwnd, dc, PW_RENDERFULLCONTENT)``——它能让窗口自己画进 DC，
**穿透遮挡**（屏幕 BitBlt 做不到：pywinauto 的 ``capture_as_image`` 走的就是屏幕
BitBlt，所以我们自己那个 ``always_on_top`` 的对话框会被一起拍进去）。

但实测（2026-10-09，本机）**对 Chromium 浏览器窗口不可用**：窗口跨显示器
（rect 实测 ``L-8 → R1928``、宽 1936）时抓到的内容**水平重复三次**（截图里同一页面
并排出现3 份）。同一台机器上用**自建窗口**（tkinter）在完全相同的正/负坐标条件下
PrintWindow 正常（红色定位标记恰好出现 1 次）⇒ **不是 PrintWindow 的通用缺陷，
是 Chromium 合成器在多显示器/负坐标下的渲染问题**。

## 改用坐标截屏，顺带也对上了影刀

改用 ``PIL.ImageGrab.grab(bbox=窗口绝对矩形, all_screens=True)`` 后：
内容正确、**无重复**（实测左右半图平均差 45.23；若重复该值会趋近 0）。

这也正好解释了维护者观察到的影刀现象：「影刀的web 元素截图会把浏览器**前面窗口**
的内容也截取到」——坐标截屏截的是**屏幕上那块矩形**，遮挡物自然在里面。
换句话说，**这不是影刀的缺陷，是它的实现口径**，我们照做即可。

代价要说清：坐标截屏**拍不到被完全遮挡的内容**（被别的窗口压住就是压住）。
但预览是给用户看「元素在哪」，而校验/高亮通道本身会把目标浏览器置前（M47.9），
所以这条代价在预览场景里不成立。真要穿遮挡还得走 PrintWindow，而它对 Chromium 坏。

## 红框换算：两条腿各需什么（实测，别凭直觉）

图 = 窗口矩形那块的屏幕像素，左上角 = ``(win.left, win.top)``。

* **桌面元素**：元素矩形 ``info.rectangle()`` 本身就是**绝对屏幕坐标**，与图同源 ⇒
  图内像素 = 屏幕坐标 − 窗口原点，**1:1 无缩放、无滚动、无 dpr**。这是桌面腿最省事的地方。
* **浏览器元素**：扩展回传的是**视口CSS 像素**的 rect。需要两样换算量：
  ``scale`` 与 ``viewport_origin``。

  - ``scale`` = 图宽 / ``viewport.width``（**不要用 ``devicePixelRatio`` 猜**——
    Windows 分数缩放下 ``round(视口宽×dpr)`` 可能与图宽差 1px，1px 偏在小元素上
    就是「框没套住」；这与 M47.11 的 ``preview_box_in_image`` 同一口径）。
  - ``viewport_origin`` = 视口左上角相对**窗口**左上角的偏移。**host侧推不出来**：
    ``client_rect()`` 返回的是窗口内**相对**坐标（实测恒为 ``(0,0,w,h)``），
    而标签栏/地址栏高度由Chromium 自己布局，host 无权威值。
    ⇒ 由扩展回传 ``window.screenX/screenY``（视口左上角的屏幕 CSS 像素坐标）。

  故扩展侧 ``viewportInfo()`` 增回 ``screenX`` / ``screenY`` / ``outerWidth`` /
  ``outerHeight``，host侧据此算偏移。

## 平台与失败口径

非 Windows / 缺 PIL / 窗口消失 / 截图为空 ⇒ 一律返回 ``None``，调用方落文案，
**绝不让预览失败升级成校验失败**（同 M47.11 的 ``captureVisible`` 口径）。
"""

from __future__ import annotations

import base64
import io
import sys
from typing import Any, Protocol

from rpa_core.capture._trace import trace as _trace

#: 浏览器窗口类名（**仅作文档参考**，判定走进程名——见 M47.9 实测教训：
#: ``Chrome_WidgetWin_1`` 被 Chrome/Edge 与定制 Chromium 客户端共用）。
_CHROME_CLASS = "Chrome_WidgetWin"

#: 已知浏览器进程名（小写）→ 与 :mod:`rpa_core.capture.foreground_window` 同源。
_BROWSER_PROCESSES = frozenset({
    "msedge.exe", "chrome.exe", "brave.exe", "opera.exe", "vivaldi.exe", "firefox.exe",
})


class _ScreenAPI(Protocol):
    """屏幕相关 Win32 调用的接缝（注入替身 ⇒ 纯换算逻辑任意平台可测）。"""

    def window_rect(self, hwnd: int) -> tuple[int, int, int, int] | None:
        """窗口绝对矩形 ``(left, top, right, bottom)``；取不到返回 None。"""

    def grab(self, bbox: tuple[int, int, int, int]) -> bytes | None:
        """截取屏幕 ``bbox`` 那块，返回 **PNG 字节**；失败返回 None。"""

    def window_exists(self, hwnd: int) -> bool:
        """窗口是否还在（截图前瞬时状态会变）。"""


class _WinScreen:
    """真实实现：pywinauto 取窗口矩形 + PIL 坐标截屏。

    **不用 ``capture_as_image``**：它走屏幕 BitBlt 且只接受 win32 wrapper，
    这里我们要的是「按绝对矩形截一块」这一件事，自己调 ``ImageGrab`` 更直白，
    也能对「窗口已消失」给出干净的 None。
    """

    def __init__(self) -> None:
        self._grabbed: list[tuple[int, int, int, int]] = []

    def window_rect(self, hwnd: int) -> tuple[int, int, int, int] | None:
        if not hwnd or sys.platform != "win32":
            return None
        try:
            import win32gui

            left, top, right, bottom = win32gui.GetWindowRect(int(hwnd))
        except Exception:  # noqa: BLE001 - 句柄失效/无权限都算取不到
            return None
        if right <= left or bottom <= top:
            return None
        return left, top, right, bottom

    def grab(self, bbox: tuple[int, int, int, int]) -> bytes | None:
        try:
            from PIL import ImageGrab
        except Exception:  # noqa: BLE001 - 缺 Pillow
            return None
        try:
            img = ImageGrab.grab(bbox=bbox, all_screens=True)
        except Exception:  # noqa: BLE001 - 屏幕锁定/无会话等
            return None
        if img is None or not img.width or not img.height:
            return None
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        # ``_grabbed`` 只给测试/调试看「实际截了哪块」，不参与换算。
        self._grabbed.append(tuple(bbox))  # type: ignore[arg-type]
        return buf.getvalue()

    def window_exists(self, hwnd: int) -> bool:
        if not hwnd or sys.platform != "win32":
            return False
        try:
            import win32gui

            return bool(win32gui.IsWindow(int(hwnd)))
        except Exception:  # noqa: BLE001
            return False


def _default_api() -> _ScreenAPI | None:
    return _WinScreen() if sys.platform == "win32" else None


# ---------------------------------------------------------------- 纯函数

def box_in_window(
    rect: dict[str, Any] | None,
    window_origin: tuple[int, int] | None,
) -> dict[str, float] | None:
    """**桌面腿**：屏幕坐标矩形 → 图内像素（同源1:1，无需任何缩放）。

    ``rect`` 用 :meth:`comtypes.Rectangle` 的形状 ``{left, top, width, height}``
    （与 ``desktop_agent`` 捕获时的``info.rectangle()`` 同口径）。
    屏幕像素与图内像素差一个窗口原点偏移——所以这段只是减法，
    **不引入 scale / scroll / dpr**：桌面元素没有视口、没有滚动、没有缩放这三件事。
    """
    if not isinstance(rect, dict) or window_origin is None:
        return None
    try:
        left = float(rect["left"])
        top = float(rect["top"])
        width = float(rect["width"])
        height = float(rect["height"])
    except (KeyError, TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        # 零面积：宁可不画，也不画一个「框住空气」的假框。
        return None
    ox, oy = float(window_origin[0]), float(window_origin[1])
    return {"x": left - ox, "y": top - oy, "width": width, "height": height}


def browser_crop_in_window(
    rect: dict[str, Any] | None,
    viewport: dict[str, Any] | None,
    image_size: tuple[int, int] | None,
    window_origin: tuple[int, int] | None,
    margin_ratio: float = 1.0,
) -> dict[str, float] | None:
    """浏览器腿：算出「以元素为中心、留适量上下文」的**裁剪区**（图内像素）。

    为什么是裁剪而不是画框（2026-10-09 真机定的案，维护者「红框是后面绘制上去的，
    偏离了实际元素，不是用户在页面上看到的黄框」）：

    -页面上那个黄框由content.js 在捕获瞬间画，是**位置权威**；
    - 截图上再画一个框是**二次换算**，误差必然存在，而且无论怎么调都对不齐
      用户眼睛看到的那一个——误差来源至少有：Windows 缩放（真机实测 150%）、
      窗口边框、视口滚动、CSS→物理像素转换。**画框这条路原理上就走不通**。
    ⇒ 改用**裁剪**：位置感由裁剪范围本身表达，不再声称「框住它」。用户看到的是
    「元素周围那一块真实画面」，错不了。

    ``margin_ratio`` 是元素各边留出的上下文，按元素自身尺寸的比例算（1.0 = 上下左右
    各留一倍），所以小元素不会被撑成整窗、大元素也不会裁成一条窄带。
    裁剪区永远**包含元素**且**不越出图**：宁可少留上下文，不切掉元素本身。

    坐标系换算与 :func:`browser_box_in_window` 同一套（那边还留着画框的旧口径，
    供 verify 那条路的既有判据使用；本函数是捕获预览的新口径）。
    """
    if not isinstance(rect, dict) or not isinstance(viewport, dict):
        return None
    if window_origin is None or not image_size:
        return None
    try:
        left = float(rect["left"])
        top = float(rect["top"])
        width = float(rect["width"])
        height = float(rect["height"])
        vp_w = float(viewport["width"])
        screen_x = float(viewport["screenX"])
        screen_y = float(viewport["screenY"])
    except (KeyError, TypeError, ValueError):
        return None
    if width <= 0 or height <= 0 or vp_w <= 0:
        return None
    scale = float(image_size[0]) / vp_w
    if scale <= 0:
        return None
    # 视口左上角相对窗口左上角的偏移：**先在 CSS 坐标系里做减法，再统一乘 scale**。
    # 原式写成 (screenX - win.left) * scale —— win.left 是**物理**像素（GetWindowRect），
    # screenX 是 **CSS** 像素，真机 150% 缩放下两个坐标系混算 ⇒ 偏移错、框画歪。
    ox, oy = float(window_origin[0]), float(window_origin[1])
    x = (left * scale) + screen_x * scale - ox
    y = (top * scale) + screen_y * scale - oy
    w = width * scale
    h = height * scale
    if x + w <= 0 or y + h <= 0:
        return None  # 完全在窗口外（元素被滚出视口）
    img_w, img_h = float(image_size[0]), float(image_size[1])
    if x >= img_w or y >= img_h:
        return None
    pad_x = w * margin_ratio
    pad_y = h * margin_ratio
    # 左上角往外扩、右下角也往外扩，然后钳到图内——**钳位只缩不挪元素**。
    cx1 = max(0.0, x - pad_x)
    cy1 = max(0.0, y - pad_y)
    cx2 = min(img_w, x + w + pad_x)
    cy2 = min(img_h, y + h + pad_y)
    cw = cx2 - cx1
    ch = cy2 - cy1
    if cw <= 0 or ch <= 0:
        return None
    return {"x": cx1, "y": cy1, "width": cw, "height": ch}


def browser_box_in_window(
    rect: dict[str, Any] | None,
    viewport: dict[str, Any] | None,
    image_size: tuple[int, int] | None,
    window_origin: tuple[int, int] | None,
) -> dict[str, float] | None:
    """**浏览器腿**：视口 CSS 像素矩形 → 坐标截屏的图内像素。

    换算两步（都是实测得来的，见模块 docstring）：

    1. ``scale = 图宽 / viewport.width``——用**图的实际宽**，不用
       ``devicePixelRatio`` 猜（分数缩放下两者可能差 1px）。
    2. ``viewport_origin = (screenX - win.left, screenY - win.top)``——视口左上角
       相对窗口左上角的偏移，由扩展回传（host侧推不出标签栏高度）。

    之后 ``图内 = rect_CSS × scale + viewport_origin``。
    与桌面腿共用同一个 ``PreviewShot`` 控件与同一个 ``box`` 形状。
    """
    if not isinstance(rect, dict) or not isinstance(viewport, dict):
        return None
    if window_origin is None or not image_size:
        return None
    try:
        left = float(rect["left"])
        top = float(rect["top"])
        width = float(rect["width"])
        height = float(rect["height"])
        vp_w = float(viewport["width"])
        screen_x = float(viewport["screenX"])
        screen_y = float(viewport["screenY"])
    except (KeyError, TypeError, ValueError):
        return None
    if width <= 0 or height <= 0 or vp_w <= 0:
        return None
    scale = float(image_size[0]) / vp_w
    if scale <= 0:
        return None
    ox, oy = float(window_origin[0]), float(window_origin[1])
    vx = (screen_x - ox) * scale
    vy = (screen_y - oy) * scale
    x = left * scale + vx
    y = top * scale + vy
    w = width * scale
    h = height * scale
    # 完全在窗口外（元素被滚出视口）⇒ 不画；部分在外 ⇒ 钳到图内，
    # 与用户「看得出有个框贴着边」的预期一致。
    if x + w <= 0 or y + h <= 0:
        return None
    if x >= float(image_size[0]) or y >= float(image_size[1]):
        return None
    # 钳位必须**同时收窄**：元素横跨 -50~50 时，光把 x 钉到 0 而不动 width，框会
    # 从0 铺到 100——比元素宽一倍，看起来「框住了旁边的东西」。
    # 正确做法是按被切掉的那截从宽高里扣掉。
    clamped_x = max(0.0, x)
    clamped_y = max(0.0, y)
    w = max(2.0, min(w - (clamped_x - x), float(image_size[0]) - clamped_x))
    h = max(2.0, min(h - (clamped_y - y), float(image_size[1]) - clamped_y))
    return {"x": clamped_x, "y": clamped_y, "width": w, "height": h}


# ---------------------------------------------------------------- 取图

def grab_window(hwnd: int, api: _ScreenAPI | None = None) -> dict[str, Any] | None:
    """截指定窗口那块屏幕，返回 ``{dataUrl, windowOrigin, imageSize}``。

    返回 ``None`` 的场景一律吞掉（窗口没了/ 平台不支持 / 缺PIL / 截到空图），
    调用方落一句文案即可——**截图失败绝不上级成校验失败**（同 ``captureVisible``）。
    """
    api = api or _default_api()
    if api is None:
        return None
    rect = api.window_rect(int(hwnd or 0))
    if rect is None:
        return None
    data = api.grab(rect)
    if not data:
        return None
    from PIL import Image  # 只为拿尺寸；Pillow 缺失已在 grab 里兜过

    try:
        with Image.open(io.BytesIO(data)) as img:
            size = img.size
    except Exception:  # noqa: BLE001
        return None
    return {
        "dataUrl": "data:image/png;base64," + base64.b64encode(data).decode("ascii"),
        "windowOrigin": [rect[0], rect[1]],
        "imageSize": [int(size[0]), int(size[1])],
        "windowRect": [rect[0], rect[1], rect[2], rect[3]],
    }


def shot_for_desktop(
    hwnd: int,
    rect: dict[str, Any] | None,
    api: _ScreenAPI | None = None,
) -> dict[str, Any] | None:
    """桌面腿的截图：窗口截图 + 元素屏幕矩形 → 图内红框。

    ``rect`` 为空（元素已消失/无矩形）时仍返回截图，只是不带 ``box``——
    让GUI 能显示「页面在，但元素位置拿不到」，而不是什么都看不到。
    """
    shot = grab_window(hwnd, api=api)
    if shot is None:
        return None
    origin = tuple(shot["windowOrigin"])  # type: ignore[arg-type]
    box = box_in_window(rect, origin)  # type: ignore[arg-type]
    if box is not None:
        shot["box"] = box
    _trace(
        "desktop_shot",
        "shot",
        hwnd=int(hwnd or 0),
        origin=list(origin),
        image=list(shot["imageSize"]),  # type: ignore[arg-type]
        has_box=box is not None,
        bytes=data_len(shot),
    )
    return shot


def shot_for_browser(
    hwnd: int,
    rect: dict[str, Any] | None,
    viewport: dict[str, Any] | None,
    api: _ScreenAPI | None = None,
) -> dict[str, Any] | None:
    """浏览器腿的截图：窗口截图 + 视口矩形 → 图内红框（统一走桌面坐标截屏）。"""
    shot = grab_window(hwnd, api=api)
    if shot is None:
        return None
    origin = tuple(shot["windowOrigin"])  # type: ignore[arg-type]
    size = tuple(shot["imageSize"])  # type: ignore[arg-type]
    box = browser_box_in_window(rect, viewport, size, origin)  # type: ignore[arg-type]
    if box is not None:
        shot["box"] = box
    _trace(
        "desktop_shot",
        "browser",
        hwnd=int(hwnd or 0),
        origin=list(origin),
        image=list(size),
        has_box=box is not None,
        bytes=data_len(shot),
    )
    return shot


def data_len(shot: dict[str, Any]) -> int:
    """dataUrl 的字节数（只给 trace / 调试看，绝不把 base64 写进日志）。"""
    url = shot.get("dataUrl")
    if not isinstance(url, str) or "," not in url:
        return 0
    return len(url.split(",", 1)[1])


# ---------------------------------------------------------------- 服务层

def browser_preview_shot(verify_result: dict[str, Any]) -> dict[str, Any] | None:
    """浏览器腿的完整预览：校验回传 → 桌面坐标截屏 → **裁剪到元素附近**。

    拆成两步的原因（实测得来的）：扩展只知道自己页里的**视口 CSS 像素**，而
    **窗口绝对矩形只有 host 拿得到**（win32 ``GetWindowRect``）。所以截图必须由
    host 做，而换算要用扩展回的 ``screenX/screenY``——两边拼起来才是完整一张图。

    产出 ``crop`` 而非 ``box``（2026-10-09 真机定案）：页面上那个黄框由 content.js
    在捕获瞬间画、位置权威；截图上再画一个框是**二次换算**，真机 150% Windows 缩放
    下偏得肉眼可见（维护者原话：「红框是后面绘制上去的，偏离了实际元素」）。

    ``verify_result`` 缺 ``windowHandle``（扩展没给/拿不到）时返回 ``None``，
    调用方落一句「未能定位浏览器窗口」即可——**命中数那条真判据不受影响**。
    """
    if not isinstance(verify_result, dict) or verify_result.get("error"):
        return None
    hwnd = verify_result.get("windowHandle")
    if not isinstance(hwnd, int) or isinstance(hwnd, bool) or hwnd <= 0:
        return None
    shot = grab_window(hwnd)
    if shot is None:
        return None
    crop = browser_crop_in_window(
        verify_result.get("rect"),
        verify_result.get("viewport"),
        tuple(shot["imageSize"]),  # type: ignore[arg-type]
        tuple(shot["windowOrigin"]),  # type: ignore[arg-type]
    )
    if crop is not None:
        shot["crop"] = crop
    # 命中数一并带走：GUI 侧一次回传就够，不必再问一遍。
    shot["count"] = verify_result.get("count")
    return shot


def desktop_preview_shot(hwnd: int, rect: dict[str, Any] | None) -> dict[str, Any] | None:
    """桌面腿的完整预览：窗口截图 + 元素屏幕矩形 → 图内红框。"""
    if not isinstance(hwnd, int) or isinstance(hwnd, bool) or hwnd <= 0:
        return None
    return shot_for_desktop(hwnd, rect)


# ------------------------------------------------- 捕获时快照（M47.12 真机反馈）

def capture_shot_from_descriptor(
    descriptor: dict[str, Any],
    hwnd: int | None,
    api: _ScreenAPI | None = None,
) -> dict[str, Any] | None:
    """**捕获那一刻**的窗口快照 + 图内红框（预览页签直接展示它，不再二次截屏）。

    为什么不用「点预览时才截」那条路（M47.12 真机撞墙的教训）：
    那条路依赖扩展回 ``windowHandle``，而 ``chrome.windows.get().nativeWindowHandle``
    在真实环境里**未必给得出**（2026-10-09 真机：版本已是0.8.0，Edge 上仍
    ``has_hwnd=false``）。而捕获时不同——**用户刚点完元素，浏览器窗口必定在眼前**，
    窗口句柄由 host 用 Z 序认（``first_browser_window()``，Chrome/Edge 都在册），
    不依赖扩展给任何东西。

    几何来源是content 侧本来就有的：``metadata.rect``（视口 CSS 像素）+ ``viewport``
    （含视口屏幕原点），换算与 :func:`browser_box_in_window` 同一套口径。

    返回 ``{dataUrl, box?, windowOrigin, imageSize}``；拿不到窗口就返回 ``None``，
    由调用方决定降级文案（**不抛**——截图不该让一次成功的捕获变成失败）。
    """
    if not isinstance(descriptor, dict) or not isinstance(hwnd, int) or hwnd <= 0:
        return None
    meta = descriptor.get("metadata")
    meta = meta if isinstance(meta, dict) else {}
    rect = meta.get("rect")
    rect = rect if isinstance(rect, dict) else None
    # 注意 viewport 在 descriptor **顶层**（content.js 的 viewportInfo()），
    # 不在 selector 里 —— 早期版本读错位置，恒为 None，红框整个消失且不报错。
    viewport = descriptor.get("viewport")
    viewport = viewport if isinstance(viewport, dict) else None
    if viewport is None:
        sel = descriptor.get("selector")
        sel = sel if isinstance(sel, dict) else {}
        legacy = sel.get("viewport")
        viewport = legacy if isinstance(legacy, dict) else None
    shot = grab_window(hwnd, api=api)
    if shot is None:
        _trace("capture_shot", "no_window", hwnd=int(hwnd))
        return None
    #裁剪区（**不画框**）：见 browser_crop_in_window 的 docstring——截图上画框是
    # 二次换算，真机 150% 缩放下必然对不齐页面上那个权威黄框。
    crop = browser_crop_in_window(
        rect,
        viewport,
        tuple(shot["imageSize"]),  # type: ignore[arg-type]
        tuple(shot["windowOrigin"]),  # type: ignore[arg-type]
    )
    if crop is not None:
        shot["crop"] = crop
    _trace(
        "capture_shot",
        "ok",
        hwnd=int(hwnd),
        bytes=data_len(shot),
        has_crop=crop is not None,
        # **换算现场全量落盘**：坐标系混用这类 bug 从症状（「框画歪了」）到根因
        # （谁在物理像素、谁在 CSS 像素）隔着三层推导，trace 里没这组数就只能猜。
        # 判据：scale≈图宽/视口宽，应与 viewport.dpr 同量级（真机 150%）；
        # window_origin 是物理像素、screenX/screenY 是 CSS 像素，不可直接相减。
        rect=rect,
        viewport=viewport,
        window_origin=shot.get("windowOrigin"),
        image_size=shot.get("imageSize"),
        crop=crop,
    )
    return shot
