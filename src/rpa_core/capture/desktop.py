"""桌面捕获会话（M10）：desktop_agent 子进程的父端包装。

一次性进程模型：start 启动 agent（等待热键或 --point 测试模式），
pick 阻塞读取 stdout 描述符行（带超时），cancel 直接终止子进程。

**平台能力**：agent 是 Windows-only（UIA hit-test，见 ``desktop_agent`` 的
``sys.platform != "win32"`` 守卫）。非 Windows 上本类不 spawn 子进程（省掉一次
必失败的进程调度），``available`` 报 False、``pick()`` 立即返回 ``unavailable``。
调用方（``HybridCaptureSession``）据此把这条腿排除出竞速 —— 否则「腿不可用」会被
「先回传者胜」当成「用户捕获了桌面元素」，反过来掐掉仍然可用的扩展腿。
"""

import json
import queue
import subprocess
import sys
import threading
import time
from typing import Any

# agent 的 UIA hit-test 依赖 pywinauto/win32gui，仅 Windows 可跑
_PLATFORM_SUPPORTED = sys.platform == "win32"
_UNAVAILABLE_PAYLOAD: dict[str, Any] = {
    "unavailable": True,
    "error": "desktop capture requires Windows",
}


def desktop_capture_available() -> bool:
    """本平台是否具备桌面捕获能力。

    供宿主在**不构造会话**（因而不在 Windows 上 spawn agent 子进程）的前提下
    做能力探测 —— GUI 的「捕获元素」提示文案、`env-status` 之类都该用它。
    """
    return _PLATFORM_SUPPORTED


class DesktopCaptureSession:
    def __init__(
        self,
        *,
        hotkey: str = "F9",
        timeout_seconds: float = 60.0,
        point: dict[str, int] | None = None,
        window_handle: int | None = None,
        hover: bool = False,
        hybrid: bool = False,
    ):
        self._available = _PLATFORM_SUPPORTED
        self._queue: queue.Queue[str] = queue.Queue()
        self._proc: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        if not self._available:
            return
        args = [
            sys.executable,
            "-m",
            "rpa_core.capture.desktop_agent",
            "--hotkey",
            hotkey,
            "--timeout",
            str(timeout_seconds),
        ]
        if point:
            args += ["--point", str(point.get("x", 0)), str(point.get("y", 0))]
        if window_handle:
            args += ["--window-handle", str(window_handle)]
        if hover:
            args += ["--hover"]
        if hybrid:
            args += ["--hybrid"]
        self._proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()

    @property
    def available(self) -> bool:
        """本平台是否具备桌面捕获能力（非 Windows 为 False）。"""
        return self._available

    def _read_stdout(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        for line in self._proc.stdout:
            self._queue.put(line)

    def pick(self, timeout_seconds: float = 90.0) -> dict[str, Any]:
        if not self._available:
            return dict(_UNAVAILABLE_PAYLOAD)
        assert self._proc is not None and self._proc.stdout is not None
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                # 短切片轮询：长 timeout 的 queue.get 在 Windows 上吞 Ctrl+C
                line = self._queue.get(timeout=min(0.5, deadline - time.monotonic()))
                break
            except queue.Empty:
                if self._proc.poll() is not None:
                    # agent 已退出但没读到行 → 崩溃信息
                    return self._crash_info({"timeout": True})
                continue
        else:
            return self._crash_info({"timeout": True})
        line = line.strip()
        if not line:
            return self._crash_info({"timeout": True})
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return self._crash_info({"error": "agent produced invalid output"})

    def _crash_info(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._proc is None:
            return payload
        code = self._proc.poll()
        if code is None or code == 0:
            return payload
        stderr = ""
        if self._proc.stderr is not None:
            try:
                stderr = self._proc.stderr.read()
            except Exception:
                stderr = ""
        payload = dict(payload)
        payload["error"] = f"desktop capture agent exited with code {code}"
        payload["stderrTail"] = stderr[-800:]
        return payload

    def cancel(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()

    def close(self) -> None:
        self.cancel()
