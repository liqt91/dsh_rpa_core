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
from urllib.parse import unquote, urlparse

from src.alerts import AlertLog, detect_for_keyword
from src.collector import build_run_command, parse_result
from src.monitor import MonitorPool
from src.snapshots import SnapshotStore, snapshot_from_outcome

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parents[1]
WORKFLOW = APP_ROOT / "workflows" / "xhs_product_search.json"
CLI_BINARY = REPO_ROOT / ".venv" / "Scripts" / "rpa-core.exe"
STATIC_DIR = APP_ROOT / "static"
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
        extra_inputs={"browserType": browser_type, "workspace": str(artifacts)},
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
    return parse_result(fresh[0])


def make_context(
    root: str | Path | None = None,
    *,
    runner: Runner | None = None,
    default_browser: str = "chrome",
    timeout: float = 180.0,
) -> AppContext:
    # 必须是绝对路径：_real_runner 的 artifact 目录会同时被「服务进程的 rglob」
    # 和「rpa-core 子进程（cwd=REPO_ROOT）」使用，相对路径会因两侧 cwd 不同而错位，
    # 导致刚写出的 result.json 被 rglob 找不到（曾致每次采集都误报「未写出 result.json」）。
    resolved_root = (Path(root) if root else APP_ROOT / "data").resolve()
    ctx = AppContext(
        root=resolved_root,
        store=SnapshotStore(resolved_root),
        alerts=AlertLog(resolved_root),
        monitor=MonitorPool(resolved_root),
        workflow=WORKFLOW,
        cli_binary=CLI_BINARY,
        repo_root=REPO_ROOT,
        runner=runner or (lambda kw, bt: _real_runner(ctx, kw, bt)),
        default_browser=default_browser,
        timeout=timeout,
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

    try:
        outcome = ctx.runner(keyword, browser_type)
    except Exception as exc:  # 子进程崩 / result.json 缺失 / 解析失败
        rid = "run-" + uuid.uuid4().hex[:12]
        ctx.monitor.set_running(keyword, rid)
        ctx.monitor.set_failed(keyword, f"{type(exc).__name__}: {exc}")
        result["run_id"] = rid
        result["error"] = str(exc)
        return result

    run_id = outcome.run_id or "(unknown)"
    # 先进入 running（从 pending/ok/failed 合法），再转 ok/failed。
    ctx.monitor.set_running(keyword, run_id)
    if outcome.group == "ok":
        snap = snapshot_from_outcome(ctx.store, keyword, outcome)
        alerts = detect_for_keyword(ctx.store, keyword)
        added = ctx.alerts.record(alerts)
        ctx.monitor.set_ok(keyword, run_id)
        result.update(
            ok=True,
            run_id=run_id,
            snapshot_items=snap.count,
            alerts_added=added,
        )
    else:
        err = outcome.error or outcome.status
        ctx.monitor.set_failed(keyword, str(err))
        result.update(
            ok=False,
            run_id=run_id,
            status=outcome.status,
            group=outcome.group,
            error=str(err),
        )
    return result


def _collect_thread(ctx: AppContext, keyword: str, browser_type: str) -> None:
    with ctx.lock:
        run_collection(ctx, keyword, browser_type)


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
            self._handle_api_get(path)
            return
        self._serve_static(path)

    def _handle_api_get(self, path: str) -> None:
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
        self._send(404, {"error": "not found", "path": path})

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
        self._send(404, {"error": "not found", "path": path})

    def _post_collect(self, data: dict[str, Any]) -> None:
        ctx = self.ctx
        keyword = str(data.get("keyword", "") or "").strip()
        if not keyword:
            self._send(400, {"error": "keyword required"})
            return
        browser = str(data.get("browserType") or ctx.default_browser)
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
