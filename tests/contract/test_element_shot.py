"""元素缩略图（M102 S1）：捕获那一刻裁一张小图存进元素库。

三组判据，缺一组就会留下一个「事后才发现」的坑：

1. **几何**：只在既有换算上开一层（桌面腿 :func:`box_in_window`、浏览器腿
   :func:`browser_box_in_window`），不许出现第二套坐标算术——150% 缩放下二次换算
   会偏，这是 ``screen_shot.py`` 头注释里的红线。
2. **落盘**：文件名是**内容哈希**（``shots/<hash>.png``），所以改名、复制、
   覆盖重捕都不必再动附属文件；同一张图重复存不重写。资产里存的是**相对路径字符串**
   ——``ElementDescriptor.screenshot`` 就是 ``str | None``（M92/P1 对齐影刀口径），
   塞 dict 进去会让 ``element validate`` 把资产判成非法。
3. **降级**：拿不到 rect / 窗口 / 图 / PIL 一律 ``None``。缩略图是增强，绝不能
   把一次成功的捕获拖成失败。

第 2 组里有一条**安全**判据（路径穿越），它不是功能需求：``screenshot`` 来自
盘上的 JSON，人改过了也不能让「显示缩略图」变成任意文件读取。
"""

from __future__ import annotations

import base64
import hashlib
import io
import time

import pytest

from rpa_core.capture import screen_shot as ss
from rpa_core.capture.screen_shot import (
    element_shot_from_descriptor,
    element_shot_payload,
    read_element_shot,
    store_element_shot,
)

PIL = pytest.importorskip("PIL", reason="元素缩略图需要 Pillow（与 grab_window 同源依赖）")
from PIL import Image  # noqa: E402 - importorskip 之后才能导


def _window_png(width: int = 200, height: int = 100) -> str:
    """造一张 ``width×height`` 的纯色 PNG dataUrl（截图的替身，不碰真屏）。"""
    image = Image.new("RGBA", (width, height), (20, 120, 200, 255))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode("ascii")


def _desktop_descriptor(rect: dict | None = None, handle: int = 4242) -> dict:
    return {
        "kind": "desktop",
        "metadata": {
            "rect": rect or {"left": 10, "top": 20, "width": 30, "height": 15},
            "windowHandle": handle,
        },
    }


class FakeScreenAPI:
    """``_ScreenAPI`` 替身：只回答「窗口矩形」和「截那块屏」。"""

    def __init__(self, png: bytes | None = None, rect=(0, 0, 200, 100)):
        self._png = png
        self._rect = rect
        self.grabbed: list[tuple[int, int, int, int]] = []

    def window_rect(self, hwnd):
        return self._rect

    def grab(self, bbox):
        self.grabbed.append(bbox)
        return self._png if self._png is not None else self._png_bytes()

    def window_exists(self, hwnd):
        return True

    def window_dpi(self, hwnd):
        return {"dpi": 96, "scale": 1.0}

    def _png_bytes(self) -> bytes:
        import base64

        return base64.b64decode(_window_png().split(",", 1)[-1])


# ---- 1. 几何：裁到元素，复用既有换算 ---------------------------------------

def test_element_shot_crops_to_the_element_box():
    """桌面腿：图内像素 = 屏幕矩形 − 窗口原点，再按 PAD 外扩一点点。"""
    shot = element_shot_from_descriptor(
        _desktop_descriptor(),
        shot={"dataUrl": _window_png(), "windowOrigin": (0, 0), "imageSize": (200, 100)},
    )
    assert shot is not None
    assert shot["png"][:8] == b"\x89PNG\r\n\x1a\n"
    # rect 30×15 + 四周各 PAD(2) ⇒ 34×19
    assert (shot["width"], shot["height"]) == (30 + 2 * ss._SHOT_PAD, 15 + 2 * ss._SHOT_PAD)
    assert shot["scale"] == 1.0


