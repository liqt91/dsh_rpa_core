"""图像元素的执行面契约（M82）：捕获出来的 image locator 真的能点。

背景（接线洞）：M77/M80 让捕获能产出 image locator（模板 V2 + DPI provenance），
`selector_errors` 也认它，但 `desktop.findElement` 只走 `DesktopLocator.model_validate`，
而它的 `require_identity` 只放行 uia/win32 —— 于是图像元素「捕得到、存得下、
校验得过，却点不动」，报的还是 pydantic 的 ValidationError（像内部崩了）。

本文件钉五件事：

1. **显式才走图像**：`driver=image` 才进图像层；语义 locator 一个字段都没变，
   而且既然没声明图像，**就不许调图像定位器**（ADR 0003 静默降级红线）。
2. **句柄唯一即算命中**：`findElement` 命中多处报 `ELEMENT_AMBIGUOUS`（句柄要被信任，
   不能随便挑一处）；零命中报 `ELEMENT_NOT_FOUND`；V1 模板报 `INVALID_INPUT` 且
   **一次匹配都不跑**（模板过期跟环境无关，等也不会好）。
3. **动作只包 click**：其余命令给干净的 `INVALID_INPUT`，不读空串、不假装输入成功。
4. **点击是真的落到矩形上**：中心点 = 矩形中点；双击/右键/辅助键的按下-抬起成对，
   辅助键**一定会抬起**（中间抛异常也不例外）。
5. **两个后端同一份实现**：uia 与 win32 的分派都走 `image_target`，各写一份必然漂移。

全部用替身 verifier + 录像鼠标，不碰真窗口、不合成真输入（真机边界另见
`docs/desktop_backends.md`「微信自绘实测」一节）。
"""

from __future__ import annotations

import sys

import pytest

from rpa_core.executors import image_target
from rpa_core.executors.desktop import DesktopExecutor, _DesktopSession
from rpa_core.executors.desktop_win32 import _Win32Session
from rpa_core.executors.image_target import (
    ImageTargetError,
    action_result,
    click_image,
    find_element_result,
    locate_image,
    perform_click,
    plan_image_click,
)
from rpa_core.model.command import CommandInvocation
from rpa_core.model.errors import ErrorCode

DESKTOP_ONLY = pytest.mark.skipif(
    sys.platform != "win32", reason="DesktopExecutor 在非 win32 上是 PLATFORM_UNSUPPORTED 占位"
)

# 模板 V2 的最小合法形状：schema=2 + 正整数尺寸（V1 会被 image_guard 拒）
IMAGE_LOCATOR = {
    "driver": "image",
    "backend": "image",
    "windowHandle": 7001,
    "imageTemplate": "aVRN",
    "templateSize": [40, 24],
    "templateSchema": 2,
    "captureDpi": 96,
    "captureScale": 1.0,
    "minConfidence": 0.8,
}


def _inv(command: str, **inputs: object) -> CommandInvocation:
    return CommandInvocation(
        command_id=command,
        command_version="1.0.0",
        run_id="run-m82",
        step_id="step-1",
        inputs=inputs,
    )


class FakeVerifier:
    """替身 verifier：记录收到的 locator，回可定制 reply。"""

    def __init__(self, reply: dict) -> None:
        self.reply = reply
        self.calls: list[dict] = []

    def verify(self, locator, *, want_shot=False):
        self.calls.append(dict(locator))
        return self.reply


class MouseRecorder:
    """录像鼠标：把 move/down/up 记成事件序列（不合成真输入）。"""

    def __init__(self) -> None:
        self.events: list[tuple[str, int, int, int]] = []

    def __call__(self, event: str, x: int, y: int, flag: int) -> None:
        self.events.append((event, x, y, flag))

    @property
    def timeline(self) -> list[str]:
        return [event[0] for event in self.events]


class KeyRecorder:
    def __init__(self) -> None:
        self.sequences: list[str] = []

    def __call__(self, sequence: str) -> None:
        self.sequences.append(sequence)


def _hit(rect: dict | None = None, count: int = 1, scale: float = 1.0) -> dict:
    return {
        "count": count,
        "windowHandle": IMAGE_LOCATOR["windowHandle"],
        "rect": rect or {"left": 100, "top": 200, "width": 40, "height": 24},
        "usedScale": scale,
    }


