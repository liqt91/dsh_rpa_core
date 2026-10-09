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

  - ``scale`` = 图宽 / ``outerWidth``（**窗口**宽）。分母不能是 ``viewport.width``——
    那会把边框+滚动条那十几像素混进缩放比（真机 dpr=1 时算出 1.0104，凭空放大 1%）。
  - ``viewport_origin`` = 视口左上角相对**窗口**左上角的偏移。它**只取决于窗口内部
    布局**（视口多高、窗口多高、边框多厚），**与窗口在屏幕上的位置无关**⇒
    ``origin_y = (outerHeight - viewport.height - border) × scale``，其中
    ``border = (outerWidth - viewport.width) / 2``（左右对称，真机实测每侧 8px）。

  **别用 ``window.screenX/screenY`` 推视口位置**（2026-10-09 真机教训）：它们是
  **窗口**左上角的屏幕坐标，不是视口的。真机 trace 里 ``screenY=-939`` 与
  ``GetWindowRect`` 的 ``top=-939`` **完全相等**就是铁证——那个减法恒等于 0，
  于是整整少算了标签栏+地址栏那一段（实测 151px），裁剪区**偏上约 143px**
  （维护者报「截图位置偏上」）。**与多屏无关**：负坐标（-939 说明窗口在副屏上方）
  由 ``ImageGrab(all_screens=True)`` 与 ``GetWindowRect`` 正确处理，那一路是对的。
  改成只依赖窗口内部布局后，多屏/负坐标/主副屏高度差**自动不再是问题**。

故扩展侧 ``viewportInfo()`` 需回 ``outerWidth`` / ``outerHeight``（窗口 CSS 尺寸）
与 ``width`` / ``height``（视口 CSS 尺寸）；``screenX`` / ``screenY`` 保留但**换算
不读**（调试时对照用：它们应恒等于窗口原点）。

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


def browser_viewport_in_window(
    viewport: dict[str, Any],
    image_size: tuple[int, int] | None,
) -> tuple[float, float, float] | None:
    """浏览器窗口内「视口左上角 +缩放比」——浏览器腿换算的**唯一真相源**。

    返回 ``(origin_x, origin_y, scale)``，三者都是**图内像素**：
    视口左上角在图内的位置，以及 CSS 像素 → 图内像素的倍数。

    ## 为什么不再用 ``window.screenX/screenY``（2026-10-09 真机定案）

    此前 ``content.js`` 的注释写着「screenX/screenY 正是视口左上角的屏幕 CSS 像素
    坐标」，host侧照此推 ``viewport_origin = screenY - win.top``。**那条注释是错的**：
    ``window.screenY`` 是**浏览器窗口**（含标签栏、地址栏）左上角的屏幕坐标，
    **不是视口的**。真机trace 一行就拆穿：

        window_origin= [229, -939]   ← GetWindowRect
        screenY       = -939          ← 与窗口原点**完全相等**

    两者相等 ⇒ 这个减法恒等于 0 ⇒ 视口顶被当成窗口顶，**整整少了标签栏+地址栏
    那一段**。实测 ``outerHeight - viewport.height = 936 - 785 = 151``（含边框），
    于是裁剪区**偏上约 143px**——元素被推到裁剪区下沿之外，看起来就是「截图位置
    偏上」。**与多屏无关**：负坐标（-939 说明窗口在副屏上方）由 ``all_screens=True``
    的 ImageGrab 与 GetWindowRect 正确处理，那一路是对的。

    ## 现在的口径：只依赖**窗口内部布局**，不碰任何屏幕坐标

    截图是从窗口左上角开始的那块矩形，所以「视口在图内的位置」只取决于窗口
    自己有多高、视口有多高——**与窗口在屏幕上的位置无关**。于是多屏/负坐标/
    主副屏高度差**自动不再是问题**（维护者报「两块屏一块比另一块更偏上」）。

    - **上下边框**：Windows 窗口有一圈不可见的 resize border，真机实测
      ``outerWidth - viewport.width = 16`` ⇒ 每侧 8px（左右对称，跨窗口稳定）。
    - **视口顶** = ``outerHeight - viewport.height - 下边框``：从窗口底边往上倒推，
      因为**下边框贴着屏幕、视口底永远紧邻它**；上边框混在 ``outerHeight`` 里被
      一次性算掉，不需要单独猜「标签栏多高」（那是 Chromium 自己布局的，host
      无权威值，也**不该**猜）。
    - **scale = 图宽 / outerWidth**：图是**窗口**的截图，图宽对应的是
      ``outerWidth``（CSS px）而不是 ``viewport.width``。此前用后者当分母，把
      16px 的边框+滚动条混进了缩放比，真机 dpr=1 时算出 1.0104，凭空放大 1%。

    缺 ``outerWidth/outerHeight``（旧扩展）时返回 ``None`` —— 宁可不出裁剪区，
    也不给一个偏 143px 的。
    """
    if not isinstance(viewport, dict) or not image_size:
        return None
    try:
        vw = float(viewport["width"])
        vh = float(viewport["height"])
        ow = float(viewport["outerWidth"])
        oh = float(viewport["outerHeight"])
        img_w = float(image_size[0])
        img_h = float(image_size[1])
    except (KeyError, TypeError, ValueError):
        return None
    if vw <= 0 or vh <= 0 or ow <= 0 or oh <= 0 or img_w <= 0 or img_h <= 0:
        return None
    # 边框：左右对称，各一半。截图为负/异常形状时不猜。
    border = (ow - vw) / 2.0
    if border < 0:
        border = 0.0
    scale = scale_of(img_w, ow)
    if scale <= 0:
        return None
    # 视口底紧邻窗口下边框 ⇒ 视口顶 = 窗口底 - 视口高 - 下边框。
    origin_y = (oh - vh - border) * scale
    origin_x = border * scale
    return origin_x, origin_y, scale


