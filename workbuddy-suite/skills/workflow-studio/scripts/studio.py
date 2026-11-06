#!/usr/bin/env python3
"""WorkBuddy Studio 的会话服务 + 命令行。

这个脚本是「Agent ↔ 人」之间唯一的会合点，两个客户端都只是它的客人：

    WorkBuddy Agent ──(CLI: open / wait / capture)──┐
                                                   ├── 127.0.0.1:7788
    PPX 编辑窗口   ──(HTTP: /inbox, /events)───────┤
    浏览器插件     ──(HTTP: /capture)──────────────┘

为什么不是「Agent 直接调窗口」：Agent 在子进程里跑，窗口随时可能被用户关掉。
把状态放在一个独立的小服务里，窗口没开时请求会留在收件箱，窗口一开就能取走；
用户在窗口里保存后，Agent 那边也能拿到一条带 sha 的事件，而不是靠猜。

依赖只有标准库（http.server / argparse / json / urllib），装不装东西都能跑。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

# 同目录的定位层。装上之后 editor/ extension/ src/ 都不在本目录，靠它把源码根找回来；
# 开发态没有 runtime.json，它会向上找到仓库根，行为与本文件以前写死的相对层级一致。
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _runtime  # noqa: E402

VERSION = "1.0.0"
SERVICE = "workbuddy-studio"
DEFAULT_PORT = 7788
MAX_EVENTS = 2000
_default_home = Path(os.environ.get("TEMP") or "/tmp") / "workbuddy-studio"
HOME = Path(os.environ.get("STUDIO_HOME") or _default_home)
ENDPOINT_FILE = HOME / "endpoint.json"
EVENTS_FILE = HOME / "events.jsonl"
LOG_FILE = HOME / "server.log"

ALLOWED_ORIGIN_PREFIXES = (
    "chrome-extension://",
    "moz-extension://",
    "safari-web-extension://",
)


def _now() -> float:
    return time.time()


def _iso(at: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(at))


# ---------------------------------------------------------------------------
# 会话状态：全部在内存，事件另存一份 jsonl 供事后取证（Agent 只看 HTTP）。
# ---------------------------------------------------------------------------


class Session:
    def __init__(self, root: Path | None = None) -> None:
        self.lock = threading.Lock()
        self.events: list[dict[str, Any]] = []
        self.inbox: list[dict[str, Any]] = []
        self.captures: list[dict[str, Any]] = []
        self.cursor = 0
        self.inbox_seq = 0
        self.waiting: dict[str, int] = {}
        self.started = _now()
        self.root = root.resolve() if root else None
        self.editor_seen = 0.0
        self.editor_path: str | None = None
        self.editor_dirty = False

    # -- 事件 -------------------------------------------------------------

    def push(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            self.cursor += 1
            event = {"id": self.cursor, "kind": kind, "at": _now(), **payload}
            self.events.append(event)
            if len(self.events) > MAX_EVENTS:
                self.events = self.events[-MAX_EVENTS:]
            if kind == "capture":
                self.captures.append(event)
                if len(self.captures) > 200:
                    self.captures = self.captures[-200:]
        _append_jsonl(event)
        return event

    def since(
        self,
        cursor: int,
        kinds: set[str] | None = None,
        path: str | None = None,
    ) -> list[dict[str, Any]]:
        with self.lock:
            items = [item for item in self.events if item["id"] > cursor]
        if kinds:
            items = [item for item in items if item["kind"] in kinds]
        if path:
            items = [item for item in items if item.get("path") == path]
        return items

    def queue_open(self, path: str, reason: str, line: int | None) -> dict[str, Any]:
        with self.lock:
            request = {
                "id": uuid.uuid4().hex[:12],
                "kind": "open",
                "path": path,
                "line": line,
                "reason": reason,
                "at": _now(),
            }
            self.inbox_seq += 1
            request["cursor"] = self.inbox_seq
            self.inbox.append(request)
            if len(self.inbox) > 200:
                self.inbox = self.inbox[-200:]
        self.push("open-request", {"path": path, "reason": reason, "requestId": request["id"]})
        return request

    def inbox_since(self, since: int) -> list[dict[str, Any]]:
        """收件箱用**独立计数**，不复用事件游标：两者生命周期不同（打开请求会被消费）。"""
        with self.lock:
            return [item for item in self.inbox if item.get("cursor", 0) > since]

    def inbox_cursor(self) -> int:
        with self.lock:
            return self.inbox_seq

    def check_path(self, raw: str) -> tuple[Path | None, str | None]:
        target = Path(raw).expanduser()
        if not target.is_absolute():
            return None, f"需要绝对路径：{raw}"
        resolved = target.resolve()
        if self.root is not None:
            try:
                resolved.relative_to(self.root)
            except ValueError:
                return None, f"超出会话根目录 {self.root}：{resolved}"
        if not resolved.is_file():
            return None, f"文件不存在：{resolved}"
        return resolved, None


def _append_jsonl(event: dict[str, Any]) -> None:
    try:
        HOME.mkdir(parents=True, exist_ok=True)
        with EVENTS_FILE.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# HTTP 层
# ---------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    session: Session
    server_version = f"{SERVICE}/{VERSION}"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003 - 基类签名
        if os.environ.get("STUDIO_VERBOSE"):
            sys.stderr.write("[studio] " + (fmt % args) + "\n")

    # -- 工具 -------------------------------------------------------------

    def _cors(self) -> None:
        origin = self.headers.get("Origin", "")
        if origin.startswith(ALLOWED_ORIGIN_PREFIXES) or origin.startswith("http://127.0.0.1") or origin.startswith("http://localhost"):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        else:
            self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Studio-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _query(self) -> dict[str, str]:
        if "?" not in self.path:
            return {}
        raw = self.path.split("?", 1)[1]
        out: dict[str, str] = {}
        for chunk in raw.split("&"):
            if not chunk:
                continue
            key, _, value = chunk.partition("=")
            out[key] = urllib.parse.unquote(value)
        return out

    # -- 路由 -------------------------------------------------------------

    def do_OPTIONS(self) -> None:  # noqa: N802 - 基类命名
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        route = self.path.split("?", 1)[0].rstrip("/") or "/"
        query = self._query()
        if route == "/health":
            self._json(200, self._health())
            return
        if route == "/inbox":
            self._inbox(query)
            return
        if route == "/events":
            self._wait_events(query)
            return
        if route == "/captures":
            since = int(query.get("since") or 0)
            with self.session.lock:
                items = [item for item in self.session.captures if item["id"] > since]
            self._json(200, {"ok": True, "captures": items})
            return
        self._json(404, {"ok": False, "code": "NOT_FOUND", "error": f"没有这个路由：{route}"})

    def _origin_allowed(self) -> bool:
        """只认扩展与本地窗口。网页里的脚本也能 fetch 127.0.0.1，
        不加这道闸，任何一个网页都能往会话里塞假事件、甚至关掉服务。"""
        origin = self.headers.get("Origin", "")
        if not origin:
            return True  # 本地 CLI / curl 不带 Origin
        return (
            origin.startswith(ALLOWED_ORIGIN_PREFIXES)
            or origin.startswith("http://127.0.0.1")
            or origin.startswith("http://localhost")
        )

    def do_POST(self) -> None:  # noqa: N802
        route = self.path.split("?", 1)[0].rstrip("/") or "/"
        if not self._origin_allowed():
            self._json(
                403,
                {"ok": False, "code": "ORIGIN_DENIED", "error": "只接受扩展与本地窗口的请求"},
            )
            return
        payload = self._body()
        if route == "/open":
            self._open(payload)
            return
        if route == "/events":
            kind = str(payload.get("kind") or "").strip()
            if not kind:
                self._json(400, {"ok": False, "code": "MISSING_KIND", "error": "事件需要一个 kind"})
                return
            body = {key: value for key, value in payload.items() if key != "kind"}
            event = self.session.push(kind, body)
            if kind == "heartbeat":
                with self.session.lock:
                    self.session.editor_seen = _now()
                    self.session.editor_path = payload.get("path")
                    self.session.editor_dirty = bool(payload.get("dirty"))
            port = getattr(self.server, "port", DEFAULT_PORT)
            self._json(200, {"ok": True, "cursor": event["id"], "port": port})
            return
        if route == "/capture":
            self._capture(payload)
            return
        if route == "/shutdown":
            self._json(200, {"ok": True, "bye": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        self._json(404, {"ok": False, "code": "NOT_FOUND", "error": f"没有这个路由：{route}"})

    # -- 具体处理 ---------------------------------------------------------

    def _health(self) -> dict[str, Any]:
        with self.session.lock:
            editor_age = _now() - self.session.editor_seen if self.session.editor_seen else None
            pending = len(self.session.inbox)
            events = self.session.cursor
            captures = len(self.session.captures)
            path = self.session.editor_path
            dirty = self.session.editor_dirty
        return {
            "ok": True,
            "service": SERVICE,
            "version": VERSION,
            "port": getattr(self.server, "port", DEFAULT_PORT),
            "pid": os.getpid(),
            "uptime": round(_now() - self.session.started, 1),
            "events": events,
            "pending": pending,
            "captures": captures,
            "editor": {
                "online": editor_age is not None and editor_age < 8,
                "lastSeen": round(editor_age, 1) if editor_age is not None else None,
                "path": path,
                "dirty": dirty,
            },
            "root": str(self.session.root) if self.session.root else None,
            "waiting": dict(self.session.waiting),
        }

    def _inbox(self, query: dict[str, str]) -> None:
        since = int(query.get("since") or 0)
        client = query.get("client") or "unknown"
        if client == "editor":
            with self.session.lock:
                self.session.editor_seen = _now()
        requests = self.session.inbox_since(since)
        pending = next((item for item in requests if item["kind"] == "open"), None)
        if pending:
            with self.session.lock:
                self.session.editor_path = pending.get("path")
        self._json(
            200,
            {
                "ok": True,
                "port": getattr(self.server, "port", DEFAULT_PORT),
                "cursor": self.session.inbox_cursor(),
                "requests": requests,
            },
        )

    def _wait_events(self, query: dict[str, str]) -> None:
        """长轮询：Agent 侧一次调用最多等 `timeout` 秒，避免空转烧 CPU。"""
        since = int(query.get("since") or 0)
        timeout = min(float(query.get("timeout") or 20), 60.0)
        kinds = {item for item in (query.get("kinds") or "").split(",") if item}
        path = query.get("path") or None
        deadline = _now() + timeout
        counter = "capture" if kinds == {"capture"} else "event"
        with self.session.lock:
            self.session.waiting[counter] = self.session.waiting.get(counter, 0) + 1
        try:
            self._poll_events(since, kinds, path, deadline)
        finally:
            with self.session.lock:
                self.session.waiting[counter] = max(0, self.session.waiting.get(counter, 1) - 1)

    def _poll_events(self, since: int, kinds: set[str], path: str | None, deadline: float) -> None:
        while True:
            items = self.session.since(since, kinds or None, path)
            if items:
                self._json(
                    200,
                    {"ok": True, "cursor": items[-1]["id"], "events": items, "timedOut": False},
                )
                return
            if _now() >= deadline:
                with self.session.lock:
                    cursor = self.session.cursor
                self._json(200, {"ok": True, "cursor": cursor, "events": [], "timedOut": True})
                return
            time.sleep(0.12)

    def _open(self, payload: dict[str, Any]) -> None:
        raw = str(payload.get("path") or "").strip()
        if not raw:
            self._json(400, {"ok": False, "code": "MISSING_PATH", "error": "缺少 path"})
            return
        target, error = self.session.check_path(raw)
        if target is None:
            self._json(400, {"ok": False, "code": "BAD_PATH", "error": error})
            return
        reason = str(payload.get("reason") or "").strip()
        line = payload.get("line")
        request = self.session.queue_open(str(target), reason, int(line) if line else None)
        with self.session.lock:
            editor_online = self.session.editor_seen and (_now() - self.session.editor_seen) < 8
        self._json(
            200,
            {
                "ok": True,
                "id": request["id"],
                "path": str(target),
                "editorOnline": bool(editor_online),
            },
        )

    def _capture(self, payload: dict[str, Any]) -> None:
        selector = str(payload.get("selector") or "").strip()
        if not selector:
            self._json(
                400,
                {"ok": False, "code": "MISSING_SELECTOR", "error": "拾取结果缺少 selector"},
            )
            return
        event = self.session.push(
            "capture",
            {
                "selector": selector,
                "alternatives": payload.get("alternatives") or [],
                "tag": payload.get("tag"),
                "text": (payload.get("text") or "")[:200],
                "url": payload.get("url"),
                "title": payload.get("title"),
                "frame": payload.get("frame") or "top",
                "rect": payload.get("rect"),
                "attributes": payload.get("attributes") or {},
                "source": payload.get("source") or "extension",
            },
        )
        self._json(200, {"ok": True, "id": event["id"], "cursor": event["id"]})


# ---------------------------------------------------------------------------
# 服务生命周期
# ---------------------------------------------------------------------------


def _write_endpoint(port: int, root: Path | None, token: str) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    payload = {
        "port": port,
        "token": token,
        "pid": os.getpid(),
        "root": str(root) if root else None,
        "version": VERSION,
        "started": _iso(_now()),
    }
    ENDPOINT_FILE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _read_endpoint() -> dict[str, Any] | None:
    try:
        return json.loads(ENDPOINT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ★ 必须显式关掉代理：开发机上常年挂着 http_proxy，urllib 会把 127.0.0.1 也丢给代理，
# 于是「本地服务明明起着」却报 502。这是最容易把本地工具做废的一个坑。
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _healthy(port: int, timeout: float = 0.5) -> bool:
    try:
        with _OPENER.open(f"http://127.0.0.1:{port}/health", timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8")).get("service") == SERVICE
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _pick_port(preferred: int) -> int:
    import socket

    for candidate in range(preferred, preferred + 24):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", candidate))
            except OSError:
                continue
        return candidate
    raise SystemExit(f"端口 {preferred}~{preferred + 23} 全被占用，起不来")


def _make_server(port_hint: int, root: Path | None):
    port = _pick_port(port_hint)
    token = uuid.uuid4().hex
    handler = type("BoundHandler", (Handler,), {"session": Session(root)})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    server.port = port  # 自定义属性：Handler 里用 self.server.port 回给客户端
    _write_endpoint(port, root, token)
    return server, port


def cmd_serve(args: argparse.Namespace) -> int:
    existing = _read_endpoint()
    if existing and _healthy(int(existing.get("port") or 0)):
        print(json.dumps({"ok": True, "already": True, **existing}, ensure_ascii=False))
        return 0
    root = Path(args.root).resolve() if args.root else None
    server, port = _make_server(args.port, root)
    _append_jsonl(
        {"kind": "server-start", "port": port, "root": str(root) if root else None, "at": _now()}
    )
    banner = {
        "ok": True,
        "service": SERVICE,
        "version": VERSION,
        "port": port,
        "root": str(root) if root else None,
        "pid": os.getpid(),
        "endpoint": str(ENDPOINT_FILE),
    }
    print(json.dumps(banner, ensure_ascii=False), flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        try:
            if _read_endpoint() and int(_read_endpoint().get("pid") or 0) == os.getpid():
                ENDPOINT_FILE.unlink()
        except (OSError, ValueError, TypeError):
            pass
        _append_jsonl({"kind": "server-stop", "at": _now()})
    return 0


# ---------------------------------------------------------------------------
# 客户端：CLI 只做一件事 —— 把 HTTP 协议包装成 Agent 能读的 JSON 行
# ---------------------------------------------------------------------------


def _call(
    method: str,
    route: str,
    payload: dict[str, Any] | None = None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    meta = _read_endpoint()
    if not meta:
        return {"ok": False, "code": "NO_SESSION", "error": "会话服务没起：先运行 studio serve"}
    port = int(meta.get("port") or DEFAULT_PORT)
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(f"http://127.0.0.1:{port}{route}", data=data, method=method)
    request.add_header("Content-Type", "application/json")
    token = meta.get("token")
    if token:
        request.add_header("X-Studio-Token", token)
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode("utf-8"))
        except (ValueError, OSError):
            return {"ok": False, "code": "HTTP_ERROR", "error": f"HTTP {exc.code}"}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {"ok": False, "code": "SESSION_DOWN", "error": f"连不上会话服务：{exc}"}


def _emit(payload: dict[str, Any], pretty: bool) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None), flush=True)


def cmd_status(args: argparse.Namespace) -> int:
    health = _call("GET", "/health")
    if not health.get("ok"):
        _emit(health, args.pretty)
        return 2
    if args.pretty:
        editor = health.get("editor") or {}
        print(f"会话   {health['version']} · 端口 {health['port']} · 已运行 {health['uptime']}s")
        print(f"编辑窗口 {'在线' if editor.get('online') else '离线'}"
              f"{' · ' + str(editor.get('path')) if editor.get('path') else ''}")
        pending = health["pending"]
        print(f"待处理 {pending} 条 · 已拾取 {health['captures']} 条 · 事件 {health['events']} 条")
        if health.get("root"):
            print(f"根目录 {health['root']}")
    else:
        _emit(health, False)
    return 0


def cmd_open(args: argparse.Namespace) -> int:
    payload: dict[str, Any] = {"path": args.path, "reason": args.reason or ""}
    if args.line:
        payload["line"] = args.line
    result = _call("POST", "/open", payload)
    if not result.get("ok"):
        _emit(result, args.pretty)
        return 2
    if args.wait is not None and args.wait >= 0:
        # 打开失败也是结论：窗口回的 open-failed 带 BAD_PATH 与可照抄的修法。
        # 不把它算进等待集合，Agent 只会干等到超时，把「窗口拒绝」误读成「用户没动」。
        event = _wait_for(args.wait, {"save", "close", "open-failed"}, result["path"])
        failure = next(
            (row for row in (event.get("events") or []) if row.get("kind") == "open-failed"),
            None,
        )
        if failure is not None:
            _emit(
                {
                    "ok": False,
                    "code": failure.get("code") or "OPEN_FAILED",
                    "error": failure.get("message") or "窗口打开失败",
                    "path": result["path"],
                    "waited": event,
                },
                args.pretty,
            )
            return 2
        merged = {**result, "waited": event}
        _emit(merged, args.pretty)
        return 0 if event.get("events") else 3
    _emit(result, args.pretty)
    return 0


def _wait_for(timeout: float, kinds: set[str], path: str | None) -> dict[str, Any]:
    since = 0
    health = _call("GET", "/health", timeout=2.0)
    if health.get("ok"):
        since = int(health.get("events") or 0)
    chunks: list[dict[str, Any]] = []
    deadline = _now() + timeout
    while True:
        remaining = max(0.5, min(deadline - _now(), 25.0))
        query = f"/events?since={since}&timeout={remaining:.1f}&kinds={','.join(sorted(kinds))}"
        if path:
            query += f"&path={urllib.parse.quote(path)}"
        result = _call("GET", query, timeout=remaining + 8)
        if not result.get("ok"):
            return result
        since = int(result.get("cursor") or since)
        chunks.extend(result.get("events") or [])
        if chunks or _now() >= deadline:
            return {"ok": True, "cursor": since, "events": chunks, "timedOut": not chunks}


def cmd_wait(args: argparse.Namespace) -> int:
    kinds = {item.strip() for item in (args.kind or "save,close").split(",") if item.strip()}
    result = _wait_for(args.timeout, kinds, args.path)
    _emit(result, args.pretty)
    if not result.get("ok"):
        return 2
    return 0 if result.get("events") else 3


def cmd_capture(args: argparse.Namespace) -> int:
    result = _wait_for(args.timeout, {"capture"}, None)
    if result.get("events"):
        latest = result["events"][-1]
        _emit({"ok": True, "capture": latest, "timedOut": False}, args.pretty)
        return 0
    _emit({"ok": bool(result.get("ok")), "capture": None, "timedOut": True,
           "hint": "让用户在页面上点一个元素（扩展已连接时）或先运行 studio serve"}, args.pretty)
    return 3


def cmd_stop(args: argparse.Namespace) -> int:
    result = _call("POST", "/shutdown", {})
    _emit(result, args.pretty)
    return 0 if result.get("ok") else 2


def _core_home() -> Path:
    """运行时数据目录：装好的窗口与 `editor.json` 都在这里。

    **不是** `RPA_CORE_HOME` —— 那个名字在 `_runtime.py` 里表示「rpa_core 源码根」，
    一个变量两种含义必然互相打脸。三处必须一致：这里、`bin/rpa-skill.mjs` 的
    `coreHome()`、窗口侧 `api.py` 的 `_launcher()`。
    """
    return Path(os.environ.get("RPA_HOME") or (Path.home() / ".rpa-core")).expanduser()


def _editor_manifest() -> dict[str, Any]:
    """`editor.json`：安装器写的窗口清单（exe 路径、版本、工作区）。读不到就是空字典。"""
    paths: list[Path] = []
    override = os.environ.get("RPA_EDITOR_MANIFEST")
    if override:
        paths.append(Path(override).expanduser())
    paths.append(_core_home() / "editor.json")
    data: dict[str, Any] = {}
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict):
            data.update(payload)
    return data


def _editor_exe() -> Path | None:
    """已安装的窗口可执行文件；没装返回 None。

    与 `_editor_dir()` 是两样东西：那个是源码树里的 PPX 工程（要 `ppx dev` + 前端依赖），
    这个是构建产物（装完即用，不需要 PPX 也不需要 node）。优先级按「谁的依赖更少」排，
    所以 `cmd_gui` 先试 exe、再退回 `ppx dev`。
    """
    candidates: list[Path] = []
    override = os.environ.get("RPA_EDITOR_EXE")
    if override:
        candidates.append(Path(override).expanduser())
    recorded = _editor_manifest().get("editor")
    if recorded:
        candidates.append(Path(str(recorded)).expanduser())
    for path in candidates:
        if path.is_file():
            return path
    return None


def _editor_dir() -> Path | None:
    """编辑窗口工程目录（一个 PPX 应用），找不到返回 None。

    窗口从来不在 wheel 与 npm 包里 —— 它是仓库内的 PPX 工程
    （`.harness/demo/ppx_probe/app`，M65 宿主实验），起它还要装 PPX 与前端依赖。
    所以「没有窗口」是这套安装的正常形态，不是坏了；doctor 据此标 SKIP 而不是 FAIL。

    先找源码根，再认历史上放过窗口的老路径 `workbuddy-suite/editor`
    （有些工作树把它挂在那儿，`_suite_dir()` 时期的名字），最后允许
    `RPA_EDITOR_DIR` 直接指定 —— 三种来源都不写死后面两段的相对层级。
    """
    candidates: list[Path] = []
    override = os.environ.get("RPA_EDITOR_DIR")
    if override:
        candidates.append(Path(override).expanduser())
    root = _runtime.find_source_root()
    if root:
        candidates.append(root / ".harness/demo/ppx_probe/app")
        candidates.append(root / "workbuddy-suite/editor")
    for path in candidates:
        if (path / "ppx.toml").is_file():
            return path
    return None


def _find_ppx() -> str | None:
    override = os.environ.get("PPX_BIN")
    if override and Path(override).is_file():
        return override
    for name in ("ppx.exe", "ppx"):
        candidate = Path(sys.executable).with_name(name)
        if candidate.is_file():
            return str(candidate)
    return shutil.which("ppx")


def cmd_gui(args: argparse.Namespace) -> int:
    """一条命令起全套：会话服务（本进程内）+ 编辑窗口（ppx dev / 打包后的 exe）。

    窗口是子进程：用户关窗 = 退出编辑，这里跟着收尾。会话服务跑在守护线程里，
    窗口关掉后进程退出，端点文件清掉 —— 不留孤儿端口。
    """
    server = None
    # 窗口的工作区根必须和会话的 --root 是同一个：会话按这个根放行绝对路径，
    # 窗口却按自己的 WORKSPACE 做越界守卫，两边不一致时桥会把异常吞成
    # INTERNAL_ERROR（M67）。常驻服务的 root 写在 /health 里，连上去照样对齐。
    root = Path(args.root).resolve() if args.root else None
    if args.serve:
        existing = _read_endpoint()
        if existing and _healthy(int(existing.get("port") or 0)):
            if root is None:
                health = _call("GET", "/health", timeout=2.0)
                raw = (health or {}).get("root")
                if raw:
                    root = Path(raw).resolve()
            print(json.dumps({"ok": True, "session": "already", "port": existing["port"]}))
        else:
            server, port = _make_server(args.port, root)
            thread = threading.Thread(
                target=server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True
            )
            thread.start()
            print(json.dumps({"ok": True, "session": "started", "port": port}))

    binary = _find_ppx()
    # 窗口有三条来路，按「依赖少」排序：--exe 指定 → 安装器装好的 exe
    # （`rpa-skill editor install`）→ 源码树里的 PPX 工程（`ppx dev`）。
    # 只认最后一条会让「装完技能也有窗口」看起来不存在 —— 而它就在 ~/.rpa-core 里。
    editor = _editor_dir()
    target_exe: Path | None = None
    if args.exe:
        # 用户给的可能是相对路径，而下面的 cwd 是 editor/ —— 必须先按当前目录解析成绝对路径，
        # 否则 Popen 会在 editor/ 底下找这个相对路径，报 [WinError 267] 目录名称无效。
        target_exe = Path(args.exe).expanduser()
        if not target_exe.is_absolute():
            target_exe = (Path.cwd() / target_exe).resolve()
        if not target_exe.is_file():
            hint = f"找不到可执行文件：{target_exe}"
            payload = {"ok": False, "code": "EXE_MISSING", "error": hint}
            print(json.dumps(payload, ensure_ascii=False))
            return 2
    else:
        target_exe = _editor_exe()

    if target_exe is not None:
        argv = [str(target_exe)]
        workdir = target_exe.parent
    elif editor is None:
        payload = {
            "ok": False,
            "code": "EDITOR_MISSING",
            "error": "这台机器上没有可用的编辑窗口。三种来路：① 装一个（Windows）："
                     "rpa-skill editor install；② 源码树形态：把技能重装到 clone 下来的仓库后 "
                     "studio.py gui 会走 ppx dev（需要先装 PPX）；③ 用 --exe 直接指定打包好的窗口。"
                     "不装窗口也能干活：改文件用文件交接、拾取元素用 rpa-core capture。",
        }
        print(json.dumps(payload, ensure_ascii=False))
        return 2
    elif binary:
        argv = [binary, "dev"]
        workdir = editor
    else:
        hint = "找不到 ppx：装好 PPX 运行时，或用 --exe 指定打包后的可执行文件"
        print(json.dumps({"ok": False, "code": "PPX_MISSING", "error": hint}, ensure_ascii=False))
        return 2
    if args.skip_frontend:
        argv.append("--skip-frontend")
    # 只改子进程的环境，不动本进程：窗口启动时读 RPA_WORKSPACE 定 WORKSPACE，
    # 于是会话推来的绝对路径与窗口的越界守卫落在同一个根上。
    child_env = dict(os.environ)
    if root is not None:
        child_env["RPA_WORKSPACE"] = str(root)
    try:
        # 打包后的窗口是自足的，cwd 落在它自己那层就够；ppx dev 必须进工程目录。
        child = subprocess.Popen(argv, cwd=str(workdir), env=child_env)
    except OSError as exc:
        print(json.dumps({"ok": False, "code": "SPAWN_FAILED", "error": str(exc)},
                         ensure_ascii=False))
        return 2
    code = child.wait()
    if server is not None:
        server.shutdown()
    print(json.dumps({"ok": True, "editorExit": code}))
    return 0


def _extension_check(rc_py: Path) -> tuple[bool, str]:
    """浏览器扩展源码目录在不在（与运行时形态无关）。

    权威是 ``rpa_core.extension_installer.extension_root()``（包内 ``extension/`` 优先，
    源码树读仓库根），本脚本不复制这套路径规则，直接问 CLI 要它的结论
    —— ``install-extension --status`` 会回填 ``_extension_dir``。

    不看形态是有原因的：``extension/`` 由 pyproject ``force-include`` 打进 wheel，
    pip / uv tool 形态一样有。早先这里把 pip/exe 形态标成「元素捕获需源码树」，
    实测是错的（exe 形态能打包 CRX，native host 也已注册），等于给用户一个
    假的能力边界。
    """
    try:
        proc = subprocess.run(
            [sys.executable, str(rc_py), "install-extension", "--status"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    raw = proc.stdout or ""
    try:
        data = json.loads(raw)
    except ValueError:
        # 解释器被宿主注入 sitecustomize 时，stderr 首行常是那条无关的 PermissionError
        # （WorkBuddy 的 shim）。挑掉它，免得 doctor 报出一条跟扩展无关的「原因」。
        lines = [
            ln
            for ln in (raw or proc.stderr or "").strip().splitlines()
            if ln.strip() and not ln.startswith("Error in sitecustomize")
        ]
        return False, (lines[0] if lines else f"退出码 {proc.returncode}")
    ext = data.get("_extension_dir")
    if not ext:
        return False, "状态里没有 _extension_dir（扩展资源没找到）"
    return (Path(ext) / "manifest.json").is_file(), str(ext)


def cmd_doctor(args: argparse.Namespace) -> int:
    # 第三项是状态：True 通过 / False 失败 / None 不适用（SKIP，不计入失败）。
    checks: list[tuple[str, bool | None, str]] = []
    checks.append(("python", sys.version_info >= (3, 10), sys.version.split()[0]))
    try:
        HOME.mkdir(parents=True, exist_ok=True)
        probe = HOME / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        checks.append(("state dir", True, str(HOME)))
    except OSError as exc:
        checks.append(("state dir", False, f"{HOME}: {exc}"))

    meta = _read_endpoint()
    health = _call("GET", "/health") if meta else {"ok": False}
    session_detail = f"port {meta.get('port')}" if meta else "未启动"
    checks.append(("session", bool(health.get("ok")), session_detail))
    # 先算一次：下面三处都靠它判「这套安装有没有窗口」，不要各算各的。
    editor_dir = _editor_dir()
    editor_exe = _editor_exe()
    have_editor = editor_exe is not None or editor_dir is not None
    if health.get("ok"):
        editor = health.get("editor") or {}
        if not have_editor:
            checks.append(("editor window", None, "不适用（这台机器没装编辑窗口）"))
        else:
            label = "在线" if editor.get("online") else "离线（先打开编辑窗口）"
            checks.append(("editor window", bool(editor.get("online")), label))

    # 「装上但跑不动」的根因通常在这一层：技能被拷进宿主目录后，与源码树的关系断了。
    # 所以先把定位结果、rpa-core 能不能调通摆在最前面。
    #
    # 但「没有源码树」不等于坏：pip 装进解释器（module）、PATH 上就有 rpa-core（exe）
    # 这两种正当形态本来就不带源码树，对它们要求 source root 会造出假红，还给出
    # 「去装源码树」的误导修法。rpa-core 到底能不能用，由下面那条单独证。
    info = _runtime.describe()
    mode = str((info.get("rpa_core") or {}).get("mode") or "")
    root = _runtime.find_source_root()
    if root:
        checks.append(("source root", True, str(root)))
    elif mode in ("module", "exe"):
        why = "PATH 上的 rpa-core" if mode == "exe" else "已装进解释器"
        checks.append(("source root", None, f"不适用（运行时是{why}，没有源码树）"))
    else:
        checks.append(("source root", False, "未找到（runtime.json 没记；重装加 --rpa-core-dir）"))
    rc_py = Path(__file__).resolve().with_name("rc.py")
    try:
        proc = subprocess.run(
            [sys.executable, str(rc_py), "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
        rc_ok = proc.returncode == 0
        first = (proc.stdout or proc.stderr or "").strip().splitlines()
        rc_detail = "可执行" if rc_ok else (first[0] if first else f"退出码 {proc.returncode}")
    except (OSError, subprocess.SubprocessError) as exc:
        rc_ok, rc_detail = False, f"{type(exc).__name__}: {exc}"
    checks.append(("rpa-core", rc_ok, rc_detail))

    # 编辑窗口现在有两条正当来路：装好的构建产物（rpa-skill editor install）与
    # 源码树里的 PPX 工程（ppx dev）。两条都没有 = SKIP，不是 FAIL —— 窗口是可选件，
    # 不装照样能生成流程、跑流程。
    if editor_exe is not None:
        version = _editor_manifest().get("version") or "?"
        checks.append(("editor", True, f"{version} · {editor_exe}"))
        if any(ord(ch) > 127 for ch in str(editor_exe)):
            # 实测过的坑：中文路径下 pythonnet 加载 Python.Runtime.dll 会直接失败。
            checks.append(
                ("editor path", False, f"含非 ASCII 字符：{editor_exe}")
            )
    elif editor_dir is not None:
        checks.append(("editor", True, f"ppx dev · {editor_dir}"))
    else:
        checks.append(
            ("editor", None, "没装（可选件：Windows 上 rpa-skill editor install）")
        )
    # rpa-core 都跑不动时别再叠一次 120s 超时，直接记红（上面那条已经把原因说了）。
    ext_ok, ext_detail = (
        _extension_check(rc_py) if rc_ok else (False, "跳过（rpa-core 不可执行）")
    )
    checks.append(("extension", ext_ok, ext_detail))

    failed = 0
    for name, passed, detail in checks:
        mark = "PASS" if passed is True else ("SKIP" if passed is None else "FAIL")
        print(f"[{mark}] {name:<15}{detail}")
        if passed is False:
            failed += 1
    return 0 if failed == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="studio", description="WorkBuddy Studio 会话服务 / CLI")
    parser.add_argument("--version", action="version", version=f"{SERVICE} {VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="在 127.0.0.1 上起会话服务")
    serve.add_argument("--port", type=int, default=DEFAULT_PORT)
    serve.add_argument("--root", default=os.environ.get("STUDIO_ROOT"), help="限制可打开的文件范围")
    serve.set_defaults(func=cmd_serve)

    open_cmd = sub.add_parser("open", help="请编辑窗口打开一个文件")
    open_cmd.add_argument("path")
    open_cmd.add_argument("--line", type=int, default=0)
    open_cmd.add_argument("--reason", default="")
    open_cmd.add_argument("--wait", type=float, default=None, help="等用户保存/关闭的秒数")
    open_cmd.add_argument("--pretty", action="store_true")
    open_cmd.set_defaults(func=cmd_open)

    wait = sub.add_parser("wait", help="等一个（或多个）事件")
    wait.add_argument("--timeout", type=float, default=30.0)
    wait.add_argument("--kind", default="save,close")
    wait.add_argument("--path", default=None)
    wait.add_argument("--pretty", action="store_true")
    wait.set_defaults(func=cmd_wait)

    capture = sub.add_parser("capture", help="等浏览器插件拾取一个元素")
    capture.add_argument("--timeout", type=float, default=60.0)
    capture.add_argument("--pretty", action="store_true")
    capture.set_defaults(func=cmd_capture)

    status = sub.add_parser("status", help="看一眼会话状态")
    status.add_argument("--pretty", action="store_true")
    status.set_defaults(func=cmd_status)

    stop = sub.add_parser("stop", help="停掉会话服务")
    stop.add_argument("--pretty", action="store_true")
    stop.set_defaults(func=cmd_stop)

    gui = sub.add_parser("gui", help="一条命令起会话 + 编辑窗口")
    gui.add_argument("--port", type=int, default=DEFAULT_PORT)
    gui.add_argument("--root", default=os.environ.get("STUDIO_ROOT"))
    gui.add_argument("--exe", default="", help="打包后的编辑窗口可执行文件（发布态）")
    gui.add_argument("--dev", action="store_true", help="开发态（没给 --exe 时本来就走 ppx dev）")
    gui.add_argument("--skip-frontend", action="store_true", help="前端已经在跑时复用")
    gui.add_argument("--no-serve", dest="serve", action="store_false", help="只开窗口，不起会话")
    gui.set_defaults(func=cmd_gui, serve=True)

    doctor = sub.add_parser("doctor", help="自检：环境 / 会话 / 编辑窗口 / 扩展")
    doctor.set_defaults(func=cmd_doctor)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
