"""元素活体校验通道（M47 S1：「校验元素」的地基）。

**按需短连接**，独立于捕获会话：编辑确认框里按下「校验元素」时，连 bridge 端点 →
下发 ``capture_verify`` → 等首个 ``capture_verify_result``（按 ``requestId`` 配对，
陈旧/异源的回传一律不认）→ 关连接。扩展侧（background）在**活跃标签页**上让
content script 现场执行 ``querySelectorAll`` 并黄框闪烁命中元素，回传命中数。

## 目标选择（M47.9：对齐影刀「最近激活的浏览器」）

默认**定点下发**：先按 **Windows Z 序**取「最近激活的浏览器」（非浏览器窗口——资源管理器、
本 GUI 对话框——自动跳过，见 ``capture.foreground_window``），只把指令发给该浏览器的端点，
不再广播全部端点。为什么改（M47.7/M47.8 的教训）：广播 + 「首个回传者胜」会让非前台浏览器
也回传/闪框，且回答不了「最近激活的是哪个」；点校验时前台常是资源管理器或本对话框 ⇒
全部端点都判非前台 ⇒ **都不闪**。定点下发后目标是**选出**的，无竞态、无 silent/延后猜测。
取不到目标（非 Windows / 无浏览器）时退回旧的「全部端点」广播语义。

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

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        target: str | None = None,
        timeout: float = 5.0,
        auto_target: bool = True,
        bring_front: bool = True,
    ):
        # 目标选择（M47.9，对齐影刀「最近激活的浏览器」）：
        # - ``endpoint``：直接指定端点名（测试注入，最高优先）；
        # - ``target``：浏览器名（msedge/chrome/…）或实例 token——**定点下发**，只连匹配端点；
        # - 二者皆无且 ``auto_target``：按 **Windows Z 序**取「最近激活的浏览器」作为 target
        #   （见 ``capture.foreground_window``）；取不到（非 Windows / 无浏览器）才退回
        #   **全部在线端点**的旧广播语义。
        # 为什么不再默认广播：广播 + 「首个回传者胜」会让**非前台**浏览器也回传/闪框，
        # 且回答不了「最近激活的是哪个」。定点下发后目标是**选出**的，没有竞态。
        self._endpoint = endpoint
        self._target = target
        self._timeout = timeout
        self._auto_target = auto_target
        # 影刀那步②：校验前把目标浏览器窗口**置前**（否则高亮在背后那个页面上，用户看不见）。
        # **只对 ``flash`` 生效**（``preview`` 边打字边抢焦、``clear`` 收场清场，都不该动焦点）。
        # 置前失败（Windows 前台锁定）不报错、只 trace——高亮仍会在正确的浏览器上画，
        # 只是可能被别的窗口挡住。
        self._bring_front = bring_front

    def verify(self, css: str) -> dict[str, Any]:
        """活体查找 ``css``（黄框闪烁），返回 ``{"count": N}`` 或 ``{"error": 原因}``。"""
        return self._exchange(css, "flash")

    def preview(self, css: str) -> dict[str, Any]:
        """驻留高亮 ``css`` 命中的元素（编辑中即时预览），回传形状同 :meth:`verify`。"""
        return self._exchange(css, "preview")

    def clear_preview(self) -> dict[str, Any]:
        """清掉页面上的预览黄框（fire-and-forget 语义也走同一条结构化通道）。"""
        return self._exchange("", "clear")

    def _candidates(self) -> tuple[list[str], str | None, int | None]:
        """选出本次要下发的端点、实际目标标识、目标窗口句柄（用于 trace / 置前）。

        顺序：``endpoint``（指定）→ ``target``（指定）→ **Z 序自动目标** → 全部（旧兜底）。
        只有走 Z 序自动识别时才拿得到 ``hwnd``（用于「置前」）；显式 target 无窗口信息。
        """
        from rpa_core.extension_exec import (
            _endpoint_browser,
            _target_matches_instance,
            list_extension_endpoints,
        )

        if self._endpoint:
            return [self._endpoint], self._endpoint, None

        names = list_extension_endpoints()
        target = str(self._target or "").strip().lower()
        hwnd: int | None = None
        if not target and self._auto_target:
            from rpa_core.capture.foreground_window import first_browser_window

            found = first_browser_window()
            if found is not None:
                target = found.browser
                hwnd = found.hwnd
        if target:
            matched = [
                name
                for name in names
                if _endpoint_browser(name) == target
                or _target_matches_instance(name, target)
            ]
            # 命中了就定点下发；**没命中**（如 Z 序识别出 msedge 但该浏览器没装扩展）
            # 退回全部：宁可多试一个端点，也不能让「识别到但没扩展」变成「点了没反应」。
            return (matched or names), target, hwnd
        return names, None, None

    def _exchange(self, css: str, mode: str) -> dict[str, Any]:
        """连接、下发（带 mode）、按 requestId 配对、断开。

        任何失败都是**结构化报错**而不是异常：GUI 侧把 ``error`` 直接展示给用户，
        不让通道故障表现为「按钮点了没反应」。
        """
        candidates, target, hwnd = self._candidates()
        # 影刀那步②：把目标浏览器置前，否则高亮画在背后那页、用户看不见。
        # **只对 ``flash``**（用户明确点了「校验元素」）：``preview`` 是编辑器里边打字边预览，
        # 用户正在跟对话框交互，这时抢焦点会把对话框顶下去、每敲一字闪一次，体验很差；
        # ``clear`` 是收场清场，更不该抢焦点。**失败无害**：置前是观感优化，绝不能因为它
        # 出错就让整个校验失败（bring_to_foreground 自身已吞异常，这里再兜一层防替身走偏）。
        if self._bring_front and mode == "flash" and hwnd:
            try:
                from rpa_core.capture.foreground_window import bring_to_foreground

                bring_to_foreground(hwnd)
            except Exception:  # noqa: BLE001 - 置前失败不该影响校验
                pass
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
                target=target,
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
        _trace("verify", "done", mode=mode, count=count, target=target, url=reply.get("url"))
        return {"count": count}
