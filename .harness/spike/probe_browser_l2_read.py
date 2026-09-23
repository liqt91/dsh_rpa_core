"""S4.2 探针：读族命令在真机上的实际产出（先实测、再写 l2 期望）。

跑法（会拉起真实浏览器窗口）：

    .venv/Scripts/python.exe .harness/spike/probe_browser_l2_read.py

产出：每条候选变体的 status / outputs / error，逐条打印 JSON。
纪律：期望一律以本探针的实测值为准来写（S2/S2.3 教训：凭代码推断会把「允许」
写成想象）。探针不改任何产品代码，只读。
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

# (名称, 命令, inputs)。sessionId 在探针里统一替换成真实会话。
CASES = [
    ("getText/text", "browser.getText", {"selector": "#t", "infoType": "text"}),
    ("getText/html", "browser.getText", {"selector": "#t", "infoType": "html"}),
    ("getText/outerHTML", "browser.getText", {"selector": "#t", "infoType": "outerHTML"}),
    ("getText/value", "browser.getText", {"selector": "#kw", "infoType": "value"}),
    ("getText/href", "browser.getText", {"selector": "#link", "infoType": "href"}),
    ("getText/zero-match", "browser.getText", {"selector": "#absent"}),
    ("getSelectOptions/options", "browser.getSelectOptions", {"selector": "#sel"}),
    ("getSelectOptions/zero-match", "browser.getSelectOptions", {"selector": "#absent"}),
    ("getPosition/box", "browser.getPosition", {"selector": "#t"}),
    ("getPosition/zero-match", "browser.getPosition", {"selector": "#absent"}),
    ("queryAll/items", "browser.queryAll", {"selector": ".q"}),
    ("queryAll/zero-match", "browser.queryAll", {"selector": "#absent"}),
    ("executeScript/args", "browser.executeScript", {"script": "return 1", "args": [1, "a"]}),
    ("executeScript/no-args", "browser.executeScript", {"script": "return 2"}),
    ("executeScript/verbatim", "browser.executeScript", {"script": "return [1,2]"}),
]


def run(executor, command, inputs):
    invocation = CommandInvocation(
        command_id=command,
        command_version="1.0.0",
        run_id="probe",
        step_id=command,
        inputs=dict(inputs),
    )
    return asyncio.run(executor.execute(invocation, asyncio.Event()))


def main() -> None:
    with l2_browser_session() as l2:
        executor = PlaywrightExecutor(ext_session=ExtensionExecSession(client=l2.client))
        nav = run(
            executor,
            "browser.navigate",
            {"browserType": l2.browser, "action": "goto", "url": l2.page_url("basic")},
        )
        assert nav.status == "success", nav.model_dump_json()
        session_id = nav.outputs["sessionId"]
        print(f"# session: {session_id} page: {l2.page_url('basic')}")
        for name, command, inputs in CASES:
            if command != "browser.navigate":
                inputs.setdefault("sessionId", session_id)
            result = run(executor, command, inputs)
            record = {
                "case": name,
                "status": result.status,
                "outputs": result.outputs,
                "error": (
                    {"code": result.error.code, "message": result.error.message}
                    if result.error
                    else None
                ),
            }
            print(json.dumps(record, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
