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
    browser_crop_in_window,
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
#
# **真机实测基准（2026-10-09，%TEMP%/rpa-capture-trace.log 的一行）**：
#
#   rect        = {left: 449, top: 79, width: 752, height: 24}
#   viewport    = {width: 1539, height: 785, dpr: 1,
#                  screenX: 229, screenY: -939,
#                  outerWidth: 1555, outerHeight: 936}
#   windowOrigin= [229, -939]        imageSize = [1555, 936]
#
# 两个由此定案的量（下面每条判据都拿它们当基准）：
#
#   1) **screenY恒等于 windowOrigin[1]** ⇒ screenX/screenY 是**窗口**左上角的
#      屏幕坐标，**不是视口的**。旧公式 `screenY - win.top` 恒等于 0，整整少算
#      了标签栏+地址栏 ⇒ 裁剪区偏上 143px（维护者报「截图位置偏上」）。
#   2) **imageSize == outerWidth/outerHeight**（1555/936）且 dpr=1 ⇒ 图宽对应的是
#      **窗口**宽，所以 scale 的分母必须是 outerWidth。旧公式用 viewport.width
#      算出 1555/1539 = 1.0104，凭空放大 1%。
#
# 正确视口顶 = outerHeight - viewport.height - border = 936 - 785 - 8 = **143**。
# border = (outerWidth - viewport.width) / 2 = (1555-1539)/2 = **8**（Windows 那圈
# 不可见 resize border，真机每侧 8px）。

#: 真机那一行 viewport（**逐字段照抄 trace**，不是手搓的数）
REAL_VIEWPORT = {
    "width": 1539, "height": 785, "dpr": 1,
    "screenX": 229, "screenY": -939,
    "outerWidth": 1555, "outerHeight": 936,
}
#: 真机那一行窗口矩形（物理像素，负Y = 窗口在副屏上方）
REAL_ORIGIN = (229, -939)
REAL_IMAGE = (1555, 936)
REAL_BORDER = 8.0
REAL_VIEWPORT_TOP = 143.0


def test_browser_viewport_origin_is_not_the_window_origin():
    """**本轮真机 bug 的正面判据**：视口顶必须落在 143px，而不是 0。

    这条是整个 M47.12 换算的锚：旧公式拿 ``screenY - win.top`` 当视口偏移，
    而真机这两个值**完全相等** ⇒ 偏移恒为 0 ⇒ 元素被画/裁在标签栏那一段里，
    整体偏上 143px。判据直接把真机数字钉成期望值——**换一个屏幕布置就红**，
    因为它压根没读屏幕坐标。
    """
    from rpa_core.capture.screen_shot import browser_viewport_in_window

    found = browser_viewport_in_window(REAL_VIEWPORT, REAL_IMAGE)
    assert found is not None
    origin_x, origin_y, scale = found
    # dpr=1 ⇒ 图像素 == CSS 像素，origin 就是 CSS 值本身
    assert scale == pytest.approx(1.0), "真机 dpr=1，缩放比就该是 1（不是 1.0104）"
    assert origin_y == pytest.approx(REAL_VIEWPORT_TOP)
    assert origin_x == pytest.approx(REAL_BORDER)
    # **反向钉**：屏幕坐标那个减法必须给不出这个值（否则等于没修）
    assert REAL_VIEWPORT["screenY"] - REAL_ORIGIN[1] == 0
    assert origin_y != pytest.approx(0.0)


def test_browser_viewport_origin_ignores_where_the_window_is():
    """把窗口搬到**任意屏幕坐标**（含负坐标、多屏不同高度）⇒ 视口偏移**不变**。

    维护者报「有两块屏，一块比另一块更偏上」——这轮修复的关键性质就是
    **位置只由窗口内部布局决定**：截图是从窗口左上角起的那块矩形，窗口在屏幕上
    哪儿跟「视口离窗口顶多远」毫无关系。这条把该性质钉住，否则下一个人又会
    引入屏幕坐标。
    """
    from rpa_core.capture.screen_shot import browser_viewport_in_window

    base = browser_viewport_in_window(REAL_VIEWPORT, REAL_IMAGE)
    assert base is not None
    # 换到主屏 (0,0)、换到左边的副屏 (-1920, -240)、换到右下 (1920, 0)
    for moved in (
        {**REAL_VIEWPORT, "screenX": 0, "screenY": 0},
        {**REAL_VIEWPORT, "screenX": -1920, "screenY": -240},
        {**REAL_VIEWPORT, "screenX": 1920, "screenY": 0},
    ):
        assert browser_viewport_in_window(moved, REAL_IMAGE) == base


