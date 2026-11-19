"""M107 S2 探针：CDP 通道能不能产出 ``isTrusted=true`` 的点击。

为什么从 S0 换成 S2（不是换思路，是被环境逼的）：
S0 走操作系统注入（``SetCursorPos`` + ``SendInput``），在本机必然失败——深信服 EDR
在创建进程时把解释器令牌降成 Low（``S-1-16-4096``），Low 进程的合成输入会被系统吞掉：
``SendInput`` 返回 1 但光标不动、``SetCursorPos`` 返回 0 且**不设错误码**。用户从 High 的
cmd 起 python，python 仍是 Low（父进程 High 也救不了），加 EDR 信任区也没用。
⇒ 只要退出码 1（OS 注入）这条路，在企业环境就会大面积失效。

S2 走 ``chrome.debugger`` 的 ``Input.dispatchMouseEvent``：事件由**浏览器自己生成**
（isTrusted=true），不经过 OS 输入队列。**所以本探针在 Low 进程里也能跑通**——
这本身就是「这条路可行」的证据，不需要任何提权、不需要窗口置前。

判据（四条同时成立才算 PASS，任缺一条都是假绿）：
1. 对照：``browser.click``（扩展内 dispatchEvent）产出的 click 事件 isTrusted === false
   —— 证明探针确实能区分两种来源（若这里也是 true，说明页面读到的不是真事件）；
2. CDP：``cdp.click`` 产出的 click 事件 isTrusted === true；
3. 落地：``#status`` 依次变成 ``clicked:1`` / ``clicked:2``（排除「trusted 但点偏了」）；
4. 坐标：CDP 那次的 mousedown/mouseup/click 落点都在按钮矩形内。

用法：
    .venv\\Scripts\\python.exe .harness\\spike\\probe_m107_cdp_click.py
"""

from __future__ import annotations

import asyncio
import functools
import json
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from rpa_core.executors.browser import PlaywrightExecutor  # noqa: E402
from rpa_core.executors.browser_ext import ExtensionExecSession  # noqa: E402
from rpa_core.extension_exec import (  # noqa: E402
    ExtensionChannelError,
    ExtensionExecClient,
)
from rpa_core.model.command import CommandInvocation  # noqa: E402
from tests.commands.l2_harness import (  # noqa: E402
    TESTAPPS_BROWSER,
    _QuietHandler,
    _QuietServer,
)

REPORT_PATH = Path(__file__).with_name("_m107_cdp_out.json")
MANIFEST = ROOT / "extension" / "manifest.json"

TARGET = "#btn"
STATUS = "#status"

# 监听器：逐条记录类型 / isTrusted / 坐标。capture 阶段挂在按钮上，冒泡前就能拿到。
INSTALL_PROBE = """
return (function () {
  window.__rpaProbe = { events: [] };
  var b = document.querySelector('#btn');
  if (!b) return 'no-target';
  var types = ['pointerover', 'mouseover', 'pointerdown',
               'mousedown', 'mouseup', 'click', 'contextmenu', 'dblclick'];
  types.forEach(function (type) {
    b.addEventListener(type, function (e) {
      window.__rpaProbe.events.push({
        type: type,
        isTrusted: e.isTrusted,
        x: e.clientX,
        y: e.clientY,
        button: e.button
      });
    }, true);
  });
  return 'installed';
})()
"""

RESET_PROBE = "window.__rpaProbe.events.length = 0; return true"
READ_PROBE = "return window.__rpaProbe || null"
READ_STATUS = "return (document.querySelector('#status') || {}).textContent || ''"
READ_RECT = """
return (function () {
  var r = document.querySelector('#btn').getBoundingClientRect();
  return { left: r.left, top: r.top, right: r.right, bottom: r.bottom,
           width: r.width, height: r.height };
})()
"""


def run(executor, command, inputs, step="probe"):
    invocation = CommandInvocation(
        command_id=command,
        command_version="1.0.0",
        run_id="m107-cdp",
        step_id=step,
        inputs=dict(inputs),
    )
    return asyncio.run(executor.execute(invocation, asyncio.Event()))


def brief(result):
    return {
        "status": result.status,
        "outputs": result.outputs,
        "error": (
            {"code": result.error.code, "message": result.error.message} if result.error else None
        ),
    }


