"""图像元素的可执行化（M82）：把捕获出来的 image locator 真的变成动作。

为什么需要这一层：M77/M80 让捕获能把光标附近裁成 image locator（模板 V2 + DPI
provenance），但 ``desktop.findElement`` 只认语义定位器（``DesktopLocator`` 的
backend 限定 uia/win32），于是图像元素**捕得到、存得下、校验得过，却点不动**——
``DesktopLocator.model_validate`` 会直接抛 ValidationError。本模块补的就是执行侧
这段接线。

分工（每一层都只有一处权威）：

* **是否走图像** —— ``capture.image_guard.is_explicit_image`` 判（ADR 0003 红线：
  语义空命中绝不自动降级，只有元素自己显式声明 ``driver="image"`` 才走这里）；
* **定位** —— 只调 ``capture.image_verification.ImageElementVerifier``；多尺度重采样的
  权威在 ``capture.image_locator.locate_by_template``，本模块**不实现任何匹配**；
* **落点** —— 复用 ``executors.base.random_point_in_rect``（与 UIA 路径同一份随机带）；
* **辅助键** —— 复用 ``executors.base.modifier_key_sequences``（与 UIA 路径同一份按下/
  抬起序列），经 pywinauto 发送，避免第二套「Ctrl 到底按了没」的实现。

图像元素没有 UIA 控件可 ``invoke()``，所以动作只能是合成鼠标。这条差异**留在证据里**
（``path="image-mouse"`` 与 ``note``），不冒充 UIA 的 ``invoke`` / ``click_input``。
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

#: 点击落点口径（与 manifest 的 clickPosition 枚举一致）
CLICK_POSITIONS = ("center", "random")

# MOUSEEVENTF_* 标志对：left=(down, up) / right=(down, up)
_BUTTON_EVENTS = {
    "left": (0x0002, 0x0004),
    "right": (0x0008, 0x0010),
}
_BUTTONS = tuple(_BUTTON_EVENTS)

#: 双击两次按下之间的间隔（毫秒）。太快会被目标应用合并成单击。
_DOUBLE_CLICK_GAP_MS = 20

_SHAPE_MESSAGES = {
    "template-schema-requires-recapture": (
        "该图像元素是旧版模板（V1，没有记录捕获时的 DPI），"
        "必须在当前环境重新捕获；本项目不做模板迁移。"
    ),
    "template-size-must-be-positive-pair": "图像模板尺寸非法（需为正整数宽高对）",
    "image-not-explicit": "locator 没有显式声明 driver=image，拒绝走图像层",
}

_VERIFIER_ERROR_CODES = {
    "invalid-window-handle": "INVALID_INPUT",
    "invalid-locator": "INVALID_INPUT",
    "image-not-explicit": "INVALID_INPUT",
    "template-schema-requires-recapture": "INVALID_INPUT",
    "template-size-must-be-positive-pair": "INVALID_INPUT",
    "locator has no imageTemplate / imageKeyword": "INVALID_INPUT",
}

#: 本模块允许抛出的错误码（封闭集）。
#:
#: 为什么需要这份白名单：这里的错误码是由「verifier 回什么」动态映射出来的，
#: `check_error_contract` 的字面量切片**看不见它**（它只认 `ErrorCode.X` 出现）。
#: 于是「图像层回了一个 manifest 没声明过的码」会静默溜过门禁。把码收敛成封闭集、
#: 由构造器校验，再让测试比对 manifest 的 `errors` 声明，盲区就被堵上。
ALLOWED_CODES = frozenset({"INVALID_INPUT", "ELEMENT_NOT_FOUND", "EXECUTOR_FAILED"})


class ImageTargetError(Exception):
    """图像目标定位/动作失败。``code`` 直接是 ``ErrorCode`` 的名字，执行器照抄。"""

    def __init__(
        self,
        message: str,
        *,
        code: str = "ELEMENT_NOT_FOUND",
        details: dict[str, Any] | None = None,
    ) -> None:
        if code not in ALLOWED_CODES:
            # 门禁看不见动态码，所以这里必须自己炸：宁可当场报编程错误，
            # 也不要把一个 manifest 没声明过的码送到运行记录里。
            raise ValueError(f"unsupported image error code: {code!r}")
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass(frozen=True)
class ImageTarget:
    """一次图像定位的结果（屏幕绝对矩形，与 ``ImageElementVerifier`` 同口径）。"""

    rect: dict[str, int]
    window_handle: int
    used_scale: float
    matched_count: int
    #: 用的是 locator 自带的句柄还是会话当前句柄（locator 里的旧句柄可能已失效）
    window_source: str


def is_image_locator(locator: Any) -> bool:
    """该 locator 是否显式选择图像层（唯一判定在 ``capture.image_guard``）。"""
    from rpa_core.capture.image_guard import is_explicit_image

    return is_explicit_image(locator)


def locate_image(
    locator: Any,
    *,
    verifier: Any = None,
    session_hwnd: int | None = None,
) -> ImageTarget:
    """按显式 image locator 定位，返回屏幕矩形。

    窗口句柄**优先用 locator 自己的**（元素可能属于会话之外的子窗口）；locator 里的句柄
    已失效（窗口被目标应用重建——微信实测遇到过）时退回会话当前句柄，并把用了哪个写进
    ``window_source``：换窗口是真实发生的事，不能悄悄发生。
    """
    from rpa_core.capture.image_guard import image_locator_errors

    if not is_image_locator(locator):
        raise ImageTargetError(
            _SHAPE_MESSAGES["image-not-explicit"],
            code="INVALID_INPUT",
            details={"reason": "image-not-explicit"},
        )
    shape_errors = image_locator_errors(locator)
    if shape_errors:
        reason = shape_errors[0]
        raise ImageTargetError(
            _SHAPE_MESSAGES.get(reason, reason),
            code="INVALID_INPUT",
            details={"reason": reason, "action": "recapture"},
        )

    payload, window_source = _with_window(locator, session_hwnd)
    reply = (verifier or _default_verifier()).verify(payload)
    if not isinstance(reply, dict):
        raise ImageTargetError("image verifier returned no result", code="EXECUTOR_FAILED")
    if reply.get("error"):
        reason = str(reply["error"])
        raise ImageTargetError(
            _SHAPE_MESSAGES.get(reason, reason),
            code=_VERIFIER_ERROR_CODES.get(reason, "EXECUTOR_FAILED"),
            details={"reason": reason},
        )

    count = _as_int(reply.get("count"))
    if count < 1:
        raise ImageTargetError(
            "image target not found",
            code="ELEMENT_NOT_FOUND",
            details={"matchedCount": 0, "usedLayer": "image"},
        )

    rect = reply.get("rect")
    if not isinstance(rect, dict) or not rect:
        raise ImageTargetError(
            "image verifier returned no rect", code="EXECUTOR_FAILED"
        )
    return ImageTarget(
        rect={key: _as_int(value) for key, value in rect.items()},
        window_handle=_as_int(payload.get("windowHandle")),
        used_scale=_as_float(reply.get("usedScale"), 1.0),
        matched_count=count,
        window_source=window_source,
    )


def plan_image_click(
    target: ImageTarget,
    *,
    click_position: str = "center",
    roll: Callable[[], float] | None = None,
) -> tuple[int, int]:
    """按落点口径算出屏幕绝对坐标（center / random，随机带与 UIA 路径同源）。"""
    if click_position not in CLICK_POSITIONS:
        raise ImageTargetError(
            f"未知的 clickPosition：{click_position!r}（只支持 center / random）",
            code="INVALID_INPUT",
        )
    width = _as_int(target.rect.get("width"))
    height = _as_int(target.rect.get("height"))
    if width <= 0 or height <= 0:
        raise ImageTargetError(
            "image rect is empty",
            code="EXECUTOR_FAILED",
            details={"rect": dict(target.rect)},
        )
    if click_position == "random":
        from .base import random_point_in_rect

        offset = random_point_in_rect((width, height), roll=roll)
    else:
        offset = (width // 2, height // 2)
    return _as_int(target.rect.get("left")) + offset[0], (
        _as_int(target.rect.get("top")) + offset[1]
    )


def perform_click(
    point: tuple[int, int],
    *,
    click_type: str = "single",
    button: str = "left",
    modifiers: Sequence[str] = (),
    mouse: Callable[[str, int, int, int], None] | None = None,
    keys: Callable[[str], None] | None = None,
) -> None:
    """在屏幕坐标上合成一次点击（支持双击 / 右键 / 辅助键）。

    ``mouse`` / ``keys`` 是注入点：单测用录像替身证明「按下-抬起成对、辅助键必抬起」，
    不需要真的动鼠标（默认测试进程禁止合成输入，见 tests/conftest.py）。
    """
    if click_type not in ("single", "double"):
        raise ImageTargetError(
            f"未知的 clickType：{click_type!r}（只支持 single / double）",
            code="INVALID_INPUT",
        )
    if button not in _BUTTON_EVENTS:
        raise ImageTargetError(
            f"未知的 button：{button!r}（只支持 left / right）", code="INVALID_INPUT"
        )
    down, up = _BUTTON_EVENTS[button]
    times = 2 if click_type == "double" else 1
    press = mouse or _default_mouse

    press("move", point[0], point[1], 0)
    if not modifiers:
        _press_release(press, down, up, times)
        return

    from .base import modifier_key_sequences

    press_keys = keys or _default_keys
    down_sequences, up_sequences = modifier_key_sequences(modifiers)
    for sequence in down_sequences:
        press_keys(sequence)
    try:
        _press_release(press, down, up, times)
    finally:
        # 抬起写在 finally 里：点击抛异常也不能把 Ctrl 永久留住（与 UIA 路径同一条纪律）。
        for sequence in up_sequences:
            press_keys(sequence)


def click_image(
    locator: Any,
    *,
    verifier: Any = None,
    session_hwnd: int | None = None,
    click_type: str = "single",
    button: str = "left",
    modifiers: Sequence[str] = (),
    click_position: str = "center",
    simulate_human: bool = True,
    roll: Callable[[], float] | None = None,
    mouse: Callable[[str, int, int, int], None] | None = None,
    keys: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """定位 + 点击，返回执行证据（实际使用层 / 缩放比 / 命中数 / 落点）。"""
    if not simulate_human and click_position == "random":
        # 与 UIA 路径同一条互斥口径：随机落点要靠真实鼠标表达，声明了就得生效。
        raise ImageTargetError(
            "simulateHuman=false 与 clickPosition=random 互斥：要指定随机落点请用 "
            "simulateHuman=true，要用最短路径请改回 clickPosition=center。",
            code="INVALID_INPUT",
        )
    target = locate_image(locator, verifier=verifier, session_hwnd=session_hwnd)
    point = plan_image_click(target, click_position=click_position, roll=roll)
    perform_click(
        point,
        click_type=click_type,
        button=button,
        modifiers=modifiers,
        mouse=mouse,
        keys=keys,
    )
    evidence: dict[str, Any] = {
        "path": "image-mouse",
        "usedLayer": "image",
        "usedScale": target.used_scale,
        "matchedCount": target.matched_count,
        "rect": dict(target.rect),
        "point": [point[0], point[1]],
        "windowHandle": target.window_handle,
        "windowHandleSource": target.window_source,
        "clickType": click_type,
        "button": button,
        "simulateHuman": bool(simulate_human),
    }
    if not simulate_human:
        # 图像元素没有 invoke() 通路：simulateHuman=false 在这里只能退化成合成鼠标，
        # 退让必须留在证据里，否则就是「勾了却没生效」的静默漂移。
        evidence["note"] = "图像元素不支持 invoke()，只能走合成鼠标"
    return evidence


def find_element_result(
    invocation: Any,
    session: Any,
    session_id: str,
    locator: dict[str, Any],
    inputs: dict[str, Any],
    *,
    resource_prefix: str = "desktop.session",
) -> Any:
    """图像元素的 `findElement`：定位成功即发 `elementId`（两个后端共用）。

    与语义分支同口径**要求唯一命中**：`elementId` 是后续命令信任的句柄，「多处匹配
    随便挑一处」会让后面的点击落在不可预期的位置（宁停不猜）。`timeoutMs` 与语义
    分支一样是「等元素出现的预算」，不是单次匹配超时。

    两个后端共用这一份：图像定位跟 uia/win32 无关，各写一份只会漂移出第二套规则。
    """
    from rpa_core.model.command import CommandResult, EffectKind, EffectRecord
    from rpa_core.model.errors import ErrorCode

    timeout_ms = int(inputs.get("timeoutMs") or 0)
    deadline = time.monotonic() + timeout_ms / 1000.0
    while True:
        try:
            target = locate_image(locator, session_hwnd=getattr(session, "window_handle", None))
        except ImageTargetError as exc:
            # 只有「这一帧没找到」值得再等；模板过期、句柄非法等结构问题等多久都一样。
            if exc.code != "ELEMENT_NOT_FOUND" or time.monotonic() >= deadline:
                return CommandResult.failure(ErrorCode(exc.code), str(exc), details=exc.details)
            time.sleep(0.1)
            continue
        if target.matched_count > 1:
            return CommandResult.failure(
                ErrorCode.ELEMENT_AMBIGUOUS,
                "Image element matched multiple targets",
                details={
                    "locator": dict(locator),
                    "matchedCount": target.matched_count,
                    "usedLayer": "image",
                    "usedScale": target.used_scale,
                },
            )
        break

    element_id = str(uuid.uuid4())
    session.elements[element_id] = dict(locator)
    return CommandResult.success(
        outputs={"elementId": element_id, "matchedCount": 1},
        diagnostics={"usedLayer": "image", "usedScale": target.used_scale},
        effects=[
            EffectRecord.committed(
                invocation,
                kind=EffectKind.READ,
                resource=f"{resource_prefix}:{session_id}:element:{element_id}",
                # 理由同语义分支：续跑要靠 details 里的 locator 还原元素缓存。
                details={
                    "operation": "findElement",
                    "locator": dict(locator),
                    "usedLayer": "image",
                    "usedScale": target.used_scale,
                },
            )
        ],
    )


def action_result(
    invocation: Any,
    session: Any,
    session_id: str,
    element_id: str,
    command: str,
    inputs: dict[str, Any],
    locator: dict[str, Any],
    *,
    click_command: str = "desktop.click",
    resource_prefix: str = "desktop.session",
) -> Any:
    """图像元素的动作分发（两个后端共用）：目前只有 click 有图像通路。

    其余命令（getText / input / select / screenshot / drag）在图像元素上没有对应的
    执行面——图像里没有可读文本、也没有 UIA 输入接口。**显式失败并说清原因**，
    不静默读空串、不假装输入成功（那也是「勾了却没生效」）。
    """
    from rpa_core.model.command import CommandResult, EffectKind, EffectRecord
    from rpa_core.model.errors import ErrorCode

    if command != click_command:
        return CommandResult.failure(
            ErrorCode.INVALID_INPUT,
            f"{command} 需要语义控件；图像元素只支持 {click_command}",
            details={"elementId": element_id, "driver": "image", "usedLayer": "image"},
        )
    post_delay = int(inputs.get("postDelayMs", 0) or 0)
    try:
        evidence = click_image(
            locator,
            session_hwnd=getattr(session, "window_handle", None),
            click_type=str(inputs.get("clickType") or "single"),
            button=str(inputs.get("button") or "left"),
            modifiers=inputs.get("modifiers") or (),
            click_position=str(inputs.get("clickPosition") or "center"),
            simulate_human=bool(inputs.get("simulateHuman", True)),
        )
    except ImageTargetError as exc:
        return CommandResult.failure(ErrorCode(exc.code), str(exc), details=exc.details)
    if post_delay > 0:
        time.sleep(post_delay / 1000)
    return CommandResult.success(
        effects=[
            EffectRecord.committed(
                invocation,
                kind=EffectKind.UNSAFE_WRITE,
                resource=f"{resource_prefix}:{session_id}:element:{element_id}",
                details={"operation": "click", **evidence},
            )
        ]
    )


def _press_release(
    press: Callable[[str, int, int, int], None], down: int, up: int, times: int
) -> None:
    for index in range(times):
        press("down", 0, 0, down)
        press("up", 0, 0, up)
        if times > 1 and index == 0:
            time.sleep(_DOUBLE_CLICK_GAP_MS / 1000.0)


def _with_window(locator: Any, session_hwnd: int | None) -> tuple[dict[str, Any], str]:
    """决定这次定位用哪个窗口句柄，并回传来源（locator / session）。"""
    payload = dict(locator)
    locator_hwnd = _as_int(payload.get("windowHandle"))
    if _window_alive(locator_hwnd):
        return payload, "locator"
    if isinstance(session_hwnd, int) and not isinstance(session_hwnd, bool):
        if _window_alive(session_hwnd):
            payload["windowHandle"] = session_hwnd
            return payload, "session"
    # 两个都不可用：原样交给 verifier 判 invalid-window-handle，不在这里猜。
    return payload, "locator"


def _window_alive(hwnd: int) -> bool:
    """句柄是否仍指向一个存在的窗口（非 Windows / 取不到一律 False）。"""
    if hwnd <= 0:
        return False
    try:
        import ctypes

        return bool(ctypes.windll.user32.IsWindow(hwnd))
    except Exception:  # noqa: BLE001 - 非 Windows / 无 user32：交给上层按不可用处理
        return False


def _default_verifier() -> Any:
    from rpa_core.capture.image_verification import ImageElementVerifier

    return ImageElementVerifier()


def _default_mouse(event: str, x: int, y: int, flag: int) -> None:
    import ctypes

    user32 = ctypes.windll.user32
    if event == "move":
        user32.SetCursorPos(int(x), int(y))
    else:
        user32.mouse_event(int(flag), 0, 0, 0, 0)


def _default_keys(sequence: str) -> None:
    from pywinauto.keyboard import send_keys

    send_keys(sequence)


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
