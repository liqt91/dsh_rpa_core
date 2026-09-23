"""S4.4 深挖探针：tabs.navigate 之后 tab 的历史栈到底长什么样。

nav/back 真机报 "Cannot find a next page in history"——要么 tabs.navigate 没把
历史推进去，要么 goBack 的语义理解有误。用 page.eval 读 history.length 定案。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from rpa_core.executors.browser import PlaywrightExecutor  # noqa: E402
from rpa_core.executors.browser_ext import ExtensionExecSession  # noqa: E402
from rpa_core.model.command import CommandInvocation  # noqa: E402
from tests.commands.l2_harness import l2_browser_session  # noqa: E402


def run(executor, command, inputs):
    invocation = CommandInvocation(
        command_id=command,
        command_version="1.0.0",
        run_id="probe",
        step_id=command,
        inputs=dict(inputs),
    )
    return asyncio.run(executor.execute(invocation, asyncio.Event()))


def brief(result):
    return {
        "status": result.status,
        "outputs": result.outputs,
        "error": (
            {"code": result.error.code, "message": result.error.message[:120]}
            if result.error
            else None
        ),
    }


def main() -> None:
    with l2_browser_session() as l2:
        ex = PlaywrightExecutor(ext_session=ExtensionExecSession(client=l2.client))
        basic = l2.page_url("basic")
        other = l2.page_url("other")

        nav = run(ex, "browser.navigate",
                  {"browserType": l2.browser, "action": "goto", "url": basic})
        sid = nav.outputs["sessionId"]
        print("# create:", json.dumps(brief(nav), ensure_ascii=False))

        h1 = run(ex, "browser.executeScript",
                 {"sessionId": sid, "script": "return history.length"})
        print("# history.length after create:", json.dumps(brief(h1)))

        nav2 = run(ex, "browser.navigate",
                   {"sessionId": sid, "browserType": l2.browser, "action": "goto",
                    "url": other})
        print("# navigate existing:", json.dumps(brief(nav2), ensure_ascii=False))

        h2 = run(ex, "browser.executeScript",
                 {"sessionId": sid, "script": "return history.length"})
        print("# history.length after navigate:", json.dumps(brief(h2)))

        back = run(ex, "browser.navigate", {"sessionId": sid, "action": "back"})
        print("# back:", json.dumps(brief(back), ensure_ascii=False))

        h3 = run(ex, "browser.executeScript",
                 {"sessionId": sid, "script": "return location.href"})
        print("# location after back:", json.dumps(brief(h3), ensure_ascii=False))


if __name__ == "__main__":
    main()
