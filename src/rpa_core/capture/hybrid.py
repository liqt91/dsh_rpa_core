"""混合捕获会话（M16）：桌面 hover + 浏览器扩展双通道，先回传者胜。

- 桌面通道：DesktopCaptureSession --hover --hybrid（浏览器内容区让位给扩展）
- 浏览器通道：以 is_extension_capture 鸭子类型挂进 devserver 会话表，
  扩展 background 轮询 pending 时可见 → 页内 Ctrl+Click 捕获经
  /api/capture/extension/result 回传
- pick 双等：扩展事件 或 桌面 agent stdout，先到先返回，取消另一侧
- 没装扩展时退化为纯桌面 hover（桌面通道照常工作，扩展端永不触发）
"""

import threading
import time
from typing import Any


class HybridCaptureSession:
    """桌面 hover + 浏览器扩展的混合捕获会话。"""

    # devserver 用鸭子类型识别扩展会话（不 import capture 包，维持隔离边界）
    is_extension_capture = True

    def __init__(self, *, desktop_factory, **desktop_kwargs: Any):
        self._desktop = desktop_factory(**desktop_kwargs)
        self._event = threading.Event()
        self._result: dict | None = None
        self._pending = True
        self._closed = False

    def start(self) -> list[str]:
        return ["hybrid"]

    @property
    def pending(self) -> bool:
        return self._pending and not self._event.is_set()

    def submit(self, payload: dict) -> None:
        """扩展 content script 捕获结果回传。"""
        if self._event.is_set():
            return
        self._result = payload
        self._event.set()

    def pick(self, timeout_seconds: float = 90.0) -> dict:
        box: dict[str, Any] = {}

        def agent_wait() -> None:
            box["desktop"] = self._desktop.pick(timeout_seconds=timeout_seconds)

        thread = threading.Thread(target=agent_wait, daemon=True)
        thread.start()
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self._event.is_set():
                # 扩展先回传：回收桌面 agent
                self._pending = False
                try:
                    self._desktop.cancel()
                except Exception:
                    pass
                thread.join(timeout=3)
                return self._result if self._result is not None else {"cancelled": True}
            if "desktop" in box:
                self._pending = False
                return box["desktop"]
            time.sleep(0.05)
        self._pending = False
        try:
            self._desktop.cancel()
        except Exception:
            pass
        thread.join(timeout=3)
        return {"timeout": True}

    def cancel(self) -> None:
        self._pending = False
        if self._result is None:
            self._result = {"cancelled": True}
        self._event.set()
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