def emit(report: dict, code: int, line: str | None = None) -> int:
    """报告先落盘再打印：谁跑都留得下证据，不用把一屏 JSON 复制回去。"""
    report.setdefault("reportPath", str(REPORT_PATH))
    text = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    try:
        REPORT_PATH.write_text(text + "\n", encoding="utf-8", newline="\n")
    except OSError as exc:
        print(f"[M107] 报告落盘失败：{exc}", file=sys.stderr)
    if line:
        print(f"[M107] {line}", file=sys.stderr)
    print(text)
    return code


def clicks(events):
    return [event for event in events if event.get("type") == "click"]


def inside(point, rect):
    in_x = rect["left"] <= point["x"] <= rect["right"]
    in_y = rect["top"] <= point["y"] <= rect["bottom"]
    return in_x and in_y


def main() -> int:
    report: dict = {"steps": {}}
    expected_version = json.loads(MANIFEST.read_text(encoding="utf-8"))["version"]
    client = ExtensionExecClient()
    status = client.status()
    host = status.get("host") or {}
    report["extension"] = {
        "online": bool(status.get("online")),
        "endpoint": host.get("endpoint"),
        "extVersion": host.get("extVersion"),
        "expectedVersion": expected_version,
    }
    if not status.get("online"):
        return emit(report, 2, "扩展不在线：先在浏览器里打开扩展弹窗确认在线")
    # 版本对不上就是「仓库改了、浏览器还跑着旧快照」——必须先重载，否则会误判成通道不可用
    if str(host.get("extVersion") or "") != expected_version:
        report["fatal"] = {
            "reason": "扩展版本与仓库不一致",
            "loaded": host.get("extVersion"),
            "expected": expected_version,
        }
        return emit(
            report,
            2,
            f"扩展在跑 {host.get('extVersion')}，仓库是 {expected_version}："
            "先到 chrome://extensions 点「重新加载」，再跑一次",
        )

    handler = functools.partial(_QuietHandler, directory=str(TESTAPPS_BROWSER))
    server = _QuietServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    page_url = f"http://127.0.0.1:{server.server_port}/basic.html"
    report["pageUrl"] = page_url

    executor = PlaywrightExecutor(ext_session=ExtensionExecSession(client=client))
    session_id = None
    try:
        nav = run(
            executor,
            "browser.navigate",
            {"browserType": "msedge", "action": "goto", "url": page_url},
            "nav",
        )
        report["steps"]["navigate"] = brief(nav)
        if nav.status != "success":
            return emit(report, 1, f"navigate 失败：{nav.error}")
        session_id = nav.outputs["sessionId"]
        report["sessionId"] = session_id
        tab_id = executor._ext_sessions.get(session_id)
        report["tabId"] = tab_id
        time.sleep(0.8)  # 等 target 激活 + 首帧渲染

        install = run(
            executor, "browser.executeScript", {"sessionId": session_id, "script": INSTALL_PROBE}
        )
        report["steps"]["install"] = brief(install)
        if install.status != "success" or install.outputs.get("result") != "installed":
            return emit(report, 1, "监听器没装上（靶页里找不到 #btn）")

        rect_out = run(
            executor, "browser.executeScript", {"sessionId": session_id, "script": READ_RECT}
        )
        rect = rect_out.outputs.get("result") or {}
        report["rect"] = rect

        # ---- ① 对照：扩展内 dispatchEvent（合成事件）----
        before = run(
            executor, "browser.executeScript", {"sessionId": session_id, "script": READ_STATUS}
        )
        report["statusBefore"] = before.outputs.get("result")
        synth = run(
            executor, "browser.click", {"sessionId": session_id, "selector": TARGET}, "synth"
        )
        report["steps"]["synthClick"] = brief(synth)
        time.sleep(0.2)
        synth_probe = run(
            executor, "browser.executeScript", {"sessionId": session_id, "script": READ_PROBE}
        )
        synth_events = (synth_probe.outputs.get("result") or {}).get("events") or []
        report["synthEvents"] = synth_events
        synth_status = run(
            executor, "browser.executeScript", {"sessionId": session_id, "script": READ_STATUS}
        ).outputs.get("result")
        report["synthStatus"] = synth_status

        # ---- ② CDP：chrome.debugger 的 Input.dispatchMouseEvent ----
        run(executor, "browser.executeScript", {"sessionId": session_id, "script": RESET_PROBE})
        cdp_payload = None
        cdp_error = None
        try:
            cdp_payload = client.submit(
                "cdp.click",
                {
                    "tabId": tab_id,
                    "selector": TARGET,
                    "clickPosition": "center",
                    "button": "left",
                    "clickType": "single",
                    "pressDelayMs": 40,
                    "detachAfter": True,
                },
                timeout_seconds=30,
            )
        except ExtensionChannelError as exc:
            cdp_error = {"code": exc.code, "message": str(exc)}
        report["steps"]["cdpClick"] = {"payload": cdp_payload, "error": cdp_error}
        time.sleep(0.3)
        cdp_probe = run(
            executor, "browser.executeScript", {"sessionId": session_id, "script": READ_PROBE}
        )
        cdp_events = (cdp_probe.outputs.get("result") or {}).get("events") or []
        report["cdpEvents"] = cdp_events
        cdp_status = run(
            executor, "browser.executeScript", {"sessionId": session_id, "script": READ_STATUS}
        ).outputs.get("result")
        report["cdpStatus"] = cdp_status

        # ---- ③ 用户实际走的那条路：browser.click + injection=cdp ----
        # 直接调 `cdp.click` 只证明扩展能派发；用户在属性面板选「调试协议真事件」时，
        # 走的是 browser.click → 执行器 _ext_cdp_click → 扩展，这条链必须单独验
        # （记忆里的教训：覆盖「实现」≠ 覆盖「用户实际走的路」）。
        run(executor, "browser.executeScript", {"sessionId": session_id, "script": RESET_PROBE})
        cmd = run(
            executor,
            "browser.click",
            {"sessionId": session_id, "selector": TARGET, "injection": "cdp"},
            "cdpCmd",
        )
        report["steps"]["cdpCommand"] = brief(cmd)
        time.sleep(0.3)
        cmd_probe = run(
            executor, "browser.executeScript", {"sessionId": session_id, "script": READ_PROBE}
        )
        cmd_events = (cmd_probe.outputs.get("result") or {}).get("events") or []
        report["cmdEvents"] = cmd_events
        cmd_status = run(
            executor, "browser.executeScript", {"sessionId": session_id, "script": READ_STATUS}
        ).outputs.get("result")
        report["cmdStatus"] = cmd_status
    finally:
        if session_id:
            try:
                run(
                    executor,
                    "browser.navigate",
                    {"sessionId": session_id, "action": "goto", "url": "about:blank"},
                    "cleanup",
                )
                run(executor, "browser.close", {"sessionId": session_id}, "close")
            except Exception as exc:  # noqa: BLE001 - 收尾失败不该掩盖验证结果
                report["closeError"] = f"{type(exc).__name__}: {exc}"
        server.shutdown()
        server.server_close()

    # ---- 判定 ----
    synth_click = clicks(report.get("synthEvents") or [])
    cdp_click = clicks(report.get("cdpEvents") or [])
    cmd_click = clicks(report.get("cmdEvents") or [])
    cd_chain_types = {event.get("type") for event in report.get("cdpEvents") or []}

    checks = {
        # 对照：合成事件必须是 false，否则说明判据分辨不出两种来源（恒绿的典型形态）
        "synthNotTrusted": bool(synth_click)
        and all(e.get("isTrusted") is False for e in synth_click),
        "synthLanded": report.get("synthStatus") == "clicked:1",
        "cdpNoError": (report.get("steps", {}).get("cdpClick") or {}).get("error") is None,
        "cdpTrusted": bool(cdp_click) and all(e.get("isTrusted") is True for e in cdp_click),
        "cdpChain": {"mousedown", "mouseup", "click"} <= cd_chain_types,
        "cdpLanded": report.get("cdpStatus") == "clicked:2",
        "cdpPointInside": all(inside(e, report["rect"]) for e in cdp_click) if cdp_click else False,
        # 执行器层（用户实际走的路）：命令必须成功、事件必须真、状态必须再进一格
        "cmdNoError": (report.get("steps", {}).get("cdpCommand") or {}).get("status") == "success",
        "cmdTrusted": bool(cmd_click) and all(e.get("isTrusted") is True for e in cmd_click),
        "cmdLanded": report.get("cmdStatus") == "clicked:3",
    }
    report["checks"] = checks
    passed = all(checks.values())
    report["verdict"] = {
        "trustedClicks": sum(1 for e in cdp_click if e.get("isTrusted") is True),
        "commandTrustedClicks": sum(1 for e in cmd_click if e.get("isTrusted") is True),
        "syntheticClicks": len(synth_click),
        "conclusion": (
            "PASS CDP op 与 browser.click(injection=cdp) 都产出 isTrusted=true 且落在按钮上，"
            "合成对照如实报 false"
            if passed
            else "FAIL 逐项看 checks（哪条 false 就对应哪条假设不成立）"
        ),
    }
    return emit(report, 0 if passed else 1)


if __name__ == "__main__":
    sys.exit(main())
