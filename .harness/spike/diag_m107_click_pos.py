"""M107 诊断：物理点击为什么没落在按钮上。

做四件事：
1. 开靶页、注入探针；
2. 打印窗口矩形 + 视口几何，按「侧栏在左 / 在右」两种假设各算一个候选点；
3. 依次把鼠标移到候选点、读回真实光标位置、点一下、读页面状态；
4. 存一张全屏截图，方便肉眼核对按钮到底在哪。

只诊断，不改产品代码。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import ctypes  # noqa: E402
import ctypes.wintypes as wt  # noqa: E402

from rpa_core.executors.browser import PlaywrightExecutor  # noqa: E402
from rpa_core.executors.browser_ext import ExtensionExecSession  # noqa: E402
from rpa_core.extension_exec import ExtensionExecClient  # noqa: E402
from rpa_core.model.command import CommandInvocation  # noqa: E402
from tests.commands.l2_harness import TESTAPPS_BROWSER, _QuietHandler, _QuietServer  # noqa: E402

TARGET = "#btn"


def run(executor, command, inputs):
    import asyncio

    invocation = CommandInvocation(
        command_id=command,
        command_version="1.0.0",
        run_id="m107diag",
        step_id=command,
        inputs=dict(inputs),
    )
    return asyncio.run(executor.execute(invocation, asyncio.Event()))


def cursor_pos() -> tuple[int, int]:
    pt = wt.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


GEOMETRY = """
return (function () {
  var el = document.querySelector('#btn');
  var r = el.getBoundingClientRect();
  return {
    rect: { left: r.left, top: r.top, width: r.width, height: r.height },
    viewport: {
      width: window.innerWidth, height: window.innerHeight,
      dpr: window.devicePixelRatio,
      outerWidth: window.outerWidth, outerHeight: window.outerHeight
    }
  };
})()
"""

INSTALL = """
return (function () {
  window.__p = { events: [] };
  document.querySelector('#btn').addEventListener('click', function (e) {
    window.__p.events.push({ isTrusted: e.isTrusted, x: e.clientX, y: e.clientY });
  }, true);
  return 'ok';
})()
"""

READ = "return window.__p || null"


def main() -> int:
    import functools
    import threading

    client = ExtensionExecClient()
    if not client.status().get("online"):
        print("扩展不在线")
        return 2

    handler = functools.partial(_QuietHandler, directory=str(TESTAPPS_BROWSER))
    server = _QuietServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()

    executor = PlaywrightExecutor(ext_session=ExtensionExecSession(client=client))
    session_id = None
    try:
        nav = run(
            executor,
            "browser.navigate",
            {
                "browserType": "msedge",
                "action": "goto",
                "url": f"http://127.0.0.1:{server.server_port}/basic.html",
            },
        )
        session_id = nav.outputs["sessionId"]
        time.sleep(1.0)
        run(executor, "browser.executeScript", {"sessionId": session_id, "script": INSTALL})
        geo = run(executor, "browser.executeScript", {"sessionId": session_id, "script": GEOMETRY})
        g = geo.outputs["result"]
        rect = g["rect"]
        vp = g["viewport"]

        import win32gui

        hits: list[tuple[int, str]] = []

        def collect(h, _):
            if win32gui.IsWindowVisible(h) and "RPA L2 靶页" in win32gui.GetWindowText(h):
                hits.append((h, win32gui.GetWindowText(h)))
            return True

        win32gui.EnumWindows(collect, None)
        hwnd = hits[0][0]
        wl, wt_, wr, wb = win32gui.GetWindowRect(hwnd)
        ww, wh = wr - wl, wb - wt_

        print(json.dumps({
            "windowRect": [wl, wt_, wr, wb],
            "windowSize": [ww, wh],
            "viewport": vp,
            "rect": rect,
            "deltaX": ww - vp["width"],
            "deltaY": wh - vp["height"],
        }, ensure_ascii=False, indent=2))

        cx = rect["left"] + rect["width"] / 2
        cy = rect["top"] + rect["height"] / 2
        # 侧栏在左：视口左 = 窗口左 + (窗宽-视口宽)；侧栏在右：视口左 = 窗口左 + 边框
        dx, dy = ww - vp["width"], wh - vp["height"]
        candidates = {
            "sidebarLeft": (wl + dx + cx, wt_ + dy + cy),
            "sidebarRight": (wl + 8 + cx, wt_ + dy + cy),
        }

        u = ctypes.windll.user32
        results = []
        for name, (x, y) in candidates.items():
            u.SetCursorPos(int(x), int(y))
            time.sleep(0.15)
            real = cursor_pos()
            u.mouse_event(0x0002, 0, 0, 0, 0)
            u.mouse_event(0x0004, 0, 0, 0, 0)
            time.sleep(0.35)
            probe = run(executor, "browser.executeScript", {"sessionId": session_id, "script": READ})
            status = run(
                executor,
                "browser.getText",
                {"sessionId": session_id, "selector": "#status", "infoType": "text"},
            )
            results.append({
                "candidate": name,
                "target": [int(x), int(y)],
                "cursorAfterMove": list(real),
                "events": (probe.outputs.get("result") or {}).get("events"),
                "status": status.outputs.get("value"),
            })

        print(json.dumps({"trials": results}, ensure_ascii=False, indent=2))

        try:
            from PIL import ImageGrab

            img = ImageGrab.grab(all_screens=True)
            out = ROOT / ".harness" / "spike" / "_m107_screen.png"
            img.save(out)
            print("screenshot:", out, img.size)
        except Exception as exc:  # noqa: BLE001
            print("screenshot failed:", exc)
    finally:
        if session_id:
            run(executor, "browser.close", {"sessionId": session_id})
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