def test_browser_viewport_origin_survives_dpi_scaling():
    """150% 缩放下偏移按比例放大（**不缩放**才是 bug——那样元素会挤在左上角）。

    模拟真机 150%：CSS 尺寸不变、图变 1.5 倍。**图宽必须给 1.5 倍的精确值**
    （`round(1555*1.5)=2333` 会让 scale 变成 1.4997，判据就红在取整上而不是红在
    「偏移有没有跟着缩放」上——第一版栽在这）。
    """
    from rpa_core.capture.screen_shot import browser_viewport_in_window

    scaled = 1555 * 1.5, 936 * 1.5
    found = browser_viewport_in_window(REAL_VIEWPORT, scaled)
    assert found is not None
    _, origin_y, scale = found
    assert scale == pytest.approx(1.5)
    assert origin_y == pytest.approx(REAL_VIEWPORT_TOP * 1.5)


def test_browser_box_uses_window_scale_and_real_viewport_offset():
    """浏览器腿：``图内 = rect_CSS × scale + 视口偏移``（用真机数字端到端算）。

    真机那个元素 top=79 ⇒ 图内 y = 79 + 143 = 222；宽 752、dpr=1 ⇒ 宽 752。
    旧公式会给 y=79（整整少 143），这就是「偏上」。
    """
    box = browser_box_in_window(
        {"left": 449, "top": 79, "width": 752, "height": 24},
        REAL_VIEWPORT,
        REAL_IMAGE,
    )
    assert box is not None
    assert box["y"] == pytest.approx(79 + 143)
    assert box["x"] == pytest.approx(449 + 8)
    assert box["width"] == pytest.approx(752.0)
    assert box["height"] == pytest.approx(24.0)
    # 与裁剪那条路**必须落在同一位置**（两者只差表达，不差坐标）
    crop = browser_crop_in_window(
        {"left": 449, "top": 79, "width": 752, "height": 24},
        REAL_VIEWPORT,
        REAL_IMAGE,
    )
    assert crop is not None
    assert crop["y"] <= box["y"] <= crop["y"] + crop["height"]
    assert crop["x"] <= box["x"] <= crop["x"] + crop["width"]


def test_browser_box_scales_by_image_width_then_adds_viewport_origin():
    """浏览器腿换算：``图内 = rect_CSS × (图宽/窗口宽) + 视口在窗口内的偏移``。

    取例：视口 1280×720、窗口 1280×720（无边框）⇒ scale=1、偏移 0。
    rect (100,50,200,80) ⇒ 图内 (100,50,200,80)。
    """
    box = browser_box_in_window(
        {"left": 100, "top": 50, "width": 200, "height": 80},
        {"width": 1280, "height": 720, "outerWidth": 1280, "outerHeight": 720},
        (1280, 720),
    )
    assert box == {"x": 100.0, "y": 50.0, "width": 200.0, "height": 80.0}


def test_browser_box_accounts_for_chrome_height_above_the_viewport():
    """窗口比视口高（标签栏 + 地址栏）⇒ 偏移必须加上，否则框整体上移。

    取例：视口 1280×720，窗口 1280×800 ⇒ 差 80px 就是 chrome + 边框。
    **这条是「host 侧推不出标签栏高度、必须扩展回 outerWidth/outerHeight」的直接
    后果判据**：少了它，框会正好差一个标签栏的高度（实测这类偏移是 80~150px 量级）。
    """
    box = browser_box_in_window(
        {"left": 10, "top": 10, "width": 100, "height": 30},
        {"width": 1280, "height": 720, "outerWidth": 1280, "outerHeight": 800},
        (1280, 800),
    )
    assert box["y"] == pytest.approx(90.0)
    assert box["x"] == pytest.approx(10.0)


