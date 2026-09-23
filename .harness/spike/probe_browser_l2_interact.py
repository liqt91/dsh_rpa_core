"""S4.3 探针：交互族命令在真机上的实际产出与副作用读侧（先实测、再写 l2 期望）。

跑法（会拉起真实浏览器窗口）：

    .venv/Scripts/python.exe .harness/spike/probe_browser_l2_interact.py

每条 = 主命令 + 可选 verify（独立读侧：getText/executeScript/getScrollPosition）。
纪律与读族探针同：期望一律以实测为准；探针不改产品代码。
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

_STATUS = {"selector": "#status", "infoType": "text"}
_MODS = {"selector": "#mods", "infoType": "text"}
_KW_VALUE = {"selector": "#kw", "infoType": "value"}
_SEL_VALUE = {"selector": "#sel", "infoType": "value"}
_CHK = {"script": "return document.querySelector('#chk').checked"}

# (名称, 命令, 主 inputs（selector 已按靶页覆盖）, [(verify 命令, verify inputs)])
CASES = [
    # -- click ---------------------------------------------------------------
    ("click/single", "browser.click", {"selector": "#btn"}, [("browser.getText", _STATUS)]),
    ("click/double", "browser.click", {"selector": "#btn", "clickType": "double"}, [("browser.getText", _STATUS)]),
    ("click/right", "browser.click", {"selector": "#btn", "button": "right"}, [("browser.getText", _STATUS)]),
    ("click/middle", "browser.click", {"selector": "#btn", "button": "middle"}, [("browser.getText", _STATUS)]),
    ("click/modifiers", "browser.click", {"selector": "#btn", "modifiers": ["Ctrl", "Win"]}, [("browser.getText", _MODS)]),
    ("click/no-human", "browser.click", {"selector": "#btn", "simulateHuman": False}, [("browser.getText", _STATUS)]),
    ("click/random-pos", "browser.click", {"selector": "#btn", "clickPosition": "random"}, [("browser.getText", _STATUS)]),
    ("click/postDelay", "browser.click", {"selector": "#btn", "postDelayMs": 300}, []),
    ("click/zero-match", "browser.click", {"selector": "#absent"}, []),
    # -- input ---------------------------------------------------------------
    ("input/fill", "browser.input", {"selector": "#kw", "text": "hi", "mode": "fill"}, [("browser.getText", _KW_VALUE)]),
    ("input/type", "browser.input", {"selector": "#kw", "text": "hi", "mode": "type"}, [("browser.getText", _KW_VALUE)]),
    ("input/clipboard", "browser.input", {"selector": "#kw", "text": "hi", "mode": "clipboard"}, [("browser.getText", _KW_VALUE)]),
    ("input/interval", "browser.input", {"selector": "#kw", "text": "hi", "mode": "type", "keyIntervalMs": 200}, [("browser.getText", _KW_VALUE)]),
    ("input/interval-0", "browser.input", {"selector": "#kw", "text": "hi", "mode": "type", "keyIntervalMs": 0}, [("browser.getText", _KW_VALUE)]),
    ("input/append", "browser.input", {"selector": "#kw", "text": "hi", "append": True}, [("browser.getText", _KW_VALUE)]),
    ("input/pressEnter", "browser.input", {"selector": "#kw", "text": "hi", "pressEnter": True}, [("browser.getText", _KW_VALUE)]),
    ("input/clickBefore", "browser.input", {"selector": "#kw", "text": "hi", "clickBeforeInput": True}, [("browser.getText", _KW_VALUE)]),
    ("input/postDelay", "browser.input", {"selector": "#kw", "text": "hi", "postDelayMs": 300}, []),
    ("input/zero-match", "browser.input", {"selector": "#absent", "text": "hi"}, []),
    # -- select ----------------------------------------------------------------
    ("select/value", "browser.select", {"selector": "#sel", "value": "beta", "selectBy": "value"}, [("browser.getText", _SEL_VALUE)]),
    ("select/label", "browser.select", {"selector": "#sel", "value": "Gamma", "selectBy": "label"}, [("browser.getText", _SEL_VALUE)]),
    ("select/index", "browser.select", {"selector": "#sel", "value": "2", "selectBy": "index"}, [("browser.getText", _SEL_VALUE)]),
    ("select/zero-match", "browser.select", {"selector": "#absent", "value": "v"}, []),
    # -- check -----------------------------------------------------------------
    ("check/check", "browser.check", {"selector": "#chk", "operation": "check"}, [("browser.executeScript", _CHK)]),
    ("check/uncheck", "browser.check", {"selector": "#chk", "operation": "uncheck"}, [("browser.executeScript", _CHK)]),
    ("check/toggle", "browser.check", {"selector": "#chk", "operation": "toggle"}, [("browser.executeScript", _CHK)]),
    ("check/zero-match", "browser.check", {"selector": "#absent"}, []),
    # -- scroll + getScrollPosition ---------------------------------------------
    ("scroll/top", "browser.scroll", {"position": "top"}, [("browser.getScrollPosition", {})]),
    ("scroll/bottom", "browser.scroll", {"position": "bottom"}, [("browser.getScrollPosition", {})]),
    ("scroll/page", "browser.scroll", {"position": "page"}, [("browser.getScrollPosition", {})]),
    ("scroll/point", "browser.scroll", {"position": "point", "x": 10, "y": 20}, [("browser.getScrollPosition", {})]),
    ("scroll/element", "browser.scroll", {"position": "bottom", "selector": "#box"}, [("browser.getScrollPosition", {"selector": "#box"})]),
    ("scroll/smooth", "browser.scroll", {"position": "bottom", "smooth": True}, [("browser.getScrollPosition", {})]),
    ("scroll/zero-match", "browser.scroll", {"position": "top", "selector": "#absent"}, []),
    ("gsp/window", "browser.getScrollPosition", {}, []),
    ("gsp/element", "browser.getScrollPosition", {"selector": "#box"}, []),
    ("gsp/zero-match", "browser.getScrollPosition", {"selector": "#absent"}, []),
    # -- setValue / setAttribute -------------------------------------------------
    ("setValue/value", "browser.setValue", {"selector": "#kw", "value": "v2", "setWay": "value"}, [("browser.getText", _KW_VALUE)]),
    ("setValue/innerText", "browser.setValue", {"selector": "#t", "value": "v2", "setWay": "innerText"}, [("browser.getText", {"selector": "#t", "infoType": "text"})]),
    ("setValue/innerHTML", "browser.setValue", {"selector": "#t", "value": "<b>b</b>", "setWay": "innerHTML"}, [("browser.getText", {"selector": "#t", "infoType": "html"})]),
    ("setValue/zero-match", "browser.setValue", {"selector": "#absent", "value": "v"}, []),
    ("setAttribute/basic", "browser.setAttribute", {"selector": "#t", "name": "data-x", "value": "y"}, [("browser.executeScript", {"script": "return document.querySelector('#t').getAttribute('data-x')"})]),
    ("setAttribute/zero-match", "browser.setAttribute", {"selector": "#absent", "name": "a", "value": "b"}, []),
    # -- hover / drag -------------------------------------------------------------
    ("hover/basic", "browser.hover", {"selector": "#hv"}, [("browser.getText", _STATUS)]),
    ("hover/zero-match", "browser.hover", {"selector": "#absent"}, []),
    ("drag/basic", "browser.drag", {"selector": "#src", "targetSelector": "#drop"}, [("browser.getText", _STATUS)]),
    ("drag/zero-match", "browser.drag", {"selector": "#absent", "targetSelector": "#drop"}, []),
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


def brief(result):
    return {
        "status": result.status,
        "outputs": result.outputs,
        "error": (
            {"code": result.error.code, "message": result.error.message}
            if result.error
            else None
        ),
    }


def main() -> None:
    with l2_browser_session() as l2:
        for name, command, inputs, verifies in CASES:
            # 每条独立会话（新标签页回 basic）：变体间零状态泄漏
            executor = PlaywrightExecutor(
                ext_session=ExtensionExecSession(client=l2.client)
            )
            nav = run(
                executor,
                "browser.navigate",
                {
                    "browserType": l2.browser,
                    "action": "goto",
                    "url": l2.page_url("basic"),
                },
            )
            if nav.status != "success":
                print(json.dumps({"case": name, "navFailed": nav.outputs}, ensure_ascii=False))
                continue
            session_id = nav.outputs["sessionId"]
            inputs = dict(inputs)
            inputs.setdefault("sessionId", session_id)
            started = time.perf_counter()
            result = run(executor, command, inputs)
            elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
            record = {"case": name, "elapsedMs": elapsed_ms, "main": brief(result)}
            for verify_command, verify_inputs in verifies:
                verify_inputs = dict(verify_inputs)
                verify_inputs.setdefault("sessionId", session_id)
                record.setdefault("verify", []).append(
                    {"command": verify_command, **brief(run(executor, verify_command, verify_inputs))}
                )
            print(json.dumps(record, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