def test_element_shot_desktop_leg_uses_box_in_window(monkeypatch):
    """桌面腿必须走 ``box_in_window``——另写一套换算是这个模块的明令红线。"""
    calls: list[object] = []
    real = ss.box_in_window

    def spy(rect, origin):
        calls.append((rect, origin))
        return real(rect, origin)

    monkeypatch.setattr(ss, "box_in_window", spy)
    shot = element_shot_from_descriptor(
        _desktop_descriptor(),
        shot={"dataUrl": _window_png(), "windowOrigin": (0, 0), "imageSize": (200, 100)},
    )
    assert shot is not None
    assert len(calls) == 1, "桌面腿必须且只能调一次 box_in_window"


def test_element_shot_browser_leg_uses_browser_geometry(monkeypatch):
    """浏览器腿走 ``browser_box_in_window``：视口 CSS 像素 ≠ 窗口像素。

    ★ ``viewport`` 放**顶层**。这是 ``content.js::buildDescriptor`` 的真实形状；
    本用例原来把它写在 ``selector.viewport``，于是「读错位置 ⇒ 网页元素恒无图」
    在测试里永远是绿的（M102 S2b 现场：桌面有截图、网页没有）。
    """
    calls: list[object] = []
    real = ss.browser_box_in_window

    def spy(rect, viewport, image_size):
        calls.append(viewport)
        return real(rect, viewport, image_size)

    monkeypatch.setattr(ss, "browser_box_in_window", spy)
    descriptor = {
        "kind": "browser",
        "viewport": {"width": 800, "height": 600, "outerWidth": 810, "outerHeight": 640},
        "metadata": {"rect": {"left": 100, "top": 100, "width": 100, "height": 20}},
        "selector": {"css": "#search"},
    }
    shot = element_shot_from_descriptor(
        descriptor,
        shot={"dataUrl": _window_png(810, 640), "windowOrigin": (0, 0), "imageSize": (810, 640)},
    )
    assert shot is not None
    assert len(calls) == 1
    # 读到的必须就是顶层那份（读错位置时这里是 None，图也就没了）
    assert calls[0] == descriptor["viewport"]
    # 视口原点 8/32 → 图内 (108,132)，裁 100×20 后仍是 100×20 加 PAD
    assert (shot["width"], shot["height"]) == (100 + 2 * ss._SHOT_PAD, 20 + 2 * ss._SHOT_PAD)


def test_element_shot_viewport_shot_uses_scale_only():
    """视口图（扩展 ``captureVisibleTab``）：rect 已经是视口坐标 ⇒ 只乘一个缩放比。

    ★ 这条用的是**真机现场的数**：视口 800 宽、窗口 1920 宽（Edge 开着侧栏）。
    窗口图口径在这里会算出负的 origin_y（把 146 的水平边框当垂直边框），元素被判成
    「滚出窗口」；视口图口径只看「图宽 / 视口宽」，侧栏多宽完全无关。
    """
    descriptor = {
        "kind": "browser",
        "viewport": {"width": 800, "height": 600, "outerWidth": 1920, "outerHeight": 1040},
        "metadata": {"rect": {"left": 400, "top": 300, "width": 100, "height": 20}},
        "selector": {"css": "#search"},
    }
    shot = element_shot_from_descriptor(
        descriptor,
        shot={
            "dataUrl": _window_png(1600, 1200),
            "imageSize": [1600, 1200],
            "space": "viewport",
        },
    )
    assert shot is not None
    # dpr=2 ⇒ 元素 200×40 图内像素，加两侧 PAD；没有兜底（兜底会是 512 长边的整图）
    assert (shot["width"], shot["height"]) == (200 + 2 * ss._SHOT_PAD, 40 + 2 * ss._SHOT_PAD)


def test_element_shot_viewport_shot_without_image_size():
    """视口图**没有 imageSize**：host 只挂 dataUrl + space，尺寸必须从图自己量。

    ★ 2026-11-20 真机「网页元素还是没有截图」的根因：trace 里
    ``capture_shot mark=viewport`` 打了、``element_shot`` 一行都没有——消费侧看到
    imageSize 缺失就静默 return None（连 trace 都不打）。上一条用例自己塞了
    imageSize，所以判据一路绿到维护者现场。
    """
    descriptor = {
        "kind": "browser",
        "viewport": {"width": 100, "height": 80},
        "metadata": {"rect": {"left": 10, "top": 20, "width": 30, "height": 12}},
        "selector": {"css": "#search"},
    }
    shot = element_shot_from_descriptor(
        descriptor,
        shot={"dataUrl": _window_png(100, 80), "space": "viewport"},
    )
    assert shot is not None
    assert (shot["width"], shot["height"]) == (
        30 + 2 * ss._SHOT_PAD,
        12 + 2 * ss._SHOT_PAD,
    )


