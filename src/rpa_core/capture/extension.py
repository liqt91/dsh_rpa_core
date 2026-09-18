"""content-script 扩展捕获会话（M14 无缝捕获路线；M20/ADR 0015 改 Native Messaging）。

与 persistent / user-browser 捕获会话的差异：没有可控浏览器进程，picker 跑在扩展的 content
script 里（用户真实浏览器的所有页面）。会话自己经**本地端点**与 bridge host 通信：

- ``start()`` 连端点并下发 ``capture_arm``（host 转发扩展 → 扩展广播到全部标签页）
- 扩展 Ctrl+Click 捕获 → 经 host 回传 ``capture_result`` → 本会话的读线程接收并唤醒 pick
- ``cancel``/``close`` 下发 ``capture_disarm`` 并关闭连接

无 HTTP 轮询、无 pending 标记、无 token（host 仅本机子进程，扩展 ID 白名单由 host manifest 强制）。
"""

from __future__ import annotations

import threading
import uuid
from typing import Any

from rpa_core import local_transport


class ExtensionCaptureSession:
    """content-script 扩展捕获会话（无子进程；arm 全部在线 bridge 端点）。"""

    # devserver 用鸭子类型识别扩展会话（不 import capture 包，维持隔离边界）
    is_extension_capture = True

    def __init__(
        self,
        *,
        transport: str = "extension",
        endpoint: str | None = None,
        **_ignored: Any,
    ):
        self.transport = transport
        self._endpoint = endpoint
        self._event = threading.Event()
        self._result: dict | None = None
        # 多浏览器并存时各有自己的 bridge 端点：全部 arm，先回传者胜
        self._channels: list[tuple[str, local_transport.Channel]] = []
        self._live = 0  # 仍在线的通道数（全部断开才结束 pick 等待）
        self._live_lock = threading.Lock()
        self._session_id = f"cap-{uuid.uuid4().hex[:8]}"
        self._offline = False

    # -- 生命周期 ------------------------------------------------------------

    def start(self) -> list[str]:
        """连接**全部**在线端点并逐一 arm；无端点（扩展未装/未连）时置离线。

        多浏览器并存（如 Edge + Chrome 各装一份扩展）时，只 arm 第一个端点会让
        另一个浏览器的网页永远无法框选——桌面腿在浏览器内容区让位给扩展，扩展
        又没收到 arm，表现为「只有一个浏览器能框选」。
        """
        from rpa_core.extension_exec import list_extension_endpoints

        candidates = [self._endpoint] if self._endpoint else list_extension_endpoints()
        for name in candidates:
            if not name:
                continue
            try:
                channel = local_transport.connect(name, timeout=2.0)
            except local_transport.LocalTransportError:
                continue
            self._channels.append((name, channel))
        if not self._channels:
            self._offline = True
            self._event.set()
            return ["*"]
        self._endpoint = self._channels[0][0]
        with self._live_lock:
            self._live = len(self._channels)
        for _name, channel in self._channels:
            channel.send({"type": "capture_arm", "sessionId": self._session_id})
            threading.Thread(
                target=self._read_loop, args=(channel,), daemon=True
            ).start()
        return ["*"]

    @property
    def offline(self) -> bool:
        """扩展腿是否离线（未连上任何 bridge 端点：扩展未装/浏览器未起）。"""
        return self._offline

    @property
    def pending(self) -> bool:
        return not self._event.is_set()

    @property
    def result_event(self) -> threading.Event:
        """结果事件（供 HybridCaptureSession 双等复用）。"""
        return self._event

    @property
    def result(self) -> dict | None:
        return self._result

    def submit(self, payload: dict) -> None:
        """外部回传结果（兼容旧调用点；常规路径由读线程自行接收）。"""
        if self._event.is_set():
            return
        self._result = payload
        self._event.set()

    def pick(self, timeout_seconds: float = 60.0, click_css: str | None = None) -> dict:
        if self._offline:
            return {
                "offline": True,
                "error": "no extension bridge endpoint: 请确认已注册 host 且扩展已加载",
            }
        if not self._event.wait(timeout=timeout_seconds):
            self.cancel()
            return {"timeout": True}
        if self._result is None:
            return {"cancelled": True}
        return self._result

    def cancel(self) -> None:
        self._disarm()
        if self._result is None:
            self._result = {"cancelled": True}
        self._event.set()

    def close(self) -> None:
        self.cancel()

    # -- 内部 ----------------------------------------------------------------

    def _read_loop(self, channel) -> None:
        """单条通道的读循环：任一通道回传 capture_result 即唤醒 pick（先回传者胜）。"""
        try:
            while True:
                try:
                    message = channel.recv()
                except local_transport.LocalTransportError:
                    break
                if message is None:
                    break
                if message.get("type") != "capture_result":
                    continue  # 忽略 focus 等广播
                session = message.get("sessionId")
                if session and session != self._session_id:
                    continue
                if message.get("cancelled"):
                    self.submit({"cancelled": True})
                else:
                    descriptor = message.get("descriptor")
                    self.submit(descriptor if isinstance(descriptor, dict) else message)
                return
        finally:
            with self._live_lock:
                self._live -= 1
                drained = self._live <= 0
            if drained:
                # 全部通道断开且无任何结果：结束等待，pick 得到 cancelled
                # （单通道语义「连接断开 = 取消」的多通道推广）
                self._event.set()

    def _disarm(self) -> None:
        channels = self._channels
        self._channels = []
        for _name, channel in channels:
            try:
                channel.send({"type": "capture_disarm", "sessionId": self._session_id})
            except Exception:  # noqa: BLE001 - 断开/已关闭时撤防失败无害
                pass
            finally:
                channel.close()
