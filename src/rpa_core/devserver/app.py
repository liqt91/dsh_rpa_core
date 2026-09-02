import threading
from typing import Any

from pydantic import ValidationError

from rpa_core.catalog import CommandCatalog
from rpa_core.compiler.compiler import WorkflowCompileError, WorkflowCompiler
from rpa_core.model.capture import ElementDescriptor
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
_BROWSER_TRANSPORTS = frozenset({"persistent", "user-browser"})


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
        element_store: WorkflowStore | None = None,
        browser_capture_factory=None,
        desktop_capture_factory=None,
    ):
        self._catalog = catalog
        self._store = store
        self._capabilities = frozenset(capabilities) if capabilities else DEFAULT_CAPABILITIES
        self._element_store = element_store
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
            with self._capture_lock:
                session_id = self._next_capture_id("desktop")
                self._desktop_sessions[session_id] = factory(**kwargs)
            return {
                "sessionId": session_id,
                "mode": "point" if kwargs.get("point") else "hotkey",
            }
        if action == "pick":
            session_id = self._capture_session_id(self._desktop_sessions, body)
            session = self._desktop_sessions[session_id]
            timeout = float(body.get("timeoutSeconds", 90))
            result = session.pick(timeout_seconds=timeout)
            if result.get("kind") == "desktop" and body.get("saveAs"):
                result.update(self._save_element(result, str(body["saveAs"])))
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
                "'transport' must be one of: persistent, user-browser",
            )
        factory = self._require_capture(self._browser_capture_factory, "browser capture")
        if action == "start":
            kwargs: dict[str, Any] = {"transport": transport}
            if transport == "persistent":
                if body.get("userDataDir"):
                    kwargs["user_data_dir"] = str(body["userDataDir"])
                kwargs["headless"] = bool(body.get("headless", False))
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
                result.update(self._save_element(result, str(body["saveAs"])))
            return result
        session_id = self._capture_session_id(self._browser_sessions, body)
        session = self._browser_sessions.pop(session_id)
        session.cancel()
        session.close()
        return {"cancelled": True, "sessionId": session_id}

    def _save_element(self, descriptor: dict, save_as: str) -> dict:
        if self._element_store is None:
            raise ApiError(501, "NOT_IMPLEMENTED", "element store is not configured")
        element = ElementDescriptor(
            kind=descriptor["kind"],
            selector=descriptor["selector"],
            verify_count=descriptor.get("verifyCount", 0),
            metadata=descriptor.get("metadata", {}),
        )
        self._element_store.write(save_as, element.document())
        return {"savedAs": save_as}

    def list_elements(self) -> dict:
        if self._element_store is None:
            raise ApiError(501, "NOT_IMPLEMENTED", "element store is not configured")
        return {"elements": self._element_store.list()}

    def get_element(self, name: str) -> dict:
        if self._element_store is None:
            raise ApiError(501, "NOT_IMPLEMENTED", "element store is not configured")
        try:
            return self._element_store.read(name)
        except WorkflowNotFoundError as exc:
            raise ApiError(404, "NOT_FOUND", str(exc)) from exc
        except WorkflowNameError as exc:
            raise ApiError(403, "FORBIDDEN", str(exc)) from exc
        except WorkflowStoreError as exc:
            raise ApiError(400, "BAD_REQUEST", str(exc)) from exc

    def put_element(self, name: str, body: Any) -> dict:
        if self._element_store is None:
            raise ApiError(501, "NOT_IMPLEMENTED", "element store is not configured")
        if not isinstance(body, dict):
            raise ApiError(400, "BAD_REQUEST", "element document must be a JSON object")
        element = _validate_element_document(body)
        self._element_store.write(name, element.document())
        return {"name": name}

    def delete_element(self, name: str) -> dict:
        if self._element_store is None:
            raise ApiError(501, "NOT_IMPLEMENTED", "element store is not configured")
        try:
            self._element_store.delete(name)
        except WorkflowNotFoundError as exc:
            raise ApiError(404, "NOT_FOUND", str(exc)) from exc
        except WorkflowNameError as exc:
            raise ApiError(403, "FORBIDDEN", str(exc)) from exc
        return {"name": name, "deleted": True}

    def verify_element(self, name: str) -> dict:
        """结构校验：描述符模型 + selector 语义。活体验证需捕获会话内完成。"""
        if self._element_store is None:
            raise ApiError(501, "NOT_IMPLEMENTED", "element store is not configured")
        try:
            document = self._element_store.read(name)
        except WorkflowNotFoundError as exc:
            raise ApiError(404, "NOT_FOUND", str(exc)) from exc
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
        return ElementDescriptor.model_validate(body)
    except ValidationError as exc:
        detail = _validation_errors(exc)[0]
        raise ApiError(400, "BAD_REQUEST", f"{detail['path']}: {detail['message']}") from exc


def _selector_errors(element: ElementDescriptor) -> list[dict]:
    errors: list[dict] = []
    if element.kind == "browser":
        css = element.selector.get("css")
        if not isinstance(css, str) or not css.strip():
            errors.append({"path": "selector.css", "message": "browser 元素需要非空 css selector"})
    elif element.kind == "desktop":
        locator = element.selector.get("locator")
        if not isinstance(locator, dict):
            errors.append({"path": "selector.locator", "message": "desktop 元素需要 locator 对象"})
            return errors
        try:
            from rpa_core.model.desktop import DesktopLocator

            DesktopLocator.model_validate(locator)
        except ValidationError as exc:
            for error in exc.errors():
                location = ".".join(str(part) for part in error["loc"]) or "<root>"
                errors.append({"path": f"selector.locator.{location}", "message": error["msg"]})
    return errors
