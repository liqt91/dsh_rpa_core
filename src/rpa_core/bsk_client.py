"""bsk CLI 子进程客户端（能力层，M14）。

`bsk` = BrowserSkill（Tencent/BrowserSkill）Rust CLI/daemon。本模块是 rpa_core
对 bsk 的唯一子进程入口：devserver 捕获会话与运行期执行器共用，避免协议分叉。

协议：`bsk --json <cmd> ...` → stdout JSON；失败形态 `{"code":..., "message":...}`
或非零退出码 + stderr。`runner` 可注入（测试无真实浏览器）。
"""

import json
import subprocess
from collections.abc import Callable
from pathlib import Path

DEFAULT_BSK_BINARY = str(Path.home() / ".local" / "bin" / "bsk.exe")

Runner = Callable[..., dict]


class BskError(RuntimeError):
    """bsk 子协议失败（携带 daemon 返回的 code/message）。"""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


class BskSessionGoneError(BskError):
    """bsk session 已失效（浏览器被关 / session 被 stop / 浏览器掉线）。"""


_GONE_CODES = {"not_found", "session_not_found"}


class BskClient:
    def __init__(self, *, bsk_binary: str | None = None, runner: Runner | None = None):
        self._bsk_binary = bsk_binary or DEFAULT_BSK_BINARY
        self._runner = runner

    def run(self, *args: str) -> dict:
        if self._runner is not None:
            payload = self._runner(*args)
            self._raise_for_error(payload, exit_code=0)
            return payload
        proc = subprocess.run(
            [self._bsk_binary, "--json", *args],
            capture_output=True, text=True, timeout=60, encoding="utf-8",
        )
        out = proc.stdout.strip()
        try:
            payload = json.loads(out) if out else {}
        except json.JSONDecodeError:
            raise BskError("protocol_error", proc.stderr.strip() or out) from None
        self._raise_for_error(
            payload, exit_code=proc.returncode, stderr=proc.stderr.strip(), raw=out
        )
        return payload

    def _raise_for_error(
        self, payload: dict, *, exit_code: int, stderr: str = "", raw: str = ""
    ) -> None:
        if not isinstance(payload, dict):
            return  # 数组载荷（如 tab list）无错误通道
        if exit_code == 0 and not payload.get("code"):
            return
        code = str(payload.get("code") or f"exit_{exit_code}")
        message = str(payload.get("message") or stderr or raw)
        if code in _GONE_CODES:
            raise BskSessionGoneError(code, message)
        raise BskError(code, message)

    # -- 常用命令封装 --------------------------------------------------------

    def session_start(self, browser_instance_id: str | None = None) -> str:
        args = ["session", "start"]
        if browser_instance_id:
            args += ["--browser", browser_instance_id]
        payload = self.run(*args)
        session_id = payload.get("session_id")
        if not session_id:
            raise BskError("protocol_error", f"no session_id in {payload}")
        return str(session_id)

    def session_stop(self, session_id: str) -> None:
        self.run("session", "stop", session_id)

    def evaluate(self, session_id: str, expression: str, *, timeout: str = "30s",
                 tab_id: str | None = None):
        args = ["evaluate", "--session", session_id, "--timeout", timeout]
        if tab_id:
            args += ["--tab-id", str(tab_id)]
        payload = self.run(*args, expression)
        return payload.get("value")

    def navigate(self, session_id: str, url: str) -> str:
        payload = self.run("navigate", "--session", session_id, url)
        return str(payload.get("final_url") or payload.get("url") or url)

    def click(self, session_id: str, selector: str, *, modifiers: str = "") -> None:
        args = ["click", "--session", session_id, "--selector", selector]
        if modifiers:
            args += ["--modifiers", modifiers]
        self.run(*args)

    def fill(self, session_id: str, selector: str, text: str) -> None:
        self.run(
            "fill", "--session", session_id, "--selector", selector, "--text", text
        )

    def browsers(self) -> list[dict]:
        payload = self.run("browsers")
        return payload if isinstance(payload, list) else []

    def tab_list(self, session_id: str, *, scope: str = "user") -> list[dict]:
        payload = self.run("tab", "list", "--session", session_id, "--scope", scope)
        if isinstance(payload, list):
            return payload
        return payload.get("tabs", []) if isinstance(payload, dict) else []

    def tab_borrow(self, session_id: str, tab_id: str) -> dict:
        """借用用户标签页进 Agent Window（返回含 tab_id/original_window_id）。"""
        return self.run("tab", "borrow", "--session", session_id, str(tab_id))

    def tab_return(self, session_id: str, tab_id: str) -> None:
        """归还借用标签页到原窗口原位置。"""
        self.run("tab", "return", "--session", session_id, str(tab_id))
