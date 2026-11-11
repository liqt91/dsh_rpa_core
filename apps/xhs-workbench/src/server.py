"""本地 HTTP 服务：把数据层（快照库 / 告警 / 监控池）与采集触发接到 8790 端口。

设计取向：

- 纯标准库 ``http.server``，绑定 ``127.0.0.1``，不暴露到公网。
- 采集触发走后台线程，UI 用 ``GET /api/monitor`` 轮询进度——和 ADR 0015 的
  「单通道、一次一个 run」对齐：用一把锁保证同一时刻只有一个采集在飞，
  第二个并发请求直接 ``409``。
- 服务端的采集逻辑与 verify_live.py 的「已验证路径」完全一致：同样的
  ``build_run_command``（含 ``browserType``/``workspace`` 两个 extra_inputs）、
  同样的 ``result.json`` 解析、同样的 ``snapshot_from_outcome`` 落库。
- 触发逻辑收敛为一个同步函数 ``run_collection``，既能被后台线程调，也能被单测
  直接调（注入假 runner，不占机）。HTTP 只是它的外壳。
"""

from __future__ import annotations

import json
import mimetypes
import os
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from src import editor_run
from src.alerts import AlertLog
from src.collector import build_run_command, parse_result
from src.commands_catalog import load_command_labels, load_commands
from src.editor_run import _items_count as _editor_items
from src.monitor import MonitorPool
from src.record import archive_screenshot, record_outcome
from src.runs import list_runs, run_dir_of, summarize_run
from src.snapshots import SnapshotStore
from src.workflow_doc import describe as describe_workflow
from src.workflow_edit import (
    delete_node,
    find_node,
    insert_node,
    move_node,
    update_params,
)
from src.workflow_edit import (
    load as load_wf,
)
from src.workflow_edit import (
    save as save_wf,
)

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parents[1]
WORKFLOW = APP_ROOT / "workflows" / "xhs_product_search.json"
CLI_BINARY = REPO_ROOT / ".venv" / "Scripts" / "rpa-core.exe"
STATIC_DIR = APP_ROOT / "static"
COMMANDS_DIR = REPO_ROOT / "commands"
I18N_JS = REPO_ROOT / "src" / "rpa_core" / "gui" / "i18n.js"
DEFAULT_PORT = 8790

# runner 签名：runner(keyword, browser_type) -> src.collector.RunOutcome
Runner = Callable[[str, str], Any]


@dataclass
class AppContext:
    root: Path
    store: SnapshotStore
    alerts: AlertLog
    monitor: MonitorPool
    workflow: Path
    cli_binary: Path
    repo_root: Path
    runner: Runner
    default_browser: str = "chrome"
    timeout: float = 180.0
    lock: threading.Lock = field(default_factory=threading.Lock)
    edit_lock: threading.Lock = field(default_factory=threading.Lock)
    commands_dir: Path = COMMANDS_DIR
    labels: dict[str, str] = field(default_factory=dict)
    manifests: dict[str, dict[str, Any]] = field(default_factory=dict)
    editor: Any = None


def _cli_env() -> dict[str, str]:
    return dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")




