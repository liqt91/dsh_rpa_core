"""混合捕获会话（M16）：桌面 hover + 浏览器扩展双通道，先回传者胜。

- 桌面通道：DesktopCaptureSession --hover --hybrid（浏览器内容区让位给扩展）
- 浏览器通道：``ExtensionCaptureSession``（自己经 bridge 端点 arm/disarm 并读回结果，
  见 ADR 0015）——本会话持有它并在 pick 里与桌面腿双等
- pick 双等：扩展结果事件 或 桌面 agent stdout，先到先返回，取消另一侧
- 没装扩展时退化为纯桌面 hover（扩展腿 start 即离线，永不触发）
"""

import threading
import time
from typing import Any

from .extension import ExtensionCaptureSession


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
        self._pending = True
        self._closed = False

    def start(self) -> list[str]:
        self._extension.start()
        # 扩展腿离线（无 bridge 端点）时退化为纯桌面 hover：pick 不再等扩展腿
        self._ext_offline = bool(getattr(self._extension, "offline", False))
        return ["hybrid"]

    @property
    def extension_offline(self) -> bool:
        """扩展腿是否离线（start 后有效；宿主据此提示网页区域不可捕获）。"""
        return self._ext_offline

    @property
    def pending(self) -> bool:
        return self._pending and not self._extension.result_event.is_set()

    def submit(self, payload: dict) -> None:
        """扩展 content script 捕获结果回传（兼容旧调用点）。"""
        self._extension.submit(payload)

    def pick(self, timeout_seconds: float = 90.0) -> dict:
        box: dict[str, Any] = {}

        def agent_wait() -> None:
            box["desktop"] = self._desktop.pick(timeout_seconds=timeout_seconds)

        thread = threading.Thread(target=agent_wait, daemon=True)
        thread.start()
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            # 离线扩展腿不参选（其 result_event 在 start 时已置位，不判会秒回 cancelled）
            if not self._ext_offline and self._extension.result_event.is_set():
                # 扩展先回传：撤防扩展 + 回收桌面 agent
                self._pending = False
                self._extension.close()
                try:
                    self._desktop.cancel()
                except Exception:
                    pass
                thread.join(timeout=3)
                result = self._extension.result
                return result if result is not None else {"cancelled": True}
            if "desktop" in box:
                self._pending = False
                self._extension.close()  # 桌面先赢：撤防扩展
                return box["desktop"]
            time.sleep(0.05)
        self._pending = False
        self._extension.close()
        try:
            self._desktop.cancel()
        except Exception:
            pass
        thread.join(timeout=3)
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

