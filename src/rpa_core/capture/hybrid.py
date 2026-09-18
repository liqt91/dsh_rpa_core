"""混合捕获会话（M16）：桌面 hover + 浏览器扩展双通道，先回传者胜。

- 桌面通道：DesktopCaptureSession --hover --hybrid（浏览器内容区让位给扩展）
- 浏览器通道：``ExtensionCaptureSession``（自己经 bridge 端点 arm/disarm 并读回结果，
  见 ADR 0015）——本会话持有它并在 pick 里与桌面腿双等
- pick 双等：扩展结果事件 或 桌面 agent stdout，先到先返回，取消另一侧
- 没装扩展时退化为纯桌面 hover（扩展腿 start 即离线，永不触发）
- **桌面腿不可用时退化为纯扩展捕获**（对称语义，2026-09-18）：桌面 agent 是
  Windows-only，非 Windows 上 ``available`` 报 False，该腿不参与竞速

**「先回传者胜」只对「有效捕获描述符」成立**（见 ``_is_capture_result``）：腿的
失败形态（``unavailable`` / ``error`` / ``timeout`` / ``cancelled`` / agent 崩溃）
都不带 ``kind``，绝不能当成「用户捕获了这条腿上的元素」。早期实现直接比对
「哪条腿先有产出」，于是在非 Windows 上桌面腿 49ms 返回 ``desktop capture
requires Windows`` 就把仍在线的扩展腿掐掉（``close()`` → disarm），用户侧表现为
「点了捕获元素，窗口闪一下就弹回，网页里 Ctrl+Click 毫无反应」。
"""

import threading
import time
from typing import Any

from .extension import ExtensionCaptureSession

# ElementDescriptor 的判别字段取值（capture/__init__ 与 GUI/devserver 同源）
_CAPTURE_KINDS = ("desktop", "browser")


def _is_capture_result(payload: Any) -> bool:
    """该腿是否产出了一个**有效的捕获描述符**（判别字段 ``kind``）。"""
    return isinstance(payload, dict) and payload.get("kind") in _CAPTURE_KINDS


class HybridCaptureSession:
    """桌面 hover + 浏览器扩展的混合捕获会话。"""

    # devserver 用鸭子类型识别扩展会话（不 import capture 包，维持隔离边界）
    is_extension_capture = True

    def __init__(
        self,
        *,
        desktop_factory,
        extension_session: ExtensionCaptureSession | None = None,
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

    def start(self) -> list[str]:
        self._extension.start()
        # 扩展腿离线（无 bridge 端点）时退化为纯桌面 hover：pick 不再等扩展腿
        self._ext_offline = bool(getattr(self._extension, "offline", False))
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

        while time.monotonic() < deadline:
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
                # 腿失败（cancelled / 连接断开 / 无 kind）：记下原因，继续等桌面腿
                extension_failure = result if isinstance(result, dict) else {}

            # ② 桌面腿
            if "desktop" in box:
                result = box.pop("desktop")
                if _is_capture_result(result):
                    self._pending = False
                    self._extension.close()  # 桌面先赢：撤防扩展
                    return result
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

    def _cancel_desktop(self, thread: threading.Thread | None) -> None:
        try:
            self._desktop.cancel()
        except Exception:
            pass
        if thread is not None:
            thread.join(timeout=3)

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
        """无可用通道时的收场结果：区分「用户主动取消」与「无腿可用」。"""
        # 用户按 Esc 取消（扩展腿回 cancelled）优先呈现为取消
        if extension_failure and extension_failure.get("cancelled"):
            return {"cancelled": True}
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

