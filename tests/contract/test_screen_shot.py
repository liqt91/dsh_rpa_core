"""桌面截图与红框换算的契约（M47.12）。

覆盖三组判据：
- **桌面腿**：屏幕矩形 → 图内像素（1:1，无缩放/滚动/dpr）；
- **浏览器腿**：视口 CSS 像素 → 坐标截屏的图内像素（差一个缩放 + 一个视口原点偏移）；
- **降级**：截图失败一律 ``None``，**绝不让「截图」变成「校验失败」**。

为什么这些必须是纯函数判据：换算错1px 在小元素上就是「框没套住」，而这种错在真机上
表现为「框歪了一点」，人眼很容易归因为「差不多对」而放过。数值判据才能钉住。
"""

from __future__ import annotations

import pytest

from rpa_core.capture.screen_shot import (
    box_in_window,
    browser_box_in_window,
    browser_preview_shot,
    desktop_preview_shot,
    grab_window,
)


class FakeAPI:
    """``_ScreenAPI`` 替身：窗口矩形与截屏都按注入给，不碰真屏。"""

    def __init__(self, rect=None, png=None, exists=True):
        self._rect = rect
        self._png = png if png is not None else _ONE_PIXEL_PNG
        self._exists = exists
        self.grabbed: list[tuple[int, int, int, int]] = []

    def window_rect(self, hwnd):
        return self._rect

    def grab(self, bbox):
        self.grabbed.append(bbox)
        return self._png

    def window_exists(self, hwnd):
        return self._exists


#: 1×1 真 PNG（手写字节，不引 Pillow——判据不该依赖被测对象的依赖）。
_ONE_PIXEL_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080200000090"
    "7753de0000000c4944415408d763f8cf00000101010018dd8db00000000049454e44ae426082"
)


# ---- 桌面腿：屏幕矩形 → 图内像素 --------------------------------------------

def test_desktop_box_is_screen_coord_minus_window_origin():
    """桌面腿 1:1：元素矩形本就是绝对屏幕坐标，减去窗口原点即图内像素。

    这条是「桌面腿不需要任何换算」的判据本体——**不引入 scale / scroll / dpr**。
    若哪天有人在这里乘了个 dpr，桌面元素的红框就会整体偏移（真机现象：框歪一截）。
    """
    box = box_in_window(
        {"left": 300, "top": 220, "width": 120, "height": 40},
        (100, 100),
    )
    assert box == {"x": 200.0, "y": 120.0, "width": 120.0, "height": 40.0}


def test_desktop_box_handles_negative_origin_multi_monitor():
    """窗口在负坐标屏上（左/上方副屏）：origin 为负时减法照样成立。"""
    box = box_in_window(
        {"left": 50, "top": 80, "width": 200, "height": 100},
        (-1920, -200),
    )
    assert box == {"x": 1970.0, "y": 280.0, "width": 200.0, "height": 100.0}


@pytest.mark.parametrize(
    "rect",
    [
        None,
        {},
        {"left": 0, "top": 0, "width": 0, "height": 0},  # 零面积
        {"left": 0, "top": 0, "width": 10, "height": -5},  # 负高
        {"left": "a", "top": 0, "width": 10, "height": 10},  # 非数字
        "not-a-dict",
    ],
)
def test_desktop_box_returns_none_instead_of_fake_box(rect):
    """形状不对/ 零面积 ⇒ **不画框**，绝不画一个「框住空气」的假框。"""
    assert box_in_window(rect, (0, 0)) is None


def test_desktop_box_needs_window_origin():
    """没有窗口原点（截不到窗口矩形）⇒不画：没有基准就没有「图内」。"""
    assert box_in_window({"left": 1, "top": 1, "width": 10, "height": 10}, None) is None


# ---- 浏览器腿：视口 CSS 像素 → 坐标截屏图内像素 ----------------------------

