import asyncio
import inspect
import math
import random
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from rpa_core.model.command import CommandInvocation, CommandResult

# 「点击位置」随机落点的中心带：15%~85%。
# 留出边距，避免落在元素边缘的圆角/内边距上；同时与「中心」的差异对用户可感知
# （否则没法验证这个开关真的生效）。
CLICK_POSITION_BAND = (0.15, 0.85)

# manifest 的 `modifiers` 枚举 → pywinauto `send_keys` 的虚拟键名。
#
# 这张表存在的原因是一个真 bug：此前两处点击分支写的是
# `pywinauto.keyboard.key_down("control")`——**这个函数在 pywinauto 0.6.9 里不存在**
# （`pywinauto.keyboard` 只有 `send_keys` / `parse_keys` / `KeyAction` 家族，
# 没有 `key_down` / `key_up`），于是「带辅助键的点击」一直是
# `AttributeError → EXECUTOR_FAILED`。参数消费门禁查不出这一类：
# `modifiers` **确实被读了**，错的是读完之后调用的 API。
# pywinauto 表达「按住某键」的官方途径是 `send_keys("{VK_CONTROL down}")`。
MODIFIER_VIRTUAL_KEYS = {
    "Alt": "VK_MENU",
    "Ctrl": "VK_CONTROL",
    "Shift": "VK_SHIFT",
    "Win": "VK_LWIN",
}

# 大小写无关查表：manifest 是枚举，但手写/导入的工作流 JSON 可能给 `ctrl`。
# 扩展侧的 `modifierFlags()` 就是这个口径（`" ctrl "` → ctrlKey），四个通道要对齐。
MODIFIER_VIRTUAL_KEYS_LOWER = {
    name.lower(): virtual_key for name, virtual_key in MODIFIER_VIRTUAL_KEYS.items()
}


def modifier_key_sequences(modifiers: Sequence[str]) -> tuple[list[str], list[str]]:
    """返回 `(按下的 send_keys 序列, 抬起的 send_keys 序列)`；抬起是按下顺序的**逆序**。

    空白与大小写容错；未知名称原样透传（交给 pywinauto 报错，不做静默丢弃——
    丢了就又是「勾了等于没勾」）。
    """
    keys = []
    for name in modifiers:
        text = str(name).strip()
        keys.append(MODIFIER_VIRTUAL_KEYS_LOWER.get(text.lower(), text))
    return [f"{{{key} down}}" for key in keys], [f"{{{key} up}}" for key in reversed(keys)]


def click_with_modifiers(
    element: Any, click_kwargs: dict[str, Any], modifiers: Sequence[str]
) -> None:
    """按住辅助键 → 真实鼠标点击 → **无论如何**抬起。

    `modifiers` 为空时就是一次普通 `click_input()`（不引入额外的全局按键动作）。
    `pywinauto` 只能在真正需要时导入：`base` 上层的纯函数要保持跨平台可单测。
    """
    if not modifiers:
        element.click_input(**click_kwargs)
        return

    from pywinauto.keyboard import send_keys

    down, up = modifier_key_sequences(modifiers)
    for sequence in down:
        send_keys(sequence)
    try:
        # 抬起写在 finally 里：`click_input()` 抛异常也不能把 Ctrl 永久留住。
        element.click_input(**click_kwargs)
    finally:
        for sequence in up:
            send_keys(sequence)


def accepts_kwarg(func: Any, name: str) -> bool:
    """`func` 是否接受关键字 `name`。

    真实需要：pywinauto 的基础 `click_input` 接受 `coords`，但
    `controls/common_controls.py` 里的包装类（列表/树/表格）把它重写成
    `click_input(button, double, wheel_dist, where, pressed)`——**没有 `coords`**。
    不做能力探测就把 `coords` 传进去会直接 `TypeError`。
    拿不到签名（动态包装、C 扩展）时按「接受」处理，真出错由调用方兜住。
    """
    if func is None:
        return False
    try:
        parameters = inspect.signature(func).parameters
    except (TypeError, ValueError):
        return True
    return name in parameters or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
    )


def _band_ratio(band: tuple[float, float], raw: Any) -> float:
    low, high = band
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 0.0
    if not math.isfinite(value):
        value = 0.0
    return low + (high - low) * min(max(value, 0.0), 1.0)


