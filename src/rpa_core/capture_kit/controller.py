"""捕获编排器：把「会话 + 浮窗 + 结果」收成一个可复用的状态机。

给宿主的最小接口只有三件事：

- ``start()``：建混合会话、起双臂、显示浮窗、后台 ``pick``；返回首屏状态；
- ``poll()``：返回 ``pending / ready / cancelled / offline / failed``；
- ``cancel()``：取消本次捕获（浮窗按钮 / 宿主取消 / 二次点击共用）。

宿主差异被收在两个注入口：

- ``on_captured``：有效描述符产出后先交给宿主落盘/绑定，再把结果回显；
- 最小化/还原由**宿主自己**做（Qt 用 ``showMinimized``/``showNormal``，ppx 用
  前端 ``window.minimize``/``window.restore``）——WebView 宿主的最小化是 JS 能力，
  Python 侧拿不到窗口句柄，硬塞一个 host 回调反而多一层不可测的间接。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from .engine import (
    NEVER_TIMEOUT,
    build_hybrid_session,
    desktop_status,
    gesture_hint,
    status_snapshot,
    web_status,
)
from .float_window import CaptureFloat

_READY_KINDS = ("desktop", "browser")


class CaptureController:
    """一次捕获的生命周期（单次使用；重复 start 请新建实例）。"""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Any] | None = None,
        on_captured: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        host: Any = None,
        float_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._factory = session_factory or build_hybrid_session
        self._on_captured = on_captured
        self._host = host  # 需提供 minimize() / restore()；None 则不收主窗
        self._float_factory = float_factory or CaptureFloat
        self._session: Any = None
        self._float: CaptureFloat | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._result: dict[str, Any] | None = None
        self._cancelled = False
        self._desktop_offline = False

    # -- 生命周期 ------------------------------------------------------------
    def start(self) -> dict[str, Any]:
        """建会话并开始捕获。返回首屏状态；双通道都不可用时返回 ``ok=False``。"""
        session = self._factory()
        session.start()
        self._session = session
        self._desktop_offline = bool(getattr(session, "desktop_offline", False))
        ext_offline = bool(getattr(session, "extension_offline", False))
        snapshot = status_snapshot(session)
        if self._desktop_offline and ext_offline:
            try:
                session.close()
            except Exception:  # noqa: BLE001
                pass
            return {
                "ok": False,
                "code": "NO_CAPTURE_CHANNEL",
                "extensionOffline": True,
                "desktopOffline": True,
                **snapshot,
            }
        window = self._float_factory()
        window.show(
            snapshot["gesture"],
            snapshot["web"],
            snapshot["desktop"],
            snapshot["webOk"],
            snapshot["desktopOk"],
            on_cancel=self.cancel,
        )
        self._float = window
        self._minimize()
        try:
            self._thread = threading.Thread(
                target=self._work, name="capture-pick", daemon=True
            )
            self._thread.start()
        except Exception:
            self._restore()
            raise
        return {
            "ok": True,
            "offline": self._desktop_offline,
            "extensionOffline": ext_offline,
            **snapshot,
        }

    @property
    def instructions(self) -> str:
        """当前会话的手势提示（供宿主面板复用）。"""
        if self._session is None:
            return ""
        return gesture_hint(self._session)

    def poll(self) -> dict[str, Any]:
        """状态机：pending / ready / cancelled / offline / failed。"""
        with self._lock:
            if self._cancelled:
                return {"state": "cancelled"}
            result = self._result
        if result is None:
            self._refresh_float()
            session = self._session
            armed = bool(getattr(session, "armed", False)) if session is not None else False
            return {"state": "pending", "offline": self._desktop_offline, "armed": armed}
        if result.get("kind") in _READY_KINDS:
            return {"state": "ready", "result": result}
        if result.get("unavailable") or result.get("offline"):
            return {
                "state": "offline",
                "reason": result.get("error") or "没有可用的捕获通道",
            }
        return {
            "state": "failed",
            "reason": result.get("error") or result.get("stderrTail") or str(result),
        }

    def cancel(self) -> None:
        """取消本次捕获，并收掉浮窗（可重入）。"""
        with self._lock:
            self._cancelled = True
        session = self._session
        try:
            if session is not None:
                session.cancel()
                session.close()
        except Exception:  # noqa: BLE001 - 取消失败也要退出等待态
            pass
        self._hide_float()
        self._restore()

    # -- 内部 ----------------------------------------------------------------
    def _work(self) -> None:
        session = self._session
        try:
            try:
                result = session.pick(timeout_seconds=NEVER_TIMEOUT)
            except Exception as exc:  # noqa: BLE001 - 任何异常都落进结果，别让轮询永挂
                result = {"error": f"捕获线程异常：{exc}"}
            if isinstance(result, dict) and result.get("kind") in _READY_KINDS:
                if self._on_captured is not None:
                    try:
                        result = self._on_captured(result)
                    except Exception as exc:  # noqa: BLE001
                        result = {"error": f"元素落盘失败：{exc}"}
        finally:
            self._hide_float()
            self._restore()
        with self._lock:
            if isinstance(result, dict) and result.get("cancelled"):
                self._cancelled = True
            else:
                self._result = result

    def _refresh_float(self) -> None:
        """等待期每进一次 poll 就刷新双腿状态（扩展 arm/判死是异步的）。"""
        session = self._session
        window = self._float
        if session is None or window is None:
            return
        web, web_ok = web_status(session)
        desktop, desktop_ok = desktop_status(session)
        window.set_status(web, desktop, web_ok, desktop_ok)

    def _hide_float(self) -> None:
        window = self._float
        if window is not None:
            window.hide()

    def _minimize(self) -> None:
        host = self._host
        if host is None:
            return
        try:
            host.minimize()
        except Exception:  # noqa: BLE001 - 收主窗失败不影响捕获本身
            pass

    def _restore(self) -> None:
        host = self._host
        if host is None:
            return
        try:
            host.restore()
        except Exception:  # noqa: BLE001 - 还原失败也别把异常抛进轮询
            pass

    # 便于宿主观察（调试/状态条）
    @property
    def offline(self) -> bool:
        return self._desktop_offline

    @property
    def cancelled(self) -> bool:
        with self._lock:
            return self._cancelled