@pytest.fixture(autouse=True)
def _fake_window_alive(monkeypatch):
    """句柄存活一律为真：本文件不依赖真窗口台账（真机复验见文档交代）。"""
    monkeypatch.setattr(image_target, "_window_alive", lambda hwnd: hwnd > 0)


# ---------------------------------------------------------------- 定位与分发


def test_semantic_locator_never_calls_image_locator():
    """没有显式 driver=image 的 locator，绝不许摸图像定位器（ADR 0003 红线）。"""
    verifier = FakeVerifier(_hit())
    with pytest.raises(ImageTargetError) as excinfo:
        locate_image({"automationId": "btnSend"}, verifier=verifier)
    assert excinfo.value.code == "INVALID_INPUT"
    assert verifier.calls == []


def test_v1_template_rejected_without_matching():
    """V1 模板（无 templateSchema）显式要求重捕获，且**一次匹配都不跑**。"""
    verifier = FakeVerifier(_hit())
    v1 = {key: value for key, value in IMAGE_LOCATOR.items() if key != "templateSchema"}
    with pytest.raises(ImageTargetError) as excinfo:
        locate_image(v1, verifier=verifier)
    assert excinfo.value.code == "INVALID_INPUT"
    assert excinfo.value.details["reason"] == "template-schema-requires-recapture"
    assert verifier.calls == []


def test_locate_passes_provenance_and_scale_to_verifier():
    """捕获期 provenance（captureScale/captureDpi）必须原样递给定位器——多尺度的输入。"""
    verifier = FakeVerifier(_hit(scale=0.8))
    target = locate_image(IMAGE_LOCATOR, verifier=verifier)
    assert verifier.calls[0]["captureScale"] == 1.0
    assert verifier.calls[0]["captureDpi"] == 96
    assert target.used_scale == 0.8
    assert target.matched_count == 1


def test_dead_locator_handle_falls_back_to_session(monkeypatch):
    """locator 里的旧句柄失效时退回会话句柄，并把来源写进证据（不悄悄换窗口）。"""
    monkeypatch.setattr(image_target, "_window_alive", lambda hwnd: hwnd == 9009)
    verifier = FakeVerifier(_hit())
    target = locate_image(IMAGE_LOCATOR, verifier=verifier, session_hwnd=9009)
    assert verifier.calls[0]["windowHandle"] == 9009
    assert target.window_source == "session"


# ---------------------------------------------------------------- 落点与点击


def test_click_center_is_rect_middle():
    target = locate_image(IMAGE_LOCATOR, verifier=FakeVerifier(_hit()))
    assert plan_image_click(target) == (120, 212)


def test_click_random_stays_inside_rect_and_is_not_constant():
    target = locate_image(IMAGE_LOCATOR, verifier=FakeVerifier(_hit()))
    rolls = iter([0.0, 1.0])
    first = plan_image_click(target, click_position="random", roll=lambda: next(rolls))
    assert 100 <= first[0] < 140 and 200 <= first[1] < 224
    second = plan_image_click(target, click_position="random", roll=lambda: 0.5)
    assert second != plan_image_click(target, click_position="random", roll=lambda: 0.9)


def test_double_right_click_presses_and_releases_pairs():
    mouse = MouseRecorder()
    perform_click((150, 160), click_type="double", button="right", mouse=mouse)
    assert mouse.timeline == ["move", "down", "up", "down", "up"]
    # 0x0008/0x0010 = MOUSEEVENTF_RIGHTDOWN / RIGHTUP
    assert [event[3] for event in mouse.events[1:]] == [0x0008, 0x0010, 0x0008, 0x0010]


def test_modifiers_are_released_even_when_click_raises():
    """辅助键必须抬起：点击中途抛异常也不能把 Ctrl 永久留住。"""
    keys = KeyRecorder()

    def boom(event: str, x: int, y: int, flag: int) -> None:
        if event == "down":
            raise RuntimeError("mouse broke")

    with pytest.raises(RuntimeError):
        perform_click((10, 10), modifiers=["Ctrl"], mouse=boom, keys=keys)
    assert keys.sequences == ["{VK_CONTROL down}", "{VK_CONTROL up}"]


