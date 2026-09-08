"""bsk 执行传输会话（M14a）：browser.* 命令映射到 bsk CLI。

与 capture/browser_bsk 的区别：那里是设计期一次性捕获会话；这里是**运行期**
执行会话，由 PlaywrightExecutor 持有，跨步骤存活直到 browser.close / executor.close。

底层子进程协议统一走 `rpa_core.bsk_client`（能力层唯一入口）。
能力差异（CSS selector only、仅主 frame）见 `commands/browser/launch.json` transport 说明。
取消语义（规则 11）：bsk 子进程调用是短命令，逐命令间隙检查取消事件；
executor close 强制 `session stop`，不悬挂 Agent Window。
"""

import time
from collections.abc import Callable
from typing import Any

from rpa_core.bsk_client import BskClient, BskError


class BskSession:
    """运行期 bsk 会话包装（同步调用，执行器经 asyncio.to_thread 调度）。"""

    def __init__(
        self,
        *,
        browser_instance_id: str | None = None,
        runner: Callable[..., dict] | None = None,
        bsk_binary: str | None = None,
    ):
        self._client = BskClient(bsk_binary=bsk_binary, runner=runner)
        self._browser_instance_id = browser_instance_id
        self.session_id: str | None = None

    def start(self) -> str:
        self.session_id = self._client.session_start(self._browser_instance_id)
        return self.session_id

    def _require(self) -> str:
        if not self.session_id:
            raise BskError("not_started", "bsk session not started")
        return self.session_id

    def evaluate(self, expression: str, *, timeout: str = "30s") -> Any:
        return self._client.evaluate(self._require(), expression, timeout=timeout)

    def navigate(self, url: str) -> str:
        return self._client.navigate(self._require(), url)

    def count(self, selector: str) -> int:
        import json

        value = self.evaluate(
            f"document.querySelectorAll({json.dumps(selector)}).length"
        )
        return int(value or 0)

    def click(self, selector: str) -> None:
        self._client.click(self._require(), selector)

    def hover(self, selector: str) -> None:
        import json
        self.evaluate(
            f"(() => {{ const el = document.querySelector({json.dumps(selector)});"
            " if (el) el.dispatchEvent(new MouseEvent('mouseover', {bubbles:true})); }})()"
        )

    def fill(self, selector: str, text: str) -> None:
        self._client.fill(self._require(), selector, text)

    def inner_text(self, selector: str) -> str:
        import json

        value = self.evaluate(
            f"(() => {{ const el = document.querySelector({json.dumps(selector)});"
            " return el ? el.innerText : null; })()"
        )
        return "" if value is None else str(value)

    def inner_texts(self, selector: str) -> list[str]:
        import json

        value = self.evaluate(
            f"Array.from(document.querySelectorAll({json.dumps(selector)}))"
            ".map((el) => el.innerText)"
        )
        return [str(item) for item in value] if isinstance(value, list) else []

    def wait_for(self, selector: str, timeout_seconds: float, cancelled) -> bool:
        """轮询命中；cancelled 是可调用对象（asyncio.Event.is_set）。命中返回 True。"""
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if cancelled():
                return False
            if self.count(selector) > 0:
                return True
            time.sleep(0.4)
        return False

    def stop(self) -> None:
        session_id = self.session_id
        self.session_id = None
        if session_id:
            try:
                self._client.session_stop(session_id)
            except Exception:
                pass
