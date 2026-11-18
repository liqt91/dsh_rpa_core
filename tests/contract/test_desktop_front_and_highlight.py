"""M78：桌面校验高亮 + 元素动作前置的契约。"""

from __future__ import annotations

from rpa_core.capture.desktop_verification import DesktopElementVerifier
from rpa_core.capture.verification import VerificationResult
from rpa_core.executors import desktop as desktop_exec
from rpa_core.executors import desktop_win32 as win32_exec


class _API:
    def enumerate_top_windows(self):
        return [(7, "Class", "w7", "app.exe", True)]

    def foreground(self):
        return 0

    def set_foreground(self, hwnd):
        return True


class _Match:
    def rectangle(self):
        class R:
            left, top, right, bottom = 10, 20, 110, 80

        return R()


def _verifier(**kw):
    return DesktopElementVerifier(
        window_factory=lambda hwnd: f"w{hwnd}",
        finder=lambda window, locator: [_Match()],
        api=_API(),
        bring_front=lambda hwnd: None,
        **kw,
    )


def test_flash_draws_once_and_preview_does_not() -> None:
    flashes: list[dict] = []
    verifier = _verifier(highlighter=flashes.append)
    result = verifier.verify({"backend": "uia"})
    assert result["count"] == 1
    assert flashes == [{"left": 10, "top": 20, "width": 100, "height": 60}]
    verifier.preview({"backend": "uia"})
    verifier.clear_preview()
    assert len(flashes) == 1


def test_shot_metadata_is_optional_and_never_breaks_hit() -> None:
    def shot(hwnd, rect):
        assert hwnd == 7
        assert rect["width"] == 100
        return {"dataUrl": "data:image/png;base64,AA==", "box": {"x": 0, "y": 0}}

    verifier = _verifier(highlighter=lambda rect: None, shot_fn=shot)
    reply = verifier.verify({"backend": "uia"}, want_shot=True)
    payload = VerificationResult.from_reply(reply).to_dict()
    assert payload["count"] == 1
    assert payload["shot"]["dataUrl"] == "data:image/png;base64,AA=="
    assert "shot" not in VerificationResult.from_reply(
        _verifier().verify({"backend": "uia"})
    ).to_dict()

    def broken_shot(hwnd, rect):
        raise RuntimeError("no shot")

    broken = _verifier(highlighter=lambda rect: None, shot_fn=broken_shot)
    assert broken.verify({"backend": "uia"}, want_shot=True)["count"] == 1


def test_visual_layer_failures_are_swallowed(monkeypatch) -> None:
    def broken_flash(rect):
        raise RuntimeError("no overlay")

    verifier = _verifier(highlighter=broken_flash)
    assert verifier.verify({"backend": "uia"})["count"] == 1


def test_both_desktop_executors_front_the_window(monkeypatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(
        "rpa_core.capture.foreground_window.bring_to_foreground", calls.append, raising=False
    )
    desktop_exec._bring_window_to_front(77)
    win32_exec._bring_window_to_front(88)
    assert calls == [77, 88]

    uia_src = desktop_exec.__file__
    win32_src = win32_exec.__file__
    assert uia_src and win32_src
    from pathlib import Path

    uia_text = Path(uia_src).read_text(encoding="utf-8")
    win32_text = Path(win32_src).read_text(encoding="utf-8")
    assert '_bring_window_to_front(session.window_handle)' in uia_text
    assert '_bring_window_to_front(session.window_handle)' in win32_text
    assert '"desktop.click", "desktop.input", "desktop.getText"' in uia_text
    assert '"desktop.win32.click", "desktop.win32.input", "desktop.win32.getText"' in win32_text
