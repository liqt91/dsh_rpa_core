"""M75 S3 判据：图像校验回传统一形状 + 门面显式 image 二次分派。

用户实际走的是 ``verify_element`` 入口 ⇒ 必须测接线（分派到了 image verifier），
不是只测 ImageElementVerifier 纯逻辑。
"""

from __future__ import annotations

import base64

from rpa_core.capture.image_locator import ImageMatch
from rpa_core.capture.image_verification import ImageElementVerifier
from rpa_core.capture.verification import VerificationResult, verify_element


def _tpl(locator: dict | None = None) -> dict:
    return {
        "driver": "image",
        "backend": "image",
        "windowHandle": 123,
        "imageTemplate": base64.b64encode(b"tpl").decode("ascii"),
        "templateSize": [20, 10],
        "templateSchema": 2,
        "captureDpi": 96,
        "captureScale": 1.0,
        **(locator or {}),
    }


def _shot() -> dict:
    return {
        "dataUrl": "data:image/png;base64," + base64.b64encode(b"png").decode("ascii"),
        "windowOrigin": [100, 200],
        "imageSize": [800, 600],
        "dpi": 96,
        "scale": 1.0,
    }


# ---- 图像 verifier 回传形状 --------------------------------------------------


def test_image_verify_hit_shape_matches_desktop():
    """命中 ⇒ {count, windowHandle, rect} 与 Web/桌面同形状，另加图像腿独有的 usedScale。"""
    verifier = ImageElementVerifier(
        shot_fn=lambda hwnd: _shot(),
        locate_fn=lambda shot, loc: ImageMatch(390, 345, 20, 10, 0.95, 1),
    )
    reply = verifier.verify(_tpl())
    assert reply == {
        "count": 1,
        "windowHandle": 123,
        "rect": {"left": 390, "top": 345, "width": 20, "height": 10},
        # 图像分支独有：语义定位没有缩放比这回事
        "usedScale": 1.0,
    }
    # 门面归一后字段一致
    result = VerificationResult.from_reply(reply)
    assert result.count == 1 and result.windowHandle == 123
    assert result.rect == {"left": 390, "top": 345, "width": 20, "height": 10}


def test_image_flash_brings_window_before_shot_only():
    """flash 必须先前置再截图；preview 不抢焦（遮挡时模板才对得上）。"""
    calls: list[int] = []

    def shot(hwnd):
        assert calls == [hwnd], "截图发生在前置之前"
        return _shot()

    verifier = ImageElementVerifier(
        shot_fn=shot,
        locate_fn=lambda shot, loc: ImageMatch(390, 345, 20, 10, 0.95, 1),
        bring_front=calls.append,
    )
    assert verifier.verify(_tpl())["count"] == 1
    assert calls == [123]
    verifier.preview(_tpl())
    assert calls == [123]


def test_image_verify_zero_hit():
    verifier = ImageElementVerifier(
        shot_fn=lambda hwnd: _shot(),
        locate_fn=lambda shot, loc: ImageMatch(0, 0, 0, 0, None, 0),
    )
    assert verifier.verify(_tpl()) == {"count": 0}


def test_image_verify_screenshot_fail_is_zero_not_error():
    """截图失败 ⇒ count=0，不是校验通道错误（同 grab_window 口径）。"""
    verifier = ImageElementVerifier(shot_fn=lambda hwnd: None)
    assert verifier.verify(_tpl()) == {"count": 0}


def test_image_verify_rejects_non_explicit_locator():
    """非显式 image ⇒ error=image-not-explicit（S2 守卫，禁静默降级）。"""
    verifier = ImageElementVerifier(shot_fn=lambda hwnd: _shot())
    assert verifier.verify({"backend": "uia", "name": "x"}) == {"error": "image-not-explicit"}


def test_image_verify_invalid_window_handle():
    verifier = ImageElementVerifier(shot_fn=lambda hwnd: _shot())
    bad = _tpl({"windowHandle": 0})
    assert verifier.verify(bad) == {"error": "invalid-window-handle"}


def test_image_verify_bad_locator():
    verifier = ImageElementVerifier()
    assert verifier.verify("not-a-dict") == {"error": "invalid-locator"}


def test_old_template_is_explicit_error_and_never_migrated():
    """V1 模板（无 templateSchema）显式报错重捕获，不按旧口径静默匹配。"""
    def must_not_shot(hwnd):
        raise AssertionError("V1 模板不该走到截图/匹配")

    verifier = ImageElementVerifier(shot_fn=must_not_shot)
    old = _tpl()
    old.pop("templateSchema")
    assert verifier.verify(old) == {"error": "template-schema-requires-recapture"}


def test_template_verify_requires_current_dpi_provenance():
    """截图侧没 DPI 就拒绝匹配，绝不猜 96（与 proposal 侧同一纪律）。"""
    shot = _shot()
    shot.pop("scale")
    verifier = ImageElementVerifier(shot_fn=lambda hwnd: shot)
    assert verifier.verify(_tpl()) == {"error": "current-dpi-unavailable"}