def test_element_shot_browser_without_viewport_falls_back_to_full_image():
    """几何算不出来（没有视口信息）⇒ 给整图缩略图，**但绝不许猜坐标**。

    浏览器腿的图就是「用户当时看到的那一页」，仍有辨识价值；而按窗口左上角硬套
    会让元素整体偏上一截（旧 bug 的复发路径），所以宁可不裁。
    """
    descriptor = {
        "kind": "browser",
        "metadata": {"rect": {"left": 100, "top": 100, "width": 100, "height": 20}},
        "selector": {"css": "#search"},
    }
    shot = element_shot_from_descriptor(
        descriptor,
        shot={"dataUrl": _window_png(810, 640), "windowOrigin": (0, 0), "imageSize": (810, 640)},
    )
    assert shot is not None
    assert max(shot["width"], shot["height"]) == ss._SHOT_MAX_EDGE  # 整图压到长边上限
    assert (shot["width"], shot["height"]) == (
        ss._SHOT_MAX_EDGE,
        max(1, int(round(640 * ss._SHOT_MAX_EDGE / 810))),
    )


def test_element_shot_desktop_without_rect_gives_up():
    """桌面腿不兜底：rect 来自 UIA，缺了就是元素没了，整窗图会误导成「元素还在」。"""
    descriptor = {"kind": "desktop", "metadata": {"windowHandle": 4242}}
    shot = element_shot_from_descriptor(
        descriptor,
        shot={"dataUrl": _window_png(200, 100), "windowOrigin": (0, 0), "imageSize": (200, 100)},
    )
    assert shot is None


def test_element_shot_shrinks_huge_tiles():
    """整窗级元素（浏览器根容器）不能存几百 KB 一张：长边收到上限以内。"""
    descriptor = _desktop_descriptor(rect={"left": 0, "top": 0, "width": 4000, "height": 3000})
    shot = element_shot_from_descriptor(
        descriptor,
        shot={
            "dataUrl": _window_png(4000, 3000),
            "windowOrigin": (0, 0),
            "imageSize": (4000, 3000),
        },
    )
    assert shot is not None
    assert max(shot["width"], shot["height"]) <= ss._SHOT_MAX_EDGE
    assert shot["scale"] < 1.0


# ---- 2. 落盘：内容哈希命名 + 去重 + 读回 -----------------------------------

def _fake_shot(png: bytes | None = None) -> dict:
    payload = png or b"\x89PNG\r\n\x1a\nfake-but-stable"
    return {"png": payload, "width": 34, "height": 19, "scale": 1.0}


def test_store_returns_a_relative_path_named_by_content_hash(tmp_path):
    """返回的是**相对路径字符串**（资产里 ``screenshot`` 就该是它），不是结构化字典。"""
    value = store_element_shot(_fake_shot(), tmp_path)
    digest = hashlib.sha256(_fake_shot()["png"]).hexdigest()[:16]
    assert value == f"shots/{digest}.png"
    assert (tmp_path / "shots" / f"{digest}.png").is_file()


def test_store_does_not_rewrite_identical_content(tmp_path):
    """同一张图重存不该改动文件（mtime 变了说明在重复写盘）。"""
    first = store_element_shot(_fake_shot(), tmp_path)
    assert first is not None
    target = tmp_path / first
    before = target.stat().st_mtime_ns
    time.sleep(0.01)
    assert store_element_shot(_fake_shot(), tmp_path) == first
    assert target.stat().st_mtime_ns == before


def test_different_content_gets_a_different_file(tmp_path):
    a = store_element_shot(_fake_shot(b"\x89PNG\r\n\x1a\nA"), tmp_path)
    b = store_element_shot(_fake_shot(b"\x89PNG\r\n\x1a\nB"), tmp_path)
    assert a is not None and b is not None and a != b
    assert len(list((tmp_path / "shots").glob("*.png"))) == 2


