"""门禁：编辑窗口 ↔ 会话的「流程路径口径」动态契约（M67）。

背景：会话侧 `studio.py::check_path` 放行绝对路径，窗口侧 `api.py::_flow_path` 只认
相对自己 WORKSPACE 的路径。两边工作区根不同 ⇒ 会话推来的每条绝对路径都越界；裸
`ValueError` 又被 `ppx_py/bridge.py` 吞成 `INTERNAL_ERROR: Python API 执行失败`，
现场只剩一句「对任何文件都失败、没有日志」。M67 把两个根对齐，并把失败改回可行动的
`BAD_PATH`、把异常落到盘上、把原因经 `open-failed` 回给 Agent。

为什么必须动态跑：只扫源码能证明「写了 `_session_root`」，证明不了「中文会话根里的
绝对路径真能打开」「把 BridgeError 换回 ValueError 会不会又变成 INTERNAL_ERROR」
「cmd_gui 注入的 env 值到底是不是那个 root」「open-failed 有没有让 --wait 立刻返回」。

夹具一律落在仓库内 `.harness/spike/`（沙箱不允许 Python 写系统 temp），且**不删**：
删除守卫会按 turn 累计，探针/判据自己做清理容易把整轮留成脏状态。
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import importlib.util
import io
import json
import logging
import os
import sys
import types
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API = ROOT / ".harness" / "demo" / "ppx_probe" / "app" / "api" / "api.py"
MAIN_JS = ROOT / ".harness" / "demo" / "ppx_probe" / "app" / "gui" / "src" / "main.js"
STUDIO = ROOT / "workbuddy-suite" / "skills" / "workflow-studio" / "scripts" / "studio.py"
TMP = ROOT / ".harness" / "spike" / "_m67_contract"

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


def unique_anchor(text: str, anchor: str, label: str) -> None:
    """静态接线闸：锚点必须恰好出现一次，且不落在注释里。"""
    count = text.count(anchor)
    assert_true(count == 1, f"{label} 锚点出现 {count} 次（应为 1）：{anchor!r}")
    line = next(row for row in text.splitlines() if anchor in row)
    assert_true(not line.lstrip().startswith(("//", "#", "*")), f"{label} 只写在注释里")


# ---------------------------------------------------------------------------
# 夹具：一个 WORKSPACE、一个 WORKSPACE 之外的中文会话根、一份 endpoint.json
# ---------------------------------------------------------------------------
WS = TMP / "ws"
CJK = TMP / "中文工作区"
STUDIO_HOME = TMP / "studio"
LOG = TMP / "editor.log"


def prepare() -> None:
    for folder in (WS / "workflows", CJK / "workflows", STUDIO_HOME):
        folder.mkdir(parents=True, exist_ok=True)
    sample = (ROOT / "examples" / "baidu-news-top10" / "workflow.json").read_bytes()
    for target in (WS / "workflows" / "probe.json", CJK / "workflows" / "probe.json"):
        target.write_bytes(sample)
    (STUDIO_HOME / "endpoint.json").write_text(
        json.dumps({"port": 7788, "root": str(CJK)}, ensure_ascii=False), encoding="utf-8"
    )
    os.environ["RPA_WORKSPACE"] = str(WS)
    os.environ["STUDIO_HOME"] = str(STUDIO_HOME)
    os.environ["RPA_EDITOR_LOG"] = str(LOG)


prepare()


def load_python(path: Path, name: str, extra_path: Path | None = None):
    if extra_path is not None and str(extra_path) not in sys.path:
        sys.path.insert(0, str(extra_path))
    spec = importlib.util.spec_from_file_location(name, path)
    assert_true(spec is not None and spec.loader is not None, f"加载不了 {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_api():
    stub = types.ModuleType("ppx_py")

    class BridgeError(Exception):
        def __init__(self, code: str, message: str):
            super().__init__(message)
            self.code = code

    stub.api_method = lambda _name: (lambda fn: fn)
    stub.BridgeError = BridgeError
    sys.modules["ppx_py"] = stub
    return load_python(API, "ppx_probe_api_m67")


api = load_api()
studio = load_python(STUDIO, "workflow_studio_m67", STUDIO.parent)
main_js = MAIN_JS.read_text(encoding="utf-8")
api_src = API.read_text(encoding="utf-8")
studio_src = STUDIO.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 后端：三种路径口径 + 结构化错误
# ---------------------------------------------------------------------------
REL = "workflows/probe.json"
ABS_INSIDE = str(WS / "workflows" / "probe.json")
ABS_CJK = str(CJK / "workflows" / "probe.json")
ABS_OUTSIDE = str(ROOT / "examples" / "baidu-news-top10" / "workflow.json")


def case_relative() -> None:
    target = api._flow_path(REL)
    assert_true(target.is_file(), f"相对路径没落到文件：{target}")
    flow = api.open_flow(REL)
    assert_true(len(flow["cards"]) > 0, "相对路径没摊出卡片")


def case_abs_inside() -> None:
    """会话推来的绝对路径，在根对齐之后必须能打开（M67 的核心修复）。"""
    target = api._flow_path(ABS_INSIDE)
    assert_true(target.is_file(), f"同根绝对路径被拒：{target}")
    flow = api.open_flow(ABS_INSIDE)
    assert_true(len(flow["cards"]) > 0, "同根绝对路径没摊出卡片")


def case_cjk_session_root() -> None:
    """中文不是原因；且证明窗口独立启动时读 endpoint 的 root 这条兜底真的生效。"""
    assert_true(api._session_root() == CJK.resolve(), f"会话根读错了：{api._session_root()}")
    target = api._flow_path(ABS_CJK)
    assert_true("中文" in str(target) and target.is_file(), f"中文会话根被拒：{target}")


def case_outside_is_bad_path() -> None:
    try:
        api._flow_path(ABS_OUTSIDE)
    except Exception as exc:  # noqa: BLE001 - 要检查异常的类型与内容
        kind = type(exc).__name__
        assert_true(kind == "BridgeError", f"抛的是 {kind}，不是 BridgeError")
        code = getattr(exc, "code", None)
        assert_true(code == "BAD_PATH", f"错误码是 {code}")
        message = str(exc)
        for piece in (str(WS), "studio.py serve --root", "studio.py gui --root", "路径越界"):
            assert_true(piece in message, f"原因里缺 {piece!r}：{message}")
        assert_true("INTERNAL_ERROR" not in message, "原因里混进了内部术语")
    else:
        raise AssertionError("工作区外的绝对路径竟然放行了")


def case_missing_is_bad_path() -> None:
    try:
        api._flow_path("workflows/nope.json")
    except Exception as exc:  # noqa: BLE001
        code = getattr(exc, "code", None)
        assert_true(code == "BAD_PATH", f"错误码 {code}")
        assert_true("文件不存在" in str(exc), f"没说清是文件不存在：{exc}")
    else:
        raise AssertionError("不存在的文件竟然放行了")


def bridge_call(handler, params: dict):
    """复刻 `ppx_py/bridge.py::Bridge.call` 的 except 顺序（BridgeError 优先、其余吞掉）。

    门禁环境没有 ppx 运行时；这个等价包装让「把 BridgeError 换回 ValueError」的注入
    能被真实地判红 —— 否则只断言异常类型，证明不了 JS/Agent 侧实际收到的错误码。
    """
    try:
        return {"ok": True, "data": handler(**params)}
    except api.BridgeError as exc:
        return {"ok": False, "error": {"code": exc.code, "message": str(exc)}}
    except Exception:  # noqa: BLE001 - 与真桥一致
        return {"ok": False, "error": {"code": "INTERNAL_ERROR", "message": "Python API 执行失败"}}


def case_bridge_keeps_code() -> None:
    result = bridge_call(api.open_flow, {"path": ABS_OUTSIDE})
    assert_true(result["ok"] is False, "越界竟然成功了")
    error = result.get("error") or {}
    assert_true(error.get("code") == "BAD_PATH", f"桥把原因吞成了 {error}")
    assert_true("studio.py serve --root" in error.get("message", ""), "可照抄的修法没传到桥外")


def case_guard_really_on() -> None:
    """去掉会话根后中文路径必须被拒 —— 证明上面「中文能打开」不是守卫没生效的假绿。"""
    real = api._session_root
    try:
        api._session_root = lambda: None
        try:
            api._flow_path(ABS_CJK)
        except Exception as exc:  # noqa: BLE001
            assert_true(getattr(exc, "code", None) == "BAD_PATH", "拿掉会话根后仍放行")
        else:
            raise AssertionError("拿掉会话根后中文路径仍放行：越界守卫是假的")
    finally:
        api._session_root = real


def case_exception_logged() -> None:
    assert_true(api.EDITOR_LOG is not None, "没装上落盘 handler")
    log_path = Path(api.EDITOR_LOG)
    assert_true(log_path.is_file(), f"日志文件没生成：{log_path}")
    # ★ 日志是滚动追加的：固定哨兵会被上一轮的内容顶替，handler 摘掉也照样绿
    # （本轮负向验证实测抓到过）。用本轮唯一值 + 只看本轮新增字节。
    sentinel = "M67-CONTRACT-EXC-" + uuid.uuid4().hex
    before = log_path.stat().st_size
    logger = logging.getLogger("ppx_py.bridge")
    try:
        raise RuntimeError(sentinel)
    except RuntimeError:
        logger.exception("contract probe")
    delta = log_path.read_bytes()[before:].decode("utf-8", errors="replace")
    assert_true(sentinel in delta, "异常没写进本轮日志")
    assert_true("Traceback" in delta, "本轮日志里没有 traceback")


# ---------------------------------------------------------------------------
# 会话侧：根注入与失败回传（真跑函数，打桩下游）
# ---------------------------------------------------------------------------
class _FakeChild:
    def wait(self) -> int:
        return 0


def run_gui(args: argparse.Namespace, *, endpoint: dict | None = None, health: dict | None = None):
    captured: dict = {}
    real = {
        "Popen": studio.subprocess.Popen,
        "_find_ppx": studio._find_ppx,
        "_editor_exe": studio._editor_exe,
        "_editor_dir": studio._editor_dir,
        "_read_endpoint": studio._read_endpoint,
        "_healthy": studio._healthy,
        "_make_server": studio._make_server,
        "_call": studio._call,
    }

    def fake_popen(argv, cwd=None, env=None):
        captured["argv"] = argv
        captured["env"] = env
        return _FakeChild()

    try:
        studio.subprocess.Popen = fake_popen
        studio._find_ppx = lambda: None
        studio._editor_exe = lambda: Path("C:/fake/RPA Probe.exe")
        studio._editor_dir = lambda: None
        studio._read_endpoint = lambda: endpoint
        studio._healthy = lambda port, timeout=0.5: bool(endpoint)
        studio._make_server = lambda port, root: (None, port)
        studio._call = lambda method, route, payload=None, **kw: health or {}
        with contextlib.redirect_stdout(io.StringIO()):
            rc = studio.cmd_gui(args)
    finally:
        for key, value in real.items():
            if key == "Popen":
                studio.subprocess.Popen = value
            else:
                setattr(studio, key, value)
    return rc, captured


def case_gui_injects_root() -> None:
    saved = os.environ.pop("RPA_WORKSPACE", None)
    try:
        args = argparse.Namespace(
            serve=False, root=str(ROOT / "examples"), port=7788, exe="", skip_frontend=False
        )
        rc, captured = run_gui(args)
        assert_true(rc == 0, f"cmd_gui 返回 {rc}")
        env = captured.get("env") or {}
        expected = str((ROOT / "examples").resolve())
        assert_true(env.get("RPA_WORKSPACE") == expected,
                    f"注入的根不对：{env.get('RPA_WORKSPACE')} != {expected}")
        args_none = argparse.Namespace(
            serve=False, root=None, port=7788, exe="", skip_frontend=False
        )
        _, captured_none = run_gui(args_none)
        assert_true("RPA_WORKSPACE" not in (captured_none.get("env") or {}),
                    "没给 --root 时凭空造了一个工作区根")
    finally:
        if saved is not None:
            os.environ["RPA_WORKSPACE"] = saved


def case_gui_reads_existing_root() -> None:
    args = argparse.Namespace(serve=True, root=None, port=7788, exe="", skip_frontend=False)
    endpoint = {"port": 7788, "root": str(CJK)}
    _, captured = run_gui(args, endpoint=endpoint, health={"root": str(CJK)})
    env = captured.get("env") or {}
    assert_true(env.get("RPA_WORKSPACE") == str(CJK.resolve()),
                f"连已有服务时没对齐它的 root：{env.get('RPA_WORKSPACE')}")


def case_open_wait_reports_failure() -> None:
    """open-failed 是结论，不是「用户没动」：--wait 必须立刻带着 BAD_PATH 返回。"""
    emitted: list[dict] = []
    seen: dict = {}
    real = {"_call": studio._call, "_wait_for": studio._wait_for, "_emit": studio._emit}
    message = "打开流程失败（路径越界）\n用 studio.py serve --root <目录> 重起"

    def fake_wait(timeout, kinds, path):
        seen["kinds"] = set(kinds)
        return {
            "ok": True,
            "events": [{"kind": "open-failed", "code": "BAD_PATH", "message": message}],
            "timedOut": False,
        }

    try:
        studio._call = lambda method, route, payload=None, **kw: {"ok": True, "path": ABS_OUTSIDE}
        studio._wait_for = fake_wait
        studio._emit = lambda payload, pretty: emitted.append(payload)
        args = argparse.Namespace(
            path=ABS_OUTSIDE, reason="", line=0, wait=30.0, pretty=False
        )
        rc = studio.cmd_open(args)
    finally:
        for key, value in real.items():
            setattr(studio, key, value)
    assert_true(rc == 2, f"打开失败竟返回 {rc}（应该 2，而不是等超时）")
    assert_true("open-failed" in seen.get("kinds", set()),
                f"--wait 没在等打开失败：{seen.get('kinds')}")
    assert_true(emitted and emitted[0].get("code") == "BAD_PATH", f"没回传 BAD_PATH：{emitted}")
    assert_true("studio.py serve --root" in emitted[0].get("error", ""), "修法没回给 Agent")


# ---------------------------------------------------------------------------
# 前端与源码接线闸（防「后端改好了、界面仍吞原因」）
# ---------------------------------------------------------------------------
def case_frontend_wiring() -> None:
    unique_anchor(main_js, "return { ok: false, error: failure }", "openFlow 返回结构化失败")
    unique_anchor(main_js, "reason.code = failure?.code || 'INTERNAL_ERROR'", "失败码透传")
    unique_anchor(main_js, "kind: 'open-failed'", "open-failed 事件")
    unique_anchor(main_js, "code: error?.code || null", "事件 payload 带 code")
    assert_true("whiteSpace = 'pre-wrap'" in main_js, "多行修法在界面上会被压成一行")


def case_source_guards() -> None:
    unique_anchor(api_src, "from ppx_py import BridgeError, api_method", "BridgeError 来源")
    tree = ast.parse(api_src)
    function = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_flow_path"
    )
    raises = [node for node in ast.walk(function) if isinstance(node, ast.Raise)]
    assert_true(raises, "_flow_path 里没有 raise，守卫形同虚设")
    for node in raises:
        call = node.exc
        assert_true(
            isinstance(call, ast.Call) and getattr(call.func, "id", None) == "BridgeError",
            "_flow_path 又抛回了裸异常（桥会吞成 INTERNAL_ERROR）",
        )
    unique_anchor(
        studio_src, 'child = subprocess.Popen(argv, cwd=str(workdir), env=child_env)',
        "窗口启动时透传 env",
    )
    unique_anchor(
        studio_src, '{"save", "close", "open-failed"}', "等待集合包含打开失败",
    )


for name, fn in (
    ("相对路径仍按 WORKSPACE 打开", case_relative),
    ("同根绝对路径可打开（会话→窗口）", case_abs_inside),
    ("中文会话根 + 中文绝对路径可打开", case_cjk_session_root),
    ("越界绝对路径返回结构化 BAD_PATH", case_outside_is_bad_path),
    ("文件不存在也返回 BAD_PATH 并说明", case_missing_is_bad_path),
    ("桥等价包装不再吞成 INTERNAL_ERROR", case_bridge_keeps_code),
    ("拿掉会话根后守卫确实会拒（防假绿）", case_guard_really_on),
    ("冻结态异常落盘且带 traceback", case_exception_logged),
    ("cmd_gui 注入同一个 root / 无 root 不造根", case_gui_injects_root),
    ("cmd_gui 连已有服务时对齐它的 root", case_gui_reads_existing_root),
    ("cmd_open --wait 立刻回传 BAD_PATH", case_open_wait_reports_failure),
    ("前端把失败原因回进 open-failed", case_frontend_wiring),
    ("源码接线闸：无裸异常 / env 透传 / 等待集合", case_source_guards),
):
    check(name, fn)

failed = [r for r in results if not r[1]]
for name, ok, detail in results:
    print(f"{'PASS' if ok else 'FAIL'} {name}{'' if ok else f' — {detail}'}")
print(
    f"EDITOR WORKSPACE CONTRACT CHECK {'FAILED' if failed else 'PASSED'} "
    f"({len(results) - len(failed)}/{len(results)})"
)
sys.exit(1 if failed else 0)