def scale_of(image_width: float, css_width: float) -> float:
    """图内像素 / CSS 像素。分母是**窗口**宽（``outerWidth``）不是视口宽。

    此前分母误用 ``viewport.width``，把边框+滚动条那16px 混进了缩放比：真机
    dpr=1 时算出 1.0104，凭空把整个元素放大 1%。
    """
    if css_width <= 0:
        return 0.0
    return float(image_width) / float(css_width)


def browser_crop_in_window(
    rect: dict[str, Any] | None,
    viewport: dict[str, Any] | None,
    image_size: tuple[int, int] | None,
    margin_ratio: float = 1.0,
) -> dict[str, float] | None:
    """浏览器腿：算出「以元素为中心、留适量上下文」的**裁剪区**（图内像素）。

    为什么是裁剪而不是画框（2026-10-09 真机定的案，维护者「红框是后面绘制上去的，
    偏离了实际元素，不是用户在页面上看到的黄框」）：

    - 页面上那个黄框由 content.js 在捕获瞬间画，是**位置权威**；
    - 截图上再画一个框是**二次换算**，误差必然存在，而且无论怎么调都对不齐
      用户眼睛看到的那一个——误差来源至少有：Windows 缩放（真机实测 150%）、
      窗口边框、视口滚动、CSS→物理像素转换。**画框这条路原理上就走不通**。
    ⇒ 改用**裁剪**：位置感由裁剪范围本身表达，不再声称「框住它」。用户看到的是
    「元素周围那一块真实画面」，错不了。

    ``margin_ratio`` 是元素各边留出的上下文，按元素自身尺寸的比例算（1.0 = 上下左右
    各留一倍），所以小元素不会被撑成整窗、大元素也不会裁成一条窄带。
    裁剪区永远**包含元素**且**不越出图**：宁可少留上下文，不切掉元素本身。

    坐标系换算见 :func:`browser_viewport_in_window`（唯一真相源）。
    """
    if not isinstance(rect, dict) or not isinstance(viewport, dict):
        return None
    if not image_size:
        return None
    found = browser_viewport_in_window(viewport, image_size)
    if found is None:
        return None
    origin_x, origin_y, scale = found
    if scale <= 0:
        return None
    try:
        left = float(rect["left"])
        top = float(rect["top"])
        width = float(rect["width"])
        height = float(rect["height"])
    except (KeyError, TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    x = left * scale + origin_x
    y = top * scale + origin_y
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
) -> dict[str, float] | None:
    """**浏览器腿**：视口 CSS 像素矩形 → 坐标截屏的图内像素（画框旧口径）。

    换算全部走 :func:`browser_viewport_in_window`（唯一真相源）。

    与 :func:`browser_crop_in_window` 的差别只在**表达**：这个给 ``box``（画一个框），
    那个给 ``crop``（裁一块画面）。坐标本身两者必须一致——**否则同一份descriptor
    在两条路上会落在不同位置**，而那正是「截图偏上」这类症状的温床。
    """
    if not isinstance(rect, dict) or not isinstance(viewport, dict):
        return None
    if not image_size:
        return None
    found = browser_viewport_in_window(viewport, image_size)
    if found is None:
        return None
    origin_x, origin_y, scale = found
    if scale <= 0:
        return None
    try:
        left = float(rect["left"])
        top = float(rect["top"])
        width = float(rect["width"])
        height = float(rect["height"])
    except (KeyError, TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    x = left * scale + origin_x
    y = top * scale + origin_y
    w = width * scale
    h = height * scale
    # 完全在窗口外（元素被滚出视口）⇒ 不画；部分在外 ⇒ 钳到图内，
    # 与用户「看得出有个框贴着边」的预期一致。
    img_w, img_h = float(image_size[0]), float(image_size[1])
    if x + w <= 0 or y + h <= 0:
        return None
    if x >= img_w or y >= img_h:
        return None
    # 钳位必须**同时收窄**：元素横跨 -50~50 时，光把 x 钉到 0 而不动 width，框会
    # 从 0 铺到 100——比元素宽一倍，看起来「框住了旁边的东西」。
    # 正确做法是按被切掉的那截从宽高里扣掉。
    clamped_x = max(0.0, x)
    clamped_y = max(0.0, y)
    w = max(2.0, min(w - (clamped_x - x), img_w - clamped_x))
    h = max(2.0, min(h - (clamped_y - y), img_h - clamped_y))
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
    box = browser_box_in_window(rect, viewport, size)  # type: ignore[arg-type]
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
    host 做；而视口在窗口内的偏移由扩展回的 ``outerWidth/outerHeight`` 与
    ``width/height`` 算出（见 :func:`browser_viewport_in_window`）——**不需要屏幕
    坐标**，因此多屏/负坐标一律不再是问题。

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
    （含**窗口** ``outerWidth/outerHeight`` 与视口 ``width/height``），换算走
    :func:`browser_viewport_in_window`（唯一真相源，不碰任何屏幕坐标）。

    返回 ``{dataUrl, crop?, windowOrigin, imageSize}``；拿不到窗口就返回 ``None``，
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
        # 判据：outerWidth/outerHeight − viewport.width/height = 窗口边框那一圈；
        # **screenX/screenY 恒等于 window_origin**（那是窗口左上角，不是视口的），
        # 所以它们**不可**用来推视口位置——2026-10-09 的「截图偏上」就栽在这里。
        rect=rect,
        viewport=viewport,
        window_origin=shot.get("windowOrigin"),
        image_size=shot.get("imageSize"),
        crop=crop,
    )
    return shot
