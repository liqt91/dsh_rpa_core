"""诊断：小红书筛选按钮 dom/cdp 两种注入的事件序列对比。只诊断，不改产品代码。

做法：每种注入都重新导航+搜索，保证初始状态一致；用 browser.executeScript
在 MAIN world 挂 capture 监听，点击后读回事件链、DOM 增量与可见浮层。
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from rpa_core.executors.browser import PlaywrightExecutor  # noqa: E402
from rpa_core.executors.browser_ext import ExtensionExecSession  # noqa: E402
from rpa_core.extension_exec import ExtensionExecClient  # noqa: E402
from rpa_core.model.command import CommandInvocation  # noqa: E402

FILTER_SEL = (
    "div.main-content > div.ai-feeds-page > div.search-layout-wrapper > "
    "div.search-layout > div.search-layout__top > div.filter"
)
INPUT_SEL = "div.feeds-content > div.search-area > div.input-box #search-input-in-feeds"

INSTALL = """return (function () {
  var sel = %s;
  var el = document.querySelector(sel);
  if (!el) return {ok: false, why: 'filter element missing'};
  window.__xhs = {events: [], htmlLen: document.body.innerHTML.length};
  var push = function (e, prefix) {
    var t = e.target;
    window.__xhs.events.push({
      t: prefix + e.type,
      trusted: e.isTrusted,
      x: e.clientX, y: e.clientY,
      button: e.button,
      target: (t && t.tagName ? t.tagName + '.' : '') + String((t && t.className) || '').slice(0, 60),
      ts: Math.round(performance.now())
    });
  };
  ['mouseover', 'mouseenter', 'mousedown', 'mouseup', 'click', 'focusin', 'focusout', 'mouseleave', 'mouseout']
    .forEach(function (typ) { el.addEventListener(typ, function (e) { push(e, ''); }, true); });
  ['mousedown', 'mouseup', 'click', 'focusin', 'focusout']
    .forEach(function (typ) { document.addEventListener(typ, function (e) { push(e, 'doc:'); }, true); });
  var r = el.getBoundingClientRect();
  return {ok: true, rect: {x: r.x, y: r.y, w: r.width, h: r.height},
          viewport: {w: window.innerWidth, h: window.innerHeight}};
})()""" % json.dumps(FILTER_SEL)

READ = """return (function () {
  var p = window.__xhs || {events: [], htmlLen: 0};
  return {
    events: p.events,
    htmlDelta: document.body.innerHTML.length - p.htmlLen,
    visiblePanels: Array.from(document.querySelectorAll(
      '[class*="dropdown"],[class*="popup"],[class*="popover"],[class*="filter-panel"],[class*="filter-panel"]'
    )).filter(function (e) {
      var r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0;
    }).slice(0, 12).map(function (e) {
      return {tag: e.tagName, cls: String(e.className || '').slice(0, 80)};
    })
  };
})()"""


def run(executor: PlaywrightExecutor, command: str, inputs: dict):
    invocation = CommandInvocation(
        command_id=command,
        command_version="1.0.0",
        run_id="xhs-click-probe",
        step_id=command,
        inputs=dict(inputs),
    )
    return asyncio.run(executor.execute(invocation, asyncio.Event()))


def main() -> int:
    client = ExtensionExecClient()
    status = client.status()
    print("extension:", json.dumps(status, ensure_ascii=False))
    if not status.get("online"):
        return 2
    executor = PlaywrightExecutor(ext_session=ExtensionExecSession(client=client))
    results = []
    for injection in ("dom", "cdp"):
        trial = {"injection": injection}
        try:
            nav = run(executor, "browser.navigate", {
                "browserType": "chrome", "action": "goto",
                "url": "https://www.xiaohongshu.com/explore",
            })
            sid = nav.outputs["sessionId"]
            trial["sessionId"] = sid
            run(executor, "browser.input", {
                "sessionId": sid, "selector": INPUT_SEL, "text": "东方财富",
                "mode": "fill", "pressEnter": True,
                "clickBeforeInput": True, "postDelayMs": 1500,
            })
            time.sleep(1.5)
            inst = run(executor, "browser.executeScript", {"sessionId": sid, "script": INSTALL})
            trial["install"] = inst.outputs.get("result")
            click = run(executor, "browser.click", {
                "sessionId": sid, "selector": FILTER_SEL,
                "clickPosition": "center", "injection": injection,
                "postDelayMs": 600,
            })
            trial["click"] = {
                "status": click.status,
                "outputs": click.outputs,
                "effects": [e.details for e in (click.effects or [])],
                "error": click.error,
            }
            time.sleep(0.4)
            read = run(executor, "browser.executeScript", {"sessionId": sid, "script": READ})
            trial["probe"] = read.outputs.get("result")
        except Exception as exc:  # noqa: BLE001 - 诊断脚本，任何失败都要落进结果
            trial["error"] = repr(exc)
        results.append(trial)
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
