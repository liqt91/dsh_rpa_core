"""M77：捕获期显式图像 proposal 的契约（替身截图，不依赖真窗口）。"""

from __future__ import annotations

import base64
import hashlib
import io

from rpa_core.capture import desktop_agent
from rpa_core.capture.image_proposal import make_image_proposal


def _shot(*, origin=(100, 100), size=(64, 64), dpi=96, scale=1.0) -> dict:
    from PIL import Image

    image = Image.new("RGB", size, (30, 60, 90))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return {
        "dataUrl": "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii"),
        "windowOrigin": list(origin),
        "imageSize": list(size),
        "windowRect": [origin[0], origin[1], origin[0] + size[0], origin[1] + size[1]],
        "dpi": dpi,
        "scale": scale,
    }


def test_image_proposal_is_explicit_and_square() -> None:
    locator = make_image_proposal(
        42, (120, 120), radius=8, grab_window=lambda hwnd: _shot()
    )
    assert locator is not None
    assert locator["driver"] == "image"
    assert locator["backend"] == "image"
    assert locator["windowHandle"] == 42
    assert locator["templateSize"] == [16, 16]
    assert locator["minConfidence"] == 0.8
    assert locator["proposal"] is True
    # M80：模板 V2 必须自带 provenance 与内容指纹，缺一不可。
    assert locator["templateSchema"] == 2
    assert locator["captureDpi"] == 96
    assert locator["captureScale"] == 1.0
    assert locator["sourceRect"] == [112, 112, 16, 16]
    raw = base64.b64decode(locator["imageTemplate"])
    assert locator["templateHash"] == hashlib.sha256(raw).hexdigest()
    from PIL import Image

    with Image.open(io.BytesIO(raw)) as patch:
        assert patch.size == (16, 16)


def test_image_proposal_rejects_bad_shot_and_outside_point() -> None:
    assert make_image_proposal(42, (120, 120), grab_window=lambda hwnd: None) is None
    assert make_image_proposal(42, (5, 5), grab_window=lambda hwnd: _shot()) is None
    assert make_image_proposal(
        42, (120, 120), grab_window=lambda hwnd: {"dataUrl": "no", "windowOrigin": [0, 0],
                                                  "imageSize": [10, 10]}
    ) is None
    assert make_image_proposal(0, (120, 120), grab_window=lambda hwnd: _shot()) is None


def test_image_proposal_refuses_shot_without_dpi_provenance() -> None:
    """M80：没有捕获时 DPI/scale 就不产 V2 模板，绝不猜 96（等值断言）。"""
    shot = _shot()
    shot.pop("dpi")
    assert make_image_proposal(42, (120, 120), grab_window=lambda hwnd: shot) is None


def test_image_proposal_record_never_pretends_measured_hit(monkeypatch) -> None:
    locator = {"driver": "image", "backend": "image", "windowHandle": 7}
    monkeypatch.setattr(desktop_agent, "make_image_proposal", lambda *a, **kw: locator)
    record = desktop_agent._image_proposal_record(7, (10, 20))
    assert record is not None
    assert record["kind"] == "image"
    assert record["matchedCount"] == 0
    assert record["proposed"] is True
    assert record["locator"] == locator


class _Info:
    control_type = None
    automation_id = None
    name = None
    class_name = "WechatSelfDraw"

    @property
    def rectangle(self):
        return None


def test_describe_info_uses_image_locator_only_in_explicit_mode(monkeypatch) -> None:
    locator = {"driver": "image", "backend": "image", "windowHandle": 7,
               "imageTemplate": "AAAA", "templateSize": [8, 8]}
    seen: list[tuple] = []

    def fake_record(hwnd, point):
        seen.append((hwnd, point))
        return {"kind": "image", "locator": locator, "matchedCount": 0,
                "penalty": 0, "proposed": True}

    monkeypatch.setattr(desktop_agent, "_image_proposal_record", fake_record)
    monkeypatch.setattr(desktop_agent, "_root_verify", lambda hwnd: None)
    monkeypatch.setattr(desktop_agent, "_verify_in_root", lambda *a, **kw: 0)

    plain = desktop_agent._describe_info(_Info(), 7)
    assert plain["selector"]["locator"].get("driver") != "image"
    assert seen == []

    image = desktop_agent._describe_info(
        _Info(), 7, image_point=(13, 21), prefer_image=True
    )
    assert seen == [(7, (13, 21))]
    assert image["selector"]["locator"] == locator
    assert any(c["kind"] == "image" and c["matchedCount"] == 0
               for c in image["selector"]["candidates"])


def test_capture_at_explicit_image_survives_uia_access_denied(monkeypatch) -> None:
    """M79 真机：微信自绘区 UIA ElementFromPoint 报拒绝访问时，显式图像仍要产出 proposal。

    反向钉住非图像模式：同一异常必须照旧抛出，绝不能「为了通过」给语义路径也加兜底。
    """
    def boom(x, y):
        raise RuntimeError("COM access denied")

    locator = {"driver": "image", "backend": "image", "windowHandle": 7,
               "imageTemplate": "AAAA", "templateSize": [8, 8]}

    monkeypatch.setattr(desktop_agent, "_element_from_point", boom)
    monkeypatch.setattr(desktop_agent, "_root_window_handle", lambda hwnd: hwnd)
    monkeypatch.setattr(desktop_agent, "capture_in_window", lambda hwnd, x, y: None)
    monkeypatch.setattr(
        desktop_agent, "_image_proposal_record",
        lambda hwnd, point: {"kind": "image", "locator": locator,
                             "matchedCount": 0, "penalty": 0, "proposed": True},
    )

    out = desktop_agent.capture_at(10, 20, 7, prefer_image=True)
    assert out["kind"] == "desktop"
    assert out["selector"]["locator"] == locator
    assert out["metadata"]["windowHandle"] == 7
    assert any(c["kind"] == "image" for c in out["selector"]["candidates"])

    import pytest

    with pytest.raises(RuntimeError, match="access denied"):
        desktop_agent.capture_at(10, 20, 7)