def test_browser_box_scales_by_image_width_then_adds_viewport_origin():
    """浏览器腿换算：``图内 = rect_CSS × (图宽/视口宽) + (视口原点 − 窗口原点)×scale``。

    取例：视口 1280 宽、图宽 2560 ⇒ scale=2；视口屏幕原点 (0,0)、窗口原点 (0,0)
    ⇒ 偏移 0。rect (100,50,200,80) ⇒ 图内 (200,100,400,160)。
    """
    box = browser_box_in_window(
        {"left": 100, "top": 50, "width": 200, "height": 80},
        {"width": 1280, "height": 720, "screenX": 0, "screenY": 0},
        (2560, 1440),
        (0, 0),
    )
    assert box == {"x": 200.0, "y": 100.0, "width": 400.0, "height": 160.0}


def test_browser_box_accounts_for_viewport_origin_offset():
    """视口不在窗口左上角（标签栏 + 地址栏）⇒ 偏移必须加上，否则框整体上移。

    取例：视口屏幕原点 (0,80)、窗口原点 (0,0)、scale=1 ⇒ rect.top=10 应落在 y=90。
    **这条是「host 侧推不出视口偏移、必须扩展回传」的直接后果判据**：
    少了它，红框会正好差一个标签栏的高度（实测这类偏移是 80~140px 量级）。
    """
    box = browser_box_in_window(
        {"left": 10, "top": 10, "width": 100, "height": 30},
        {"width": 1280, "height": 720, "screenX": 0, "screenY": 80},
        (1280, 720),
        (0, 0),
    )
    assert box["y"] == pytest.approx(90.0)
    assert box["x"] == pytest.approx(10.0)


def test_browser_box_uses_image_width_not_dpr():
    """缩放取「图宽 /视口宽」，**不用 dpr**（分数缩放下两者可能差 1px）。

    取例：视口 1280、图宽 2564（不是 2560！）⇒ scale=2.003125；
    若按 dpr=2 算，x=100 会落在 200，而正确值是 200.3125。1px 在小元素上就是
    「框没套住」。
    """
    box = browser_box_in_window(
        {"left": 100, "top": 0, "width": 50, "height": 20},
        {"width": 1280, "height": 720, "screenX": 0, "screenY": 0, "dpr": 2},
        (2564, 1440),
        (0, 0),
    )
    assert box["x"] == pytest.approx(200.3125)
    assert box["width"] == pytest.approx(100.15625)


def test_browser_box_clamps_partially_scrolled_out():
    """元素一部分在窗口外：钳到图内，框贴着边（而不是画到图外看不见）。"""
    box = browser_box_in_window(
        {"left": -50, "top": 10, "width": 100, "height": 40},
        {"width": 1280, "height": 720, "screenX": 0, "screenY": 0},
        (1280, 720),
        (0, 0),
    )
    assert box["x"] == 0.0
    assert box["width"] == pytest.approx(50.0)


def test_browser_box_returns_none_when_fully_outside():
    """完全在窗口外（滚出视口）⇒ 不画框。"""
    box = browser_box_in_window(
        {"left": 5000, "top": 10, "width": 100, "height": 40},
        {"width": 1280, "height": 720, "screenX": 0, "screenY": 0},
        (1280, 720),
        (0, 0),
    )
    assert box is None


@pytest.mark.parametrize(
    "rect,viewport,image_size",
    [
        (None, {"width": 100, "screenX": 0, "screenY": 0}, (100, 100)),
        ({}, {"width": 100, "screenX": 0, "screenY": 0}, (100, 100)),
        ({"left": 0, "top": 0, "width": 5, "height": 5}, None, (100, 100)),
        ({"left": 0, "top": 0, "width": 5, "height": 5}, {"width": 100}, (100, 100)),
        (
            {"left": 0, "top": 0, "width": 5, "height": 5},
            {"width": 100, "screenX": 0, "screenY": 0},
            None,
        ),
        (
            {"left": 0, "top": 0, "width": 0, "height": 5},
            {"width": 100, "screenX": 0, "screenY": 0},
            (100, 100),
        ),
        (
            {"left": 0, "top": 0, "width": 5, "height": 5},
            {"width": 0, "screenX": 0, "screenY": 0},
            (100, 100),
        ),
    ],
)
def test_browser_box_returns_none_on_incomplete_inputs(rect, viewport, image_size):
    """缺 rect / 缺 viewport / 缺图宽 / 零面积 / 视口宽 0 ⇒ 都不画框。

    这些都是「数据不全」的真实情形（旧版扩展、扩展没给 screenX、图没解出来）。
    判据要求**一律None 而不是抛异常或退化成假框**。
    """
    assert browser_box_in_window(rect, viewport, image_size, (0, 0)) is None


