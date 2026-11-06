"""门禁：元素拾取编排层的**动态**契约（M66）。

`scripts/check_element_pick_ui.mjs` 管静态与渲染层；本文件真跑后端的
`resolve_target` / `_pick_plan` / `start_capture` / `capture_poll` / `write_element`。
为什么必须跑：只扫源码能证明「写了 `resolve_target`」，证明不了「忽略命令类型时
它会不会照样返回 browser」——这正是假绿灯最容易藏的地方。

加载 `api.py` 时给 `ppx_py` 打桩（门禁环境没有 ppx 运行时）；所有 monkeypatch
都在 try/finally 里还原，不污染同进程后续的判据。
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API = ROOT / ".harness" / "demo" / "ppx_probe" / "app" / "api" / "api.py"

results: list[tuple[str, bool, str]] = []


def check(name: str, fn) -> None:
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 - 判据要把任何异常都记成红
        results.append((name, False, f"{type(exc).__name__}: {exc}"))
    else:
        results.append((name, True, ""))


def assert_true(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def load_api():
    stub = types.ModuleType("ppx_py")

    class BridgeError(Exception):
        def __init__(self, code: str, message: str):
            super().__init__(message)
            self.code = code

    stub.api_method = lambda _name: (lambda fn: fn)
    stub.BridgeError = BridgeError
    sys.modules["ppx_py"] = stub
    spec = importlib.util.spec_from_file_location("ppx_probe_api_m66", API)
    assert_true(spec is not None and spec.loader is not None, f"加载不了 {API}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


api = load_api()
ONLINE = {"runtime": True, "extensionOnline": True, "browsers": {}, "fix": None}
OFFLINE = {"runtime": True, "extensionOnline": False, "browsers": {}, "fix": "浏览器还没连上"}


def case_resolve_command() -> None:
    browser = api.resolve_target("browser.input", ONLINE)
    desktop = api.resolve_target("desktop.click", ONLINE)
    assert_true(browser == {
        "target": "browser", "hover": False, "reason": "这一步作用在网页上", "decidedBy": "command",
    }, f"browser 命令被判成 {browser}")
    assert_true(desktop["target"] == "desktop" and desktop["hover"] is True, f"desktop={desktop}")
    assert_true(desktop["decidedBy"] == "command", "桌面腿的决策依据丢了")


def case_resolve_capability() -> None:
    online = api.resolve_target(None, ONLINE)
    offline = api.resolve_target("unknown.thing", OFFLINE)
    assert_true(online["target"] == "browser" and online["decidedBy"] == "capability", f"{online}")
    assert_true(offline["target"] == "desktop" and offline["hover"] is True, f"{offline}")


def case_resolve_override() -> None:
    forced = api.resolve_target("browser.input", ONLINE, "desktop")
    assert_true(forced["target"] == "desktop" and forced["decidedBy"] == "override", f"{forced}")
    assert_true(api.resolve_target("browser.input", ONLINE, "auto")["decidedBy"] == "command",
                "'自动判断'不该变成 override")


def case_capability_snapshot() -> None:
    real_run, real_argv = api.subprocess.run, api._rpa_argv
    payload = {"browsers": {"chrome": {"installed": True, "online": True}}}
    try:
        api._rpa_argv = lambda: ["fake-rpa-core"]
        api.subprocess.run = lambda *a, **k: subprocess.CompletedProcess(
            a[0], 0, json.dumps(payload, ensure_ascii=False), ""
        )
        snap = api.capability_snapshot()
        assert_true(snap["runtime"] is True and snap["extensionOnline"] is True, f"{snap}")
        assert_true(snap["fix"] is None, "能力齐全却给了修法")
        payload = {"browsers": {"chrome": {"installed": True, "online": False}}}
        snap = api.capability_snapshot()
        assert_true(snap["extensionOnline"] is False and snap["fix"], f"离线没给修法：{snap}")
    finally:
        api.subprocess.run, api._rpa_argv = real_run, real_argv


def _patch_offline():
    """把能力真值换成离线，并记下 spawn 是否发生。"""
    real = (api.capability_snapshot, api._scan_missing, api._flow_path, api.subprocess.Popen)
    calls: list[list[str]] = []

    def fake_popen(argv, *a, **k):
        calls.append(argv)
        raise AssertionError("离线时居然 spawn 了")

    api.capability_snapshot = lambda: dict(OFFLINE)
    api._scan_missing = lambda path: [
        {"name": "userField", "command": "browser.input", "nodeId": "n1"}
    ]
    api._flow_path = lambda path: Path("C:/rpa-m66-fixture/flow.json")
    api.subprocess.Popen = fake_popen
    return real, calls


def case_offline_never_spawns() -> None:
    saved, calls = _patch_offline()
    try:
        plan = api._pick_plan("demo.json", "userField")
        assert_true(plan["blocked"] is True and plan["fix"], f"离线没被拦：{plan}")
        res = api.start_capture("demo.json", "userField")
        assert_true(res["ok"] is False and res["state"] == "blocked", f"{res}")
        assert_true(res["code"] == "CAPABILITY_OFFLINE", f"错误码={res['code']}")
        assert_true(calls == [], "离线时起了子进程，用户又要空等一分钟")
    finally:
        (api.capability_snapshot, api._scan_missing, api._flow_path, api.subprocess.Popen) = saved


def case_offline_with_override_still_blocked() -> None:
    saved, calls = _patch_offline()
    try:
        res = api.start_capture("demo.json", "userField", "browser")
        assert_true(res["ok"] is False and not calls, f"强制网页腿绕过了能力闸：{res}")
    finally:
        (api.capability_snapshot, api._scan_missing, api._flow_path, api.subprocess.Popen) = saved


class FakeProc:
    def __init__(self, alive: bool = True, code: int | None = None):
        self.alive = alive
        self.code = code
        self.returncode = code

    def poll(self):
        return None if self.alive else self.code


def case_capture_poll_states() -> None:
    real_captures, real_dir = api._CAPTURES, api._elements_dir
    tmp = Path(sys.executable).parent  # 不写盘：用不存在的目录保证 captured=False
    api._elements_dir = lambda path: tmp / "__m66_no_such_dir__"
    key = ("demo.json", "userField")
    try:
        for state, alive, code, deadline, cancelled in (
            ("waiting", True, None, api.time.time() + 60, False),
            ("timeout", False, 3, api.time.time() - 1, False),
            ("failed", False, 1, api.time.time() + 60, False),
            ("cancelled", False, -9, api.time.time() + 60, True),
        ):
            api._CAPTURES[key] = {
                "name": "userField", "path": "demo.json", "proc": FakeProc(alive, code),
                "deadlineAt": deadline, "cancelled": cancelled, "nodeId": "n1",
                "decision": {"target": "browser"}, "stdout": [], "stderr": [],
            }
            res = api.capture_poll("demo.json", "userField")
            assert_true(res["state"] == state, f"期望 {state}，得到 {res['state']}")
            assert_true("reason" in res and "deadlineAt" in res, f"状态契约缺字段：{res}")
    finally:
        api._CAPTURES, api._elements_dir = real_captures, real_dir


def case_write_element() -> None:
    import tempfile

    real_dir, real_scan = api._elements_dir, api._scan_missing
    with tempfile.TemporaryDirectory(prefix="rpa-m66-") as raw:
        folder = Path(raw)
        api._elements_dir = lambda path: folder
        api._scan_missing = lambda path: [{"name": "userField", "nodeId": "n1"}]
        try:
            bad = api.write_element("demo.json", "../evil", "#x")
            assert_true(bad["ok"] is False, "路径穿越没拦住")
            ok = api.write_element("demo.json", "userField", "#username")
            assert_true(ok["ok"] is True and ok["boundNodeId"] == "n1", f"{ok}")
            doc = json.loads((folder / "userField.json").read_text(encoding="utf-8"))
            assert_true(
                doc["selector"] == {"css": "#username"} and doc["kind"] == "browser",
                f"{doc}",
            )
            assert_true((folder / "userField.json").read_bytes().count(b"\r\n") == 0, "写成了 CRLF")
        finally:
            api._elements_dir, api._scan_missing = real_dir, real_scan


for name, fn in (
    ("命令类型决定唯一 target", case_resolve_command),
    ("缺命令时按能力真值收敛", case_resolve_capability),
    ("覆盖只本次生效、auto 不越权", case_resolve_override),
    ("能力快照解析在线/离线与修法", case_capability_snapshot),
    ("能力离线：不 spawn、返回 blocked", case_offline_never_spawns),
    ("覆盖为网页腿也不能绕过能力闸", case_offline_with_override_still_blocked),
    ("轮询状态机：waiting/timeout/failed/cancelled", case_capture_poll_states),
    ("手填元素：拒绝路径穿越、schema 与 LF", case_write_element),
):
    check(name, fn)

failed = [r for r in results if not r[1]]
for name, ok, detail in results:
    print(f"{'PASS' if ok else 'FAIL'} {name}{'' if ok else f' — {detail}'}")
print(f"ELEMENT PICK CONTRACT CHECK {'FAILED' if failed else 'PASSED'} "
      f"({len(results) - len(failed)}/{len(results)})")
sys.exit(1 if failed else 0)