def random_point_in_rect(
    rect_size: tuple[int, int],
    band: tuple[float, float] = CLICK_POSITION_BAND,
    roll: Callable[[], float] | None = None,
) -> tuple[int, int]:
    """元素矩形内的随机落点（元素相对坐标，偏中心带）。

    纯函数：随机源可注入（`roll`），便于测试「落在带内」与「不是恒定值」。
    用 `(边长 - 1)` 换算，保证 1px 宽/高的元素也落在界内。
    """
    width, height = int(rect_size[0]), int(rect_size[1])
    if width <= 0 or height <= 0:
        raise ValueError(f"元素矩形不可用：{rect_size}")
    source = roll or random.random
    x = round((width - 1) * _band_ratio(band, source()))
    y = round((height - 1) * _band_ratio(band, source()))
    return x, y


@dataclass(frozen=True)
class ClickPlan:
    """一次点击的执行计划：走哪条路径、落在哪。

    两条路径来自 pywinauto 的能力差异：
    - `"input"` = `click_input()`，**真实鼠标**（会移动光标，能表达按钮/双击/辅助键/落点）；
    - `"invoke"` = `invoke()`，UIA/MSAA 直接调用（不移动鼠标，只表达普通单击）。

    `note` 记录「为什么没按声明执行」——退让必须留在证据里，静默退让就是新的漂移。
    """

    path: str
    coords: tuple[int, int] | None = None
    note: str = ""

    def evidence(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"path": self.path}
        if self.coords is not None:
            payload["coords"] = list(self.coords)
        if self.note:
            payload["note"] = self.note
        return payload


def plan_click(
    *,
    simulate_human: bool,
    click_position: str,
    plain_left_single: bool,
    has_input_click: bool,
    has_invoke: bool,
    accepts_coords: bool,
    rect_size: tuple[int, int] | None = None,
    roll: Callable[[], float] | None = None,
) -> ClickPlan:
    """决定点击路径与落点。规则：**能退让就退让，互斥就报错**。

    - `simulateHuman=false` → 最短路径 `invoke()`；但**只在普通左键单击**时可用——
      双击/右键/中键/带辅助键 `invoke()` 表达不了，退回真实鼠标路径（静默忽略参数就是新漂移）；
    - `simulateHuman=true`（默认）→ 真实鼠标路径；
    - `clickPosition=random` 要坐标，只有真实鼠标路径有：与 `simulateHuman=false` **互斥**，
      抛 `ValueError`（静默按中心点点下去是「声明了不生效」的另一副面孔）；
    - 元素能力不足时退让（无 `invoke` / 无 `click_input` / `click_input` 不收 `coords`），
      退让原因写进 `note`。

    全部输入是标量/布尔：不依赖 pywinauto，可跨平台单测。
    """
    if click_position not in ("center", "random"):
        raise ValueError(
            f"未知的 clickPosition：{click_position!r}（只支持 center / random）"
        )

    if not simulate_human:
        if click_position == "random":
            raise ValueError(
                "simulateHuman=false 走 invoke() 直接调用、不移动鼠标，与 clickPosition=random "
                "互斥：要指定随机落点请用 simulateHuman=true（真实鼠标路径），"
                "要用最短路径请改回 clickPosition=center。"
            )
        if not plain_left_single:
            return ClickPlan(
                "input", None, "invoke() 表达不了双击/非左键/辅助键，退回真实鼠标路径"
            )
        if not has_invoke:
            return ClickPlan("input", None, "元素不支持 invoke()，退回真实鼠标路径")
        return ClickPlan("invoke")

    if not has_input_click:
        if not has_invoke:
            raise ValueError("元素既不支持 click_input() 也不支持 invoke()，无法点击")
        return ClickPlan("invoke", None, "元素不支持 click_input()，退回 invoke()")

    if click_position == "center":
        return ClickPlan("input")

    if not accepts_coords or rect_size is None:
        return ClickPlan(
            "input", None, "元素的 click_input() 不接受 coords（或取不到矩形），随机落点退回中心"
        )
    return ClickPlan("input", random_point_in_rect(rect_size, roll=roll))


def _element_rect_size(element: Any) -> tuple[int, int] | None:
    """元素矩形尺寸；取不到（元素已消失、包装类不支持）时返回 None 由调用方退让。"""
    getter = getattr(element, "rectangle", None)
    if getter is None:
        return None
    try:
        rect = getter()
        return int(rect.width()), int(rect.height())
    except Exception:  # noqa: BLE001 —— 能力探测：任何失败都按「拿不到」处理并退让
        return None