def test_random_point_without_real_mouse_is_rejected():
    """simulateHuman=false 与 clickPosition=random 互斥（与 UIA 路径同一条口径）。"""
    with pytest.raises(ImageTargetError) as excinfo:
        click_image(
            IMAGE_LOCATOR,
            verifier=FakeVerifier(_hit()),
            simulate_human=False,
            click_position="random",
            mouse=MouseRecorder(),
        )
    assert excinfo.value.code == "INVALID_INPUT"


def test_click_image_evidence_records_layer_and_scale():
    mouse = MouseRecorder()
    evidence = click_image(
        IMAGE_LOCATOR, verifier=FakeVerifier(_hit(scale=0.8)), mouse=mouse, simulate_human=False
    )
    assert evidence["usedLayer"] == "image"
    assert evidence["usedScale"] == 0.8
    assert evidence["path"] == "image-mouse"
    assert evidence["point"] == [120, 212]
    # 图像元素没有 invoke() 通路：退让必须留在证据里
    assert evidence["note"]


# ------------------------------------------------------- findElement / 动作分发


class FakeSession:
    def __init__(self, window_handle: int = 7001) -> None:
        self.window_handle = window_handle
        self.elements: dict[str, dict] = {}


def test_find_element_mints_handle_and_stores_locator(monkeypatch):
    monkeypatch.setattr(image_target, "_default_verifier", lambda: FakeVerifier(_hit()))
    session = FakeSession()
    result = find_element_result(
        _inv("desktop.findElement", locator=IMAGE_LOCATOR),
        session,
        "s1",
        IMAGE_LOCATOR,
        {"timeoutMs": 0},
    )
    assert result.status == "success"
    element_id = result.outputs["elementId"]
    assert session.elements[element_id] == IMAGE_LOCATOR
    assert result.outputs["matchedCount"] == 1
    assert result.diagnostics["usedLayer"] == "image"
    assert result.effects[0].details["usedLayer"] == "image"


def test_find_element_requires_unique_match(monkeypatch):
    """多处匹配必须报歧义：elementId 是后续命令信任的句柄，不随便挑一处。"""
    monkeypatch.setattr(
        image_target, "_default_verifier", lambda: FakeVerifier(_hit(count=3))
    )
    session = FakeSession()
    result = find_element_result(
        _inv("desktop.findElement", locator=IMAGE_LOCATOR),
        session,
        "s1",
        IMAGE_LOCATOR,
        {"timeoutMs": 0},
    )
    assert result.status == "error"
    assert result.error.code == ErrorCode.ELEMENT_AMBIGUOUS
    assert result.error.details["matchedCount"] == 3
    assert session.elements == {}


def test_find_element_zero_hit_is_not_found(monkeypatch):
    monkeypatch.setattr(
        image_target, "_default_verifier", lambda: FakeVerifier({"count": 0})
    )
    result = find_element_result(
        _inv("desktop.findElement", locator=IMAGE_LOCATOR),
        FakeSession(),
        "s1",
        IMAGE_LOCATOR,
        {"timeoutMs": 0},
    )
    assert result.error.code == ErrorCode.ELEMENT_NOT_FOUND


def test_action_on_image_only_supports_click(monkeypatch):
    """getText / input / select 在图像元素上给干净失败，不是 pydantic 崩。"""
    session = FakeSession()
    session.elements["e1"] = IMAGE_LOCATOR
    for command in ("desktop.getText", "desktop.input", "desktop.select"):
        result = action_result(
            _inv(command, elementId="e1", text="x"),
            session,
            "s1",
            "e1",
            command,
            {"text": "x"},
            IMAGE_LOCATOR,
        )
        assert result.status == "error"
        assert result.error.code == ErrorCode.INVALID_INPUT
        assert "desktop.click" in result.error.message


def test_action_click_uses_rect_and_writes_evidence(monkeypatch):
    mouse = MouseRecorder()
    monkeypatch.setattr(image_target, "_default_verifier", lambda: FakeVerifier(_hit()))
    monkeypatch.setattr(image_target, "_default_mouse", mouse)
    session = FakeSession()
    session.elements["e1"] = IMAGE_LOCATOR
    result = action_result(
        _inv("desktop.click", elementId="e1", postDelayMs=0),
        session,
        "s1",
        "e1",
        "desktop.click",
        {"clickType": "single", "button": "left"},
        IMAGE_LOCATOR,
    )
    assert result.status == "success"
    assert mouse.timeline == ["move", "down", "up"]
    details = result.effects[0].details
    assert details["path"] == "image-mouse"
    assert details["usedLayer"] == "image"
    assert details["point"] == [120, 212]