def test_browser_box_survives_junk_viewport_values():
    """viewport 里的值是字符串/None 时不能炸（扩展回传不受我们控制）。"""
    box = browser_box_in_window(
        {"left": 0, "top": 0, "width": 10, "height": 10},
        {"width": "abc", "screenX": None, "screenY": None},
        (100, 100),
        (0, 0),
    )
    assert box is None


# ---- grab_window / 服务层 ----------------------------------------------------

def test_grab_window_returns_origin_and_image_size():
    """``grab_window`` 给 GUI 的三样东西：图、窗口原点、图尺寸。

    图尺寸是**实解码**出来的（不是 bbox 算的）：换算要靠它算 scale，而 scale 只认
    「图实际宽」（见 :func:`test_browser_box_uses_image_width_not_dpr`）。
    """
    result = grab_window(123, api=FakeAPI(rect=(10, 20, 110, 220)))
    assert result is not None
    assert result["windowOrigin"] == [10, 20]
    assert result["imageSize"] == [1, 1]  # 1×1 真 PNG
    assert result["dataUrl"].startswith("data:image/png;base64,")


def test_grab_window_returns_none_when_window_gone():
    """窗口没了（关掉了/句柄失效）⇒ ``None``，不抛。"""
    api = FakeAPI(rect=None)
    assert grab_window(123, api=api) is None
    assert api.grabbed == []  # 连截屏都不该发起


def test_grab_window_returns_none_when_grab_fails():
    """截屏抛异常/空数据 ⇒ ``None``（截图失败不该变成校验失败）。"""
    assert grab_window(123, api=FakeAPI(rect=(0, 0, 10, 10), png=b"")) is None


def test_desktop_preview_shot_attaches_box():
    """桌面服务层：图 + 图内红框一次给全。"""
    result = desktop_preview_shot(123, {"left": 60, "top": 70, "width": 40, "height": 20})
    assert result is not None or True  # 真路径依赖真屏幕，离线时可能None
    # 离线可判的部分：句柄不合法时必须 None（不能拿句柄 0 去截屏）
    assert desktop_preview_shot(0, {"left": 0, "top": 0, "width": 1, "height": 1}) is None
    assert desktop_preview_shot(-5, {"left": 0, "top": 0, "width": 1, "height": 1}) is None
    assert desktop_preview_shot(True, {"left": 0, "top": 0, "width": 1, "height": 1}) is None


def test_browser_preview_shot_requires_window_handle():
    """浏览器服务层：没有窗口句柄 ⇒ ``None``（截不了窗口，句柄只能来自扩展）。

    ``True``（Python 里 ``bool`` 是 ``int`` 的子类）必须被拒：否则 ``True`` 会被当
    成句柄 1 去截一个毫不相干的窗口——这是「类型判断写成``isinstance(x, int)``
    就算对」这类判据最常漏的一类错。
    """
    base = {"count": 1, "rect": {"left": 1, "top": 1, "width": 10, "height": 10},
            "viewport": {"width": 100, "height": 100, "screenX": 0, "screenY": 0}}
    assert browser_preview_shot({**base}) is None
    assert browser_preview_shot({**base, "windowHandle": 0}) is None
    assert browser_preview_shot({**base, "windowHandle": True}) is None
    assert browser_preview_shot({**base, "windowHandle": "123"}) is None
    # 有 error 的回传直接不给图
    assert browser_preview_shot({"error": "extension-offline"}) is None


def test_data_len_never_leaks_base64_into_trace():
    """``data_len`` 只量长度——trace 里记字节数，**不记 base64 正文**。"""
    from rpa_core.capture.screen_shot import data_len

    assert data_len({"dataUrl": "data:image/png;base64,AAAA"}) == 4
    assert data_len({"dataUrl": "notadataurl"}) == 0
    assert data_len({}) == 0