def _real_runner(ctx: AppContext, keyword: str, browser_type: str) -> Any:
    """真机采集：拼命令 → 跑子进程 → 读 result.json。与 verify_live.run_live 同源。

    重要：rpa-core 在 stdout 打印的「run 开始」事件 id 和它真正写 ``result.json`` 的
    目录名**不是同一个**。所以**绝不**用 stdout 里的 run_id 去拼路径，而是直接到
    ``run_artifacts`` 下找本次 run 新产出的 ``result.json``（用运行前的快照做差集，
    再按修改时间取最新），从根本上绕开 run_id 不一致的坑。
    """
    artifacts = ctx.root / "run_artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    before = {p.resolve() for p in artifacts.rglob("result.json")}
    cmd = build_run_command(
        ctx.workflow,
        keyword,
        artifacts,
        binary=str(ctx.cli_binary),
        extra_inputs={
            "browserType": browser_type,
            "workspace": str(artifacts),
            # 截图必须是**整串**引用（`${inputs.x}` 只认整串，混在文本里会原样输出字面量），
            # 所以把文件名也交给这里决定，不能在流程里写 `${inputs.workspace}/results.png`。
            "shotPath": str(artifacts / "results.png"),
        },
    )
    try:
        proc = subprocess.run(  # noqa: S603
            cmd,
            cwd=str(ctx.repo_root),
            env=_cli_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=ctx.timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"run 超过 {ctx.timeout}s 未结束") from exc

    out_tail = proc.stdout[-1200:] if proc.stdout else "(无 stdout)"
    err_tail = proc.stderr[-2000:] if proc.stderr else "(无 stderr)"
    fresh = sorted(
        (p.resolve() for p in artifacts.rglob("result.json") if p.resolve() not in before),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not fresh:
        # rpa-core 没写出 result.json：多半是 workflow 中途失败。真实原因在子进程 stderr。
        raise RuntimeError(
            f"run 未写出 result.json（rc={proc.returncode}）。多半是 workflow 中途失败。\n"
            f"--- rpa-core stderr ---\n{err_tail}\n--- stdout ---\n{out_tail}"
        )
    archive_screenshot(artifacts, fresh[0].parent)
    return parse_result(fresh[0])


def make_context(
    root: str | Path | None = None,
    *,
    runner: Runner | None = None,
    default_browser: str = "chrome",
    timeout: float = 180.0,
    workflow: str | Path | None = None,
) -> AppContext:
    # 必须是绝对路径：_real_runner 的 artifact 目录会同时被「服务进程的 rglob」
    # 和「rpa-core 子进程（cwd=REPO_ROOT）」使用，相对路径会因两侧 cwd 不同而错位，
    # 导致刚写出的 result.json 被 rglob 找不到（曾致每次采集都误报「未写出 result.json」）。
    resolved_root = (Path(root) if root else APP_ROOT / "data").resolve()
    labels = load_command_labels(I18N_JS)
    manifests: dict[str, dict[str, Any]] = {}
    for mf in sorted(COMMANDS_DIR.rglob("*.json")):
        try:
            raw = json.loads(mf.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(raw, dict) and raw.get("id"):
            manifests[str(raw["id"])] = raw
    ctx = AppContext(
        root=resolved_root,
        store=SnapshotStore(resolved_root),
        alerts=AlertLog(resolved_root),
        monitor=MonitorPool(resolved_root),
        # workflow 可注入：编辑接口的单测用临时副本，绝不许改到仓库里的真流程
        workflow=Path(workflow).resolve() if workflow else WORKFLOW,
        cli_binary=CLI_BINARY,
        repo_root=REPO_ROOT,
        runner=runner or (lambda kw, bt: _real_runner(ctx, kw, bt)),
        default_browser=default_browser,
        timeout=timeout,
        labels=labels,
        manifests=manifests,
    )
    return ctx


def run_collection(
    ctx: AppContext, keyword: str, browser_type: str | None = None
) -> dict[str, Any]:
    """同步执行一次采集并落库：登记监控 → 跑 runner → 落快照 → 比告警 → 写状态。

    直接返回结果字典，方便单测断言；后台线程只是包了一层锁来调它。
    """
    import uuid

    browser_type = browser_type or ctx.default_browser
    keyword = keyword.strip()
    ctx.monitor.register(keyword)
    result: dict[str, Any] = {
        "keyword": keyword,
        "browserType": browser_type,
        "ok": False,
    }

    # ★ 必须在**调用 runner 之前**进 running，不能等它跑完再置状态。
    # 否则整段采集（十几秒）期间监控池仍挂着上一次的 ok/failed，前端轮询第一秒就读到
    # 「完成」——这正是「点了采集，直接跳出采集完成、没有任何数据」这个症状的来源。
    # 进度条可信的前提是「running 覆盖整个执行窗口」。
    provisional = "run-" + uuid.uuid4().hex[:12]
    try:
        ctx.monitor.set_running(keyword, provisional)
    except ValueError as exc:  # 已暂停的 keyword 不允许开跑
        result["error"] = str(exc)
        return result

    try:
        outcome = ctx.runner(keyword, browser_type)
    except Exception as exc:  # 子进程崩 / result.json 缺失 / 解析失败
        ctx.monitor.set_failed(keyword, f"{type(exc).__name__}: {exc}")
        entry = ctx.monitor.get(keyword)
        result["run_id"] = (entry.last_run_id if entry else None) or provisional
        result["error"] = str(exc)
        return result

    result.update(record_outcome(ctx, keyword, outcome))
    return result


def _collect_thread(ctx: AppContext, keyword: str, browser_type: str) -> None:
    with ctx.lock:
        run_collection(ctx, keyword, browser_type)


# --------------------------------------------------------------------------- #
# 编辑器支撑：校验 / 类型收拢 / 插入默认值
# --------------------------------------------------------------------------- #


def _validate_workflow(ctx: AppContext) -> dict[str, Any]:
    """跑 ``rpa-core validate`` 子进程。校验器只有能力层一份，这里不自己实现。"""
    try:
        proc = subprocess.run(  # noqa: S603
            [str(ctx.cli_binary), "validate", str(ctx.workflow)],
            cwd=str(ctx.repo_root),
            env=_cli_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"valid": False, "rc": -1, "output": None, "error": f"validate 子进程异常：{exc}"}
    parsed: Any = None
    try:
        parsed = json.loads(proc.stdout)
    except json.JSONDecodeError:
        pass
    valid = proc.returncode == 0
    if isinstance(parsed, dict):
        valid = valid and bool(parsed.get("valid", True))
    return {
        "valid": valid,
        "rc": proc.returncode,
        "output": parsed if parsed is not None else (proc.stdout or proc.stderr)[-1200:],
    }


def _coerce_params(ctx: AppContext, command: str, params: dict[str, Any]) -> dict[str, Any]:
    """按 manifest 的字段类型把表单的字符串收拢成正确类型；引用形态（${...} / [...]）不转。"""
    manifest = ctx.manifests.get(command) or {}
    props = (manifest.get("input_schema") or {}).get("properties") or {}
    out: dict[str, Any] = {}
    for key, value in params.items():
        spec = props.get(key) or {}
        ftype = spec.get("type")
        ftype = ftype[0] if isinstance(ftype, list) else ftype
        if not isinstance(value, str):
            out[key] = value
            continue
        text = value.strip()
        if not text or text.startswith("${") or (text.startswith("[") and text.endswith("]")):
            out[key] = value
            continue
        try:
            if ftype == "integer":
                out[key] = int(text)
            elif ftype == "number":
                out[key] = float(text)
            elif ftype == "boolean":
                out[key] = text.lower() in ("true", "1", "yes", "是")
            elif ftype in ("array", "object"):
                out[key] = json.loads(text)
            else:
                out[key] = value
        except (ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"参数 {key} 无法按 {ftype} 解析：{value!r}") from exc
    return out


def _insert_defaults(ctx: AppContext, command: str) -> dict[str, Any]:
    """新节点的 with 初值：required 字段按 manifest 类型给占位值（default 优先）。"""
    manifest = ctx.manifests.get(command) or {}
    schema = manifest.get("input_schema") or {}
    props = schema.get("properties") or {}
    defaults: dict[str, Any] = {}
    for key in schema.get("required") or []:
        spec = props.get(key) or {}
        if "default" in spec:
            defaults[key] = spec["default"]
            continue
        ftype = spec.get("type")
        ftype = ftype[0] if isinstance(ftype, list) else ftype
        defaults[key] = {
            "integer": 0, "number": 0, "boolean": False, "array": [], "object": {},
        }.get(ftype, "")
    return defaults


# --------------------------------------------------------------------------- #
# HTTP 层
# --------------------------------------------------------------------------- #


def _snapshots_view(ctx: AppContext) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for kw in ctx.store.all_keywords():
        latest = ctx.store.latest(kw)
        out.append(
            {
                "keyword": kw,
                "runs": len(ctx.store.history(kw)),
                "latest": latest.to_json() if latest else None,
            }
        )
    return out


class _Handler(BaseHTTPRequestHandler):
    """``BaseHTTPRequestHandler`` 的子类由 ``make_handler`` 产出，``ctx`` 挂在类上。"""

    ctx: AppContext

    def _send(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any] | None:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None

    def _serve_static(self, path: str) -> None:
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        fp = (STATIC_DIR / rel).resolve()
        if fp.is_file() and (fp == STATIC_DIR or STATIC_DIR in fp.parents):
            ctype, _ = mimetypes.guess_type(str(fp))
            data = fp.read_bytes()
            self.send_response(200)
            self.send_header(
                "Content-Type", ctype or "application/octet-stream"
            )
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self._send(404, {"error": "not found", "path": path})

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path

        if path.startswith("/api/"):
            self._handle_api_get(path, parsed.query)
            return
        self._serve_static(path)

    def _handle_api_get(self, path: str, query: str = "") -> None:
        ctx = self.ctx
        if path == "/api/health":
            self._send(200, {"ok": True})
            return
        if path == "/api/snapshots":
            self._send(200, {"keywords": _snapshots_view(ctx)})
            return
        if path.startswith("/api/snapshots/"):
            kw = unquote(path[len("/api/snapshots/") :])
            if kw not in ctx.store.all_keywords():
                self._send(404, {"error": "unknown keyword", "keyword": kw})
                return
            self._send(
                200,
                {
                    "keyword": kw,
                    "history": [s.to_json() for s in ctx.store.history(kw)],
                },
            )
            return
        if path == "/api/products":
            items = [p.to_json() for p in ctx.store.product_index().values()]
            self._send(200, {"items": items, "count": len(items)})
            return
        if path == "/api/alerts":
            alerts = [a.to_json() for a in ctx.alerts.all()]
            self._send(200, {"alerts": alerts, "count": len(alerts)})
            return
        if path == "/api/monitor":
            self._send(
                200,
                {
                    "entries": [e.to_json() for e in ctx.monitor.all()],
                    "counts": ctx.monitor.counts_by_state(),
                },
            )
            return
        if path == "/api/workflow":
            try:
                self._send(200, describe_workflow(ctx.workflow, ctx.labels))
            except (OSError, ValueError) as exc:
                self._send(500, {"error": f"流程文件读不出来：{exc}"})
            return
        if path == "/api/commands":
            self._send(200, {"commands": load_commands(ctx.commands_dir, ctx.labels)})
            return
        if path == "/api/editor/status":
            run = ctx.editor
            self._send(200, run.status() if run else {"state": "idle", "lines": 0})
            return
        if path == "/api/editor/log":
            self._get_editor_log(query)
            return
        if path == "/api/runs":
            items = list_runs(ctx.root / "run_artifacts")
            self._send(200, {"runs": items, "count": len(items)})
            return
        if path.startswith("/api/runs/"):
            self._get_run(path[len("/api/runs/") :])
            return
        self._send(404, {"error": "not found", "path": path})

    def _get_run(self, rest: str) -> None:
        """``/api/runs/<run_id>`` 返回步骤时间线，``/api/runs/<run_id>/shot`` 返回现场截图。"""
        ctx = self.ctx
        artifacts = ctx.root / "run_artifacts"
        want_shot = rest.endswith("/shot")
        run_id = unquote(rest[: -len("/shot")] if want_shot else rest)
        try:
            run_dir = run_dir_of(artifacts, run_id)
        except ValueError as exc:
            self._send(400, {"error": str(exc)})
            return
        if not (run_dir / "result.json").is_file():
            self._send(404, {"error": "unknown run", "run_id": run_id})
            return
        if want_shot:
            shot = run_dir / "results.png"
            if not shot.is_file():
                self._send(404, {"error": "no screenshot for this run", "run_id": run_id})
                return
            self._send_file(shot, "image/png")
            return
        self._send(200, summarize_run(run_dir))

    def _send_file(self, path: Path, content_type: str) -> None:
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _get_editor_log(self, query: str) -> None:
        ctx = self.ctx
        run = ctx.editor
        if run is None:
            self._send(200, {"lines": [], "cursor": 0, "state": "idle"})
            return
        run.drain_log()
        try:
            since = int(parse_qs(query).get("since", ["0"])[0])
        except ValueError:
            since = 0
        since = max(0, min(since, len(run.lines)))
        self._send(
            200,
            {
                "lines": run.lines[since:],
                "cursor": len(run.lines),
                "state": run.state,
                "run_id": run.run_dir.name if run.run_dir else None,
                "items": _editor_items(run.outcome),
            },
        )

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        data = self._read_json()
        if data is None:
            self._send(400, {"error": "invalid json body"})
            return
        ctx = self.ctx

        if path == "/api/collect":
            self._post_collect(data)
            return
        if path == "/api/pause":
            self._post_state_change("pause", data)
            return
        if path == "/api/resume":
            self._post_state_change("resume", data)
            return
        if path == "/api/pause-all":
            n = ctx.monitor.pause_all()
            self._send(200, {"paused": n})
            return
        if path == "/api/resume-all":
            n = ctx.monitor.resume_all()
            self._send(200, {"resumed": n})
            return
        if path == "/api/validate":
            self._send(200, _validate_workflow(ctx))
            return
        if path == "/api/workflow/update":
            self._post_wf_update(data)
            return
        if path == "/api/workflow/insert":
            self._post_wf_insert(data)
            return
        if path == "/api/workflow/delete":
            self._post_wf_delete(data)
            return
        if path == "/api/workflow/move":
            self._post_wf_move(data)
            return
        if path == "/api/editor/run":
            self._post_editor_run(data)
            return
        if path == "/api/editor/pause":
            self._post_editor_ctl(editor_run.pause)
            return
        if path == "/api/editor/resume":
            self._post_editor_ctl(editor_run.resume)
            return
        if path == "/api/editor/stop":
            self._post_editor_ctl(editor_run.stop)
            return
        self._send(404, {"error": "not found", "path": path})

    # ---- 工作流编辑 ----

    def _wf_after(self, validate: bool = True) -> None:
        ctx = self.ctx
        steps = describe_workflow(ctx.workflow, ctx.labels)["steps"]
        payload: dict[str, Any] = {"ok": True, "steps": steps}
        if validate:
            payload["validate"] = _validate_workflow(ctx)
        self._send(200, payload)

    def _post_wf_update(self, data: dict[str, Any]) -> None:
        ctx = self.ctx
        node_id = str(data.get("node_id") or "")
        params = data.get("params")
        if not node_id or not isinstance(params, dict):
            self._send(400, {"error": "node_id 与 params 必填"})
            return
        with ctx.edit_lock:
            doc = load_wf(ctx.workflow)
            try:
                found = find_node(doc, node_id)
                if found is None:
                    raise KeyError(f"节点不存在：{node_id}")
                node = found[2]
                coerced = _coerce_params(ctx, str(node.get("command") or ""), params)
                update_params(doc, node_id, coerced)
                save_wf(ctx.workflow, doc)
            except (KeyError, ValueError) as exc:
                self._send(400, {"error": str(exc)})
                return
        self._wf_after()

    def _post_wf_insert(self, data: dict[str, Any]) -> None:
        ctx = self.ctx
        command = str(data.get("command") or "")
        after = str(data.get("after") or "") or None
        if command not in ctx.manifests:
            self._send(400, {"error": f"未知指令：{command}"})
            return
        with ctx.edit_lock:
            doc = load_wf(ctx.workflow)
            node = insert_node(doc, command, after, _insert_defaults(ctx, command))
            save_wf(ctx.workflow, doc)
        with ctx.edit_lock:
            steps = describe_workflow(ctx.workflow, ctx.labels)["steps"]
            payload = {"ok": True, "steps": steps, "nodeId": node["id"]}
            payload["validate"] = _validate_workflow(ctx)
        self._send(200, payload)

    def _post_wf_delete(self, data: dict[str, Any]) -> None:
        ctx = self.ctx
        node_id = str(data.get("node_id") or "")
        if not node_id:
            self._send(400, {"error": "node_id 必填"})
            return
        with ctx.edit_lock:
            doc = load_wf(ctx.workflow)
            try:
                delete_node(doc, node_id)
                save_wf(ctx.workflow, doc)
            except (KeyError, ValueError) as exc:
                self._send(400, {"error": str(exc)})
                return
        self._wf_after()

    def _post_wf_move(self, data: dict[str, Any]) -> None:
        ctx = self.ctx
        node_id = str(data.get("node_id") or "")
        try:
            direction = int(data.get("direction"))
        except (TypeError, ValueError):
            self._send(400, {"error": "direction 必须是 ±1"})
            return
        with ctx.edit_lock:
            doc = load_wf(ctx.workflow)
            try:
                move_node(doc, node_id, direction)
                save_wf(ctx.workflow, doc)
            except (KeyError, ValueError) as exc:
                self._send(400, {"error": str(exc)})
                return
        self._wf_after()

    # ---- 编辑器执行控制 ----

    def _post_editor_run(self, data: dict[str, Any]) -> None:
        ctx = self.ctx
        keyword = str(data.get("keyword", "") or "").strip()
        if not keyword:
            self._send(400, {"error": "keyword required"})
            return
        browser = str(data.get("browserType") or ctx.default_browser)
        entry = ctx.monitor.get(keyword)
        if entry is not None and entry.state == "paused":
            active = ctx.editor and ctx.editor.active
            hint = "（本次运行被暂停，可在编辑器「继续」）" if active else ""
            self._send(409, {"error": f"{keyword} 已暂停{hint}：先恢复/停止再来"})
            return
        if not ctx.lock.acquire(blocking=False):
            self._send(409, {"error": "已有采集在飞"})
            return
        try:
            editor_run.start(ctx, keyword, browser)
        except BaseException as exc:  # noqa: BLE001 - 放锁兜底
            ctx.lock.release()
            self._send(500, {"error": str(exc)})
            return
        self._send(202, {"accepted": True, "keyword": keyword, "browserType": browser})

    def _post_editor_ctl(self, action: Any) -> None:
        try:
            action(self.ctx)
        except ValueError as exc:
            self._send(409, {"error": str(exc)})
            return
        self._send(200, {"ok": True})

    def _post_collect(self, data: dict[str, Any]) -> None:
        ctx = self.ctx
        keyword = str(data.get("keyword", "") or "").strip()
        if not keyword:
            self._send(400, {"error": "keyword required"})
            return
        browser = str(data.get("browserType") or ctx.default_browser)
        entry = ctx.monitor.get(keyword)
        if entry is not None and entry.state == "paused":
            # 暂停是用户显式意图：直接拒，不要先收下再在后台报错（前端会一直等 running）
            self._send(409, {"error": f"{keyword} 已暂停，先在监控池恢复再采集"})
            return
        if ctx.lock.locked():
            self._send(409, {"error": "another collection already in flight"})
            return
        threading.Thread(
            target=_collect_thread,
            args=(ctx, keyword, browser),
            daemon=True,
        ).start()
        self._send(
            202, {"accepted": True, "keyword": keyword, "browserType": browser}
        )

    def _post_state_change(self, action: str, data: dict[str, Any]) -> None:
        ctx = self.ctx
        keyword = str(data.get("keyword", "") or "").strip()
        if not keyword:
            self._send(400, {"error": "keyword required"})
            return
        try:
            if action == "pause":
                ctx.monitor.pause(keyword)
            else:
                ctx.monitor.resume(keyword)
        except (KeyError, ValueError) as exc:
            self._send(409, {"error": str(exc)})
            return
        entry = ctx.monitor.get(keyword)
        self._send(200, entry.to_json() if entry else {"keyword": keyword})

    def log_message(self, *args: Any) -> None:  # 静默默认访问日志
        return


def make_handler(ctx: AppContext) -> type[_Handler]:
    class Handler(_Handler):
        pass

    Handler.ctx = ctx
    return Handler


def make_server(ctx: AppContext, host: str = "127.0.0.1", port: int = DEFAULT_PORT):
    from http.server import ThreadingHTTPServer

    return ThreadingHTTPServer((host, port), make_handler(ctx))
