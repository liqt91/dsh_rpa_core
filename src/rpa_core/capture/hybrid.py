"""混合捕获会话（M16）：桌面 hover + 浏览器扩展双通道，先回传者胜。

- 桌面通道：DesktopCaptureSession --hover --hybrid（浏览器内容区让位给扩展）
- 浏览器通道：``ExtensionCaptureSession``（自己经 bridge 端点 arm/disarm 并读回结果，
  见 ADR 0015）——本会话持有它并在 pick 里与桌面腿双等
- pick 双等：扩展结果事件 或 桌面 agent stdout，先到先返回，取消另一侧
- 没装扩展时退化为纯桌面 hover（扩展腿 start 即离线，永不触发）
- **桌面腿不可用时退化为纯扩展捕获**（对称语义，2026-09-18）：桌面 agent 是
  Windows-only，非 Windows 上 ``available`` 报 False，该腿不参与竞速

**「先回传者胜」只对「有效捕获描述符」成立**（见 ``_is_capture_result``）：腿的
失败形态（``unavailable`` / ``error`` / ``timeout`` / agent 崩溃）
都不带 ``kind``，绝不能当成「用户捕获了这条腿上的元素」。早期实现直接比对
「哪条腿先有产出」，于是在非 Windows 上桌面腿 49ms 返回 ``desktop capture
requires Windows`` 就把仍在线的扩展腿掐掉（``close()`` → disarm），用户侧表现为
「点了捕获元素，窗口闪一下就弹回，网页里 Ctrl+Click 毫无反应」。

**「扩展在线」要 ack 才成立**（2026-09-28 修）：``offline`` 只看端点能否连上，
而端点由 host 持有——host 还在、扩展已断（或 host 是残留进程）时它照样可连接，
此时扩展收不到 ``capture_arm``、页面里永远不会出现高亮框，而桌面腿又在浏览器
内容区让位（hybrid）→ **两条腿都产不出东西，用户静默等满 90 秒**（维护者报障
「第一次点击没有红框，再次点击提示已有捕获任务进行中」的现场就是这种形状：
crash log 里扩展腿读线程活着 ⇒ ``offline is False`` ⇒ 不弹离线确认、不降级）。
现在扩展腿在 ``ARM_ACK_TIMEOUT_SECONDS`` 内未回 ack 即判死（见 ``_arm_deadline``），
并把等待预算收窄到 ``DEGRADED_TIMEOUT_SECONDS``。

**「用户取消」是会话级信号，不是单腿失败**（2026-09-28 修）：``cancelled`` 只可能由
用户的显式手势产生（桌面 hover 里按 Esc → ``desktop_agent._hover_capture``；
网页里按 Esc → ``content.js`` 的 ``rpa-capture-cancelled``），意图是「我不捕获了」，
不是「这条通道坏了」。旧实现把它记成腿失败、继续等另一条腿——hybrid 下另一条腿
（桌面 agent）本来一直在跑，要等用户再操作或等满超时；而 M41 S5 之后宿主传的
``timeout_seconds`` 是 ``inf``（``CAPTURE_TIMEOUT_SECONDS``）⇒ **永久**停在捕获态：
桌面红框随 agent 的 ``overlay.destroy()`` 消失，但主窗仍最小化、扩展仍 arm、
``_capture_session`` 仍非 None（维护者 2026-09-28 报障「捕获元素时，在桌面按 esc，
只不显示红框，但还是在捕获模式中」）。现在任一腿报 ``cancelled`` 即调
``_finish_cancelled`` 收场：disarm 另一条腿并返回 ``{"cancelled": True}``。
"""

import threading
import time
from typing import Any

from .extension import ExtensionCaptureSession

# ElementDescriptor 的判别字段取值（capture/__init__ 与 GUI/devserver 同源）
_CAPTURE_KINDS = ("desktop", "browser")

# arm 宽限：host 冷启动 + 扩展广播的往返余量。扩展正常时 ack 在毫秒级到达，
# 只有「端点在线但扩展没在听」才会耗满它。
ARM_ACK_TIMEOUT_SECONDS = 3.0
# 扩展腿判死后的剩余等待预算：主要通道（网页捕获）已不可用，不必让用户干等满超时。
DEGRADED_TIMEOUT_SECONDS = 30.0
_ARM_ACK_ERROR = (
    "浏览器插件未响应：bridge 端点已连接，但扩展未确认 capture_arm"
    "（端点可能来自残留 host 进程；重开浏览器或重装插件后重试）"
)


def _is_capture_result(payload: Any) -> bool:
    """该腿是否产出了一个**有效的捕获描述符**（判别字段 ``kind``）。"""
    return isinstance(payload, dict) and payload.get("kind") in _CAPTURE_KINDS