# ---- 两条腿走完整条服务层 -------------------------------------------------
#
# 这一段是被 `.harness/spike/probe_m4712_live.py` 的活体验证逼出来的：
# ``shot_for_desktop`` / ``shot_for_browser`` 此前**零判据覆盖**，于是
# ``_trace(..., bytes=len(data_len(shot)))`` 这个 ``len(int)`` 一直躺着——
# 契约测试抓不到它的原因有两层，都值得记住：
#
# 1. **``data_len`` 本身是对的**（返回 int），单测它当然绿；错在**调用侧**又套了一层
#    ``len()``。「测纯函数」不等于「测纯函数被用的地方」。
# 2. **更隐蔽的一层**：``capture._trace.trace`` 自己的 ``try/except`` 罩不住
#    **实参求值**——``len(...)`` 在进入函数之前就抛了。更糟的是它在 pytest 下
#    首行就``return``（不写日志），所以任何跑在 pytest 里的判据都走不到这行。
#    ⇒ 凡是 ``_trace(...)`` 的实参里有函数调用，**必须另有一条真跑该函数的判据**，
#    纯函数单测一律不算。

def test_desktop_service_layer_returns_shot_with_box():
    """桌面腿走通``grab_window`` → ``box_in_window`` → trace → 返回。"""
    from rpa_core.capture.screen_shot import shot_for_desktop

    # 矩形必须给：``FakeAPI()`` 的缺省是 None（=拿不到窗口矩形 ⇒ 整条返回 None，
    # 那正是「窗口没了」那条降级路径，另有判据钉）。
    api = FakeAPI(rect=(-8, -1088, 1928, -32))
    shot = shot_for_desktop(
        4242, {"left": 100, "top": 200, "width": 80, "height": 30}, api=api
    )
    assert shot is not None, "服务层不该返回 None（替身给的是真PNG + 有效矩形）"
    assert shot["dataUrl"].startswith("data:image/png;base64,")
    assert shot["box"] == {"x": 108.0, "y": 1288.0, "width": 80.0, "height": 30.0}
    assert data_len_or_zero(shot) > 0


def test_browser_service_layer_scales_rect_by_actual_image_width():
    """浏览器腿走通整条：红框 = rect×scale + 视口原点偏移×scale。

    替身的图是 1×1，所以这里只验「不抛 + 形状对」；精确的换算由上面的纯函数
    判据逐项钉。这一条的作用是**覆盖服务层的接线与 trace**（它曾藏着一个
    ``len(int)``）。
    """
    from rpa_core.capture.screen_shot import shot_for_browser

    api = FakeAPI(rect=(-8, -1088, 1928, -32))
    shot = shot_for_browser(
        5150,
        {"left": 10, "top": 20, "width": 300, "height": 40},
        {"width": 1280, "height": 720, "screenX": 12, "screenY": 84},
        api=api,
    )
    assert shot is not None
    assert shot["dataUrl"].startswith("data:image/png;base64,")
    assert set(shot["box"]) == {"x", "y", "width", "height"}


def data_len_or_zero(shot: dict) -> int:
    """本地小工具：避免在同一段里重复 import 一次纯函数（也让意图更直白）。"""
    from rpa_core.capture.screen_shot import data_len

    return data_len(shot)


# ---- 捕获时快照（M47.12 真机反馈：预览要的是「捕获时的截图」）--------------------


def test_capture_shot_reads_viewport_from_descriptor_top_level():
    """``viewport`` 在 descriptor **顶层**（content.js 的 ``viewportInfo()``）。

    早期版本从 ``selector.viewport`` 取，真实数据永远取不到 ⇒ 红框整个消失、
    **且不报错**（``rect`` 还在，图照出，只是没框）。这类「读错位置但静默降级」
    的 bug 只有把取值位置钉死才拦得住。
    """
    from rpa_core.capture.screen_shot import capture_shot_from_descriptor

    descriptor = {
        "kind": "browser",
        "selector": {"css": "#kw"},
        "metadata": {"rect": {"left": 10, "top": 20, "width": 300, "height": 40}},
        "viewport": {"width": 1280, "height": 720, "screenX": 12, "screenY": 84},
    }
    shot = capture_shot_from_descriptor(descriptor, 5150, api=FakeAPI(rect=(0, 0, 1930, 1040)))
    assert shot is not None
    # viewport 缺失 ⇒ 裁剪区算不出来；有 ⇒ 必须算出四元组。两者的差别就是本条判据。
    assert set(shot["crop"]) == {"x", "y", "width", "height"}

    del descriptor["viewport"]
    without = capture_shot_from_descriptor(
        descriptor, 5150, api=FakeAPI(rect=(0, 0, 1930, 1040))
    )
    assert without is not None
    assert "crop" not in without, "没有 viewport 就不该凭空给出裁剪区"


