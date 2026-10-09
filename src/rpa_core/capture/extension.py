"""content-script 扩展捕获会话（M14 无缝捕获路线；M20/ADR 0015 改 Native Messaging）。

与 persistent / user-browser 捕获会话的差异：没有可控浏览器进程，picker 跑在扩展的 content
script 里（用户真实浏览器的所有页面）。会话自己经**本地端点**与 bridge host 通信：

- ``start()`` 连端点并下发 ``capture_arm``（host 转发扩展 → 扩展广播到全部标签页）
- 扩展收到 arm 后回 ``capture_armed``（ack），本会话据此判定「扩展真的在响应」——
  端点可连接只证明 **host** 活着，不证明扩展活着（残留 host 占着端点就是这种情形）
- 扩展侧捕获手势（⌘/Ctrl+Click、右键）→ 经 host 回传 ``capture_result`` →
  本会话的读线程接收并唤醒 pick
- ``cancel``/``close`` 下发 ``capture_disarm`` 并关闭连接

无 HTTP 轮询、无 pending 标记、无 token（host 仅本机子进程，扩展 ID 白名单由 host manifest 强制）。
"""

from __future__ import annotations

import re
import sys
import threading
import uuid
from pathlib import Path
from typing import Any

from rpa_core import local_transport
from rpa_core.capture._trace import trace as _trace


def expected_extension_build() -> str | None:
    """仓库源码里扩展的当前构建标识（EXT_BUILD 常量，与 manifest version 一致）。

    Load unpacked 的扩展**不会**因源码文件更新而自动重载：浏览器里跑的是加载那一刻的
    快照。拿扩展回传的 ``extBuild`` 与这里的期望值对账即可判「扩展过期」，不用靠症状猜。
    仅源码检出形态可定位（src 布局向上三级到仓库根）；打包安装后返回 None，不判过期。
    """
    try:
        source = Path(__file__).resolve().parents[3] / "extension" / "background.js"
        match = re.search(r'const EXT_BUILD = "([^"]+)"', source.read_text(encoding="utf-8"))
    except OSError:
        return None
    return match.group(1) if match else None


def capture_click_label() -> str:
    """页内捕获手势的修饰键文案（提示用户用哪个手势）。

    macOS 在**系统层**把 Control+Click 改写成"次要点击"（secondary click），浏览器因此
    只派发 mousedown(button=2)/contextmenu，**永远不派发 ctrlKey 的 click**——所以给 Mac
    用户的提示必须写 ⌘+Click，否则他照着 Ctrl+Click 做会毫无反应（扩展侧也已同时接受
    右键，见 ``extension/content.js`` 的 isCaptureModifier/isSecondaryClick）。
    """
    return "⌘+Click" if sys.platform == "darwin" else "Ctrl+Click"


