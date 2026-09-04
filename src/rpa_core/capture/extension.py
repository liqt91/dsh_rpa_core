"""content-script 扩展捕获会话（M14 无缝捕获路线）。

与 bsk/persistent 的差异：没有可控浏览器进程，picker 跑在扩展的 content
script 里（用户真实浏览器的所有页面）。devserver 只维护 pending 标记：
- start() 标记 pending（扩展 background 轮询到后广播 arm 到全部标签页）
- 扩展 Ctrl+Click 捕获 → POST /api/capture/extension/result → submit() 唤醒 pick
- cancel/close 清除 pending（扩展下次轮询后撤防）

通信不引入 WebSocket（stdlib http.server 无 WS）：轮询 + POST 足够
（捕获是低频设计期动作）。
"""

import threading
from typing import Any


class ExtensionCaptureSession:
    """content-script 扩展捕获会话（纯内存，无子进程）。"""

    # devserver 用鸭子类型识别扩展会话（不 import capture 包，维持隔离边界）
    is_extension_capture = True

    def __init__(self, *, transport: str = "extension", **_ignored: Any):
        self.transport = transport
        self._event = threading.Event()
        self._result: dict | None = None
        self._pending = False

    def start(self) -> list[str]:
        self._pending = True
        return ["*"]  # content script 已注入全部页面，无 pages 列表

    @property
    def pending(self) -> bool:
        return self._pending and not self._event.is_set()

    def submit(self, payload: dict) -> None:
        self._result = payload
        self._event.set()

    def pick(self, timeout_seconds: float = 60.0, click_css: str | None = None) -> dict:
        if not self._event.wait(timeout=timeout_seconds):
            self._pending = False
            return {"timeout": True}
        self._pending = False
        return self._result if self._result is not None else {"cancelled": True}

    def cancel(self) -> None:
        self._pending = False
        if self._result is None:
            self._result = {"cancelled": True}
        self._event.set()

    def close(self) -> None:
        self.cancel()