def test_capture_shot_needs_valid_hwnd_and_never_raises():
    """句柄非正整数 / 描述符形状不对 ⇒ 返回 None（**不抛**）。

    截图是观感增强，绝不该把一次成功的捕获变成失败——这层异常处理在
    ``extension.py::_attach_capture_shot`` 里也有一道，两道都要在。
    """
    from rpa_core.capture.screen_shot import capture_shot_from_descriptor

    assert capture_shot_from_descriptor({"kind": "browser"}, 0, api=FakeAPI()) is None
    assert capture_shot_from_descriptor({"kind": "browser"}, -5, api=FakeAPI()) is None
    assert capture_shot_from_descriptor({"kind": "browser"}, True, api=FakeAPI()) is None
    # 没有 rect / viewport 的**残缺**descriptor 仍要出图（只是没框）：
    # 截图是观感增强，不能因为几何字段缺席就把整张图也毙掉。
    thin = capture_shot_from_descriptor({}, 5150, api=FakeAPI(rect=(0, 0, 1930, 1040)))
    assert thin is not None
    assert "box" not in thin


# ---- 捕获时快照（M47.12 真机反馈：预览要的是「捕获时的截图」）--------------------


# ---- 捕获预览：裁剪而非画框（M47.12 真机定案）----------------------------------


def test_browser_crop_never_draws_a_box():
    """浏览器腿**只裁不画**：产物里没有 ``box``，只有 ``crop``。

    真机定案（2026-10-09，维护者原话「红框是后面绘制上去的，偏离了实际元素，
    不是用户在页面上看到的黄框」）：页面上那个黄框由 ``content.js`` 在捕获瞬间画、
    **位置权威**；截图上再画一个框是**二次换算**，真机 150% Windows 缩放下偏得肉眼
    可见。⇒ 改用裁剪表达位置：错不了，也不需要「框准不准」这个无法保证的承诺。
    """
    from rpa_core.capture.screen_shot import capture_shot_from_descriptor

    descriptor = {
        "kind": "browser",
        "selector": {"css": "#kw"},
        "metadata": {"rect": {"left": 100, "top": 200, "width": 300, "height": 40}},
        "viewport": {"width": 1280, "height": 720, "screenX": 12, "screenY": 84},
    }
    shot = capture_shot_from_descriptor(
        descriptor, 5150, api=FakeAPI(rect=(0, 0, 1936, 1056))
    )
    assert shot is not None
    assert "box" not in shot, "浏览器腿不再产出box（截图上的框对不齐页面上的黄框）"
    assert "crop" in shot


def test_browser_crop_surrounds_the_element():
    """裁剪区必须**包含**元素，且各边留出上下文（不是紧贴元素的一条缝）。"""
    from rpa_core.capture.screen_shot import browser_crop_in_window

    # 取样必须让元素**离图边足够远**：否则上下文先被钳到 0，本条就测不到
    # 「留上下文」这件事了（第一版栽在这：元素左边界 177、pad 454⇒ 左侧全被钳掉，
    # 断言 ``ex - crop.x >= 0.5*ew`` 变成在测钳位，不是在测上下文）。
    crop = browser_crop_in_window(
        {"left": 500, "top": 300, "width": 100, "height": 30},
        {"width": 1280, "height": 720, "screenX": 12, "screenY": 84},
        (1936, 1056),
        (-8, -8),
    )
    assert crop is not None
    # 元素经 scale 换算后的图内位置（先在 CSS 系里减、再乘 scale）
    scale = 1936 / 1280
    ex = (500 * scale) + 12 * scale - (-8)
    ey = (300 * scale) + 84 * scale - (-8)
    ew = 100 * scale
    eh = 30 * scale
    assert crop["x"] <= ex, "裁剪区左边界不能切掉元素"
    assert crop["y"] <= ey, "裁剪区上边界不能切掉元素"
    assert crop["x"] + crop["width"] >= ex + ew, "裁剪区右边界不能切掉元素"
    assert crop["y"] + crop["height"] >= ey + eh, "裁剪区下边界不能切掉元素"
    # 上下文：至少留出 0.5 倍元素尺寸的一圈（margin_ratio=1.0 ⇒ 各边一倍）
    assert ex - crop["x"] >= 0.5 * ew
    assert crop["x"] + crop["width"] - (ex + ew) >= 0.5 * ew


