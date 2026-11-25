"""运行接线·界面探针：在真 WebView2 里把「▶ 运行 / ■ 停止」真点一遍。

为什么要这一层（而不是只用 `exec_probe.py`）：
后端 RPC 绿只说明「数据对」，说明不了「界面上真长出来了」——DOM 里有没有事件行、
按钮的 disabled 有没有跟着变、正在跑的节点有没有被标出来，这些都只在**渲染层**能证。

为什么不用鼠标点击（PostMessage）驱动：上一轮实测在 WebView2 上时灵时不灵
（键盘进不了 `<select>`、连点后续失效），靠它做的判据会变成假绿。这里换成
`window.evaluate_js` 直接调 `document.querySelector(...).click()` —— 走的是同一条
事件通路，但不需要命中像素。

桥接：页面用真 `main.js` + 真 `api.py`。中间的 ppx Bridge 由本文件的 `Bridge`
按 `bridge.py` 的契约复刻（`{ok, data, error, requestId}` + 同名错误码）——
ppx-js 只认 `window.pywebview.api.call`，所以换掉的是外壳，不是被测代码。

跑法（在 ppx_probe 目录下）：
    ./.venv/Scripts/python.exe ui_probe.py
"""

from __future__ import annotations

import ctypes
import inspect
import json
import subprocess
import sys
import time
import urllib.request
from ctypes import wintypes
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = next(p for p in HERE.parents if (p / "src" / "rpa_core").is_dir())
GUI = HERE / "app" / "gui"
SANDBOX = HERE / "_sandbox"
TITLE = "RPA UI Probe"
PORT = 5174
SLOW = SANDBOX / "ui_slow.json"
SLOW_REL = SLOW.relative_to(REPO).as_posix()
BAD_REL = (SANDBOX / "bad.json").relative_to(REPO).as_posix()

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.FindWindowW.restype = wintypes.HWND

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


class Bridge:
    """`ppx_py.bridge.Bridge.call` 的等价物（参数绑定 + 错误码 + JSON 可序列化）。"""

    def __init__(self) -> None:
        self.methods: dict[str, object] = {}
        self.log: list[str] = []

    def register(self, api: object) -> None:
        for _, member in inspect.getmembers(api, predicate=callable):
            name = getattr(member, "__ppx_api_method__", None)
            if name:
                self.methods[name] = member

    def call(self, method: str, params: object = None, request_id: str | None = None) -> dict:
        self.log.append(method)
        handler = self.methods.get(method)
        if handler is None:
            return {"ok": False, "data": None, "error": {"code": "METHOD_NOT_FOUND", "message": method},
                    "requestId": request_id}
        kwargs = dict(params) if isinstance(params, dict) else {}
        try:
            inspect.signature(handler).bind(**kwargs)
        except TypeError as exc:
            return {"ok": False, "data": None, "error": {"code": "INVALID_PARAMS", "message": str(exc)},
                    "requestId": request_id}
        try:
            data = handler(**kwargs)
            json.dumps(data, allow_nan=False)
        except Exception as exc:  # noqa: BLE001 - 与 bridge 同口径：一律 INTERNAL_ERROR
            return {"ok": False, "data": None,
                    "error": {"code": "INTERNAL_ERROR", "message": f"{type(exc).__name__}: {exc}"},
                    "requestId": request_id}
        return {"ok": True, "data": data, "error": None, "requestId": request_id}


SNAPSHOT = """(() => {
  const log = document.querySelector('#runLog')
  const summary = log.querySelector('.runSummary')
  return JSON.stringify({
    hidden: document.querySelector('#runBox').hidden,
    open: document.querySelector('#runBox').open,
    title: document.querySelector('#runTitle').textContent,
    kinds: [...log.querySelectorAll('.ev .kind')].map((n) => n.textContent),
    rows: log.querySelectorAll('.ev').length,
    nodes: [...log.querySelectorAll('.ev .node')].map((n) => n.textContent),
    summary: summary ? summary.textContent : '',
    summaryBad: !!summary && !!summary.querySelector('.bad'),
    status: document.querySelector('#status').textContent,
    runDisabled: document.querySelector('#runBtn').disabled,
    stopDisabled: document.querySelector('#stopBtn').disabled,
    runningCards: [...document.querySelectorAll('.card.running')].length,
    hasRunBtn: !!document.querySelector('#runBtn'),
    boxTitle: document.querySelector('#runBox').title,
    barHeight: Math.round(document.querySelector('#bar').getBoundingClientRect().height),
  })
})()"""


