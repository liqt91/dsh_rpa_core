"""门禁：元素编排层的**动态**契约（M68，替换 M66 的六态拾取）。

`scripts/check_element_pick_ui.mjs` 管静态与渲染层；本文件真跑后端的
`resolve_target` / `_pick_plan` / `start_capture` / `capture_poll` / `commit_element`
/ `save_element` / `delete_element`。

为什么必须跑：只扫源码能证明「写了 commit_element」，证明不了「捕获时真的没落盘」
或「中文名真能写成文件」——这两条正是本轮交互改版的落点，也最容易写成恒绿判据。

加载 `api.py` 时给 `ppx_py` 打桩（门禁环境没有 ppx 运行时）；所有 monkeypatch
都在 try/finally 里还原，不污染同进程后续的判据。桌面浮窗一律打桩成 None：
门禁跑起来不该弹窗抢前台。
"""

from __future__ import annotations

import importlib.util
import json
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
    spec = importlib.util.spec_from_file_location("ppx_probe_api_m68", API)
    assert_true(spec is not None and spec.loader is not None, f"加载不了 {API}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


api = load_api()
ONLINE = {"runtime": True, "extensionOnline": True, "browsers": {}, "fix": None}
OFFLINE = {"runtime": True, "extensionOnline": False, "browsers": {}, "fix": "浏览器还没连上"}

FLOW = {
    "schema_version": "1.0",
    "id": "m68-fixture",
    "name": "元素编排夹具",
    "inputs": {},
    "root": {
        "type": "sequence",
        "id": "root",
        "children": [
            {
                "type": "action",
                "id": "openHome",
                "command": "browser.click",
                "with": {},
                "elementRefs": {"selector": "userField"},
            },
            {
                "type": "if",
                "id": "ifNode",
                "condition": {"op": "truthy", "left": "${inputs.keyword}"},
                "then": [
                    {
                        "type": "action",
                        "id": "inThen",
                        "command": "browser.click",
                        "with": {},
                        "elementRefs": {"selector": "userField"},
                    }
                ],
                "else": [],
            },
        ],
    },
}


def case_resolve_command() -> None:
    browser = api.resolve_target("browser.input", ONLINE)
    desktop = api.resolve_target("desktop.click", ONLINE)
    assert_true(
        browser
        == {
            "target": "browser",
            "hover": False,
            "reason": "这一步作用在网页上",
            "decidedBy": "command",
        },
        f"browser 命令被判成 {browser}",
    )
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
    assert_true(
        api.resolve_target("browser.input", ONLINE, "auto")["decidedBy"] == "command",
        "'自动判断'不该变成 override",
    )


def case_capability_snapshot() -> None:
    real_popen, real_argv = api.subprocess.Popen, api._rpa_argv
    payload = {"browsers": {"chrome": {"installed": True, "online": True}}}

    class _FakePopen:
        """按 ``_env_status_json`` 的调用面伪造：stdout 是临时文件，wait 写入并返回 rc。"""

        def __init__(self, args, **kwargs):
            self._out = kwargs["stdout"]

        def wait(self, timeout=None):
            self._out.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            return 0

        def kill(self):
            raise AssertionError("env-status 正常返回不该 kill")

    try:
        api._rpa_argv = lambda: ["fake-rpa-core"]
        api.subprocess.Popen = _FakePopen
        snap = api.capability_snapshot()
        assert_true(snap["runtime"] is True and snap["extensionOnline"] is True, f"{snap}")
        assert_true(snap["fix"] is None, "能力齐全却给了修法")
        payload = {"browsers": {"chrome": {"installed": True, "online": False}}}
        snap = api.capability_snapshot()
        assert_true(snap["extensionOnline"] is False and snap["fix"], f"离线没给修法：{snap}")
        # 提示里不许再出现「手动填写选择器」：那条入口本轮已经删掉，说了也点不到。
        assert_true("手动填写" not in snap["fix"], f"修法还在指一条不存在的入口：{snap['fix']}")
    finally:
        api.subprocess.Popen, api._rpa_argv = real_popen, real_argv
    # 形态闸（2026-11-13 真机事故的回归判据）：env-status 必须走临时文件而不是
    # 管道——shim 被杀后孙进程仍持有管道，收尾读会把处理线程钉死；超时预算必须
    # 容得下热机实测 ~3.6s（3s 预算必然误杀，冷机更久）。
    src = Path(api.__file__).read_text(encoding="utf-8")
    assert_true("capture_output=True" not in src, "又回到管道读 stdout（kill 后会钉死线程）")
    assert_true("TemporaryFile" in src, "env-status 没落临时文件")
    assert_true(
        api._ENV_STATUS_TIMEOUT >= 5.0,
        f"env-status 超时预算 {api._ENV_STATUS_TIMEOUT}s 容不下热机 ~3.6s",
    )


class _Silence:
    """把桌面浮窗全部打成静默，并记录「浮窗里说过什么」。

    门禁不该弹窗抢前台；记录本轮用来断言「删除等业务逻辑**没有**再借浮窗说话」
    （kit 只管捕获/编辑，业务提示归网页端）。
    """

    def __init__(self) -> None:
        self.notices: list[tuple[str, str]] = []
        self.saved: tuple | None = None

    def __enter__(self):
        self.saved = (api._new_float, api._notify)
        notices = self.notices

        class _Win:
            def show_notice(self, title, detail="", on_close=None, auto_hide_ms=0):
                notices.append((title, detail))

            def hide(self):
                pass

        api._new_float = lambda: _Win()
        api._notify = lambda title, detail="", **kw: notices.append((title, detail))
        return self

    def __exit__(self, *exc):
        api._new_float, api._notify = self.saved
        return False


def _patch_offline() -> tuple[tuple, list]:
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
    api._flow_path = lambda path: Path("C:/rpa-m68-fixture/flow.json")
    api.subprocess.Popen = fake_popen
    return real, calls


def case_offline_never_spawns() -> None:
    saved, calls = _patch_offline()
    try:
        with _Silence() as quiet:
            plan = api._pick_plan("demo.json", "userField")
            assert_true(plan["blocked"] is True and plan["fix"], f"离线没被拦：{plan}")
            res = api.start_capture("demo.json", "userField")
            assert_true(res["ok"] is False and res["state"] == "blocked", f"{res}")
            assert_true(res["code"] == "CAPABILITY_OFFLINE", f"错误码={res['code']}")
            assert_true(calls == [], "离线时起了子进程，用户又要空等一分钟")
            assert_true(quiet.notices, "被挡住却没在浮窗里说（面板里不该有提示）")
    finally:
        (api.capability_snapshot, api._scan_missing, api._flow_path, api.subprocess.Popen) = saved


def case_capture_needs_no_name() -> None:
    """点按钮就抓：不传 name 也能起，默认名由后端给（元素1 / 元素2…）。"""
    import tempfile

    real = (
        api.capability_snapshot,
        api._scan_missing,
        api._flow_path,
        api._elements_dir,
        api.CaptureController,
    )
    base = Path(tempfile.mkdtemp(prefix="rpa-m68-"))
    seen: dict[str, object] = {}

    class _Ctl:
        def __init__(self, **kwargs):
            seen["kwargs"] = kwargs

        def start(self):
            return {"ok": True, "offline": False}

        def poll(self):
            return {"state": "pending"}

        def cancel(self):
            seen["cancelled"] = True

    key = ("probe.json", "?")
    try:
        api.capability_snapshot = lambda: dict(ONLINE)
        api._scan_missing = lambda path: []
        api._flow_path = lambda path: base / "workflows" / "probe.json"
        api._elements_dir = lambda path: base / "workflows" / "elements"
        api.CaptureController = _Ctl
        with _Silence():
            res = api.start_capture("probe.json")
        assert_true(res["ok"] is True, f"不传名字就起不来：{res}")
        assert_true(res["name"] is None, f"不该有名字：{res}")
        assert_true(res["suggestedName"] == "元素1", f"默认名不对：{res}")
        assert_true("on_captured" in seen["kwargs"], "没接捕获回调")
        # 编辑框收进 capture_kit：控制器必须接到编辑框三件套（win32 上抓完自己弹框）
        for hook in ("edit_dialog_factory", "edit_model", "on_commit"):
            assert_true(hook in seen["kwargs"], f"控制器没接编辑框注入口：{hook}")
        entry = api._CAPTURES.get(("probe.json", res["captureId"]))
        assert_true(entry is not None, "开始后没有会话记录")
        assert_true(entry["name"] is None and entry["overwrite"] is False, f"{entry}")
        assert_true(entry["captured"] is None, "还没抓到就写进 captured 了")
        api.cancel_capture("probe.json", res["captureId"])
        assert_true(seen.get("cancelled") is True, "取消没传到控制器")
    finally:
        (
            api.capability_snapshot,
            api._scan_missing,
            api._flow_path,
            api._elements_dir,
            api.CaptureController,
        ) = real
        api._CAPTURES.pop(key, None)


class _FakeController:
    def __init__(self, state: str, result: dict | None = None, reason: str = "") -> None:
        self._state = state
        self._result = result
        self._reason = reason

    def poll(self) -> dict:
        if self._state == "ready":
            return {"state": "ready", "result": self._result}
        if self._state in ("offline", "failed"):
            return {"state": self._state, "reason": self._reason}
        if self._state == "committed":
            return {"state": "committed", "name": self._reason or "元素1"}
        return {"state": self._state, "offline": False, "armed": True}

    def cancel(self) -> None:
        self._state = "cancelled"


def case_capture_holds_descriptor_without_writing() -> None:
    """捕获只暂存：poll=ready 时盘上什么都没有，preview 里带默认名。"""
    import tempfile

    real_dir, real_scan = api._elements_dir, api._scan_missing
    with tempfile.TemporaryDirectory(prefix="rpa-m68-") as raw:
        folder = Path(raw) / "elements"
        api._elements_dir = lambda path: folder
        api._scan_missing = lambda path: []
        key = ("demo.json", "c1")
        try:
            entry = {"name": None, "suggestName": "元素1", "nodeId": None, "overwrite": False}
            doc = api._hold_captured(
                entry,
                {
                    "kind": "browser",
                    "selector": {"css": "#q"},
                    "verifyCount": 2,
                    "metadata": {"title": "示例页"},
                },
            )
            assert_true(doc["kind"] == "browser" and doc["selector"] == {"css": "#q"}, f"{doc}")
            assert_true(entry["preview"]["name"] == "元素1", f"{entry['preview']}")
            assert_true(
                entry["preview"]["selector"] == "#q" and entry["preview"]["matches"] == 2,
                f"preview 缺定位/命中数：{entry['preview']}",
            )
            api._CAPTURES[key] = {**entry, "controller": _FakeController("ready", doc)}
            polled = api.capture_poll("demo.json", "c1")
            assert_true(polled["state"] == "ready", f"{polled}")
            assert_true(polled["preview"]["name"] == "元素1", f"{polled}")
            assert_true(not folder.exists() or not list(folder.glob("*.json")), "捕获就落盘了")
        finally:
            api._elements_dir, api._scan_missing = real_dir, real_scan
            api._CAPTURES.pop(key, None)


def case_commit_writes_chinese_name() -> None:
    """用户确认才入库；中文名真能写成 UTF-8 / LF 的文件。"""
    import tempfile

    real_dir = api._elements_dir
    with tempfile.TemporaryDirectory(prefix="rpa-m68-") as raw:
        folder = Path(raw) / "elements"
        api._elements_dir = lambda path: folder
        key = ("demo.json", "c2")
        try:
            entry = {
                "path": "demo.json",
                "name": None,
                "suggestName": "元素1",
                "nodeId": "openHome",
                "overwrite": False,
            }
            doc = api._hold_captured(
                entry, {"kind": "browser", "selector": {"css": "#q"}, "verifyCount": 1}
            )
            api._CAPTURES[key] = {**entry, "captured": doc, "controller": _FakeController("ready")}
            res = api.commit_element("demo.json", "c2", "  搜索框  ")
            assert_true(res["ok"] is True and res["name"] == "搜索框", f"{res}")
            written = folder / "搜索框.json"
            assert_true(written.is_file(), f"没写成中文名文件：{list(folder.iterdir())}")
            body = written.read_bytes()
            assert_true(body.count(b"\r\n") == 0, "写成了 CRLF")
            parsed = json.loads(body.decode("utf-8"))
            assert_true(parsed["name"] == "搜索框", f"名字没写进文档：{parsed}")
            assert_true(parsed["selector"]["css"] == "#q", f"选择器丢了：{parsed}")
            assert_true(api._CAPTURES.get(key) is None, "入库后没释放这次捕获")
        finally:
            api._elements_dir = real_dir
            api._CAPTURES.pop(key, None)


def case_commit_rejects_bad_and_duplicate_names() -> None:
    import tempfile

    real_dir = api._elements_dir
    with tempfile.TemporaryDirectory(prefix="rpa-m68-") as raw:
        folder = Path(raw) / "elements"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "占用.json").write_text("{}", encoding="utf-8")
        api._elements_dir = lambda path: folder
        key = ("demo.json", "c3")
        try:
            entry = {
                "path": "demo.json",
                "name": None,
                "suggestName": "元素1",
                "nodeId": None,
                "overwrite": False,
            }
            doc = api._hold_captured(entry, {"kind": "browser", "selector": {"css": "#q"}})
            api._CAPTURES[key] = {**entry, "captured": doc, "controller": _FakeController("ready")}
            for bad in ("../evil", "a/b", "a.b", "", "  "):
                res = api.commit_element("demo.json", "c3", bad)
                assert_true(res["ok"] is False and res["code"] == "BAD_NAME", (bad, res))
            taken = api.commit_element("demo.json", "c3", "占用")
            assert_true(taken["ok"] is False and taken["code"] == "NAME_TAKEN", f"{taken}")
            assert_true(api._CAPTURES.get(key) is not None, "名字不合法却把捕获丢了")
        finally:
            api._elements_dir = real_dir
            api._CAPTURES.pop(key, None)