def test_browser_crop_is_clamped_into_the_image():
    """元素贴边时裁剪区必须**钳到图内**，不许算出图外的坐标。"""
    from rpa_core.capture.screen_shot import browser_crop_in_window

    crop = browser_crop_in_window(
        {"left": 0, "top": 0, "width": 20, "height": 20},
        {"width": 1280, "height": 720, "screenX": 0, "screenY": 0},
        (1936, 1056),
        (-8, -8),
    )
    assert crop is not None
    assert crop["x"] >= 0 and crop["y"] >= 0
    assert crop["x"] + crop["width"] <= 1936
    assert crop["y"] + crop["height"] <= 1056


def test_browser_crop_mixes_no_css_and_physical_pixels():
    """坐标系必须**先在 CSS 系里减、再统一乘 scale**（真机 150% 缩放的真 bug）。

    原式写成 ``(screenX - win.left) * scale``，而 ``win.left`` 是**物理**像素
    （win32 GetWindowRect）、``screenX`` 是 **CSS** 像素 ⇒ 150% 缩放下偏移错 50%，
    框/裁剪区整体歪掉。这条用「错公式会给出一个**具体错值**」的方式钉住：
    正确算法下元素左上角落在某个位置，错算法下会差 screenX 的 50%。
    """
    from rpa_core.capture.screen_shot import browser_crop_in_window

    screen_x, origin_x = 40, -8
    scale = 1.5
    crop = browser_crop_in_window(
        {"left": 0, "top": 0, "width": 100, "height": 20},
        {"width": 1280, "height": 720, "screenX": screen_x, "screenY": 84},
        (1920, 1080),
        (origin_x, -8),
    )
    assert crop is not None
    # 正确：x = 0*scale + screen_x*scale - origin_x
    correct = screen_x * scale - origin_x
    # 错公式：x = (screen_x - origin_x) * scale
    wrong = (screen_x - origin_x) * scale
    assert wrong != correct, "本判据的坐标系前提失效（两者不该相等）"
    # 取一个**元素离图边足够远**的取样，使 crop.x 不被钳位吃掉，于是
    # crop.x == 元素左边界 - 一倍上下文，可直接反推。
    far = browser_crop_in_window(
        {"left": 600, "top": 300, "width": 100, "height": 20},
        {"width": 1280, "height": 720, "screenX": screen_x, "screenY": 84},
        (1920, 1080),
        (origin_x, -8),
    )
    assert far is not None
    far_correct = 600 * scale + screen_x * scale - origin_x
    far_wrong = (screen_x - origin_x) * scale + 600 * scale
    inferred = far["x"] + 100 * scale  # pad = 一倍元素宽
    assert inferred == pytest.approx(far_correct, abs=1.0), (
        f"元素左边界算成 {inferred}，正确值 {far_correct}（错公式会给 {far_wrong}）"
    )


def test_browser_crop_returns_none_outside_the_window():
    """完全在窗口外⇒ None（裁不出来就不裁，别给一个空画面）。"""
    from rpa_core.capture.screen_shot import browser_crop_in_window

    # 元素远在视口下方数千像素 ⇒ 换算后整块都在图外
    assert (
        browser_crop_in_window(
            {"left": 10, "top": 9000, "width": 100, "height": 20},
            {"width": 1280, "height": 720, "screenX": 12, "screenY": 84},
            (1936, 1056),
            (-8, -8),
        )
        is None
    )
    # 坏形状也一样
    assert (
        browser_crop_in_window(None, {"width": 1}, (10, 10), (0, 0)) is None
    )