class ExtensionCaptureSession:
    """content-script 扩展捕获会话（无子进程；arm 全部在线 bridge 端点）。"""

    # devserver 用鸭子类型识别扩展会话（不 import capture 包，维持隔离边界）
    is_extension_capture = True

    def __init__(
        self,
        *,
        transport: str = "extension",
        endpoint: str | None = None,
        rearm_poll: float = 1.5,
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
        # arm ack：扩展收到 capture_arm 后回 capture_armed。端点可连接 ≠ 扩展在响应，
        # 这个事件是「扩展腿真的活着」的唯一证据（见 armed 属性）。
        self._armed = threading.Event()
        # arm ack 时扩展自报的构建标识，与仓库期望不一致 = 浏览器里跑的是旧快照
        # （Load unpacked **不自动重载**，必须手动点刷新）。记下来供GUI 直接提示，
        # 否则这类症状（「预览里没有截图」）在现场只能靠猜——M47.12 真机反馈。
        self._build: str | None = None
        self._build_expected: str | None = None
        # 离线起步的补 arm 守望（M47.6）：捕获开始时浏览器没开 → arm 广播无人接收，
        # 捕获中途打开浏览器后扩展上线也永远收不到 arm，网页里不会出现红框。
        # 守望线程轮询新端点并收编补 arm；cancel/close 置停。
        self._rearm_closed = threading.Event()
        self._rearm_poll = rearm_poll

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
            _trace("extension", "offline")
            self._start_rearm_watch()
            return ["*"]
        self._endpoint = self._channels[0][0]
        with self._live_lock:
            self._live = len(self._channels)
        for _name, channel in self._channels:
            channel.send({"type": "capture_arm", "sessionId": self._session_id})
            _trace("extension", "arm_sent", channels=len(self._channels))
            threading.Thread(
                target=self._read_loop, args=(channel,), daemon=True
            ).start()
        return ["*"]

    @property
    def offline(self) -> bool:
        """扩展腿是否离线（未连上任何 bridge 端点：扩展未装/浏览器未起）。"""
        return self._offline

    @property
    def armed(self) -> bool:
        """扩展是否已确认收到 ``capture_arm``（ack 已到）。

        **这是「扩展腿真的在响应」的唯一证据**：``offline`` 只看端点能否连上，
        而端点由 host 持有——host 还在、扩展已断（或 host 是残留进程）时端点照样
        可连接，此时 ``offline is False`` 但 ``armed`` 永远不为真。宿主据此把这类
        「假在线」的腿判死，而不是静默等到超时。
        """
        return self._armed.is_set()

    @property
    def build_mismatch(self) -> tuple[str, str] | None:
        """扩展自报构建与仓库期望不一致时返回 ``(实际, 期望)``，一致或未知时``None``。

        **Load unpacked 载入即快照**：改了 ``extension/`` 下的代码而没去
        ``chrome://extensions`` 点刷新，浏览器里跑的仍是旧版本。这类症状
        （新字段读不到、新行为不生效）在现场没有任何提示，只能靠猜——M47.12
        真机就撞上了（跑0.6.3、期望 0.8.0 ⇒ ``windowHandle`` 拿不到 ⇒ 预览空白）。
        """
        build, expected = self._build, self._build_expected
        if build and expected and build != expected:
            return (build, expected)
        return None

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

    def _attach_capture_shot(self, descriptor: dict) -> None:
        """捕获成功那一刻给descriptor 挂上窗口快照（预览页签直接展示它）。

        **为什么截在这里而不是「点预览时」**（M47.12 真机撞墙的教训）：
        「点预览时再截」依赖扩展回 ``windowHandle``，而
        ``chrome.windows.get().nativeWindowHandle`` 在真实环境里未必给得出——2026-10-09
        真机实测：扩展已是最新0.8.0，**Edge 上仍然 has_hwnd=false**，预览整页空白。
        捕获时则不同：用户刚点完元素，浏览器窗口必定在眼前，句柄由 host 用 Z 序认
        （``first_browser_window()``，Chrome/Edge 都在册），**不依赖扩展给任何东西**。

        挂不上也不影响捕获：``descriptor`` 照样提交，预览页签显示一句降级说明即可。
        """
        try:
            from rpa_core.capture.foreground_window import first_browser_window
            from rpa_core.capture.screen_shot import capture_shot_from_descriptor

            found = first_browser_window()
            if found is None:
                _trace("capture_shot", "no_browser_window")
                return
            shot = capture_shot_from_descriptor(descriptor, found.hwnd)
            if shot is None:
                return
            # 挂在 descriptor 内部而不是另开通道：预览页签读的就是它。
            descriptor["captureShot"] = shot
        except Exception:  # noqa: BLE001 - 截图绝不该让一次成功的捕获变成失败
            _trace("capture_shot", "error")

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
        self._rearm_closed.set()  # 会话收场：补 arm 守望即刻退出
        self._disarm()
        if self._result is None:
            self._result = {"cancelled": True}
        self._event.set()

    def close(self) -> None:
        self.cancel()

    # -- 内部 ----------------------------------------------------------------

    def _start_rearm_watch(self) -> None:
        """离线起步的补 arm 守望：会话存续期间新端点上线即收编补 arm。

        现场（2026-09-29 维护者反馈）：捕获开始时没开浏览器 → arm 广播无人接收；
        捕获中途打开浏览器网页 → 扩展上线但永远错过了 arm，页面不出现红框。
        守望轮询在线端点，把「起步后新出现的」连接进来补发 ``capture_arm`` 并复活
        本腿（清 offline / 清 result 事件，重新参与 pick 双等）。
        """
        from rpa_core.extension_exec import list_extension_endpoints

        def watch() -> None:
            while not self._rearm_closed.is_set():
                try:
                    # 与 start() 同款候选逻辑：注入 endpoint 时只守望它
                    # （测试/单端点场景不得收编真机上的其他在线端点）
                    candidates = (
                        [self._endpoint] if self._endpoint else list_extension_endpoints()
                    )
                except Exception:  # noqa: BLE001 - 轮询失败下轮再试
                    candidates = []
                for name in candidates:
                    if not name:
                        continue
                    if self._rearm_closed.is_set():
                        return
                    if any(n == name for n, _ in tuple(self._channels)):
                        continue
                    self._adopt_endpoint(name)
                self._rearm_closed.wait(self._rearm_poll)

        threading.Thread(target=watch, daemon=True).start()

    def _adopt_endpoint(self, name: str) -> None:
        """把起步后新上线的端点收编进本会话：连接 + 补发 arm + 复活扩展腿。

        顺序敏感：先清 result 事件、再清 offline——若反过来，hybrid 会在「offline
        已 False 但事件还 set 着（result 为 None）」的窗口里把这条腿记成失败出局。
        """
        try:
            channel = local_transport.connect(name, timeout=2.0)
        except local_transport.LocalTransportError:
            return  # 端点刚上线又掉了：下轮轮询再试
        if self._rearm_closed.is_set():
            try:
                channel.close()
            except Exception:  # noqa: BLE001
                pass
            return
        self._channels.append((name, channel))
        self._event.clear()  # 复活：腿重新参与等待（offline 起步时事件已置位）
        self._offline = False
        with self._live_lock:
            self._live += 1
        self._endpoint = self._channels[0][0]
        try:
            channel.send({"type": "capture_arm", "sessionId": self._session_id})
        except Exception:  # noqa: BLE001 - 发送失败：读循环随断开自然收场
            return
        _trace("extension", "rearm_adopted", endpoint=name)
        threading.Thread(
            target=self._read_loop, args=(channel,), daemon=True
        ).start()

    def _read_loop(self, channel) -> None:
        """单条通道的读循环：任一通道回传 capture_result 即唤醒 pick（先回传者胜）。"""
        try:
            while True:
                try:
                    message = channel.recv()
                except (local_transport.LocalTransportError, OSError):
                    # OSError 是兜底：通道在别处被 close 后读取会抛底层错误，
                    # 逃逸出去就是线程未捕获异常（本会话已 disarm，无需上报）
                    break
                if message is None:
                    break
                if not isinstance(message, dict):
                    continue
                message_type = message.get("type")
                session = message.get("sessionId")
                if session and session != self._session_id:
                    continue
                if message_type == "capture_armed":
                    # ack：扩展真的收到了本会话的 arm（见 armed 属性）。
                    # extBuild 对账：不一致 = 浏览器里跑的是旧快照（Load unpacked 不自动
                    # 重载），任何「已开网页不生效」类症状先查这里。
                    build = message.get("extBuild")
                    expected = expected_extension_build()
                    _trace(
                        "extension", "arm_acked",
                        build=build,
                        expected=expected,
                        stale=None not in (build, expected) and build != expected,
                    )
                    if build and expected and build != expected:
                        _trace("extension", "arm_stale", build=build, expected=expected)
                    self._build = build
                    self._build_expected = expected
                    self._armed.set()
                    continue
                if message_type == "capture_arm_progress":
                    # background 广播完成后的现场取证（冷启动归因 2026-09-29）：
                    # ack 只证明 background 收到 arm，不证明 arm 送达了标签页里的
                    # content script——这几个数就是「送达/补注入/失败」的分解。
                    _trace(
                        "extension", "arm_progress",
                        build=message.get("extBuild"),
                        armed=message.get("armed"),
                        tabs=message.get("tabs"),
                        armed_count=message.get("armedCount"),
                        injected=message.get("injected"),
                        failed=message.get("failed"),
                    )
                    continue
                if message_type != "capture_result":
                    continue  # 忽略 focus 等广播
                if message.get("cancelled"):
                    self.submit({"cancelled": True})
                else:
                    # contentBuild：页面里 content script 自己报的构建（background 新
                    # ≠ 页面里的脚本新，见 background.js 的 rpa-capture-result 转发）
                    _trace(
                        "extension", "result_received",
                        content_build=message.get("contentBuild"),
                    )
                    descriptor = message.get("descriptor")
                    if isinstance(descriptor, dict):
                        self._attach_capture_shot(descriptor)
                        self.submit(descriptor)
                    else:
                        self.submit(message)
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