def serve() -> subprocess.Popen | None:
    """起一个独立的 vite（端口 5174，不碰 `ppx dev` 用的 5173）。"""
    try:
        with urllib.request.urlopen(f"http://localhost:{PORT}/", timeout=1):
            return None  # 已经有人在这端口上服务了
    except Exception:  # noqa: BLE001
        pass
    npm = "npm.cmd" if sys.platform == "win32" else "npm"
    log = (HERE / "_ui_vite.log").open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [npm, "run", "dev", "--", "--port", str(PORT), "--strictPort"],
        cwd=str(GUI),
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://localhost:{PORT}/", timeout=1):
                return proc
        except Exception:  # noqa: BLE001
            time.sleep(0.4)
    proc.terminate()
    raise RuntimeError(f"vite 60s 没起来，看 {HERE / '_ui_vite.log'}")


def wait_js(window, expression: str, expect, timeout: float = 30.0, interval: float = 0.15):
    """反复求值直到 `expect(value)` 成立（返回最后一次的值，不管成不成）。"""
    deadline = time.time() + timeout
    value = None
    while time.time() < deadline:
        value = window.evaluate_js(expression)
        if expect(value):
            return value
        time.sleep(interval)
    return value


def snap(window) -> dict:
    """`evaluate_js` 的返回形态各版本不一：有的把 JS 字符串原样给回，有的先 json.loads。
    两种都吃，避免这一层把「页面其实是对的」判成红。"""
    value = window.evaluate_js(SNAPSHOT)
    return json.loads(value) if isinstance(value, str) else value


def shot(name: str, hwnd: int) -> str:
    from PIL import ImageGrab

    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    path = HERE / f"_ui_{name}.png"
    ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom)).convert("RGB").save(path)
    return path.name


def worker(window, bridge: Bridge) -> None:
    report: dict[str, object] = {}
    try:
        # ---- 首屏：按钮在不在、运行日志是否隐藏 ---------------------------
        cards = wait_js(
            window,
            "document.querySelectorAll('#cards .card').length",
            lambda v: isinstance(v, int) and v > 0,
            timeout=40,
        )
        first = snap(window)
        check("首屏卡片渲染出来了", isinstance(cards, int) and cards > 0, f"{cards} 张卡")
        check("header 里有「▶ 运行」", first["hasRunBtn"] is True)
        check("未运行时运行日志是收起的", first["hidden"] is True, json.dumps(first["title"], ensure_ascii=False))
        check("未运行时停止按钮是禁用的", first["stopDisabled"] is True)

        hwnd = int(user32.FindWindowW(None, TITLE) or 0)
        shots = []
        if hwnd:
            shots.append(shot("1_boot", hwnd))

        # ---- 跑一次 struct（4 ms 跑完，验收口）---------------------------
        window.evaluate_js("document.querySelector('#runBtn').click()")
        settled = wait_js(
            window,
            "document.querySelector('#runLog .runSummary') ? 1 : 0",
            lambda v: v == 1,
            timeout=60,
        )
        done = snap(window)
        check("点「▶ 运行」后日志面板展开", done["hidden"] is False and done["open"] is True)
        check("事件行渲染出来了", done["rows"] >= 2, f"{done['rows']} 行 · {done['kinds']}")
        check("事件标签是中文（不是原始 type 名）",
              all(k in {"运行开始", "运行结束"} for k in done["kinds"]), str(done["kinds"]))
        check("结论块是「成功」样式", done["summaryBad"] is False and "运行成功" in done["summary"],
              done["summary"].splitlines()[0] if done["summary"] else "")
        check("状态栏报出 runId 与返回值", "run-" in done["status"] and "yes" in done["status"], done["status"])
        check("收口后运行按钮恢复可点、停止按钮禁用",
              done["runDisabled"] is False and done["stopDisabled"] is True)
        check("收口后没有卡片还标着「正在跑」", done["runningCards"] == 0)
        check("运行面板的 tooltip 交代了解释器与 cwd",
              ".venv" in done["boxTitle"] and "cwd" in done["boxTitle"],
              done["boxTitle"].replace("\n", " | "))
        # ★ 版式判据：状态栏里那条长路径曾经把按钮挤成两行（header 从 40px 涨到 60px）
        check("状态栏再长也不撑高 header（≤44px）", done["barHeight"] <= 44, f"{done['barHeight']}px")
        if hwnd:
            shots.append(shot("2_struct_done", hwnd))

        # ---- 换成慢流程，验「运行中」这一态 ------------------------------
        window.evaluate_js(
            "(() => { const s = document.querySelector('#flowPicker');"
            f" s.value = {json.dumps(SLOW_REL)};"
            " s.dispatchEvent(new Event('change')); })()"
        )
        wait_js(window, "document.querySelector('#flowTitle').textContent",
                lambda v: isinstance(v, str) and "slow" in v, timeout=20)
        window.evaluate_js("document.querySelector('#runBtn').click()")
        live = wait_js(window, "document.querySelectorAll('.card.running').length",
                       lambda v: isinstance(v, int) and v > 0, timeout=40)
        during = snap(window)
        check("运行中：有卡片被标成正在执行", isinstance(live, int) and live > 0, f"{live} 张卡")
        check("运行中：停止可点、运行禁用",
              during["stopDisabled"] is False and during["runDisabled"] is True)
        check("运行中：日志标题带「运行中」", "运行中" in during["title"], during["title"])
        check("运行中：header 也没被撑高", during["barHeight"] <= 44, f"{during['barHeight']}px")
        if hwnd:
            shots.append(shot("3_running", hwnd))

        # ---- 停止 ---------------------------------------------------------
        window.evaluate_js("document.querySelector('#stopBtn').click()")
        wait_js(window, "document.querySelector('#runLog .runSummary') ? 1 : 0",
                lambda v: v == 1, timeout=30)
        stopped = snap(window)
        check("停止后结论块是「已停止」", "已停止" in stopped["summary"], stopped["summary"].splitlines()[0])
        check("停止后状态栏说「已停止」", "已停止" in stopped["status"], stopped["status"])
        check("停止不会抹掉已产生的事件", stopped["rows"] >= 2, f"{stopped['rows']} 行")
        check("停止后按钮回到常态",
              stopped["runDisabled"] is False and stopped["stopDisabled"] is True)
        check("停止后没有卡片还标着正在跑", stopped["runningCards"] == 0)
        if hwnd:
            shots.append(shot("4_stopped", hwnd))

        # ---- 编译失败也要在界面上分得清 ----------------------------------
        # ★ option 的 value 是**相对路径**，不是列表上显示的 `sandbox/bad` 标签。
        #   写成标签时 `select.value = ...` 会静默变成空串，openFlow 失败，currentPath
        #   还停在上一个流程上 —— 于是「点了运行」其实又跑了一次慢流程，
        #   而判据读到的却是上一条成功数据（第一版就是这么假绿/假红的）。
        window.evaluate_js(
            "(() => { const s = document.querySelector('#flowPicker');"
            f" s.value = {json.dumps(BAD_REL)};"
            " s.dispatchEvent(new Event('change')); })()"
        )
        switched = wait_js(window, "document.querySelector('#flowTitle').textContent",
                           lambda v: isinstance(v, str) and "bad" in v, timeout=20)
        check("切到 bad.json 真的切过去了", isinstance(switched, str) and "bad" in switched, str(switched))
        window.evaluate_js("document.querySelector('#runBtn').click()")
        wait_js(window, "document.querySelector('#runLog .runSummary') ? 1 : 0",
                lambda v: v == 1, timeout=30)
        bad = snap(window)
        check("编译失败：结论块是失败样式且带原因",
              bad["summaryBad"] is True and "Unknown parameter" in bad["summary"],
              bad["summary"].splitlines()[0] if bad["summary"] else "")
        check("编译失败：标题上直接看到 COMPILE_FAILED",
              "COMPILE_FAILED" in bad["title"], bad["title"])
        check("编译失败：标题不是「运行中」", "运行中" not in bad["title"], bad["title"])
        if hwnd:
            shots.append(shot("5_compile_failed", hwnd))

        report["shots"] = shots
        report["rpc"] = {
            "total": len(bridge.log),
            "runCalls": len([m for m in bridge.log if m.startswith("rpa.run") or m == "rpa.startRun"]),
        }
    except Exception as exc:  # noqa: BLE001 - 探针把异常当证据
        check(f"界面探针异常：{type(exc).__name__}", False, str(exc))
    finally:
        print("UI_REPORT " + json.dumps(report, ensure_ascii=False))
        window.destroy()


