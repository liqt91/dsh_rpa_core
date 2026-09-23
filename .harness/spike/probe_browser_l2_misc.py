"""S4.5+S4.6 探针：cookies / screenshot / waitFor / waitLoad / stopLoading 真机实测。

跑法（会拉起真实浏览器窗口）：

    .venv/Scripts/python.exe .harness/spike/probe_browser_l2_misc.py

纪律同前：期望一律以实测为准。cookie 作用域是 127.0.0.1 的 host-only cookie。
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
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
        slow = l2.page_url("slow")
        tmp = Path(tempfile.mkdtemp(prefix="rpa-l2-probe-"))

        def new_executor_with_session(url=basic):
            ex = PlaywrightExecutor(
                ext_session=ExtensionExecSession(client=l2.client)
            )
            nav = run(ex, "browser.navigate",
                      {"browserType": browser, "action": "goto", "url": url})
            assert nav.status == "success", nav.model_dump_json()
            return ex, nav.outputs["sessionId"]

        def emit(name, result, extra=None):
            record = {"case": name, "main": brief(result)}
            if extra:
                record.update(extra)
            print(json.dumps(record, ensure_ascii=False, default=str))

        # -- cookies -------------------------------------------------------------
        ex, sid = new_executor_with_session()
        emit("cookieSet/basic", run(ex, "browser.cookieSet",
             {"sessionId": sid, "cookies": [{"name": "k", "value": "v", "url": basic}]}))
        emit("cookieGet/existing", run(ex, "browser.cookieGet",
             {"sessionId": sid, "name": "k"}))
        emit("cookieGet/missing", run(ex, "browser.cookieGet",
             {"sessionId": sid, "name": "absent"}))
        emit("cookieGetAll/all", run(ex, "browser.cookieGetAll", {"sessionId": sid}))
        emit("cookieGetAll/name-filter", run(ex, "browser.cookieGetAll",
             {"sessionId": sid, "name": "k"}))
        emit("cookieGetAll/domain-miss", run(ex, "browser.cookieGetAll",
             {"sessionId": sid, "domain": "nosuch.test"}))
        emit("cookieRemove/k", run(ex, "browser.cookieRemove",
             {"sessionId": sid, "name": "k"}))
        emit("cookieGet/after-remove", run(ex, "browser.cookieGet",
             {"sessionId": sid, "name": "k"}))
        emit("cookieSet/empty", run(ex, "browser.cookieSet",
             {"sessionId": sid, "cookies": []}))

        # -- screenshot ------------------------------------------------------------
        ex, sid = new_executor_with_session()
        shot = tmp / "l2-shot.png"
        result = run(ex, "browser.screenshot",
                     {"sessionId": sid, "savePath": str(shot)})
        extra = {"fileExists": shot.exists(), "fileSize": shot.stat().st_size if shot.exists() else 0}
        emit("screenshot/basic", result, extra)

        # -- waitFor 四态 ----------------------------------------------------------
        ex, sid = new_executor_with_session()
        emit("waitFor/visible", run(ex, "browser.waitFor",
             {"sessionId": sid, "selector": "#t"}))
        emit("waitFor/hidden", run(ex, "browser.waitFor",
             {"sessionId": sid, "selector": "#hidden", "state": "hidden"}))
        emit("waitFor/attached", run(ex, "browser.waitFor",
             {"sessionId": sid, "selector": "#hidden", "state": "attached"}))
        emit("waitFor/detached", run(ex, "browser.waitFor",
             {"sessionId": sid, "selector": "#absent", "state": "detached"}))
        started = time.perf_counter()
        result = run(ex, "browser.waitFor",
                     {"sessionId": sid, "selector": "#absent", "state": "visible",
                      "timeoutMs": 600})
        emit("waitFor/timeout", result,
             {"elapsedMs": round((time.perf_counter() - started) * 1000, 1)})

        # -- waitLoad / stopLoading -------------------------------------------------
        ex, sid = new_executor_with_session()
        emit("waitLoad/loaded", run(ex, "browser.waitLoad", {"sessionId": sid}))

        ex, sid = new_executor_with_session()
        # 让页面开始加载 slow（executeScript 触发导航、不等加载完成），再 stopLoading
        run(ex, "browser.executeScript",
            {"sessionId": sid, "script": f"location.href = {json.dumps(slow)}; return 1"})
        time.sleep(0.3)
        emit("stopLoading/mid-load", run(ex, "browser.stopLoading", {"sessionId": sid}))


if __name__ == "__main__":
    main()
