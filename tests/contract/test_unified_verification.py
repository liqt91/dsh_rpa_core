"""M74 S1 统一校验门面（verification.py）的契约判据。

重点是**分派**：用户实际走的是 ``verify_element`` 入口，判据必须证明它按 kind
分派到了正确的一端、回传统一形状、未知 kind 不静默降级成 browser——只测内部
纯函数（from_reply）不算覆盖接线。
"""

from __future__ import annotations

import pytest

from rpa_core.capture.verification import VerificationResult, verify_element


class SpyVerifier:
    """替身：记录三 mode 调用与入参，回可定制的 reply。"""

    def __init__(self, reply: dict | None = None):
        self.calls: list[tuple[str, tuple, dict]] = []
        self.reply = reply if reply is not None else {"count": 1}

    def verify(self, query, *, want_shot=False):
        self.calls.append(("flash", (query,), {"want_shot": want_shot}))
        return self.reply

    def preview(self, query):
        self.calls.append(("preview", (query,), {}))
        return self.reply

    def clear_preview(self):
        self.calls.append(("clear", (), {}))
        return self.reply


def _element(kind: str, selector: dict | None = None) -> dict:
    return {"kind": kind, "selector": selector or {}}


# ---- 分派：按 kind 走对端 ----------------------------------------------------


def test_browser_dispatches_to_web_verifier_with_css():
    web = SpyVerifier({"count": 2, "windowHandle": 42, "rect": {"left": 1}})
    desk = SpyVerifier()
    result = verify_element(
        _element("browser", {"css": "div.x"}),
        mode="flash",
        browser_verifier=web,
        desktop_verifier=desk,
    )
    assert web.calls == [("flash", ("div.x",), {"want_shot": False})]
    assert desk.calls == []  # 没碰桌面端
    assert result.count == 2 and result.windowHandle == 42
    assert result.rect == {"left": 1}


def test_desktop_dispatches_to_desktop_verifier_with_locator():
    web = SpyVerifier()
    desk = SpyVerifier({"count": 1})
    locator = {"backend": "uia", "name": "发送"}
    result = verify_element(
        _element("desktop", {"locator": locator}),
        mode="flash",
        browser_verifier=web,
        desktop_verifier=desk,
    )
    assert desk.calls == [("flash", (locator,), {"want_shot": False})]
    assert web.calls == []
    assert result.count == 1


def test_default_desktop_verifier_receives_captured_window(monkeypatch):
    """遮挡场景：元素记得自己来自哪个窗口，默认 verifier 必须先查那个窗口。

    纯 Z 序会先撞上「另一个长得一样的记事本」——M79 真机实测过。这里钉住门面
    把 metadata.windowHandle 交给默认桌面 verifier，而不是让它凭空丢掉。
    """
    import rpa_core.capture.verification as vf

    seen: list[int | None] = []
    spy = SpyVerifier({"count": 1, "windowHandle": 9})

    def factory(*, expected_hwnd=None):
        seen.append(expected_hwnd)
        return spy

    monkeypatch.setattr(vf, "_default_desktop_verifier", factory)
    element = _element("desktop", {"locator": {"backend": "uia"}})
    element["metadata"] = {"windowHandle": 9}
    result = verify_element(element, mode="flash")
    assert seen == [9]
    assert result.windowHandle == 9


def test_unknown_kind_is_error_and_never_falls_back_to_browser():
    """未知 kind 必须结构化报错，**绝不**静默当 browser（M74 硬口径）。

    这里刻意两个替身都塞进去：若门面里有人写 `else: 走 browser`，本用例就红。
    """
    web = SpyVerifier({"count": 99})
    desk = SpyVerifier({"count": 99})
    result = verify_element(
        _element("qt-hlay"), mode="flash", browser_verifier=web, desktop_verifier=desk
    )
    assert result.error == "unsupported-kind"
    assert web.calls == [] and desk.calls == []


def test_unknown_kind_from_object_element():
    class Element:
        kind = "image"
        selector = {"css": "x"}

    result = verify_element(Element(), mode="flash")
    assert result.error == "unsupported-kind"


# ---- mode 映射（与 Web 三 mode 对齐）----------------------------------------


@pytest.mark.parametrize(
    ("mode", "expected_call"),
    [
        ("flash", "flash"),
        ("preview", "preview"),
        ("clear", "clear"),
    ],
)
def test_mode_maps_to_verifier_method(mode, expected_call):
    web = SpyVerifier({"count": 0})
    verify_element(_element("browser", {"css": "x"}), mode=mode, browser_verifier=web)
    assert [c[0] for c in web.calls] == [expected_call]


def test_unsupported_mode_is_structured_error():
    web = SpyVerifier()
    result = verify_element(_element("browser"), mode="blink", browser_verifier=web)
    assert result.error == "unsupported-mode"
    assert web.calls == []


def test_want_shot_forwarded_only_to_flash():
    web = SpyVerifier()
    verify_element(
        _element("browser", {"css": "x"}),
        mode="flash",
        want_shot=True,
        browser_verifier=web,
    )
    assert web.calls[0][2] == {"want_shot": True}


# ---- 形状统一 ----------------------------------------------------------------


def test_both_kinds_return_same_shape():
    web = SpyVerifier({"count": 3, "windowHandle": 7, "rect": {"top": 2}, "extBuild": "1"})
    desk = SpyVerifier({"count": 1, "windowHandle": 8, "rect": {"top": 3}})
    a = verify_element(_element("browser", {"css": "x"}), mode="flash", browser_verifier=web)
    b = verify_element(
        _element("desktop", {"locator": {}}), mode="flash", desktop_verifier=desk
    )
    assert set(a.to_dict()) == set(b.to_dict())
    assert set(a.to_dict()) == {"count", "windowHandle", "rect"}
    # 扩展字段不丢（预览页还要 viewport / extBuild）
    assert a.raw["extBuild"] == "1"


def test_error_shape_has_no_hit_fields():
    r = VerificationResult.from_reply({"error": "verify-timeout", "count": 5})
    assert r.to_dict() == {"count": 0, "error": "verify-timeout"}


# ---- from_reply：垃圾回传不进命中数 ------------------------------------------


@pytest.mark.parametrize(
    "reply",
    [None, [], "x", {}, {"count": True}, {"count": -1}, {"count": "many"}],
)
def test_bad_reply_is_structured_error(reply):
    assert VerificationResult.from_reply(reply).error == "bad-reply"


def test_bad_hwnd_dropped_but_count_kept():
    r = VerificationResult.from_reply({"count": 1, "windowHandle": True, "rect": [1, 2]})
    assert r.count == 1 and r.windowHandle is None and r.rect is None