def main() -> int:
    SANDBOX.mkdir(parents=True, exist_ok=True)
    # 慢流程：4 × 0.6s，跑起来看得见「正在执行」的那一态
    SLOW.write_bytes(
        (
            json.dumps(
                {
                    "schema_version": "1.0",
                    "id": "ui-slow",
                    "name": "ui slow flow",
                    "inputs": {},
                    "root": {
                        "type": "sequence",
                        "id": "root",
                        "children": [
                            {
                                "type": "action",
                                "id": f"step{i}",
                                "command": "workflow.sleep",
                                "with": {"seconds": 0.6},
                            }
                            for i in range(1, 5)
                        ],
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        ).encode("utf-8")
    )
    if not (SANDBOX / "bad.json").is_file():
        print("缺夹具 _sandbox/bad.json —— 先跑一次 exec_probe.py")
        return 2

    sys.path.insert(0, str(HERE / "app"))
    import api.api as A  # noqa: E402

    bridge = Bridge()
    bridge.register(A)

    vite = serve()
    try:
        import webview

        window = webview.create_window(
            TITLE,
            url=f"http://localhost:{PORT}/",
            width=1400,
            height=880,
            x=80,
            y=40,
            js_api=bridge,
        )
        webview.start(lambda w: worker(w, bridge), window)
    finally:
        if vite is not None:
            vite.terminate()

    print()
    if failures:
        print(f"{len(failures)} 项未通过：")
        for label in failures:
            print(f"  - {label}")
        return 1
    print("全部通过：界面层（按钮 / 事件行 / 运行中标出来 / 停止 / 编译失败）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
