"""真人化鼠标注入（M107 S1）：SendInput 代替 ``mouse_event``，移动走缓动轨迹。

## 为什么换掉 ``mouse_event``

它是 Windows XP 时代的 legacy 入口。现代反作弊与安全软件**优先**把它当注入
特征；``SendInput`` 是当前官方入口，可带 ``dwExtraInfo`` 标记来源，也能一次
提交一批事件。影刀的「拟人化」底层同样是这一档（AutoIt 内部走 SendInput）。

## 为什么移动要缓动而不是瞬移

瞬移到位（``SetCursorPos`` 一步跳过去）是机器最明显的特征之一：真实轨迹有
加减速、有轻微弧度、耗时随距离增长。这里用三次贝塞尔 + ease-out 采样，
步数与总时长都按距离给：

- 距离 < 8px 直接落点（微小移动做轨迹没意义，还会拖慢点击）；
- 时长 ``clamp(80 + 距离×0.35, 120, 900)`` 再乘 0.7~1.3 的随机；
- 步数 ``clamp(距离/12, 6, 40)``。

## 测试边界

本模块**所有**真实副作用都收在 ``_Win32Mouse`` 一个类里：单测传入替身即可
录下「移动序列 / 按下抬起标志 / 每步间隔」，不需要动真鼠标（测试进程通常
也没有输入权限）。
"""

from __future__ import annotations

import math
import random
import time
from collections.abc import Sequence
from typing import Any, Protocol

# -- MOUSEEVENTF_* 标志 -------------------------------------------------------
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000  # 配合 ABSOLUTE：坐标按整个虚拟桌面归一化

# GetSystemMetrics 索引（虚拟桌面，多屏/负坐标都靠它）
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

# 缓动参数（对外可调，单测据此断言边界）
MIN_MOVE_DISTANCE = 8.0
MIN_DURATION_MS = 120.0
MAX_DURATION_MS = 900.0
MIN_STEPS = 6
MAX_STEPS = 40

# 到位后到按下之间的停顿：真人不会「一移到位立刻按」，这几十毫秒是最廉价、
# 也最像人的一个特征。太快（<40ms）像机器，太慢（>120ms）拖节奏。
SETTLE_MIN_MS = 40.0
SETTLE_MAX_MS = 120.0


class MouseAPI(Protocol):
    """真实鼠标后端需要的最小能力集（单测用替身实现）。"""

    def cursor_pos(self) -> tuple[int, int]: ...

    def virtual_screen(self) -> tuple[int, int, int, int]: ...

    def send(self, events: Sequence[dict[str, int]]) -> int: ...


class _Win32Mouse:
    """ctypes 实现：只在真正注入时才 import，纯函数层保持跨平台可测。"""

    def __init__(self) -> None:
        import ctypes
        import ctypes.wintypes as wt

        self._ctypes = ctypes
        self._wt = wt
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._user32.SendInput.argtypes = [
            wt.UINT,
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        self._user32.SendInput.restype = wt.UINT

    def cursor_pos(self) -> tuple[int, int]:
        point = self._wt.POINT()
        if not self._user32.GetCursorPos(self._ctypes.byref(point)):
            return (0, 0)
        return (int(point.x), int(point.y))

    def virtual_screen(self) -> tuple[int, int, int, int]:
        metrics = self._user32.GetSystemMetrics
        return (
            int(metrics(SM_XVIRTUALSCREEN)),
            int(metrics(SM_YVIRTUALSCREEN)),
            int(metrics(SM_CXVIRTUALSCREEN)),
            int(metrics(SM_CYVIRTUALSCREEN)),
        )

    def send(self, events: Sequence[dict[str, int]]) -> int:
        ctypes = self._ctypes

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [
                ("dx", ctypes.c_long),
                ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong),
                ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.c_size_t),
            ]

        class INPUT(ctypes.Structure):
            class _UNION(ctypes.Union):
                _fields_ = [("mi", MOUSEINPUT)]

            _anonymous_ = ("u",)
            _fields_ = [("type", ctypes.c_ulong), ("u", _UNION)]

        payload = [
            INPUT(
                type=0,  # INPUT_MOUSE
                u=INPUT._UNION(
                    mi=MOUSEINPUT(
                        int(item.get("dx", 0)),
                        int(item.get("dy", 0)),
                        int(item.get("data", 0)),
                        int(item.get("flags", 0)),
                        0,
                        0,
                    )
                ),
            )
            for item in events
        ]
        if not payload:
            return 0
        array = (INPUT * len(payload))(*payload)
        return int(
            self._user32.SendInput(
                len(payload), ctypes.cast(array, ctypes.c_void_p), ctypes.sizeof(INPUT)
            )
        )


