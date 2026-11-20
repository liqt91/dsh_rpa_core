"""编辑期校验（影刀 §5 主动校验的本地复刻）的契约。

三条要守住的：结构错要**本地短路**（不许跨进程去报「未找到」）、成功口径是
**恰好 1 个**、只读（不改描述符）。
"""

from __future__ import annotations

import copy

import pytest

from rpa_core.capture_kit import edit_check as ec

DESKTOP_DOC = {
    "kind": "desktop",
    "selector": {"locator": {"backend": "uia", "controlType": "Button"}},
    "metadata": {"windowHandle": 1234},
}
DESKTOP_SEL = {"locator": {"backend": "uia", "controlType": "Button"}}


class _FakeVerifier:
    """桌面活体校验替身：只记录被调了几次、最后一次吃到的 locator。"""

    def __init__(self, reply):
        self.reply = reply
        self.calls = 0
        self.seen = []

    def verify(self, locator, *, want_shot=False):
        self.calls += 1
        self.seen.append(locator)
        reply = dict(self.reply)
        if want_shot:
            reply.setdefault("shot", {"dataUrl": "data:image/png;base64,AA"})
        return reply


@pytest.mark.parametrize(
    "css",
    ["div.a > b.c", "div[a='b]c']", "#id .cls:nth-child(2)"],
)
def test_browser_structure_accepts_sane_selectors(css):
    assert ec.check_structure("browser", {"css": css}) is None


@pytest.mark.parametrize(
    ("css", "needle"),
    [
        ("", "不能为空"),
        ("a\nb", "只能是一行"),
        ("div(", "括号不配对"),
        ("div]", "方括号不配对"),
        ('div[title="', "引号没有闭合"),
        ("div[title=]", "缺少值"),
        ("div.a >", "后面还缺一层"),
        ("div.a,", "后面还缺一层"),
    ],
)
def test_browser_structure_rejects_malformed(css, needle):
    error = ec.check_structure("browser", {"css": css})
    assert error is not None and needle in error, error


def test_desktop_structure_uses_the_model_as_authority():
    # 空 locator / 缺身份字段都被模型挡下，报错应是中文而不抛异常
    assert "空的" in ec.check_structure("desktop", {"locator": {}})
    empty_identity = ec.check_structure(
        "desktop", {"locator": {"backend": "uia"}}
    )
    assert empty_identity is not None and "身份字段" in empty_identity
    assert ec.check_structure("desktop", {"locator": None}) is not None
    assert ec.check_structure("desktop", "not-a-dict") is not None
    assert ec.check_structure("desktop", DESKTOP_SEL) is None


def test_structure_error_short_circuits_without_touching_the_target():
    """结构错必须本地拦下：活体替身一次都不该被调用。

    否则语法错误会被报成「未找到元素」，用户会去改一个其实写对了的地方。
    """
    verifier = _FakeVerifier({"count": 1})
    result = ec.check_draft(
        DESKTOP_DOC, {"locator": {"backend": "uia"}}, verifier=verifier
    )
    assert verifier.calls == 0
    assert result.success is False
    assert result.error_code == "syntax"
    assert "身份字段" in result.message


@pytest.mark.parametrize(
    ("reply", "success", "needle"),
    [
        ({"count": 1, "windowHandle": 1234}, True, "找到 1 个"),
        ({"count": 3}, False, "找到 3 个"),
        ({"count": 0}, False, "未找到元素"),
        ({"count": 0, "hint": "tree-missing"}, False, "无障碍树"),
        ({"error": "desktop-verifier-unavailable"}, False, "无法校验"),
    ],
)
def test_success_means_exactly_one_match(reply, success, needle):
    verifier = _FakeVerifier(reply)
    result = ec.check_draft(DESKTOP_DOC, DESKTOP_SEL, verifier=verifier)
    assert verifier.calls == 1
    assert result.success is success
    assert needle in result.message


def test_draft_carries_window_handle_and_shot_back():
    verifier = _FakeVerifier({"count": 1, "windowHandle": 1234})
    result = ec.check_draft(DESKTOP_DOC, DESKTOP_SEL, verifier=verifier)
    assert result.window_handle == 1234
    assert isinstance(result.shot, dict)


def test_draft_carries_metadata_into_verification(monkeypatch):
    """窗口句柄必须跟着 draft 走：桌面腿靠它限定查找范围（快且不会误命中）。"""
    import rpa_core.capture.verification as verification

    captured: dict = {}

    def fake_verify(draft, **_kwargs):
        captured.update(draft)
        return verification.VerificationResult(count=1, windowHandle=1234)

    monkeypatch.setattr(verification, "verify_element", fake_verify)
    ec.check_draft(DESKTOP_DOC, DESKTOP_SEL)
    assert captured["kind"] == "desktop"
    assert captured["metadata"] == {"windowHandle": 1234}
    assert captured["selector"] == DESKTOP_SEL


def test_semantic_and_unknown_types_are_reported_not_faked():
    assert ec.check_draft(DESKTOP_DOC, DESKTOP_SEL, validate_type="semantic").error_code == (
        "unsupported"
    )
    assert ec.check_draft(DESKTOP_DOC, DESKTOP_SEL, validate_type="bogus").error_code == (
        "unsupported-type"
    )


def test_structure_only_type_never_goes_live():
    verifier = _FakeVerifier({"count": 1})
    result = ec.check_draft(
        DESKTOP_DOC, DESKTOP_SEL, validate_type="structure", verifier=verifier
    )
    assert verifier.calls == 0
    assert result.success is True


def test_check_draft_is_read_only():
    """只读：跑完一遍，描述符与选择器一个字都不该变。"""
    doc_before = copy.deepcopy(DESKTOP_DOC)
    sel_before = copy.deepcopy(DESKTOP_SEL)
    ec.check_draft(DESKTOP_DOC, DESKTOP_SEL, verifier=_FakeVerifier({"count": 1}))
    assert DESKTOP_DOC == doc_before
    assert DESKTOP_SEL == sel_before
