"""桌面捕获会话（M10）：desktop_agent 子进程的父端包装。

一次性进程模型：start 启动 agent（等待热键或 --point 测试模式），
pick 阻塞读取 stdout 描述符行（带超时），cancel 直接终止子进程。
"""

import json
import queue
import subprocess
import sys
import threading
from typing import Any


class DesktopCaptureSession:
    def __init__(
        self,
        *,
        hotkey: str = "F9",
        timeout_seconds: float = 60.0,
        point: dict[str, int] | None = None,
        window_handle: int | None = None,
    ):
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
        self._proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self._queue: queue.Queue[str] = queue.Queue()
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()

    def _read_stdout(self) -> None:
        assert self._proc.stdout is not None
        for line in self._proc.stdout:
            self._queue.put(line)

    def pick(self, timeout_seconds: float = 90.0) -> dict[str, Any]:
        try:
            line = self._queue.get(timeout=timeout_seconds)
        except queue.Empty:
            return self._crash_info({"timeout": True})
        line = line.strip()
        if not line:
            return self._crash_info({"timeout": True})
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return self._crash_info({"error": "agent produced invalid output"})

    def _crash_info(self, payload: dict[str, Any]) -> dict[str, Any]:
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
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()

    def close(self) -> None:
        self.cancel()