_DEFAULT_API: MouseAPI | None = None


def default_api() -> MouseAPI:
    """进程内共享一个后端实例（重复 WinDLL 有开销）。"""
    global _DEFAULT_API
    if _DEFAULT_API is None:
        _DEFAULT_API = _Win32Mouse()
    return _DEFAULT_API


# -- 纯函数：坐标与轨迹 -------------------------------------------------------


def normalize_absolute(
    x: int, y: int, screen: tuple[int, int, int, int]
) -> tuple[int, int]:
    """屏幕坐标 → SendInput 的 0..65535 归一化坐标。

    ``MOUSEEVENTF_ABSOLUTE`` 的坐标系是**整个虚拟桌面**（多屏、负坐标都包含），
    所以必须先用 SM_X/Y/CX/CYVIRTUALSCREEN 减去原点再归一化；直接用主屏宽高
    会在副屏上整体偏移一个屏幕的量。
    """
    left, top, width, height = screen
    if width <= 1 or height <= 1:
        return (0, 0)
    nx = round((x - left) * 65535 / (width - 1))
    ny = round((y - top) * 65535 / (height - 1))
    return (max(0, min(65535, nx)), max(0, min(65535, ny)))


def ease_out_cubic(t: float) -> float:
    """ease-out：先快后慢（真人接近目标时会减速）。``t`` 会被夹到 [0,1]。"""
    t = 0.0 if t < 0 else (1.0 if t > 1 else t)
    return 1.0 - (1.0 - t) ** 3


def bezier_point(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    t: float,
) -> tuple[float, float]:
    """三次贝塞尔取点（控制点决定弧度的形状）。"""
    u = 1.0 - t
    x = u**3 * p0[0] + 3 * u**2 * t * p1[0] + 3 * u * t**2 * p2[0] + t**3 * p3[0]
    y = u**3 * p0[1] + 3 * u**2 * t * p1[1] + 3 * u * t**2 * p2[1] + t**3 * p3[1]
    return (x, y)


def plan_move(
    start: tuple[int, int],
    end: tuple[int, int],
    *,
    rng: random.Random | None = None,
) -> tuple[list[tuple[int, int]], float]:
    """规划一条缓动轨迹：返回 ``(点列, 总时长秒)``。

    距离过近时只返回终点（瞬移），时长 0；否则按上面的步数/时长公式采样，
    控制点在连线两侧随机偏移，制造轻微弧度。
    """
    rng = rng or random.Random()
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    distance = math.hypot(dx, dy)
    if distance < MIN_MOVE_DISTANCE:
        return [end], 0.0

    duration_ms = 80.0 + distance * 0.35
    duration_ms = max(MIN_DURATION_MS, min(MAX_DURATION_MS, duration_ms))
    duration_ms *= rng.uniform(0.7, 1.3)
    steps = int(max(MIN_STEPS, min(MAX_STEPS, distance / 12.0)))

    # 垂直于连线的单位向量，用来给控制点加弧度
    if distance > 0:
        nx, ny = -dy / distance, dx / distance
    else:
        nx, ny = 0.0, 0.0
    bow = min(40.0, distance * 0.12)
    offset1 = rng.uniform(-bow, bow)
    offset2 = rng.uniform(-bow, bow)
    p0 = (float(start[0]), float(start[1]))
    p3 = (float(end[0]), float(end[1]))
    p1 = (p0[0] + dx * 0.3 + nx * offset1, p0[1] + dy * 0.3 + ny * offset1)
    p2 = (p0[0] + dx * 0.7 + nx * offset2, p0[1] + dy * 0.7 + ny * offset2)

    points: list[tuple[int, int]] = []
    for index in range(1, steps + 1):
        raw = index / steps
        # ease-out 作用在**参数轴**上：前期 t 增长快（位移大）、后期放缓（减速逼近）
        eased = ease_out_cubic(raw)
        px, py = bezier_point(p0, p1, p2, p3, eased)
        points.append((round(px), round(py)))
    points[-1] = end
    return points, duration_ms / 1000.0


# -- 注入 ---------------------------------------------------------------------


def move_smooth(
    start: tuple[int, int],
    end: tuple[int, int],
    *,
    api: MouseAPI | None = None,
    sleep: Any = None,
    rng: random.Random | None = None,
    plan: tuple[list[tuple[int, int]], float] | None = None,
) -> dict[str, Any]:
    """从 ``start`` 缓动到 ``end``。返回轨迹证据（点数/时长/起点终点）。"""
    api = api or default_api()
    sleep = sleep or time.sleep
    points, duration = plan if plan is not None else plan_move(start, end, rng=rng)
    screen = api.virtual_screen()
    per_step = duration / max(1, len(points))
    for point in points:
        api.send([_move_event(point[0], point[1], screen)])
        if per_step > 0:
            sleep(per_step)
    return {
        "points": len(points),
        "durationMs": round(duration * 1000.0, 1),
        "from": [int(start[0]), int(start[1])],
        "to": [int(end[0]), int(end[1])],
    }