def _is_cancelled(payload: Any) -> bool:
    """该腿的结果是否表示**用户主动取消**（而非通道失败）。

    与「失败」必须分开：取消是**会话级**意图（Esc 只有一次），失败只是这条腿出局。
    两者都不带 ``kind``，直接按 ``_is_capture_result`` 的否定分支处理会把取消降格成
    「腿失败，继续等另一条腿」，于是会话永远不结束（见模块 docstring）。
    """
    return isinstance(payload, dict) and payload.get("cancelled") is True


class HybridCaptureSession:
    """桌面 hover + 浏览器扩展的混合捕获会话。"""

    # devserver 用鸭子类型识别扩展会话（不 import capture 包，维持隔离边界）
    is_extension_capture = True

    def __init__(
        self,
        *,
        desktop_factory,
        extension_session: ExtensionCaptureSession | None = None,
        arm_ack_timeout: float = ARM_ACK_TIMEOUT_SECONDS,
        degraded_timeout: float = DEGRADED_TIMEOUT_SECONDS,
        **desktop_kwargs: Any,
    ):
        # 让位标志必须随桌面腿下发：agent 在浏览器内容区抑制高亮/忽略手势，
        # 把网页正文让给扩展的页内捕获（M14 实机验收的让位语义）
        desktop_kwargs.setdefault("hybrid", True)
        self._desktop = desktop_factory(**desktop_kwargs)
        self._extension = extension_session or ExtensionCaptureSession()
        self._ext_offline = False
        self._desktop_offline = False
        self._pending = True
        self._closed = False
        # arm ack 宽限与降级预算（可注入以便测试；见模块 docstring）
        self._arm_ack_timeout = arm_ack_timeout
        self._degraded_timeout = degraded_timeout
        self._arm_deadline: float | None = None
        # 扩展腿是否因「未 ack」被判死（宿主据此在浮窗上显示真实通道状态）
        self._extension_unresponsive = False

    def start(self) -> list[str]:
        self._extension.start()
        # 扩展腿离线（无 bridge 端点）时退化为纯桌面 hover：pick 不再等扩展腿
        self._ext_offline = bool(getattr(self._extension, "offline", False))
        if not self._ext_offline:
            # 端点连上了才开始计时 ack 宽限——没连上（offline）就没有「等 ack」可言
            self._arm_deadline = time.monotonic() + self._arm_ack_timeout
        # 桌面腿不可用（非 Windows：agent 是 UIA 实现）时退化为纯扩展捕获。
        # 鸭子类型：假实现没有 available 属性，默认按可用处理。
        self._desktop_offline = not bool(getattr(self._desktop, "available", True))
        if self._desktop_offline:
            # 不再参与竞速，顺手回收（未 spawn 子进程时是无害的空操作）
            try:
                self._desktop.close()
            except Exception:
                pass
        return ["hybrid"]

    @property
    def extension_offline(self) -> bool:
        """扩展腿是否离线（start 后有效；宿主据此提示网页区域不可捕获）。"""
        return self._ext_offline

    @property
    def desktop_offline(self) -> bool:
        """桌面腿是否不可用（start 后有效；宿主据此提示桌面区域不可捕获）。"""
        return self._desktop_offline

    @property
    def extension_unresponsive(self) -> bool:
        """扩展腿是否因「端点在线但未确认 arm」被判死（pick 期间有效）。

        与 ``extension_offline`` 的区别：那个是「根本没端点」，这个是「端点在、
        扩展没在听」——两者的用户处置完全不同（前者去装插件，后者重开浏览器）。
        """
        return self._extension_unresponsive

    @property
    def pending(self) -> bool:
        return self._pending and not self._extension.result_event.is_set()

    def submit(self, payload: dict) -> None:
        """扩展 content script 捕获结果回传（兼容旧调用点）。"""
        self._extension.submit(payload)

    def pick(self, timeout_seconds: float = 90.0) -> dict:
        box: dict[str, Any] = {}
        thread: threading.Thread | None = None
        if not self._desktop_offline:
            def agent_wait() -> None:
                box["desktop"] = self._desktop.pick(timeout_seconds=timeout_seconds)

            thread = threading.Thread(target=agent_wait, daemon=True)
            thread.start()

        extension_failure: dict[str, Any] | None = None
        desktop_failure: dict[str, Any] | None = None
        deadline = time.monotonic() + timeout_seconds
        degraded = False

        while time.monotonic() < deadline:
            # ⓪ 扩展腿「假在线」判死：端点连上了，但扩展超期没确认 arm。
            #    这一步是「静默等满 90 秒」的解药——端点可连接只证明 host 活着。
            if (
                not self._ext_offline
                and extension_failure is None
                and self._arm_deadline is not None
                and time.monotonic() > self._arm_deadline
                and not self._extension_armed()
            ):
                extension_failure = {"error": _ARM_ACK_ERROR}
                self._extension_unresponsive = True
                if not degraded:
                    # 主通道（网页捕获）已不可用：不让用户干等满超时，收窄剩余预算。
                    # 只收窄一次——桌面腿还在跑，用户可能正在桌面应用上找元素。
                    degraded = True
                    deadline = min(deadline, time.monotonic() + self._degraded_timeout)

            # ① 扩展腿：仅在尚未出局时参选
            if (
                not self._ext_offline
                and extension_failure is None
                and self._extension.result_event.is_set()
            ):
                result = self._extension.result
                if _is_capture_result(result):
                    self._pending = False
                    self._extension.close()
                    self._cancel_desktop(thread)
                    return result
                if _is_cancelled(result):
                    # 用户按 Esc（网页侧）收掉整个会话——桌面 agent 也一并回收
                    return self._finish_cancelled(thread)
                # 腿失败（连接断开 / 无 kind）：记下原因，继续等桌面腿
                extension_failure = result if isinstance(result, dict) else {}

            # ② 桌面腿
            if "desktop" in box:
                result = box.pop("desktop")
                if _is_capture_result(result):
                    self._pending = False
                    self._extension.close()  # 桌面先赢：撤防扩展
                    return result
                if _is_cancelled(result):
                    # 用户按 Esc（桌面 hover）——这正是「只不显示红框」那一刻：
                    # agent 的 finally 已经 destroy 掉 overlay，此处必须收掉会话本身
                    return self._finish_cancelled(thread)
                desktop_failure = result if isinstance(result, dict) else {}

            # ③ 两条腿都已出局 → 立即收场，不干等满超时
            if self._exhausted(extension_failure, desktop_failure):
                self._pending = False
                self._extension.close()
                self._cancel_desktop(thread)
                return self._exhausted_result(extension_failure, desktop_failure)

            time.sleep(0.05)

        self._pending = False
        self._extension.close()
        self._cancel_desktop(thread)
        return {"timeout": True}

    def cancel(self) -> None:
        self._pending = False
        self._extension.cancel()
        try:
            self._desktop.cancel()
        except Exception:
            pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.cancel()
        try:
            self._desktop.close()
        except Exception:
            pass

    # -- 内部 ----------------------------------------------------------------

    def _extension_armed(self) -> bool:
        """扩展是否已确认 arm（鸭子类型：假实现没有 ``armed`` 时按「已确认」处理）。

        默认 True 是刻意的：本判定只用于抓「真实的假在线」，不该让没有 ack 概念的
        替身会话被判死（既有契约测试的假扩展就是这种形状）。
        """
        return bool(getattr(self._extension, "armed", True))

    def _cancel_desktop(self, thread: threading.Thread | None) -> None:
        try:
            self._desktop.cancel()
        except Exception:
            pass
        if thread is not None:
            thread.join(timeout=3)

    def _finish_cancelled(self, thread: threading.Thread | None) -> dict[str, Any]:
        """用户取消的统一收场：disarm 另一条腿 + 返回 ``cancelled``。

        ``cancelled`` 是**会话级**信号（见模块 docstring），所以这里必须把另一条腿也
        收掉——否则它的红框/子进程会留在场上（hybrid 下桌面 agent 一直在轮询全局按键，
        不收它就永远活着；扩展不收它就永远停在 arm 态）。
        """
        self._pending = False
        self._extension.close()  # 撤防扩展：网页里的红框随 capture_disarm 下线
        self._cancel_desktop(thread)
        return {"cancelled": True}

    def _exhausted(
        self,
        extension_failure: dict[str, Any] | None,
        desktop_failure: dict[str, Any] | None,
    ) -> bool:
        """两条腿是否都已不可能再产出结果。"""
        extension_live = not self._ext_offline and extension_failure is None
        desktop_live = not self._desktop_offline and desktop_failure is None
        return not extension_live and not desktop_live

    def _exhausted_result(
        self,
        extension_failure: dict[str, Any] | None,
        desktop_failure: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """无可用通道时的收场结果：给出「为什么两条腿都产不出东西」。

        这里**不再**判 ``cancelled``：用户取消已在 ``pick`` 的两条腿分支里被
        ``_is_cancelled`` 拦下并走 ``_finish_cancelled``（会话级收场），走不到这条
        路径——留着那个分支就是一段永远不执行的代码，后来人会为它写一条永远打不中
        的用例。回归由 ``test_hybrid_esc_*`` 钉住（它们要求取消**立即**收场，而不是
        等另一条腿也出局）。
        """
        if desktop_failure is not None and desktop_failure.get("timeout") \
                and extension_failure is not None and extension_failure.get("timeout"):
            return {"timeout": True}
        reasons: list[str] = []
        if self._desktop_offline:
            reasons.append("桌面捕获仅支持 Windows（本平台不可用）")
        elif desktop_failure is not None:
            reasons.append(
                str(desktop_failure.get("error") or "desktop capture agent failed")
            )
        if self._ext_offline:
            reasons.append("浏览器插件离线（无 bridge 端点）")
        elif extension_failure is not None:
            reasons.append(str(extension_failure.get("error") or "扩展捕获通道已结束"))
        return {
            "unavailable": True,
            "error": "; ".join(reasons) or "no capture channel available",
            "desktop": desktop_failure,
            "extension": extension_failure,
        }