def case_commit_needs_a_live_capture() -> None:
    res = api.commit_element("demo.json", "nope", "anything")
    assert_true(res["ok"] is False and res["code"] == "NO_CAPTURE", f"{res}")


def _fixture_flow(raw: str) -> tuple[Path, Path]:
    base = Path(raw) / "workflows"
    base.mkdir(parents=True, exist_ok=True)
    flow = base / "probe.json"
    flow.write_bytes((json.dumps(FLOW, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return base, flow


def case_rename_rewrites_refs() -> None:
    """改名不动引用 = 资产与流程脱钩：盘上有文件、流程还指着旧名。"""
    import tempfile

    real_dir, real_flow = api._elements_dir, api._flow_path
    with tempfile.TemporaryDirectory(prefix="rpa-m68-") as raw:
        base, flow = _fixture_flow(raw)
        api._flow_path = lambda path: flow
        api._elements_dir = lambda path: base / "elements"
        try:
            elem = base / "elements"
            elem.mkdir(parents=True, exist_ok=True)
            (elem / "userField.json").write_text(
                json.dumps({"kind": "browser", "selector": {"css": "#q"}}, ensure_ascii=False),
                encoding="utf-8",
            )
            res = api.save_element("probe.json", "userField", "登录框")
            assert_true(res["ok"] is True and res["name"] == "登录框", f"{res}")
            assert_true((elem / "登录框.json").is_file(), "文件没改名")
            assert_true(not (elem / "userField.json").exists(), "旧文件还在")
            doc = json.loads(flow.read_text(encoding="utf-8"))
            refs = [doc["root"]["children"][0]["elementRefs"]["selector"]]
            refs.append(doc["root"]["children"][1]["then"][0]["elementRefs"]["selector"])
            assert_true(refs == ["登录框", "登录框"], f"引用没跟着改：{refs}")
            assert_true(res["renamedRefs"] == ["inThen", "openHome"], f"{res['renamedRefs']}")
            assert_true(flow.read_bytes().count(b"\r\n") == 0, "流程文件被写成 CRLF")
        finally:
            api._elements_dir, api._flow_path = real_dir, real_flow


def case_rename_rejects_duplicate_and_missing() -> None:
    import tempfile

    real_dir, real_flow = api._elements_dir, api._flow_path
    with tempfile.TemporaryDirectory(prefix="rpa-m68-") as raw:
        base, flow = _fixture_flow(raw)
        api._flow_path = lambda path: flow
        api._elements_dir = lambda path: base / "elements"
        try:
            elem = base / "elements"
            elem.mkdir(parents=True, exist_ok=True)
            for name in ("a", "b"):
                (elem / f"{name}.json").write_text("{}", encoding="utf-8")
            dup = api.save_element("probe.json", "a", "b")
            assert_true(dup["ok"] is False and dup["code"] == "NAME_TAKEN", f"{dup}")
            missing = api.save_element("probe.json", "nope", "c")
            assert_true(missing["ok"] is False and missing["code"] == "NOT_FOUND", f"{missing}")
            same = api.save_element("probe.json", "a", "a")
            assert_true(same["ok"] is True and same["unchanged"] is True, f"{same}")
        finally:
            api._elements_dir, api._flow_path = real_dir, real_flow


def case_delete_guard_returns_refs_without_float() -> None:
    """删除被引用时拒绝，引用节点结构化回给网页端；桌面浮窗不掺和业务提示。"""
    import tempfile

    real_dir, real_flow = api._elements_dir, api._flow_path
    with tempfile.TemporaryDirectory(prefix="rpa-m68-") as raw:
        base, flow = _fixture_flow(raw)
        api._flow_path = lambda path: flow
        api._elements_dir = lambda path: base / "elements"
        try:
            elem = base / "elements"
            elem.mkdir(parents=True, exist_ok=True)
            (elem / "userField.json").write_text("{}", encoding="utf-8")
            with _Silence() as quiet:
                res = api.delete_element("probe.json", "userField")
            assert_true(res["ok"] is False and res["code"] == "ELEMENT_IN_USE", f"{res}")
            assert_true(bool(res.get("referencedBy")), "没把引用节点回给网页端")
            assert_true(
                not quiet.notices, f"kit 越界：删除失败还弹了桌面浮窗 {quiet.notices}"
            )
            assert_true((elem / "userField.json").is_file(), "被引用还删了文件")
        finally:
            api._elements_dir, api._flow_path = real_dir, real_flow


def case_poll_states() -> None:
    """轮询只看后台产出，没有 deadline；离线/失败要把原因送进浮窗（只送一次）。"""
    key = ("demo.json", "c9")
    try:
        for state, expected in (("pending", "pending"), ("ready", "ready"),
                                ("offline", "offline"), ("failed", "failed")):
            with _Silence() as quiet:
                api._CAPTURES[key] = {
                    "controller": _FakeController(state, {"kind": "browser"}, "没收到点选"),
                    "preview": {"name": "元素1"},
                    "notified": False,
                    "nodeId": None,
                }
                res = api.capture_poll("demo.json", "c9")
                assert_true(res["state"] == expected, f"期望 {expected}，得到 {res}")
                if expected in ("offline", "failed"):
                    assert_true(
                        len(quiet.notices) == 1, f"{expected} 没在浮窗里说：{quiet.notices}"
                    )
                    again = api.capture_poll("demo.json", "c9")
                    assert_true(again["state"] == expected, "重复轮询改了状态")
                    assert_true(len(quiet.notices) == 1, "同一次失败在浮窗里说了两遍")
                if expected == "ready":
                    assert_true(res["preview"]["name"] == "元素1", f"{res}")
    finally:
        api._CAPTURES.pop(key, None)


def case_unknown_capture() -> None:
    res = api.capture_poll("demo.json", "ghost")
    assert_true(res["ok"] is False and res["code"] == "UNKNOWN_CAPTURE", f"{res}")
    cancel = api.cancel_capture("demo.json", "ghost")
    assert_true(cancel["ok"] is False and cancel["code"] == "UNKNOWN_CAPTURE", f"{cancel}")


def case_poll_edit_states() -> None:
    """轮询新三态：editing 透传继续等；committed 带名字、会话读后即焚；discarded 同。"""
    key = ("demo.json", "c9")
    try:
        api._CAPTURES[key] = {"name": None, "controller": _FakeController("editing")}
        res = api.capture_poll("demo.json", "c9")
        assert_true(res["ok"] and res["state"] == "editing", f"editing 没透传：{res}")
        assert_true(key in api._CAPTURES, "editing 就把会话烧了")

        api._CAPTURES[key]["controller"] = _FakeController("committed", reason="搜索框")
        res = api.capture_poll("demo.json", "c9")
        assert_true(res["state"] == "committed" and res["name"] == "搜索框", f"{res}")
        assert_true(key not in api._CAPTURES, "committed 后会话没焚")

        api._CAPTURES[key] = {"name": None, "controller": _FakeController("discarded")}
        res = api.capture_poll("demo.json", "c9")
        assert_true(res["state"] == "discarded", f"{res}")
        assert_true(key not in api._CAPTURES, "discarded 后会话没焚")
    finally:
        api._CAPTURES.pop(key, None)


def case_edit_element_desktop_dialog() -> None:
    """详情也走桌面编辑框：弹框带资产全文；改名联动 refs、重名回显、取消落 discarded。"""
    import tempfile

    real = (api._new_edit_dialog, api._elements_dir, api._flow_path)
    with tempfile.TemporaryDirectory(prefix="rpa-m68-edit-") as raw:
        base = Path(raw)
        elements = base / "elements"
        elements.mkdir()
        flow = base / "flow.json"
        (elements / "搜索框.json").write_text(
            json.dumps(
                {"kind": "browser", "selector": {"css": "#q"}, "name": "搜索框"},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        flow.write_text(
            json.dumps(
                {"root": {"children": [{"id": "n1", "elementRefs": {"selector": "搜索框"}}]}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        api._elements_dir = lambda path: elements
        api._flow_path = lambda path: flow
        dialogs: list = []

        class _Dlg:
            def __init__(self):
                self._on_save = None
                self._on_close = None

            def show(self, **kwargs):
                self.kwargs = kwargs
                self._on_save = kwargs.get("on_save")
                self._on_close = kwargs.get("on_close")

            def click_save(self, name, selector=""):
                error = self._on_save(name, selector)
                if error is None:
                    self._on_close(True)
                return error

            def click_cancel(self):
                self._on_close(False)

        def _mk():
            dialog = _Dlg()
            dialogs.append(dialog)
            return dialog

        try:
            api._new_edit_dialog = _mk
            res = api.edit_element("flow.json", "搜索框")
            assert_true(res["ok"] and res["state"] == "editing", f"{res}")
            assert_true("元素详情" in dialogs[0].kwargs["title"], f"{dialogs[0].kwargs}")
            assert_true(dialogs[0].kwargs["name"] == "搜索框", f"{dialogs[0].kwargs}")
            assert_true('"css": "#q"' in dialogs[0].kwargs["meta"], "详情没带资产全文")
            edit_id = res["editId"]

            error = dialogs[0].click_save("登录框", "#new-css")
            assert_true(error is None, f"改名被拦：{error}")
            assert_true((elements / "登录框.json").is_file(), "没改名")
            assert_true(not (elements / "搜索框.json").exists(), "旧名还在")
            doc = json.loads(flow.read_text(encoding="utf-8"))
            assert_true(
                doc["root"]["children"][0]["elementRefs"]["selector"] == "登录框",
                "详情框改名没联动 refs",
            )
            # 影刀式：详情框里改的不只是名字，定位也要落盘
            saved = json.loads((elements / "登录框.json").read_text(encoding="utf-8"))
            assert_true(saved["selector"]["css"] == "#new-css", f"定位没改：{saved}")
            poll = api.capture_poll("flow.json", edit_id)
            assert_true(poll["state"] == "committed" and poll["name"] == "登录框", f"{poll}")
            assert_true(("flow.json", edit_id) not in api._CAPTURES, "committed 后会话没焚")

            # 重名：错误回显、会话仍 editing；取消 → discarded
            (elements / "占位.json").write_text("{}", encoding="utf-8")
            res2 = api.edit_element("flow.json", "登录框")
            error2 = dialogs[1].click_save("占位", "#new-css")
            assert_true(error2 and "占位" in error2, f"重名没回显：{error2}")
            poll2 = api.capture_poll("flow.json", res2["editId"])
            assert_true(poll2["state"] == "editing", f"重名后不该变终态：{poll2}")
            dialogs[1].click_cancel()
            poll3 = api.capture_poll("flow.json", res2["editId"])
            assert_true(poll3["state"] == "discarded", f"{poll3}")

            missing = api.edit_element("flow.json", "幽灵")
            assert_true(missing["ok"] is False and missing["code"] == "NOT_FOUND", f"{missing}")
        finally:
            api._new_edit_dialog, api._elements_dir, api._flow_path = real
            for k in list(api._CAPTURES):
                if k[0] == "flow.json":
                    api._CAPTURES.pop(k, None)

    # 非 Windows 兜底：框不可用要回明确的 fallback 码（前端据此弹页面框）
    real_new = api._new_edit_dialog
    try:
        api._new_edit_dialog = lambda: None
        res = api.edit_element("flow.json", "x")
        assert_true(
            res["ok"] is False and res["code"] == "EDIT_DIALOG_UNAVAILABLE", f"{res}"
        )
    finally:
        api._new_edit_dialog = real_new


def case_selector_edit_validation() -> None:
    """选择器编辑的校验与写回（影刀式：定位可改，但非法的不放行、不落半成品）。"""
    browser_doc = {"kind": "browser", "selector": {"css": "#q"}}
    empty = api._selector_edit_error(browser_doc, "")
    assert_true(empty == "选择器不能为空", f"空选择器没拦：{empty}")
    newline = api._selector_edit_error(browser_doc, "a\nb")
    assert_true(newline is not None, "换行没拦")
    fine = api._selector_edit_error(browser_doc, "#new")
    assert_true(fine is None, f"合法选择器被拦：{fine}")
    api._apply_selector_edit(browser_doc, "  #new  ")
    assert_true(browser_doc["selector"]["css"] == "#new", f"{browser_doc}")

    desktop_doc = {"kind": "desktop", "selector": {"locator": {"path": []}}}
    assert_true(api._selector_edit_error(desktop_doc, "not json") is not None, "坏 JSON 没拦")
    assert_true(api._selector_edit_error(desktop_doc, '{"x": 1}') is not None, "缺 locator 没拦")
    good = '{"locator": {"path": [{"name": "确定"}]}}'
    good_err = api._selector_edit_error(desktop_doc, good)
    assert_true(good_err is None, f"合法桌面定位被拦：{good_err}")
    api._apply_selector_edit(desktop_doc, good)
    assert_true(desktop_doc["selector"]["locator"]["path"][0]["name"] == "确定", f"{desktop_doc}")

    # 确认框的保存链：改过的定位必须写进入库文件
    import tempfile

    real_dir = api._elements_dir
    with tempfile.TemporaryDirectory(prefix="rpa-m68-sel-") as raw:
        folder = Path(raw) / "elements"
        api._elements_dir = lambda path: folder
        key = ("demo.json", "c7")
        try:
            entry = {
                "path": "demo.json",
                "name": None,
                "suggestName": "元素1",
                "nodeId": None,
                "overwrite": False,
            }
            api._hold_captured(entry, {"kind": "browser", "selector": {"css": "#q"}})
            api._CAPTURES[key] = {**entry, "controller": _FakeController("editing")}
            bad = api._commit_from_dialog(api._CAPTURES[key], "搜索框", "  ")
            assert_true(bad == "选择器不能为空", f"空定位没拦：{bad}")
            ok = api._commit_from_dialog(api._CAPTURES[key], "搜索框", "  #better ")
            assert_true(ok is None, f"合法定位被拦：{ok}")
            written = json.loads((folder / "搜索框.json").read_text(encoding="utf-8"))
            assert_true(written["selector"]["css"] == "#better", f"定位没入库：{written}")

            # 影刀式层级接线：browser 且 selector.path 存在 ⇒ 编辑模型给层级；
            # 没有 path ⇒ 退化为文本编辑（简式形态），不许给空树。
            with_path = {
                "kind": "browser",
                "selector": {
                    "css": "#q",
                    "path": [
                        {"tag": "span", "id": None, "classes": ["t"],
                         "nthOfType": None, "fragment": "span.t"},
                    ],
                },
            }
            api._CAPTURES[key]["captured"] = with_path
            model = api._capture_edit_model(api._CAPTURES[key])
            assert_true("levels" in model, f"有 path 却没给层级模型：{model}")
            assert_true(model["levels"][0]["classes"][0]["value"] == "t", f"{model['levels']}")
            no_path = dict(with_path)
            no_path["selector"] = {"css": "#q"}
            api._CAPTURES[key]["captured"] = no_path
            model2 = api._capture_edit_model(api._CAPTURES[key])
            assert_true(
                "levels" not in model2 and model2.get("selector"),
                f"无 path 没退化：{model2}",
            )
        finally:
            api._elements_dir = real_dir
            api._CAPTURES.pop(key, None)


def case_all_refs_are_structured() -> None:
    """引用清单必须带 param/name：只有 nodeId 时网页端说不清引用在哪个参数上。

    ref-only 下被引用参数不在 with 里，界面显示「未设置」是正常形态；删除提示
    只给「第 N 步」会指到一条用户看不见的引用，正是本次报的问题。
    """
    import tempfile

    real_flow = api._flow_path
    with tempfile.TemporaryDirectory(prefix="rpa-m72-refs-") as raw:
        _, flow = _fixture_flow(raw)
        api._flow_path = lambda path: flow
        try:
            all_refs = api._all_element_refs("probe.json")
            assert_true(len(all_refs) == 2, f"引用条数不对：{all_refs}")
            assert_true(
                {r["nodeId"] for r in all_refs} == {"openHome", "inThen"},
                f"没递归到 if.then：{all_refs}",
            )
            for ref in all_refs:
                assert_true(
                    set(ref) == {"nodeId", "param", "name", "command"},
                    f"引用字段不全：{ref}",
                )
                assert_true(ref["param"] == "selector" and ref["name"] == "userField", f"{ref}")
            hits = api._element_references("probe.json", "userField")
            assert_true(len(hits) == 2, f"删除护栏漏了引用：{hits}")
            assert_true(
                api._element_references("probe.json", "ghost") == [],
                "幽灵资产被判成有引用",
            )
        finally:
            api._flow_path = real_flow


def case_unbind_ref_writes_back() -> None:
    """网页端「解除引用 → 保存」真落盘；不传 element_refs 不许动引用。"""
    import tempfile

    class _Manifest:
        input_schema = {
            "properties": {"selector": {"type": "string"}, "sessionId": {"type": "string"}}
        }

    real = (api._flow_path, api._compile, api.open_flow, api._catalog_snapshot)
    with tempfile.TemporaryDirectory(prefix="rpa-m72-unbind-") as raw:
        _, flow = _fixture_flow(raw)
        api._flow_path = lambda path: flow
        api._compile = lambda doc: {
            "ok": True, "digest": "x", "capabilities": [], "workflowId": "w",
        }
        api.open_flow = lambda path: {"cards": [], "sha": "x"}
        api._catalog_snapshot = lambda: {"browser.click": _Manifest()}
        params = {"selector": "", "sessionId": ""}
        try:
            kept = api.save_node_params("probe.json", "openHome", params)
            assert_true(kept["ok"] is True, f"不传 element_refs 却保存失败：{kept}")
            assert_true(
                len(api._element_references("probe.json", "userField")) == 2,
                "没传 element_refs 却把引用改了",
            )
            unbound = api.save_node_params("probe.json", "openHome", params, element_refs={})
            assert_true(unbound["ok"] is True, f"解除引用写回失败：{unbound}")
            doc = json.loads(flow.read_text(encoding="utf-8"))
            assert_true(
                "elementRefs" not in doc["root"]["children"][0],
                f"element_refs={{}} 没把键删掉：{doc['root']['children'][0]}",
            )
            assert_true(
                doc["root"]["children"][1]["then"][0]["elementRefs"]["selector"] == "userField",
                "解除一个节点把别的节点引用也清了",
            )
            assert_true(
                len(api._element_references("probe.json", "userField")) == 1,
                "解除后删除护栏没跟着少一条",
            )
        finally:
            (api._flow_path, api._compile, api.open_flow, api._catalog_snapshot) = real


for name, fn in (
    ("命令类型决定唯一 target", case_resolve_command),
    ("缺命令时按能力真值收敛", case_resolve_capability),
    ("覆盖只本次生效、auto 不越权", case_resolve_override),
    ("能力快照解析在线/离线与修法", case_capability_snapshot),
    ("能力离线：不 spawn、返回 blocked", case_offline_never_spawns),
    ("点击即抓：不需要预填名字", case_capture_needs_no_name),
    ("捕获只暂存：ready 时盘上为空", case_capture_holds_descriptor_without_writing),
    ("确认入库：中文名写成 UTF-8/LF", case_commit_writes_chinese_name),
    ("入库拒绝非法名与重名", case_commit_rejects_bad_and_duplicate_names),
    ("没有捕获就不能入库", case_commit_needs_a_live_capture),
    ("改名联动流程引用", case_rename_rewrites_refs),
    ("改名拒绝重名/不存在/同名为空操作", case_rename_rejects_duplicate_and_missing),
    ("删除被引用：回引用节点、不弹桌面浮窗", case_delete_guard_returns_refs_without_float),
    ("轮询状态机：pending/ready/offline/failed", case_poll_states),
    ("未知捕获会话有明确错误码", case_unknown_capture),
    ("轮询新三态：editing/committed/discarded", case_poll_edit_states),
    ("详情走桌面编辑框：改名/重名/取消", case_edit_element_desktop_dialog),
    ("选择器编辑：校验、写回、入库", case_selector_edit_validation),
    ("引用清单结构化：nodeId+param+name+递归分支", case_all_refs_are_structured),
    ("解除引用随保存写回，不传则不动引用", case_unbind_ref_writes_back),
):
    check(name, fn)

failed = [r for r in results if not r[1]]
for name, ok, detail in results:
    print(f"{'PASS' if ok else 'FAIL'} {name}{'' if ok else f' — {detail}'}")
print(
    f"ELEMENT PICK CONTRACT CHECK {'FAILED' if failed else 'PASSED'} "
    f"({len(results) - len(failed)}/{len(results)})"
)
sys.exit(1 if failed else 0)