def move_to(
    x: int,
    y: int,
    *,
    duration_ms: float | None = None,
    api: MouseAPI | None = None,
    sleep: Any = None,
    rng: random.Random | None = None,
) -> dict[str, Any]:
    """把光标移到 ``(x, y)``：默认缓动，``duration_ms<=0`` 时瞬移。"""
    api = api or default_api()
    start = api.cursor_pos()
    if duration_ms is not None and duration_ms <= 0:
        screen = api.virtual_screen()
        api.send([_move_event(int(x), int(y), screen)])
        return {"points": 1, "durationMs": 0.0, "from": list(start), "to": [int(x), int(y)]}
    result = move_smooth(start, (int(x), int(y)), api=api, sleep=sleep, rng=rng)
    if duration_ms is not None:
        result["requestedMs"] = float(duration_ms)
    return result


def settle(
    *,
    min_ms: float = SETTLE_MIN_MS,
    max_ms: float = SETTLE_MAX_MS,
    rng: random.Random | None = None,
    sleep: Any = None,
) -> float:
    """光标到位后、按下前停一小段随机时间。返回实际停顿毫秒数。

    独立成函数而不是塞进 ``move_to``：``move_to`` 是纯轨迹规划，停顿属于
    「点击前的犹豫」，只在真要按下去的那条路上发生（拖拽起点不模拟犹豫）。
    """
    rng = rng or random.Random()
    low, high = (min_ms, max_ms) if min_ms <= max_ms else (max_ms, min_ms)
    delay_ms = rng.uniform(low, high) if high > low else low
    # 先定稿再 sleep：返回值与真实等待必须是同一个数，否则证据和事实对不上
    delay_ms = round(delay_ms, 1)
    if delay_ms > 0:
        (sleep or time.sleep)(delay_ms / 1000.0)
    return delay_ms


def send_button(
    flag: int,
    *,
    api: MouseAPI | None = None,
    extra_info: int = 0,
) -> int:
    """按 ``MOUSEEVENTF_*`` 标志发一次按下/抬起/滚轮。

    ``extra_info`` 原样塞进 ``dwExtraInfo``：调用方可以用它标记「这批事件来自
    RPA」，排查注入问题时比无标记更容易分辨。
    """
    api = api or default_api()
    item: dict[str, int] = {"flags": int(flag)}
    if extra_info:
        item["data"] = int(extra_info)
    return api.send([item])


def click_at(
    x: int,
    y: int,
    *,
    button: str = "left",
    click_type: str = "single",
    duration_ms: float | None = None,
    api: MouseAPI | None = None,
    sleep: Any = None,
    rng: random.Random | None = None,
) -> dict[str, Any]:
    """移动（缓动）→ 按下抬起。辅助键仍由上层 ``perform_click`` 管，不在这里。"""
    down, up = _BUTTON_FLAGS[button]
    move_evidence = move_to(
        x, y, duration_ms=duration_ms, api=api, sleep=sleep, rng=rng
    )
    times = 2 if click_type == "double" else 1
    for _ in range(times):
        send_button(down, api=api)
        send_button(up, api=api)
    return {**move_evidence, "button": button, "clickType": click_type, "repeats": times}


_MOVE_FLAGS = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK


def _move_event(x: int, y: int, screen: tuple[int, int, int, int]) -> dict[str, int]:
    """一次绝对移动事件（坐标按虚拟桌面归一化）。"""
    nx, ny = normalize_absolute(int(x), int(y), screen)
    return {"dx": nx, "dy": ny, "flags": _MOVE_FLAGS}


_BUTTON_FLAGS: dict[str, tuple[int, int]] = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}


__all__ = [
    "MOUSEEVENTF_ABSOLUTE",
    "MOUSEEVENTF_LEFTDOWN",
    "MOUSEEVENTF_LEFTUP",
    "MOUSEEVENTF_MOVE",
    "MOUSEEVENTF_RIGHTDOWN",
    "MOUSEEVENTF_RIGHTUP",
    "MOUSEEVENTF_VIRTUALDESK",
    "MouseAPI",
    "bezier_point",
    "click_at",
    "default_api",
    "ease_out_cubic",
    "move_smooth",
    "move_to",
    "normalize_absolute",
    "plan_move",
    "send_button",
    "settle",
]