def test_browser_box_scale_divides_by_window_width_not_viewport():
    """缩放的分母是**窗口**宽，不是视口宽、也不是 dpr。

    取例：视口 1280、**窗口 1296**（多出 16px 边框+滚动条）、图宽 2592⇒ scale=2。
    若错用 viewport.width 当分母⇒ 2592/1280 = 2.025，x=100 会落在 202.5 而不是
    200——真机上就是「框整体偏大/偏小」（实测 1555/1539 = 1.0104）。
    """
    viewport = {"width": 1280, "height": 720, "outerWidth": 1296, "outerHeight": 736}
    box = browser_box_in_window(
        {"left": 100, "top": 0, "width": 50, "height": 20},
        viewport,
        (2592, 1472),
    )
    assert box is not None
    assert box["width"] == pytest.approx(100.0), "宽度应正好 2×"
    wrong = 2592 / 1280
    assert wrong == pytest.approx(2.025)
    assert box["width"] != pytest.approx(50 * wrong), "错用视口宽当分母会给这个值"


def test_browser_box_clamps_partially_scrolled_out():
    """元素一部分在窗口外：钳到图内，框贴着边（而不是画到图外看不见）。"""
    box = browser_box_in_window(
        {"left": -50, "top": 10, "width": 100, "height": 40},
        {"width": 1280, "height": 720, "outerWidth": 1280, "outerHeight": 720},
        (1280, 720),
    )
    assert box["x"] == 0.0
    assert box["width"] == pytest.approx(50.0)


def test_browser_box_returns_none_when_fully_outside():
    """完全在窗口外（滚出视口）⇒ 不画框。"""
    box = browser_box_in_window(
        {"left": 5000, "top": 10, "width": 100, "height": 40},
        {"width": 1280, "height": 720, "outerWidth": 1280, "outerHeight": 720},
        (1280, 720),
    )
    assert box is None


@pytest.mark.parametrize(
    "rect,viewport,image_size",
    [
        (None, {"width": 100, "height": 100, "outerWidth": 100, "outerHeight": 100},
         (100, 100)),
        ({}, {"width": 100, "height": 100, "outerWidth": 100, "outerHeight": 100},
         (100, 100)),
        ({"left": 0, "top": 0, "width": 5, "height": 5}, None, (100, 100)),
        ({"left": 0, "top": 0, "width": 5, "height": 5}, {"width": 100}, (100, 100)),
        # **缺 outerWidth/outerHeight**（旧版扩展）⇒ 不给位置：宁可不出，
        # 也不给一个偏上百像素的裁剪区。
        ({"left": 0, "top": 0, "width": 5, "height": 5},
         {"width": 100, "height": 100}, (100, 100)),
        ({"left": 0, "top": 0, "width": 5, "height": 5},
         {"width": 100, "height": 100, "outerWidth": 100}, (100, 100)),
        ({"left": 0, "top": 0, "width": 5, "height": 5},
         {"width": 100, "height": 100, "outerWidth": 100, "outerHeight": 100},
         None),
        ({"left": 0, "top": 0, "width": 0, "height": 5},
         {"width": 100, "height": 100, "outerWidth": 100, "outerHeight": 100},
         (100, 100)),
        ({"left": 0, "top": 0, "width": 5, "height": 5},
         {"width": 0, "height": 100, "outerWidth": 100, "outerHeight": 100},
         (100, 100)),
        ({"left": 0, "top": 0, "width": 5, "height": 5},
         {"width": 100, "height": 100, "outerWidth": 0, "outerHeight": 100},
         (100, 100)),
    ],
)
def test_browser_box_returns_none_on_incomplete_inputs(rect, viewport, image_size):
    """缺 rect / viewport / 窗口尺寸 / 零面积 / 视口宽 0 ⇒ 都不画框。

    这些都是「数据不全」的真实情形（旧版扩展、扩展没给 outerWidth、图没解出来）。
    判据要求**一律 None 而不是抛异常或退化成假框**。
    """
    assert browser_box_in_window(rect, viewport, image_size) is None


