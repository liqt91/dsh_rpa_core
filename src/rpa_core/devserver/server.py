import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from rpa_core.catalog import CommandCatalog, load_catalog

from .app import ApiError, DevServerApp
from .store import WorkflowStore

MAX_BODY_BYTES = 1024 * 1024
_DEFAULT_PORT = 8765
_JSON_TYPE = "application/json; charset=utf-8"

_WORKFLOW_SEGMENT_PREFIX = "/api/workflows/"
_CAPTURE_PREFIX = "/api/capture/"
_STATIC_PREFIX = "/static/"

# ADR 0008 §4：硬编码静态资源 allowlist，不开放任意路径、不做目录列举。
_STATIC_FILES = {
    "app.js": "text/javascript; charset=utf-8",
    "styles.css": "text/css; charset=utf-8",
    "i18n.js": "text/javascript; charset=utf-8",
    "icons.js": "text/javascript; charset=utf-8",
}
_STATIC_ROOT = Path(__file__).resolve().parent / "static"


class _RequestHandler(BaseHTTPRequestHandler):
    server_version = "rpa_core_devserver/0.1"
    protocol_version = "HTTP/1.1"

    @property
    def app(self) -> DevServerApp:
        return self.server.app  # type: ignore[attr-defined]

    @property
    def editor_html(self) -> bytes:
        return self.server.editor_html  # type: ignore[attr-defined]

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def do_PUT(self) -> None:
        self._handle("PUT")

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        pass

    def _handle(self, method: str) -> None:
        try:
            status, body, content_type = self._route(method)
        except ApiError as exc:
            self._send_json(exc.status, {"error": exc.code, "message": exc.message})
            return
        self._send(status, body, content_type)

    def _route(self, method: str) -> tuple[int, bytes, str]:
        path = unquote(urlparse(self.path).path)
        if path == "/":
            if method != "GET":
                raise ApiError(405, "METHOD_NOT_ALLOWED", "use GET for the editor page")
            return 200, self.editor_html, "text/html; charset=utf-8"
        if path.startswith(_STATIC_PREFIX):
            if method != "GET":
                raise ApiError(405, "METHOD_NOT_ALLOWED", "use GET for static assets")
            return self._route_static(path)
        payload = self._route_api(method, path)
        return 200, _encode(payload), _JSON_TYPE

    def _route_static(self, path: str) -> tuple[int, bytes, str]:
        name = path[len(_STATIC_PREFIX) :]
        content_type = _STATIC_FILES.get(name)
        if content_type is None:
            raise ApiError(404, "NOT_FOUND", f"no route for {path}")
        file_path = _STATIC_ROOT / name
        if file_path.resolve().parent != _STATIC_ROOT:
            raise ApiError(403, "FORBIDDEN", f"no route for {path}")
        return 200, file_path.read_bytes(), content_type

    def _route_api(self, method: str, path: str) -> dict:
        if path == "/api/catalog":
            if method != "GET":
                raise ApiError(405, "METHOD_NOT_ALLOWED", "use GET for /api/catalog")
            return self.app.catalog_overview()
        if path == "/api/compile":
            if method != "POST":
                raise ApiError(405, "METHOD_NOT_ALLOWED", "use POST for /api/compile")
            return self.app.compile(self._read_json(required=True))
        if path == "/api/workflows":
            if method != "GET":
                raise ApiError(405, "METHOD_NOT_ALLOWED", "use GET for /api/workflows")
            return self.app.list_workflows()
        if path.startswith(_WORKFLOW_SEGMENT_PREFIX):
            name = path[len(_WORKFLOW_SEGMENT_PREFIX) :]
            if "/" in name or not name:
                raise ApiError(404, "NOT_FOUND", f"no route for {path}")
            if method == "GET":
                return self.app.get_workflow(name)
            if method == "PUT":
                return self.app.put_workflow(name, self._read_json(required=True))
            raise ApiError(405, "METHOD_NOT_ALLOWED", "use GET or PUT for workflow resources")
        if path.startswith(_CAPTURE_PREFIX):
            if method != "POST":
                raise ApiError(405, "METHOD_NOT_ALLOWED", "use POST for capture endpoints")
            segments = path[len(_CAPTURE_PREFIX) :].split("/")
            if len(segments) != 2:
                raise ApiError(404, "NOT_FOUND", f"no route for {path}")
            kind, action = segments
            body = self._read_json(required=False)
            if kind == "desktop":
                return self.app.capture_desktop(action)
            if kind == "browser":
                return self.app.capture_browser(action, body)
            raise ApiError(404, "NOT_FOUND", f"no route for {path}")
        raise ApiError(404, "NOT_FOUND", f"no route for {path}")

    def _drain_body(self, length: int) -> None:
        remaining = length
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 65536))
            if not chunk:
                break
            remaining -= len(chunk)

    def _read_json(self, required: bool) -> Any:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            if required:
                raise ApiError(400, "BAD_REQUEST", "request body is required")
            return {}
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ApiError(400, "BAD_REQUEST", "invalid Content-Length header") from exc
        if length < 0:
            raise ApiError(400, "BAD_REQUEST", "invalid Content-Length header")
        if length > MAX_BODY_BYTES:
            self._drain_body(length)
            raise ApiError(413, "PAYLOAD_TOO_LARGE", f"request body exceeds {MAX_BODY_BYTES} bytes")
        if length == 0:
            if required:
                raise ApiError(400, "BAD_REQUEST", "request body is required")
            return {}
        body = self.rfile.read(length)
        try:
            return json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ApiError(400, "BAD_REQUEST", "request body is not valid JSON") from exc

    def _send_json(self, status: int, payload: dict) -> None:
        self._send(status, _encode(payload), _JSON_TYPE)

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if status >= 400:
            self.close_connection = True
        self.end_headers()
        self.wfile.write(body)


def _encode(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


class DevServer:
    def __init__(
        self,
        *,
        commands_root: Path,
        workflows_root: Path,
        port: int = _DEFAULT_PORT,
        catalog: CommandCatalog | None = None,
        capabilities: set[str] | None = None,
    ):
        if catalog is None:
            catalog = load_catalog(commands_root)
        self.app = DevServerApp(catalog, WorkflowStore(workflows_root), capabilities)
        editor_path = Path(__file__).resolve().parent / "static" / "index.html"
        self.editor_html = editor_path.read_bytes()
        self._httpd = ThreadingHTTPServer(("127.0.0.1", port), _RequestHandler)
        self._httpd.app = self.app  # type: ignore[attr-defined]
        self._httpd.editor_html = self.editor_html  # type: ignore[attr-defined]
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self._httpd.server_address[1]

    @property
    def host(self) -> str:
        return self._httpd.server_address[0]

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("dev server already started")
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def serve(self) -> None:
        try:
            self._httpd.serve_forever()
        finally:
            self._httpd.server_close()

    def __enter__(self) -> "DevServer":
        self.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.stop()
