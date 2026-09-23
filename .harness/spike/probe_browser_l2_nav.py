"""S4.4 探针：导航/attach/标签页族在真机上的实际产出（先实测、再写 l2 期望）。

跑法（会拉起真实浏览器窗口）：

    .venv/Scripts/python.exe .harness/spike/probe_browser_l2_nav.py

每条 = 可选 pre 步 + 主命令 + 可选 verify 读侧。纪律同前两个探针。
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
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
            {"code": result.error.code, "message": result.error.message[:100]}
            if result.error
            else None
        ),
    }


def main() -> None:
    with l2_browser_session() as l2:
        browser = l2.browser
        basic = l2.page_url("basic")
        other = l2.page_url("other")
        slow = l2.page_url("slow")

        def new_executor():
            return PlaywrightExecutor(
                ext_session=ExtensionExecSession(client=l2.client)
            )

        def new_session(ex, url=basic):
            nav = run(ex, "browser.navigate",
                      {"browserType": browser, "action": "goto", "url": url})
            assert nav.status == "success", nav.model_dump_json()
            return nav.outputs["sessionId"], nav.outputs.get("tabId")

        def emit(name, main, verify=None):
            record = {"case": name, "main": brief(main)}
            if verify is not None:
                record["verify"] = brief(verify)
            print(json.dumps(record, ensure_ascii=False, default=str))

        # -- navigate -----------------------------------------------------------
        ex = new_executor()
        emit("nav/goto-new", run(ex, "browser.navigate",
             {"browserType": browser, "action": "goto", "url": basic}))

        ex = new_executor()
        sid, _ = new_session(ex)
        emit("nav/goto-existing", run(ex, "browser.navigate",
             {"sessionId": sid, "browserType": browser, "action": "goto", "url": other}))

        ex = new_executor()
        sid, _ = new_session(ex)
        run(ex, "browser.navigate",
            {"sessionId": sid, "browserType": browser, "action": "goto", "url": other})
        emit("nav/back", run(ex, "browser.navigate", {"sessionId": sid, "action": "back"}))

        ex = new_executor()
        sid, _ = new_session(ex)
        run(ex, "browser.navigate",
            {"sessionId": sid, "browserType": browser, "action": "goto", "url": other})
        run(ex, "browser.navigate", {"sessionId": sid, "action": "back"})
        emit("nav/forward", run(ex, "browser.navigate", {"sessionId": sid, "action": "forward"}))

        ex = new_executor()
        sid, _ = new_session(ex)
        emit("nav/reload", run(ex, "browser.navigate", {"sessionId": sid, "action": "reload"}))

        ex = new_executor()
        started = time.perf_counter()
        emit("nav/onTimeout-stop", run(ex, "browser.navigate",
             {"browserType": browser, "action": "goto", "url": slow,
              "timeoutMs": 1000, "onTimeout": "stop"}))
        print(f"# onTimeout-stop elapsed: {time.perf_counter() - started:.2f}s")

        ex = new_executor()
        emit("nav/onTimeout-error", run(ex, "browser.navigate",
             {"browserType": browser, "action": "goto", "url": slow,
              "timeoutMs": 1000, "onTimeout": "error"}))

        ex = new_executor()
        emit("nav/goto-without-url", run(ex, "browser.navigate",
             {"browserType": browser, "action": "goto"}))

        # -- attach（无会话命令；启动页 basic 标签页天然存在） -------------------
        ex = new_executor()
        emit("attach/url-substring", run(ex, "browser.attach", {"pattern": "basic"}))

        ex = new_executor()
        run(ex, "browser.navigate", {"browserType": browser, "action": "goto", "url": other})
        emit("attach/title", run(ex, "browser.attach",
             {"pattern": "Other", "matchBy": "title"}))

        ex = new_executor()
        emit("attach/url-not-title", run(ex, "browser.attach",
             {"pattern": "Other", "matchBy": "url"}))

        ex = new_executor()
        emit("attach/regex", run(ex, "browser.attach",
             {"pattern": r"^http://127\.0\.0\.1:\d+/basic\.html", "useRegex": True}))

        ex = new_executor()
        emit("attach/invalid-regex", run(ex, "browser.attach",
             {"pattern": "(", "useRegex": True}))

        ex = new_executor()
        emit("attach/no-match", run(ex, "browser.attach", {"pattern": "absent-pattern"}))

        # -- close ---------------------------------------------------------------
        ex = new_executor()
        sid, _ = new_session(ex)
        main = run(ex, "browser.close", {})
        verify = run(ex, "browser.getText", {"sessionId": sid, "selector": "#t"})
        emit("close/detach", main, verify)

        ex = new_executor()
        emit("close/no-session", run(ex, "browser.close", {}))

        ex = new_executor()
        sid, _ = new_session(ex)
        emit("close/explicit", run(ex, "browser.close", {"sessionId": sid}))

        # -- closeTabs -------------------------------------------------------------
        ex = new_executor()
        sid, tab = new_session(ex)
        main = run(ex, "browser.closeTabs", {"sessionId": sid, "tabIds": [tab]})
        verify = run(ex, "browser.listPages", {})
        emit("closeTabs/explicit", main, verify)

        ex = new_executor()
        sid, tab = new_session(ex)
        emit("closeTabs/partial", run(ex, "browser.closeTabs",
             {"sessionId": sid, "tabIds": [tab, 999999]}))

        ex = new_executor()
        sid, tab = new_session(ex)
        emit("closeTabs/mutual", run(ex, "browser.closeTabs",
             {"sessionId": sid, "tabIds": [tab], "all": True}))

        ex = new_executor()
        sid, tab = new_session(ex)
        emit("closeTabs/neither", run(ex, "browser.closeTabs", {"sessionId": sid}))

        ex = new_executor()
        sid, tab = new_session(ex)
        emit("closeTabs/not-array", run(ex, "browser.closeTabs",
             {"sessionId": sid, "tabIds": "7"}))

        # -- listPages --------------------------------------------------------------
        ex = new_executor()
        emit("listPages/enumerated", run(ex, "browser.listPages", {}))


if __name__ == "__main__":
    main()
