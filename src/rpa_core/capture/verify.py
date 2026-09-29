"""元素活体校验通道（M47 S1：「校验元素」的地基）。

**按需短连接**，独立于捕获会话：编辑确认框里按下「校验元素」时，连 bridge 端点 →
下发 ``capture_verify`` → 等首个 ``capture_verify_result``（按 ``requestId`` 配对，
陈旧/异源的回传一律不认）→ 关连接。扩展侧（background）在**活跃标签页**上让
content script 现场执行 ``querySelectorAll`` 并黄框闪烁命中元素，回传命中数。

为什么**不让捕获会话保活**（差距方案 C1 的原设想）：捕获完成时页面本就已撤防
（``background.sendCapture → disarmCapture``），校验需要的不是 armed 态，而是
「content script 可达 + scripting API」——按需通道完全满足，且免去会话所有权、
迟到结果、超时策略三组生命周期改造（M40 的挂住教训：任何真等待都要防挂死；
短连接 + 硬超时的失败模式是「报错」，不是「挂住」）。
"""

from __future__ import annotations

import threading
import uuid
from typing import Any

from rpa_core import local_transport
from rpa_core.capture._trace import trace as _trace


class ElementVerifier:
    """按需校验通道：一次 ``verify(css)`` = 连接、请求、配对、断开。

    同一条通道三个 mode（M48）：

    - ``verify``（``mode="flash"``）：黄框闪 1.6s——「点一下看看现在命中几个」；
    - ``preview``（``mode="preview"``）：黄框**驻留**——编辑器里改 css 时即时高亮，
      每次预览先清上一轮；
    - ``clear_preview``（``mode="clear"``）：只清场——编辑器关掉时收走黄框。
    """

    def __init__(self, *, endpoint: str | None = None, timeout: float = 5.0):
        # 指定 endpoint 时只连它（测试注入）；默认连**全部**在线端点（多浏览器并存时
        # 任一浏览器响应即可——与捕获会话的「先回传者胜」同语义）
        self._endpoint = endpoint
        self._timeout = timeout

    def verify(self, css: str) -> dict[str, Any]:
        """活体查找 ``css``（黄框闪烁），返回 ``{"count": N}`` 或 ``{"error": 原因}``。"""
        return self._exchange(css, "flash")

    def preview(self, css: str) -> dict[str, Any]:
        """驻留高亮 ``css`` 命中的元素（编辑中即时预览），回传形状同 :meth:`verify`。"""
        return self._exchange(css, "preview")

    def clear_preview(self) -> dict[str, Any]:
        """清掉页面上的预览黄框（fire-and-forget 语义也走同一条结构化通道）。"""
        return self._exchange("", "clear")

    def _exchange(self, css: str, mode: str) -> dict[str, Any]:
        """连接、下发（带 mode）、按 requestId 配对、断开。

        任何失败都是**结构化报错**而不是异常：GUI 侧把 ``error`` 直接展示给用户，
        不让通道故障表现为「按钮点了没反应」。
        """
        from rpa_core.extension_exec import list_extension_endpoints

        candidates = [self._endpoint] if self._endpoint else list_extension_endpoints()
        channels = []
        for name in candidates:
            if not name:
                continue
            try:
                channels.append(local_transport.connect(name, timeout=2.0))
            except local_transport.LocalTransportError:
                continue
        if not channels:
            return {"error": "extension-offline"}

        session_id = f"ver-{uuid.uuid4().hex[:8]}"
        request_id = uuid.uuid4().hex
        done = threading.Event()
        box: dict[str, Any] = {}
        live = len(channels)

        def read_loop(channel) -> None:
            nonlocal live
            try:
                while True:
                    try:
                        message = channel.recv()
                    except (local_transport.LocalTransportError, OSError):
                        # 通道在别处被关：与 extension.py 的读循环同款兜底
                        break
                    if message is None:
                        break
                    if not isinstance(message, dict) or "reply" in box:
                        continue
                    if (
                        message.get("type") == "capture_verify_result"
                        and message.get("requestId") == request_id
                    ):
                        box["reply"] = message
                        done.set()
                        return
                    if message.get("type") == "error":
                        # bridge 对不认识的消息类型的拒绝（白名单漏项等）：它是**对本
                        # 客户端本次请求**的应答，立即透出，不能干等 5s 超时——那会把
                        # 「通道配置错了」伪装成「扩展没响应」（M47.2 真机踩过）。
                        box["reply"] = message
                        done.set()
                        return
            finally:
                live -= 1
                if live <= 0:
                    # 全部通道断开仍无配对结果：放行等待（最终走 verify-timeout）
                    done.set()

        for channel in channels:
            channel.send(
                {
                    "type": "capture_verify",
                    "sessionId": session_id,
                    "requestId": request_id,
                    "css": css,
                    "mode": mode,
                }
            )
            threading.Thread(target=read_loop, args=(channel,), daemon=True).start()

        done.wait(timeout=self._timeout)
        reply = box.get("reply")
        for channel in channels:
            try:
                channel.close()
            except Exception:  # noqa: BLE001 - 收尾尽力而为
                pass
        if reply is None:
            # 超时必须留痕：这条路径在真机上排查过一轮（M47.2 bridge 白名单漏
            # capture_verify，trace 里却无痕，只能翻 ext-host.log 才实锤）
            _trace(
                "verify",
                "timeout",
                mode=mode,
                endpoints=[c for c in candidates if c],
                timeout=self._timeout,
            )
            return {"error": "verify-timeout"}
        if reply.get("type") == "error":
            detail = str((reply.get("error") or {}).get("message") or reply.get("error"))
            return {"error": f"bridge-error: {detail}"}
        if reply.get("error"):
            return {"error": str(reply["error"])}
        count = reply.get("count")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            return {"error": "bad-reply"}
        _trace("verify", "done", mode=mode, count=count, url=reply.get("url"))
        return {"count": count}
