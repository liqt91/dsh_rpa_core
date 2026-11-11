"""PPX 原型编辑器的运行期桥（ADR 0022-R1：编辑面松绑，运行仍走文件契约）。

做什么：
- 注入与 ``ppx_py/bridge.py`` 语义一致的替身（`api_method` 打标 + `BridgeError`
  + 参数绑定 + ``{ok,data,error,requestId}`` 信封），然后按路径 import 原型的
  ``app/api/api.py``，把打了标记的函数收进 registry。
- 工作台适配（**不改原型本体**，全在这层）：
  * ``RPA_WORKSPACE`` 指到 ``apps/xhs-workbench``：bootstrap 扫到的就是本目录的
    ``workflows/``，元素资产目录（``<flowDir>/elements``）也随之对齐；
  * ``rpa.bootstrap`` 的 flows 追加扁平布局的 ``workflows/*.json``（原型只扫
    ``*/workflow.json``），且排在最前——编辑器默认打开小红书主流程；
  * ``xhs.createFlow``：新建流程骨架（原型没有「新建」）；
  * ``xhs.activeRun`` / ``xhs.pauseRun`` / ``xhs.resumeRun``：对原型 ``_runs``
    里的活跃 run 原子写 ``control.json``（暂停），resume 时 spawn
    ``rpa-core resume`` 并把新进程塞回原 entry——runPoll 是文件驱动的
    （events.jsonl/result.json 在同一 run 目录续写），界面无感知。

不做：不改原型任何文件；不装 ppx_py；不碰 ``_flows`` 的 sha 乐观锁语义。
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import logging
import os
import re
import subprocess
import sys
import threading
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parents[1]
PPX_API_PATH = REPO_ROOT / ".harness" / "demo" / "ppx_probe" / "app" / "api" / "api.py"
PPX_DIST = REPO_ROOT / ".harness" / "demo" / "ppx_probe" / "app" / "gui" / "dist"
XHS_WORKFLOWS = APP_ROOT / "workflows"


# --------------------------------------------------------------------------- #
# 假 ppx_py：只复刻桥边界（事实源：ppx_py/bridge.py），不是 PPX 本体
# --------------------------------------------------------------------------- #


class BridgeError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _api_method(name: str):
    if not name or "." not in name:
        raise ValueError("PPX API 名称必须包含命名空间，例如 user.greet")

    def decorate(function):
        function.__ppx_api_method__ = name
        return function

    return decorate


def _install_fake_ppx_py() -> None:
    if "ppx_py" in sys.modules:
        return
    import types

    fake = types.ModuleType("ppx_py")
    fake.BridgeError = BridgeError
    fake.api_method = _api_method
    sys.modules["ppx_py"] = fake


# --------------------------------------------------------------------------- #
# api.py 装载与 RPC 分派
# --------------------------------------------------------------------------- #

_api = None
_api_lock = threading.Lock()
# 编辑器 run 的落点。原型默认是 ``<WORKSPACE>/_sandbox``；工作台把它收到 data root 下，
# 保证「读侧（流程页列 run）」和「写侧（编辑器起 run）」用的是同一个根。
_sandbox_root: Path | None = None


def configure(data_root: str | Path) -> None:
    """指定编辑器 run 的 sandbox 根（``<data_root>/_sandbox``）。可重复调用，幂等。"""
    global _sandbox_root
    _sandbox_root = Path(data_root).resolve() / "_sandbox"
    if _api is not None:
        _apply_sandbox(_api)


def _apply_sandbox(api: Any) -> None:
    """把原型的 SANDBOX/RUNS 指到配置的根（原型在模块顶层算过一次，这里覆写）。"""
    if _sandbox_root is None:
        return
    api.SANDBOX = _sandbox_root
    api.RUNS = _sandbox_root / "runs"


def runs_root(data_root: str | Path) -> Path:
    return Path(data_root).resolve() / "_sandbox" / "runs"


def _load_api():
    """单例装载原型 api.py。RPA_WORKSPACE 必须在 import 前设好（模块顶层读它）。"""
    global _api
    if _api is not None:
        return _api
    with _api_lock:
        if _api is not None:
            return _api
        if not PPX_API_PATH.is_file():
            raise RuntimeError(f"PPX 原型不在：{PPX_API_PATH}")
        _install_fake_ppx_py()
        # 工作台进程独占这个变量：元素资产按 <flowDir>/elements 解析，workspace 必须是
        # apps/xhs-workbench，setdefault 会被外部环境变量顶歪（元素引用静默解析不到）。
        os.environ["RPA_WORKSPACE"] = str(APP_ROOT)
        src = str(REPO_ROOT / "src")
        if src not in sys.path:
            sys.path.insert(0, src)
        spec = importlib.util.spec_from_file_location("xhs_ppx_api", PPX_API_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _apply_sandbox(module)
        _api = module
    return _api


def _registry() -> dict[str, Any]:
    api = _load_api()
    out: dict[str, Any] = {}
    for _, member in inspect.getmembers(api, predicate=callable):
        name = getattr(member, "__ppx_api_method__", None)
        if name:
            out[name] = member
    out.update(_xhs_methods())
    if _recorder is not None and "rpa.runPoll" in out:
        out["rpa.runPoll"] = _wrap_poll(out["rpa.runPoll"])
    return out


# --------------------------------------------------------------------------- #
# 终态落库钩子：编辑器里跑的 run 也要进快照/告警/监控池
#
# 为什么必须在桥层做：批量采集走 src.server.run_collection（跑完直接落库），
# 而 PPX 编辑器的运行通道是自己 spawn 子进程 + 轮询文件，**不经过 run_collection**。
# 不补这一层，用户在编辑器里跑完，商品库/告警/监控池一个字都不会变——
# 同一件事两条产出路径却又只接了一条（本项目反复踩的坑）。
# --------------------------------------------------------------------------- #

_recorder: Any = None
_recorded: set[str] = set()


def set_recorder(recorder: Any) -> None:
    """注册「run 终态 → 落库」回调：``recorder(flow_path, result_path) -> dict``。"""
    global _recorder
    _recorder = recorder
    _recorded.clear()


def _wrap_poll(poll: Any) -> Any:
    def wrapper(run_id: str, since: int = 0) -> dict[str, Any]:
        payload = poll(run_id, since)
        try:
            _record_if_finished(run_id, payload)
        except Exception:  # noqa: BLE001 - 落库失败绝不能让界面轮询断掉
            LOGGER.exception("record editor run failed: runId=%s", run_id)
        return payload

    return wrapper


def _record_if_finished(run_id: str, payload: Any) -> None:
    if _recorder is None or not isinstance(payload, dict) or not payload.get("ok"):
        return
    if payload.get("running") or payload.get("cancelled") or run_id in _recorded:
        return
    real = payload.get("realRunId")
    artifacts = payload.get("artifacts")
    if not real or not artifacts:
        return
    result_path = Path(artifacts) / str(real) / "result.json"
    if not result_path.is_file():
        return
    entry = _runs().get(run_id) or {}
    flow_path = str(entry.get("path") or "")
    outcome = _recorder(flow_path, result_path)
    # paused 是「暂时收口」，resume 后同一个 run 还会再终态一次；不记账，等真正的终态。
    if isinstance(outcome, dict) and outcome.get("paused"):
        return
    _recorded.add(run_id)


def dispatch(method: str, params: Any = None, request_id: str | None = None) -> dict[str, Any]:
    """复刻 Bridge.call 的分派语义与信封（ppx-js 在 JS 侧按这个信封拆包）。"""
    request_id = request_id or uuid.uuid4().hex
    try:
        handler = _registry().get(method)
        if handler is None:
            raise BridgeError("METHOD_NOT_FOUND", f"未注册的 API: {method}")
        if params is None:
            arguments, keywords = (), {}
        elif isinstance(params, Mapping):
            arguments, keywords = (), dict(params)
        elif isinstance(params, Sequence) and not isinstance(params, (str, bytes, bytearray)):
            arguments, keywords = tuple(params), {}
        else:
            raise BridgeError("INVALID_PARAMS", "params 必须是对象、数组或 null")
        try:
            inspect.signature(handler).bind(*arguments, **keywords)
        except TypeError as exc:
            raise BridgeError("INVALID_PARAMS", str(exc)) from exc
        data = handler(*arguments, **keywords)
        try:
            json.dumps(data, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise BridgeError("INVALID_RESULT", "返回值必须是可序列化的 JSON 数据") from exc
        return {"ok": True, "data": data, "error": None, "requestId": request_id}
    except BridgeError as exc:
        return _failure(exc.code, str(exc), request_id)
    except Exception:
        LOGGER.exception("xhs ppx rpc failed: method=%s requestId=%s", method, request_id)
        return _failure("INTERNAL_ERROR", "Python API 执行失败", request_id)


def _failure(code: str, message: str, request_id: str) -> dict[str, Any]:
    return {
        "ok": False,
        "data": None,
        "error": {"code": code, "message": message},
        "requestId": request_id,
    }


# --------------------------------------------------------------------------- #
# 工作台适配方法（xhs.* 命名空间，不碰 rpa.* 的原型语义）
# --------------------------------------------------------------------------- #


def _xhs_methods() -> dict[str, Any]:
    return {
        "xhs.createFlow": _create_flow,
        "xhs.activeRun": _active_run,
        "xhs.pauseRun": _pause_run,
        "xhs.resumeRun": _resume_run,
        # studio.* 是「Agent 把流程推给窗口」的可选会话通道，由桌面宿主的 launcher
        # 提供，浏览器里没有对应物。给出「明确离线」而不是 METHOD_NOT_FOUND：
        # 前者是事实（没有 Studio 在推），后者会让前端每轮轮询都抛一次异常。
        "studio.pollSession": _studio_offline,
        "studio.notify": _studio_offline,
    }


def _studio_offline(since: int = 0, kind: str = "", payload: Any = None) -> dict[str, Any]:
    return {"ok": True, "online": False, "cursor": int(since or 0), "requests": []}


def bootstrap_with_xhs_flows() -> dict[str, Any]:
    """原型 bootstrap + 追加工作台扁平布局的流程，且排最前（编辑器默认打开它）。"""
    api = _load_api()
    data = api.bootstrap()
    flows = data.get("flows") or []
    ours = [
        {"path": f"workflows/{wf.name}", "label": f"workflows/{wf.stem}"}
        for wf in sorted(XHS_WORKFLOWS.glob("*.json"))
    ]
    known = {f["path"] for f in flows}
    data["flows"] = [f for f in ours if f["path"] not in known] + flows
    return data


def _slug(name: str) -> str:
    cleaned = re.sub(r"[^\w一-鿿-]+", "-", name.strip()).strip("-").lower()
    return cleaned or "untitled"


def _create_flow(name: str) -> dict[str, Any]:
    """新建流程骨架到 workflows/<slug>.json（扁平布局，与主流程同构）。"""
    name = (name or "").strip()
    if not name:
        raise BridgeError("INVALID_PARAMS", "流程名不能为空")
    slug = _slug(name)
    path = XHS_WORKFLOWS / f"{slug}.json"
    if path.exists():
        raise BridgeError("ALREADY_EXISTS", f"已存在：workflows/{slug}.json")
    document = {
        "schema_version": "1.0",
        "id": f"xhs-{slug}",
        "name": name,
        "inputs": {
            "keyword": "AI效率工具",
            "workspace": "run_artifacts/xhs",
            "shotPath": "run_artifacts/xhs/results.png",
            "browserType": "chrome",
        },
        "root": {"type": "sequence", "id": "root", "children": []},
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return {
        "path": f"workflows/{path.name}",
        "label": f"workflows/{path.stem}",
        "id": document["id"],
    }


def _runs() -> dict[str, Any]:
    return _load_api()._runs  # noqa: SLF001 - 桥层对原型内部表的**只读+定点写**是刻意设计


def _active_run() -> dict[str, Any] | None:
    for run_id, entry in _runs().items():
        proc = entry.get("proc")
        if proc is not None and proc.poll() is None:
            return {
                "runId": run_id,
                "realRunId": entry.get("realRunId"),
                "path": entry.get("path"),
                "startedAt": entry.get("startedAt"),
            }
    return None


def _control_write(run_dir: Path, pause: bool) -> None:
    payload = {"pause": pause, "requestedAt": datetime.now(UTC).isoformat()}
    tmp = run_dir / f"control.json.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    os.replace(tmp, run_dir / "control.json")


def _pause_run(run_id: str) -> dict[str, Any]:
    entry = _runs().get(run_id)
    if entry is None:
        raise BridgeError("UNKNOWN_RUN", f"没有这个 run：{run_id}")
    proc = entry.get("proc")
    if proc is None or proc.poll() is not None:
        raise BridgeError("RUN_NOT_ACTIVE", "该 run 已结束（paused 收口的继续请用「继续」）")
    real = entry.get("realRunId")
    if not real:
        raise BridgeError("RUN_DIR_PENDING", "run 目录还没建立（刚启动），稍等再暂停")
    _control_write(Path(entry["artifacts"]) / real, True)
    return {"ok": True, "runId": run_id, "realRunId": real}


def _resume_run(run_id: str) -> dict[str, Any]:
    """两种继续：运行中撤销暂停请求；已 paused 收口的起 resume 子进程并塞回原 entry。"""
    entry = _runs().get(run_id)
    if entry is None:
        raise BridgeError("UNKNOWN_RUN", f"没有这个 run：{run_id}")
    real = entry.get("realRunId")
    if not real:
        raise BridgeError("RUN_DIR_PENDING", "run 目录还没建立，稍等")
    run_dir = Path(entry["artifacts"]) / real
    proc = entry.get("proc")
    if proc is not None and proc.poll() is None:
        _control_write(run_dir, False)  # 撤销尚未收口的暂停请求
        return {"ok": True, "kind": "unpause", "runId": run_id}
    if not (run_dir / "checkpoint.json").is_file():
        raise BridgeError("NO_CHECKPOINT", "这个 run 没有检查点，续跑不了")
    api = _load_api()
    argv = [
        *api._rpa_argv(),  # noqa: SLF001 - 与 startRun 同一来源，保证解释器/可执行文件一致
        "resume",
        str(api._flow_path(entry["path"])),  # noqa: SLF001
        "--artifacts",
        str(entry["artifacts"]),
        "--run-id",
        real,
    ]
    new_proc = subprocess.Popen(  # noqa: S603
        argv,
        cwd=str(entry["work"]),
        env=api._run_env(),  # noqa: SLF001
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    entry["proc"] = new_proc
    entry["cancelled"] = False
    for stream, sink in ((new_proc.stdout, entry["stdout"]), (new_proc.stderr, entry["stderr"])):
        threading.Thread(  # noqa: SLF001 - 复用原型的读管道实现，不重写
            target=api._pump, args=(stream, sink, entry), daemon=True
        ).start()
    return {"ok": True, "kind": "resume", "runId": run_id, "pid": new_proc.pid}