def test_clip_template_rect_is_the_shared_geometry() -> None:
    """红框与裁剪必须同一份几何（M84）：`sourceRect` 恒等于 `clip_template_rect`。

    维护者真机反馈「没有更细的粒度」，根因之一就是高亮框画的是语义元素（整个窗口），
    而真正截下来的是光标周围一小块——两者不同源时，用户看到的框会骗人。
    """
    from rpa_core.capture.image_proposal import clip_template_rect

    assert clip_template_rect((100, 100, 164, 164), (120, 120), radius=8) == (
        112, 112, 128, 128,
    )
    # 贴窗边：裁到窗口内，不越界截到隔壁窗口
    assert clip_template_rect((100, 100, 164, 164), (102, 102), radius=8) == (
        100, 100, 110, 110,
    )
    # 点在窗口外 / 矩形退化 / 半径非法 ⇒ 不产框
    assert clip_template_rect((100, 100, 164, 164), (99, 120), radius=8) is None
    assert clip_template_rect((100, 100, 100, 164), (100, 120), radius=8) is None
    assert clip_template_rect((100, 100, 164, 164), (120, 120), radius=0) is None

    locator = make_image_proposal(42, (120, 120), radius=8, grab_window=lambda h: _shot())
    assert locator is not None
    left, top, right, bottom = clip_template_rect(
        (100, 100, 164, 164), (120, 120), radius=8
    )
    assert locator["sourceRect"] == [left, top, right - left, bottom - top]


def _window_info() -> object:
    from ctypes import wintypes

    class _WindowInfo(_Info):
        @property
        def rectangle(self):
            return wintypes.RECT(0, 0, 800, 600)

    return _WindowInfo()


def _image_locator() -> dict:
    return {
        "driver": "image",
        "backend": "image",
        "windowHandle": 7,
        "imageTemplate": "AAAA",
        "templateSize": [32, 32],
        "sourceRect": [234, -616, 32, 32],
    }


def test_describe_info_image_rect_is_the_template_box(monkeypatch) -> None:
    """图像主定位时预览矩形必须是那块模板，而不是整个窗口（等值断言）。"""
    locator = _image_locator()
    monkeypatch.setattr(
        desktop_agent,
        "_image_proposal_record",
        lambda hwnd, point: {"kind": "image", "locator": locator,
                             "matchedCount": 0, "penalty": 0, "proposed": True},
    )
    monkeypatch.setattr(desktop_agent, "_root_verify", lambda hwnd: None)
    monkeypatch.setattr(desktop_agent, "_verify_in_root", lambda *a, **kw: 0)

    out = desktop_agent._describe_info(
        _window_info(), 7, image_point=(250, -600), prefer_image=True
    )
    assert out["metadata"]["rect"] == {
        "left": 234, "top": -616, "width": 32, "height": 32,
    }

    plain = desktop_agent._describe_info(_window_info(), 7)
    assert plain["metadata"]["rect"] == {
        "left": 0, "top": 0, "width": 800, "height": 600,
    }


def test_capture_at_image_rect_is_the_template_box(monkeypatch) -> None:
    """点捕获路径同样：图像主定位 ⇒ metadata.rect 是模板框（负坐标也不许丢）。"""
    locator = _image_locator()

    def boom(x, y):
        raise RuntimeError("COM access denied")

    monkeypatch.setattr(desktop_agent, "_element_from_point", boom)
    monkeypatch.setattr(desktop_agent, "_root_window_handle", lambda hwnd: hwnd)
    monkeypatch.setattr(
        desktop_agent,
        "_image_proposal_record",
        lambda hwnd, point: {"kind": "image", "locator": locator,
                             "matchedCount": 0, "penalty": 0, "proposed": True},
    )

    out = desktop_agent.capture_at(250, -600, 7, prefer_image=True)
    assert out["metadata"]["rect"] == {
        "left": 234, "top": -616, "width": 32, "height": 32,
    }


def test_capture_with_retry_single_attempt_in_image_mode(monkeypatch) -> None:
    """图像模式的 verifyCount 恒 0，重试条件永不成立 ⇒ 只允许一轮。

    反向钉住语义模式：仍按原样重试（不许为了省事把重试也删掉）。
    """
    calls: list[tuple] = []

    def fake_capture(x, y, scope, **kwargs):
        calls.append((x, y, scope, kwargs))
        return {"kind": "desktop", "selector": {"locator": {}},
                "verifyCount": 0, "metadata": {}}

    class _NoSleep:
        @staticmethod
        def sleep(_seconds):
            return None

    monkeypatch.setattr(desktop_agent, "capture_at", fake_capture)
    monkeypatch.setattr(desktop_agent, "time", _NoSleep)

    desktop_agent.capture_with_retry(10, 20, 7, prefer_image=True)
    assert len(calls) == 1
    assert calls[0][3]["prefer_image"] is True

    calls.clear()
    desktop_agent.capture_with_retry(10, 20, 7)
    assert len(calls) == 4
    assert all(kw.get("prefer_image") is False for _, _, _, kw in calls)