def plan_click_for_element(
    element: Any,
    *,
    simulate_human: bool,
    click_position: str,
    click_type: str,
    button: str,
    modifiers: Sequence[str] | None = None,
    roll: Callable[[], float] | None = None,
) -> ClickPlan:
    """从元素能力推导点击计划（两后端共用，避免各写一套判断）。"""
    input_click = getattr(element, "click_input", None)
    return plan_click(
        simulate_human=simulate_human,
        click_position=click_position,
        plain_left_single=(
            click_type == "single" and button == "left" and not (modifiers or [])
        ),
        has_input_click=input_click is not None,
        has_invoke=hasattr(element, "invoke"),
        accepts_coords=accepts_kwarg(input_click, "coords"),
        rect_size=_element_rect_size(element),
        roll=roll,
    )


@dataclass(frozen=True)
class ElementWaitOutcome:
    """等待式元素解析的结果。

    保留**整个匹配集合**而不是只留第一个：win32 后端还要按 `found_index` 选序
    （`_pick(matches, found_index)`），只回传第一个会把那个语义吃掉。

    把「等了多久、轮询了几次」一并带出来：失败时进 `details`、成功时进执行证据——
    否则「这一步为什么花了两秒」在证据里看不出来。
    """

    matches: list[Any]
    waited_ms: float
    polls: int

    @property
    def matched(self) -> bool:
        return bool(self.matches)

    def details(self) -> dict[str, Any]:
        return {"waitedMs": int(self.waited_ms), "polls": self.polls}


def wait_for_element(
    find: Callable[[], list[Any]],
    timeout_ms: int | float | None,
    poll_interval_seconds: float = 0.1,
) -> ElementWaitOutcome:
    """在 `timeout_ms` 内轮询 `find()`，命中（匹配集非空）即返回。

    `timeout_ms` 为 0/空时**只查一次、不做任何等待**——未声明等待预算的节点行为与从前完全一致。
    向后兼容是硬要求：这个参数此前在所有桌面命令上都是死的，没有任何既有流程在依赖「等」。

    命中判据是「匹配集非空」而非「按 `found_index` 选得出来」：索引越界时继续等是有意义的
    （元素集合可能在等待期间变多），而选序失败仍由调用方按原语义报 ELEMENT_NOT_FOUND。

    **计时用 `perf_counter` 而不是 `monotonic`**：Windows 上 `time.monotonic()` 是
    `GetTickCount64()`、分辨率 15.6ms，比这里的轮询间隔还粗，会把「等了多久」记成
    跳变的读数（实测 50ms 的 sleep 读成 47ms/63ms）。M28 S4 已经为度量立过同一条规矩。
    """
    budget_seconds = max(float(timeout_ms or 0), 0.0) / 1000.0
    started = time.perf_counter()
    deadline = started + budget_seconds
    polls = 0
    while True:
        matches = find() or []
        polls += 1
        if matches:
            return ElementWaitOutcome(matches, (time.perf_counter() - started) * 1000.0, polls)
        now = time.perf_counter()
        if now >= deadline:
            return ElementWaitOutcome([], (now - started) * 1000.0, polls)
        time.sleep(min(poll_interval_seconds, max(deadline - now, 0.0)))


def resolve_session_id(
    requested: Any,
    sessions: dict[str, Any],
    last_active: str | None = None,
) -> str:
    """解析会话 id：显式指定 > 最近激活 > 唯一会话。

    会话类命令（除 attach/close 等生命周期命令外）允许省略 sessionId，
    此时默认作用于最近激活的会话；只在存在多个候选且无激活记录时返回空串，
    由调用方报 SESSION_NOT_FOUND，避免歧义命中。
    """
    explicit = str(requested or "").strip()
    if explicit:
        return explicit
    if last_active and last_active in sessions:
        return last_active
    if len(sessions) == 1:
        return next(iter(sessions))
    return ""


class CommandExecutor(ABC):
    @abstractmethod
    async def execute(
        self, invocation: CommandInvocation, cancellation: asyncio.Event
    ) -> CommandResult:
        raise NotImplementedError

    async def close(self) -> None:
        return None
