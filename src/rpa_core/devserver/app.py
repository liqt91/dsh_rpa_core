import threading
from typing import Any

from pydantic import ValidationError

from rpa_core.catalog import CommandCatalog
from rpa_core.compiler.compiler import WorkflowCompileError, WorkflowCompiler
from rpa_core.model.capture import (
    ElementDescriptor,
    ElementDocumentError,
    selector_errors,
    validate_element_document,
)
from rpa_core.model.workflow import Workflow

from .store import (
    WorkflowNameError,
    WorkflowNotFoundError,
    WorkflowStore,
    WorkflowStoreError,
)

DEFAULT_CAPABILITIES = frozenset(
    {
        "browser.read",
        "browser.control",
        "desktop.control",
        "process.start",
        "workspace.write",
    }
)

_CAPTURE_ACTIONS = frozenset({"start", "pick", "cancel"})
_BROWSER_TRANSPORTS = frozenset({"persistent", "user-browser", "bsk", "extension"})


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class DevServerApp:
    def __init__(
        self,
        catalog: CommandCatalog,
        store: WorkflowStore,
        capabilities: set[str] | None = None,
        browser_capture_factory=None,
        desktop_capture_factory=None,
    ):
        self._catalog = catalog
        self._store = store
        self._capabilities = frozenset(capabilities) if capabilities else DEFAULT_CAPABILITIES
        self._browser_capture_factory = browser_capture_factory
        self._desktop_capture_factory = desktop_capture_factory
        self._browser_sessions: dict[str, Any] = {}
        self._desktop_sessions: dict[str, Any] = {}
        self._capture_lock = threading.RLock()
        self._capture_seq = 0

    def close(self) -> None:
        with self._capture_lock:
            sessions = list(self._browser_sessions.values()) + list(
                self._desktop_sessions.values()
            )
            self._browser_sessions.clear()
            self._desktop_sessions.clear()
        for session in sessions:
            try:
                session.close()
            except Exception:
                pass

    def _next_capture_id(self, prefix: str) -> str:
        with self._capture_lock:
            self._capture_seq += 1
            return f"{prefix}-{self._capture_seq}"

    def _capture_session_id(self, registry: dict, body: dict) -> str:
        session_id = str(body.get("sessionId") or "")
        if not session_id or session_id not in registry:
            raise ApiError(404, "NOT_FOUND", f"capture session not found: {session_id}")
        return session_id

    def _require_capture(self, factory, label: str):
        if factory is None:
            raise ApiError(501, "NOT_IMPLEMENTED", f"{label} backend is not configured")
        return factory

    def _element_store(self, flow: str) -> WorkflowStore:
        """流程目录下的元素资产根：<workflows>/<flow>/elements（不自动建目录）。"""
        folder = self._store.directory(flow)
        return WorkflowStore(folder / "elements", create=False)

    def _flow_from_body(self, body: dict, *, require: bool) -> str | None:
        flow = str(body.get("flow") or "")
        if require and not flow:
            raise ApiError(400, "BAD_REQUEST", "missing 'flow' (workflow name)")
        return flow or None

    def catalog_overview(self) -> dict:
        commands = []
        for command_id in sorted(self._catalog):
            manifest = self._catalog[command_id]
            commands.append(
                {
                    "id": manifest.id,
                    "version": manifest.version,
                    "kind": manifest.kind.value,
                    "effect": manifest.effect.model_dump(mode="json"),
                    "input_schema": manifest.input_schema,
                    "output_schema": manifest.output_schema,
                    "errors": [error.value for error in manifest.errors],
                }
            )
        return {"digest": self._catalog.digest, "commands": commands}

    def compile(self, body: Any) -> dict:
        if not isinstance(body, dict):
            raise ApiError(400, "BAD_REQUEST", "request body must be a JSON object")
        document = body.get("workflow")
        if not isinstance(document, dict):
            raise ApiError(400, "BAD_REQUEST", "request body must contain a 'workflow' object")
        capabilities = body.get("capabilities", sorted(self._capabilities))
        if not isinstance(capabilities, list) or not all(
            isinstance(item, str) for item in capabilities
        ):
            raise ApiError(400, "BAD_REQUEST", "'capabilities' must be a list of strings")
        try:
            workflow = Workflow.model_validate(document)
        except ValidationError as exc:
            return self._compile_result(False, _validation_errors(exc))
        try:
            WorkflowCompiler(self._catalog).compile(workflow, set(capabilities))
        except WorkflowCompileError as exc:
            return self._compile_result(False, [{"message": str(exc)}])
        return self._compile_result(True, [])

    def list_workflows(self) -> dict:
        return {"workflows": self._store.list()}

    def get_workflow(self, name: str) -> dict:
        try:
            return self._store.read(name)
        except WorkflowNotFoundError as exc:
            raise ApiError(404, "NOT_FOUND", str(exc)) from exc
        except WorkflowNameError as exc:
            raise ApiError(403, "FORBIDDEN", str(exc)) from exc
        except WorkflowStoreError as exc:
            raise ApiError(400, "BAD_REQUEST", str(exc)) from exc

    def put_workflow(self, name: str, body: Any) -> dict:
        try:
            size = self._store.write(name, body)
        except WorkflowNameError as exc:
            raise ApiError(403, "FORBIDDEN", str(exc)) from exc
        except WorkflowStoreError as exc:
            raise ApiError(400, "BAD_REQUEST", str(exc)) from exc
        return {"name": name, "bytes": size}

    def capture_desktop(self, action: str, body: Any) -> dict:
        if action not in _CAPTURE_ACTIONS:
            raise ApiError(404, "NOT_FOUND", f"unknown capture action: {action}")
        body = body if isinstance(body, dict) else {}
        factory = self._require_capture(self._desktop_capture_factory, "desktop capture")
        if action == "start":
            kwargs: dict[str, Any] = {}
            if body.get("hotkey"):
                kwargs["hotkey"] = str(body["hotkey"])
            if body.get("timeoutSeconds"):
                kwargs["timeout_seconds"] = float(body["timeoutSeconds"])
            if isinstance(body.get("point"), dict):
                kwargs["point"] = body["point"]
            if body.get("windowHandle"):
                kwargs["window_handle"] = int(body["windowHandle"])
            if body.get("hover"):
                kwargs["hover"] = True
                # 混合捕获：已配对扩展时自动双通道（网页走扩展、桌面走 UIA），
                # 可用 body hybrid:false 显式关闭
                if body.get("hybrid", True) and self._read_token() is not None:
                    kwargs["hybrid"] = True
            with self._capture_lock:
                session_id = self._next_capture_id("desktop")
                self._desktop_sessions[session_id] = factory(**kwargs)
            return {
                "sessionId": session_id,
                "mode": "hybrid" if kwargs.get("hybrid")
                else "hover" if kwargs.get("hover")
                else "point" if kwargs.get("point") else "hotkey",
            }
        if action == "pick":
            session_id = self._capture_session_id(self._desktop_sessions, body)
            session = self._desktop_sessions[session_id]
            timeout = float(body.get("timeoutSeconds", 90))
            result = session.pick(timeout_seconds=timeout)
            if result.get("kind") in ("desktop", "browser") and body.get("saveAs"):
                flow = self._flow_from_body(body, require=True)
                result.update(self._save_element(flow, result, str(body["saveAs"])))
            return result
        session_id = self._capture_session_id(self._desktop_sessions, body)
        self._desktop_sessions.pop(session_id, None).cancel()
        return {"cancelled": True, "sessionId": session_id}

    def capture_browser(self, action: str, body: Any) -> dict:
        if action not in _CAPTURE_ACTIONS:
            raise ApiError(404, "NOT_FOUND", f"unknown capture action: {action}")
        body = body if isinstance(body, dict) else {}
        transport = body.get("transport") if action == "start" else "persistent"
        if action == "start" and transport not in _BROWSER_TRANSPORTS:
            raise ApiError(
                400,
                "BAD_REQUEST",
                "'transport' must be one of: persistent, user-browser, bsk",
            )
        factory = self._require_capture(self._browser_capture_factory, "browser capture")
        if action == "start":
            kwargs: dict[str, Any] = {"transport": transport}
            if transport == "persistent":
                if body.get("userDataDir"):
                    kwargs["user_data_dir"] = str(body["userDataDir"])
                kwargs["headless"] = bool(body.get("headless", False))
            elif transport == "bsk":
                if body.get("browserInstanceId"):
                    kwargs["browser_instance_id"] = str(body["browserInstanceId"])
                if body.get("pageUrl"):
                    kwargs["page_url"] = str(body["pageUrl"])
            elif transport == "extension":
                pass  # content-script 扩展：无启动参数，picker 已在所有页面待命
            else:
                kwargs["browser_type"] = str(body.get("browserType", "edge"))
                if body.get("userDataDir"):
                    kwargs["user_data_dir"] = str(body["userDataDir"])
                if body.get("pageUrl"):
                    kwargs["page_url"] = str(body["pageUrl"])
            if body.get("startUrl"):
                kwargs["start_url"] = str(body["startUrl"])
            with self._capture_lock:
                session_id = self._next_capture_id("browser")
                session = self._browser_sessions[session_id] = factory(**kwargs)
            try:
                pages = session.start()
            except Exception as exc:
                self._browser_sessions.pop(session_id, None)
                session.close()
                raise ApiError(502, "CAPTURE_START_FAILED", str(exc)) from exc
            return {"sessionId": session_id, "pages": pages}
        if action == "pick":
            session_id = self._capture_session_id(self._browser_sessions, body)
            session = self._browser_sessions[session_id]
            timeout = float(body.get("timeoutSeconds", 60))
            result = session.pick(
                timeout_seconds=timeout,
                click_css=body.get("clickCss"),
            )
            if result.get("kind") == "browser" and body.get("saveAs"):
                flow = self._flow_from_body(body, require=True)
                result.update(self._save_element(flow, result, str(body["saveAs"])))
            return result
        session_id = self._capture_session_id(self._browser_sessions, body)
        session = self._browser_sessions.pop(session_id)
        session.cancel()
        session.close()
        return {"cancelled": True, "sessionId": session_id}

    # -- content-script 扩展捕获（M14 无缝路线）：token 配对 + pending/result --

    @property
    def _token_path(self):
        return self._store.root / ".capture-extension-token"

    def _read_token(self) -> str | None:
        try:
            return self._token_path.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None

    def extension_token(self, body: Any) -> dict:
        """配对 token：PUT/POST body {"token": "..."} 显式写入；GET 返回当前状态与值。"""
        if isinstance(body, dict) and body.get("token"):
            self._token_path.write_text(str(body["token"]), encoding="utf-8")
            return {"configured": True, "token": str(body["token"])}
        return {"configured": self._read_token() is not None,
                "token": self._read_token()}

    def _require_extension_token(self, headers) -> None:
        expected = self._read_token()
        provided = headers.get("X-Capture-Token", "")
        if not provided:
            raise ApiError(403, "FORBIDDEN", "missing capture extension token")
        if expected is None:
            # TOFU（trust on first use）：loopback 本地工具，首次接触自动采纳并持久化，
            # 免手动配对；此后只认这个 token，轮换需删 .capture-extension-token
            self._token_path.write_text(provided, encoding="utf-8")
            return
        if provided != expected:
            raise ApiError(403, "FORBIDDEN", "invalid capture extension token")

    def _pending_extension_session(self) -> str | None:
        for registry in (self._browser_sessions, self._desktop_sessions):
            for session_id, session in registry.items():
                if getattr(session, "is_extension_capture", False) and session.pending:
                    return session_id
        return None

    def extension_pending(self, headers) -> dict:
        """扩展 background 轮询：是否有激活的扩展捕获会话。"""
        self._require_extension_token(headers)
        session_id = self._pending_extension_session()
        return {"pending": session_id is not None, "sessionId": session_id}

    def extension_result(self, headers, body: Any) -> dict:
        """扩展 content script 捕获结果回传。"""
        self._require_extension_token(headers)
        if not isinstance(body, dict):
            raise ApiError(400, "BAD_REQUEST", "result body must be a JSON object")
        session_id = str(body.get("sessionId") or self._pending_extension_session() or "")
        session = self._browser_sessions.get(session_id) or self._desktop_sessions.get(
            session_id
        )
        if session is None or not getattr(session, "is_extension_capture", False):
            raise ApiError(404, "NOT_FOUND", f"no pending extension session: {session_id}")
        session.submit(body.get("descriptor", body))
        return {"received": True, "sessionId": session_id}

    def _save_element(self, flow: str, descriptor: dict, save_as: str) -> dict:
        store = self._element_store(flow)
        element = ElementDescriptor(
            kind=descriptor["kind"],
            selector=descriptor["selector"],
            verify_count=descriptor.get("verifyCount", 0),
            metadata=descriptor.get("metadata", {}),
        )
        store.write(save_as, element.document())
        return {"savedAs": save_as, "flow": flow}

    def list_elements(self, flow: str) -> dict:
        try:
            return {"elements": self._element_store(flow).list()}
        except WorkflowNameError as exc:
            raise ApiError(403, "FORBIDDEN", str(exc)) from exc

    def get_element(self, flow: str, name: str) -> dict:
        try:
            return self._element_store(flow).read(name)
        except WorkflowNotFoundError as exc:
            raise ApiError(404, "NOT_FOUND", str(exc)) from exc
        except WorkflowNameError as exc:
            raise ApiError(403, "FORBIDDEN", str(exc)) from exc
        except WorkflowStoreError as exc:
            raise ApiError(400, "BAD_REQUEST", str(exc)) from exc

    def put_element(self, flow: str, name: str, body: Any) -> dict:
        if not isinstance(body, dict):
            raise ApiError(400, "BAD_REQUEST", "element document must be a JSON object")
        element = _validate_element_document(body)
        try:
            self._element_store(flow).write(name, element.document())
        except WorkflowNameError as exc:
            raise ApiError(403, "FORBIDDEN", str(exc)) from exc
        return {"name": name, "flow": flow}

    def delete_element(self, flow: str, name: str) -> dict:
        try:
            self._element_store(flow).delete(name)
        except WorkflowNotFoundError as exc:
            raise ApiError(404, "NOT_FOUND", str(exc)) from exc
        except WorkflowNameError as exc:
            raise ApiError(403, "FORBIDDEN", str(exc)) from exc
        return {"name": name, "flow": flow, "deleted": True}

    def verify_element(self, flow: str, name: str) -> dict:
        """结构校验：描述符模型 + selector 语义。活体验证需捕获会话内完成。"""
        try:
            document = self._element_store(flow).read(name)
        except WorkflowNotFoundError as exc:
            raise ApiError(404, "NOT_FOUND", str(exc)) from exc
        except WorkflowNameError as exc:
            raise ApiError(403, "FORBIDDEN", str(exc)) from exc
        errors: list[dict] = []
        try:
            element = _validate_element_document(document)
        except ApiError as exc:
            return {"valid": False, "errors": [{"message": exc.message}], "verifyCount": None}
        errors.extend(_selector_errors(element))
        return {
            "valid": not errors,
            "errors": errors,
            "verifyCount": None,
            "note": "结构校验；活体验证（命中数）需在捕获会话内完成",
        }

    def _compile_result(self, valid: bool, errors: list[dict]) -> dict:
        return {"valid": valid, "errors": errors, "catalog_digest": self._catalog.digest}


def _validation_errors(exc: ValidationError) -> list[dict]:
    errors = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "<root>"
        errors.append({"path": location, "message": error["msg"]})
    return errors


def _validate_element_document(body: dict) -> ElementDescriptor:
    try:
        return validate_element_document(body)
    except ElementDocumentError as exc:
        raise ApiError(400, "BAD_REQUEST", str(exc)) from exc


def _selector_errors(element: ElementDescriptor) -> list[dict]:
    return selector_errors(element)