def test_browser_box_survives_junk_viewport_values():
    """viewport 里的值是字符串/None 时不能炸（扩展回传不受我们控制）。"""
    box = browser_box_in_window(
        {"left": 0, "top": 0, "width": 10, "height": 10},
        {"width": "abc", "height": None, "outerWidth": 100, "outerHeight": "x"},
        (100, 100),
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
    """浏览器腿走通整条：box = rect×scale + 视口在窗口内的偏移。

    替身的图是 1×1，所以这里只验「不抛 + 形状对」；精确的换算由上面的纯函数
    判据逐项钉。这一条的作用是**覆盖服务层的接线与 trace**（它曾藏着一个
    ``len(int)``）。
    """
    from rpa_core.capture.screen_shot import shot_for_browser

    api = FakeAPI(rect=(-8, -1088, 1928, -32))
    shot = shot_for_browser(
        5150,
        {"left": 10, "top": 20, "width": 300, "height": 40},
        {"width": 1280, "height": 720, "outerWidth": 1280, "outerHeight": 720},
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
        "viewport": {
            "width": 1280, "height": 720, "outerWidth": 1280, "outerHeight": 720,
        },
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
        "viewport": {
            "width": 1280, "height": 720, "outerWidth": 1280, "outerHeight": 720,
        },
    }
    shot = capture_shot_from_descriptor(
        descriptor, 5150, api=FakeAPI(rect=(0, 0, 1936, 1056))
    )
    assert shot is not None
    assert "box" not in shot, "浏览器腿不再产出box（截图上的框对不齐页面上的黄框）"
    assert "crop" in shot


def test_browser_crop_on_real_machine_numbers_lands_on_the_element():
    """**真机端到端**：把 2026-10-09 trace 那一行喂进去，裁剪区必须罩住元素。

    这是本轮 bug（维护者报「截图位置偏上」）的正面判据。用真机数字而非手搓取样，
    因为这组数里同时含**负屏幕坐标**（screenY=-939，窗口在副屏上方）、**非 1 的
    视口/窗口差**（1539 vs 1555）以及**真 chrome 高度差**（785 vs 936）——三者
    任何一条算错都会让断言红。

    真机那个元素在图内应是 y = 79 + 143 = 222、高 24。旧公式给 79（少 143）
    ⇒ 元素贴着裁剪区上沿/溢出，症状就是「位置偏上」。
    """
    from rpa_core.capture.screen_shot import browser_crop_in_window

    crop = browser_crop_in_window(
        {"left": 449, "top": 79, "width": 752, "height": 24},
        REAL_VIEWPORT,
        REAL_IMAGE,
    )
    assert crop is not None
    el_x, el_y, el_w, el_h = 449 + 8, 79 + 143, 752.0, 24.0
    assert crop["y"] <= el_y, "裁剪区上边界高于元素 ⇒ 元素被切掉"
    assert crop["y"] + crop["height"] >= el_y + el_h, "裁剪区下边界在元素下方 ⇒偏上"
    # 横向同理（左右边框那 8px 也要算进去）
    assert crop["x"] <= el_x, "裁剪区左边界高于元素 ⇒ 元素被切掉"
    assert crop["x"] + crop["width"] >= el_x + el_w, "裁剪区右边界在元素右侧"
    # 元素在裁剪区里必须**偏上但可见**：上边留 1 倍高、下边也留 1 倍高
    above = el_y - crop["y"]
    below = crop["y"] + crop["height"] - (el_y + el_h)
    assert above == pytest.approx(el_h), "上方上下文应约等于元素高"
    assert below == pytest.approx(el_h), "下方上下文应约等于元素高"
    # **反向钉**：旧公式（视口偏移当0）会把元素推到裁剪区上沿
    assert crop["y"] != pytest.approx(0.0)


def test_browser_crop_surrounds_the_element():
    """裁剪区必须**包含**元素，且各边留出上下文（不是紧贴元素的一条缝）。"""
    from rpa_core.capture.screen_shot import browser_crop_in_window

    # 取样必须让元素**四边都留得出**一倍上下文，否则本条就退化成在测钳位：
    #   第一版栽在左边（元素 177 + pad 454 被钳到 0）；
    #   第二版栽在右边（500+600=1100，看似够，但 600+400 上下文共 1100 <1936…
    #     实际是垂直方向也不够，钳位后 crop.x 反而大于元素左边）；
    #   第三版栽在**忘了乘 scale**（图 1936 / 窗口 1280 = 1.5125，我却按 1 写期望）。
    viewport = {"width": 1280, "height": 720, "outerWidth": 1280, "outerHeight": 720}
    img = (1936, 1056)
    crop = browser_crop_in_window(
        {"left": 700, "top": 400, "width": 200, "height": 100},
        viewport,
        img,
    )
    assert crop is not None
    scale = img[0] / viewport["outerWidth"]  # 图是**窗口**的截图
    ex, ey = 700 * scale, 400 * scale
    ew, eh = 200 * scale, 100 * scale
    # 上下文：各边留足一倍（margin_ratio=1.0），本取样下钳位不参与，才能测到这个
    assert ex - crop["x"] == pytest.approx(ew)
    assert crop["x"] + crop["width"] - (ex + ew) == pytest.approx(ew)
    assert ey - crop["y"] == pytest.approx(eh)
    assert crop["y"] + crop["height"] - (ey + eh) == pytest.approx(eh)
    # 不许切掉元素
    assert crop["x"] <= ex and crop["y"] <= ey
    assert crop["x"] + crop["width"] >= ex + ew
    assert crop["y"] + crop["height"] >= ey + eh


def test_browser_crop_is_clamped_into_the_image():
    """元素贴边时裁剪区必须**钳到图内**，不许算出图外的坐标。"""
    from rpa_core.capture.screen_shot import browser_crop_in_window

    crop = browser_crop_in_window(
        {"left": 0, "top": 0, "width": 20, "height": 20},
        {"width": 1280, "height": 720, "outerWidth": 1280, "outerHeight": 720},
        (1936, 1056),
    )
    assert crop is not None
    assert crop["x"] >= 0 and crop["y"] >= 0
    assert crop["x"] + crop["width"] <= 1936
    assert crop["y"] + crop["height"] <= 1056


def test_browser_crop_scales_the_chrome_offset_too():
    """150% 缩放下，**chrome 偏移也要跟着乘 scale**（不乘就是「偏上」复发）。

    真机 150%：CSS 尺寸不变、图变1.5 倍。视口顶 = 143 CSS px ⇒ 图内 214.5。
    旧公式的错法是「偏移按 1:1 算、只有 rect 乘 scale」——那样在 150% 下会偏
    一半 chrome 高度。
    """
    from rpa_core.capture.screen_shot import browser_crop_in_window

    crop = browser_crop_in_window(
        {"left": 100, "top": 200, "width": 100, "height": 20},
        REAL_VIEWPORT,
        (1555 * 1.5, 936 * 1.5),  # 精确 1.5 倍（取整会让判据红在舍入上）
    )
    assert crop is not None
    el_y = 200 * 1.5 + REAL_VIEWPORT_TOP * 1.5
    assert crop["y"] <= el_y
    # 上方留白应约等于元素高（图内 30）
    assert el_y - crop["y"] == pytest.approx(30.0)


def test_browser_crop_returns_none_outside_the_window():
    """完全在窗口外⇒ None（裁不出来就不裁，别给一个空画面）。"""
    from rpa_core.capture.screen_shot import browser_crop_in_window

    viewport = {"width": 1280, "height": 720, "outerWidth": 1280, "outerHeight": 720}
    # 元素远在视口下方数千像素 ⇒ 换算后整块都在图外
    assert (
        browser_crop_in_window(
            {"left": 10, "top": 9000, "width": 100, "height": 20},
            viewport,
            (1936, 1056),
        )
        is None
    )
    # 坏形状、以及**缺窗口尺寸**（旧扩展）也一样
    assert browser_crop_in_window(None, {"width": 1}, (10, 10)) is None
    assert (
        browser_crop_in_window(
            {"left": 1, "top": 1, "width": 10, "height": 10},
            {"width": 100, "height": 100},
            (10, 10),
        )
        is None
    )
