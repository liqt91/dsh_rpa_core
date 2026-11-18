"""M75 S4 判据：desktop.imageLocate 执行器（命令层）。

钉三件事：① 参数互斥/缺失报 INVALID_INPUT；② 命中回矩形 + usedLayer=image 证据
（ADR 0003 实际使用层）；③ 零命中报 ELEMENT_NOT_FOUND，不静默降级到语义定位。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib

import pytest

from rpa_core.executors.desktop_image import DesktopImageExecutor
from rpa_core.model.command import CommandInvocation
from rpa_core.model.errors import ErrorCode


def _inv(command: str = "desktop.imageLocate", **inputs: object) -> CommandInvocation:
    return CommandInvocation(
        command_id=command,
        command_version="1.0.0",
        run_id="run-m75",
        step_id="step-1",
        inputs=inputs,
    )


def _run(executor: DesktopImageExecutor, invocation: CommandInvocation):
    return asyncio.run(executor.execute(invocation, asyncio.Event()))


class _Verifier:
    """替身 verifier：记录收到的 locator，回可定制 reply。"""

    def __init__(self, reply: dict) -> None:
        self.reply = reply
        self.locators: list[dict] = []

    def verify(self, locator, *, want_shot=False):
        self.locators.append(locator)
        return self.reply


# ---- 参数校验 ----------------------------------------------------------------


def test_missing_both_targets_is_invalid_input():
    result = _run(DesktopImageExecutor(), _inv(windowHandle=1))
    assert result.status == "error"
    assert result.error is not None and result.error.code == ErrorCode.INVALID_INPUT


def test_mutually_exclusive_targets_is_invalid_input():
    result = _run(
        DesktopImageExecutor(),
        _inv(windowHandle=1, imageTemplate="aVRN", imageKeyword="登录"),
    )
    assert result.error is not None and result.error.code == ErrorCode.INVALID_INPUT


@pytest.mark.parametrize("bad_handle", [0, -1, True, "123"])
def test_bad_window_handle_is_invalid_input(bad_handle):
    result = _run(DesktopImageExecutor(), _inv(windowHandle=bad_handle, imageKeyword="x"))
    assert result.error is not None and result.error.code == ErrorCode.INVALID_INPUT


# ---- 命中 + 证据 -------------------------------------------------------------


def test_hit_returns_absolute_rect_and_used_layer_evidence():
    verifier = _Verifier({"count": 1, "windowHandle": 123, "rect": {
        "left": 390, "top": 345, "width": 20, "height": 10
    }})
    result = _run(
        DesktopImageExecutor(verifier=verifier),
        _inv(windowHandle=123, imageTemplate="aVRN", templateSize=[20, 10], minConfidence=0.8),
    )
    assert result.status == "success"
    assert result.outputs == {
        "left": 390, "top": 345, "width": 20, "height": 10, "count": 1
    }
    assert result.diagnostics["usedLayer"] == "image"  # ADR 0003 证据
    # M80：命令式模板也要打 V2 标记与内容指纹；captureScale 缺失时按当前截图侧算。
    assert verifier.locators[0]["templateSchema"] == 2
    assert verifier.locators[0]["templateHash"] == hashlib.sha256(
        base64.b64decode("aVRN")
    ).hexdigest()


def test_executor_passes_explicit_driver_to_verifier():
    """执行器必须把 driver=image 显式传给 verifier（守卫据此放行，不走语义层）。"""
    verifier = _Verifier({"count": 1, "rect": {"left": 0, "top": 0, "width": 1, "height": 1}})
    _run(
        DesktopImageExecutor(verifier=verifier),
        _inv(windowHandle=7, imageKeyword="登录"),
    )
    assert verifier.locators[0]["driver"] == "image"
    assert verifier.locators[0]["windowHandle"] == 7
    assert verifier.locators[0]["imageKeyword"] == "登录"


def test_keyword_path_does_not_need_template_size():
    verifier = _Verifier({"count": 1, "rect": {"left": 1, "top": 2, "width": 3, "height": 4}})
    result = _run(
        DesktopImageExecutor(verifier=verifier),
        _inv(windowHandle=7, imageKeyword="OK", minConfidence=0.5),
    )
    assert result.status == "success"
    assert "templateSize" not in verifier.locators[0]


# ---- 零命中 / 错误 -----------------------------------------------------------


def test_zero_hit_is_element_not_found():
    verifier = _Verifier({"count": 0})
    result = _run(
        DesktopImageExecutor(verifier=verifier), _inv(windowHandle=7, imageKeyword="x")
    )
    assert result.error is not None and result.error.code == ErrorCode.ELEMENT_NOT_FOUND


def test_verifier_error_maps_to_executor_failed():
    verifier = _Verifier({"error": "image-not-explicit"})
    result = _run(
        DesktopImageExecutor(verifier=verifier), _inv(windowHandle=7, imageKeyword="x")
    )
    assert result.error is not None and result.error.code == ErrorCode.EXECUTOR_FAILED


def test_cancelled_before_execute():
    inv = _inv(windowHandle=7, imageKeyword="x")
    result = asyncio.run(DesktopImageExecutor().execute(inv, _cancelled_event()))
    assert result.status == "cancelled"


def _cancelled_event() -> asyncio.Event:
    ev = asyncio.Event()
    ev.set()
    return ev


def test_unsupported_command_is_executor_failed():
    result = _run(DesktopImageExecutor(), _inv("desktop.unknown", windowHandle=1))
    assert result.error is not None and result.error.code == ErrorCode.EXECUTOR_FAILED
