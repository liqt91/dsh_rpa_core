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

_WORKFLOW_SEGMENT_PREFIX = "/api/workflows/"
_CAPTURE_PREFIX = "/api/capture/"


class _RequestHandler(BaseHTTPRequestHandler):
    server_version = "rpa_core_devserver/0.1"
    protocol_version = "HTTP/1.1"

    @property
    def app(self) -> DevServerApp:
        return self.server.app  # type: ignore[attr-defined]

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
            status, payload = self._route(method)
        except ApiError as exc:
            self._send(exc.status, {"error": exc.code, "message": exc.message})
            return
        self._send(status, payload)

    def _route(self, method: str) -> tuple[int, dict]:
        path = unquote(urlparse(self.path).path)
        if path == "/api/catalog":
            if method != "GET":
                raise ApiError(405, "METHOD_NOT_ALLOWED", "use GET for /api/catalog")
            return 200, self.app.catalog_overview()
        if path == "/api/compile":
            if method != "POST":
                raise ApiError(405, "METHOD_NOT_ALLOWED", "use POST for /api/compile")
            return 200, self.app.compile(self._read_json(required=True))
        if path == "/api/workflows":
            if method != "GET":
                raise ApiError(405, "METHOD_NOT_ALLOWED", "use GET for /api/workflows")
            return 200, self.app.list_workflows()
        if path.startswith(_WORKFLOW_SEGMENT_PREFIX):
            name = path[len(_WORKFLOW_SEGMENT_PREFIX) :]
            if "/" in name or not name:
                raise ApiError(404, "NOT_FOUND", f"no route for {path}")
            if method == "GET":
                return 200, self.app.get_workflow(name)
            if method == "PUT":
                return 200, self.app.put_workflow(name, self._read_json(required=True))
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
                return 200, self.app.capture_desktop(action)
            if kind == "browser":
                return 200, self.app.capture_browser(action, body)
            raise ApiError(404, "NOT_FOUND", f"no route for {path}")
        raise ApiError(404, "NOT_FOUND", f"no route for {path}")

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

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if status >= 400:
            self.close_connection = True
        self.end_headers()
        self.wfile.write(body)


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
        self._httpd = ThreadingHTTPServer(("127.0.0.1", port), _RequestHandler)
        self._httpd.app = self.app  # type: ignore[attr-defined]
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