# ------------------------------------------------------------ 两个后端的接线


@DESKTOP_ONLY
def test_uia_executor_dispatches_image_element(monkeypatch):
    """接线判据：`desktop.click` 拿到的 elementId 是图像元素时走图像面，不炸 validate。"""
    mouse = MouseRecorder()
    monkeypatch.setattr(image_target, "_default_verifier", lambda: FakeVerifier(_hit()))
    monkeypatch.setattr(image_target, "_default_mouse", mouse)

    executor = DesktopExecutor()
    session = _DesktopSession(process_id=1, window_handle=7001, elements={"e1": IMAGE_LOCATOR})
    executor._sessions["s1"] = session

    result = executor._execute_sync(
        _inv("desktop.click", sessionId="s1", elementId="e1", postDelayMs=0)
    )
    assert result.status == "success", result.error
    assert mouse.timeline == ["move", "down", "up"]
    assert result.effects[0].details["usedLayer"] == "image"


@DESKTOP_ONLY
def test_win32_executor_dispatches_image_element(monkeypatch):
    mouse = MouseRecorder()
    monkeypatch.setattr(image_target, "_default_verifier", lambda: FakeVerifier(_hit()))
    monkeypatch.setattr(image_target, "_default_mouse", mouse)

    executor = _win32_executor()
    if executor is None:
        pytest.skip("Win32DesktopExecutor 在非 win32 上是占位 None")
    session = _Win32Session(process_id=1, window_handle=7001, elements={"e1": IMAGE_LOCATOR})
    executor._sessions["s1"] = session

    result = executor._execute_sync(
        _inv("desktop.win32.click", sessionId="s1", elementId="e1", postDelayMs=0)
    )
    assert result.status == "success", result.error
    assert result.effects[0].resource.startswith("desktop.win32.session:s1:element:e1")
    assert result.effects[0].details["usedLayer"] == "image"


@DESKTOP_ONLY
def test_uia_find_element_accepts_image_locator(monkeypatch):
    """接线判据：`desktop.findElement` 遇到图像 locator 不再抛 ValidationError。"""
    monkeypatch.setattr(
        image_target, "_default_verifier", lambda: FakeVerifier(_hit(scale=0.8))
    )
    executor = DesktopExecutor()
    executor._sessions["s1"] = _DesktopSession(process_id=1, window_handle=7001, elements={})

    result = executor._execute_sync(
        _inv("desktop.findElement", sessionId="s1", locator=IMAGE_LOCATOR, timeoutMs=0)
    )
    assert result.status == "success", result.error
    assert result.outputs["elementId"]
    assert result.diagnostics["usedScale"] == 0.8


def _win32_executor():
    from rpa_core.executors.desktop_win32 import Win32DesktopExecutor

    return Win32DesktopExecutor() if Win32DesktopExecutor is not None else None


# ------------------------------------------------------- 错误码闭合（补门禁盲区）


@pytest.mark.parametrize(
    "manifest",
    [
        "commands/desktop/findElement.json",
        "commands/desktop/click.json",
        "commands/desktop_win32/findElement.json",
        "commands/desktop_win32/click.json",
    ],
)
def test_image_error_codes_are_declared_by_manifests(manifest):
    """图像层用动态码返回 ⇒ `check_error_contract` 的字面量切片看不见它。

    这条把「本模块可能抛的码」与 manifest 的 `errors` 声明逐一对上：图像层新增一个
    manifest 没声明的码时，这里会先红，而不是等运行记录里冒出个野码。
    """
    import json
    from pathlib import Path

    declared = set(json.loads(Path(manifest).read_text(encoding="utf-8"))["errors"])
    assert image_target.ALLOWED_CODES <= declared, manifest


def test_unknown_image_error_code_is_rejected_loudly():
    with pytest.raises(ValueError):
        ImageTargetError("boom", code="NOT_A_REAL_CODE")