# ---- 门面二次分派 ------------------------------------------------------------


class _Spy:
    def __init__(self, reply: dict | None = None):
        self.calls: list[tuple[str, object]] = []
        self.reply = reply or {"count": 1}

    def verify(self, query, *, want_shot=False):
        self.calls.append(("flash", query))
        return self.reply

    def preview(self, query):
        self.calls.append(("preview", query))
        return self.reply

    def clear_preview(self):
        self.calls.append(("clear", None))
        return self.reply


def test_facade_dispatches_explicit_image_to_image_verifier():
    """元素 selector.locator 显式 driver=image ⇒ 走 image_verifier，不碰 desktop。"""
    image = _Spy({"count": 1, "windowHandle": 123, "rect": {"left": 1}})
    desk = _Spy({"count": 99})
    result = verify_element(
        {"kind": "desktop", "selector": {"locator": _tpl()}},
        mode="flash",
        image_verifier=image,
        desktop_verifier=desk,
    )
    assert image.calls == [("flash", _tpl())]
    assert desk.calls == []
    assert result.count == 1 and result.windowHandle == 123


def test_facade_semantic_locator_never_dispatches_to_image():
    """★ 语义 locator（即使空命中）绝不走 image_verifier（ADR 0003 红线接线层）。"""
    image = _Spy({"count": 1})
    desk = _Spy({"count": 0})
    result = verify_element(
        {"kind": "desktop", "selector": {"locator": {"backend": "uia", "name": "x"}}},
        mode="flash",
        image_verifier=image,
        desktop_verifier=desk,
    )
    assert image.calls == []
    assert desk.calls == [("flash", {"backend": "uia", "name": "x"})]
    assert result.count == 0


def test_facade_unknown_kind_still_errors():
    """M74 既有判据不退化：未知 kind 仍结构化报错。"""
    result = verify_element({"kind": "qt-hlay"}, mode="flash")
    assert result.error == "unsupported-kind"


# ---- M80：--shot 诊断包 --------------------------------------------------------


def test_image_verify_want_shot_returns_used_scale_and_box():
    """要诊断就把「哪个尺度命中的」和「命中框」一起给出来：box 必须同源于命中矩形。"""
    verifier = ImageElementVerifier(
        shot_fn=lambda hwnd: _shot(),
        locate_fn=lambda shot, loc: ImageMatch(390, 345, 20, 10, 0.95, 1, used_scale=0.8),
    )
    reply = verifier.verify(_tpl(), want_shot=True)
    assert reply["count"] == 1
    assert reply["usedScale"] == 0.8
    # 390-100=290, 345-200=145：屏幕矩形减去窗口原点，不引入任何 scale
    assert reply["shot"]["box"] == {"x": 290.0, "y": 145.0, "width": 20.0, "height": 10.0}


def test_image_verify_want_shot_without_origin_keeps_hit_and_skips_shot():
    """截图缺窗口原点 ⇒ 只丢回执，命中数不受影响（截图失败绝不上级成校验失败）。"""
    shot = {"dataUrl": "data:image/png;base64," + base64.b64encode(b"png").decode("ascii")}
    verifier = ImageElementVerifier(
        shot_fn=lambda hwnd: shot,
        locate_fn=lambda s, loc: ImageMatch(390, 345, 20, 10, 0.95, 1, used_scale=1.0),
    )
    reply = verifier.verify(_tpl(), want_shot=True)
    assert reply["count"] == 1
    assert "shot" not in reply
    assert reply["usedScale"] == 1.0


def test_image_verify_miss_never_returns_diagnostic_package():
    """零命中 ⇒ 只有 {count:0}，不给半成品诊断包（避免 GUI 拿到空框坐标）。"""
    verifier = ImageElementVerifier(
        shot_fn=lambda hwnd: _shot(),
        locate_fn=lambda s, loc: ImageMatch(0, 0, 0, 0, 0.1, 0),
    )
    assert verifier.verify(_tpl(), want_shot=True) == {"count": 0}


def test_facade_passes_used_scale_through_to_dict():
    """usedScale 必须穿到 to_dict：否则 CLI/GUI 看不到重采样到底生效没有。"""
    image = ImageElementVerifier(
        shot_fn=lambda hwnd: _shot(),
        locate_fn=lambda s, loc: ImageMatch(390, 345, 20, 10, 0.95, 1, used_scale=0.8),
    )
    result = verify_element(
        {"kind": "desktop", "selector": {"locator": _tpl()}},
        mode="flash",
        want_shot=True,
        image_verifier=image,
        desktop_verifier=_Spy(),
    )
    out = result.to_dict()
    assert out["count"] == 1
    assert out["usedScale"] == 0.8