def test_read_returns_the_same_bytes_that_were_stored(tmp_path):
    value = store_element_shot(_fake_shot(), tmp_path)
    assert read_element_shot(tmp_path, value) == _fake_shot()["png"]


def test_read_returns_none_for_missing_or_broken_entry(tmp_path):
    assert read_element_shot(tmp_path, None) is None
    assert read_element_shot(tmp_path, "") is None
    assert read_element_shot(tmp_path, "shots/deadbeefdeadbeef.png") is None


def test_read_refuses_path_traversal(tmp_path):
    """``screenshot`` 是盘上的字符串，人改过也不能读出去。

    ★ 那个「越界」的文件必须**真的存在且可读**：第一版把 secret.png 写在元素目录
      里面，于是 ``../secret.png`` 根本不存在、照样返回 None——判据恒绿，注入
      ``if False:`` 都打不红（M102 负向验证当场抓到）。所以下面还留一条正例：
      合法路径必须读得回来，否则「拒绝越界」和「文件不存在」分不开。
    """
    elements = tmp_path / "elements"
    (elements / "shots").mkdir(parents=True)
    (tmp_path / "secret.png").write_bytes(b"not yours")
    assert read_element_shot(elements, "../secret.png") is None
    assert read_element_shot(elements, "shots/../../secret.png") is None
    # 正例：目录内且真实存在 ⇒ 读得到（这条不在就是假绿灯）
    (elements / "shots" / "ok.png").write_bytes(b"fine")
    assert read_element_shot(elements, "shots/ok.png") == b"fine"


def test_payload_is_a_data_url_on_demand(tmp_path):
    value = store_element_shot(_fake_shot(), tmp_path)
    url = element_shot_payload(tmp_path, value)
    assert url is not None and url.startswith("data:image/png;base64,")
    assert element_shot_payload(tmp_path, "shots/none.png") is None


# ---- 3. 降级：任何一环不行就 None，绝不抛 ----------------------------------

def test_no_rect_means_no_shot():
    assert element_shot_from_descriptor({"kind": "desktop", "metadata": {}}) is None
    assert element_shot_from_descriptor({"kind": "desktop", "metadata": None}) is None
    assert element_shot_from_descriptor(None) is None  # 形状不对也不许抛


def test_no_window_means_no_shot(monkeypatch):
    """既没传 hwnd、资产里也没记窗口句柄 ⇒ 截不到窗口，直接 None。"""
    descriptor = _desktop_descriptor(handle=0)
    assert element_shot_from_descriptor(descriptor) is None


def test_grab_failure_means_no_shot():
    descriptor = _desktop_descriptor()
    api = FakeScreenAPI(png=b"")
    api._png = b""
    # window_rect 给了但 grab 返回空 ⇒ grab_window 自己返回 None，这里照样降级
    assert element_shot_from_descriptor(descriptor, api=api) is None


def test_garbage_data_url_means_no_shot():
    descriptor = _desktop_descriptor()
    shot = {"dataUrl": "data:image/png;base64,ZZZ", "windowOrigin": (0, 0), "imageSize": (10, 10)}
    assert element_shot_from_descriptor(descriptor, shot=shot) is None


def test_element_outside_the_window_means_no_shot():
    """元素滚出视口 ⇒ 不存空图（与 preview 的口径一致）。"""
    descriptor = _desktop_descriptor(rect={"left": 900, "top": 900, "width": 50, "height": 20})
    shot = {"dataUrl": _window_png(), "windowOrigin": (0, 0), "imageSize": (200, 100)}
    assert element_shot_from_descriptor(descriptor, shot=shot) is None


def test_store_degrades_when_directory_is_unwritable(tmp_path):
    """``elements/`` 被人占成一个文件 ⇒ 存不进去，返回 None（不抛）。"""
    blocker = tmp_path / "elements"
    blocker.write_bytes(b"i am a file, not a dir")
    assert store_element_shot(_fake_shot(), blocker) is None


def test_store_rejects_a_shot_without_png():
    assert store_element_shot(None, "/tmp/never-used") is None
    assert store_element_shot({"png": b""}, "/tmp/never-used") is None
    assert store_element_shot({"png": "bytes-as-str"}, "/tmp/never-used") is None
