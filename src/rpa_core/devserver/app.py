from typing import Any

from pydantic import ValidationError

from rpa_core.catalog import CommandCatalog
from rpa_core.compiler.compiler import WorkflowCompileError, WorkflowCompiler
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
    ):
        self._catalog = catalog
        self._store = store
        self._capabilities = frozenset(capabilities) if capabilities else DEFAULT_CAPABILITIES

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

    def capture_desktop(self, action: str) -> dict:
        if action not in _CAPTURE_ACTIONS:
            raise ApiError(404, "NOT_FOUND", f"unknown capture action: {action}")
        raise ApiError(501, "NOT_IMPLEMENTED", "desktop capture is delivered in M10 (ADR 0007)")

    def capture_browser(self, action: str, body: Any) -> dict:
        if action not in _CAPTURE_ACTIONS:
            raise ApiError(404, "NOT_FOUND", f"unknown capture action: {action}")
        if action == "start":
            transport = body.get("transport") if isinstance(body, dict) else None
            if transport not in _BROWSER_TRANSPORTS:
                raise ApiError(
                    400,
                    "BAD_REQUEST",
                    "'transport' must be one of: persistent, user-browser",
                )
        raise ApiError(501, "NOT_IMPLEMENTED", "browser capture is delivered in M10 (ADR 0007)")

    def _compile_result(self, valid: bool, errors: list[dict]) -> dict:
        return {"valid": valid, "errors": errors, "catalog_digest": self._catalog.digest}


def _validation_errors(exc: ValidationError) -> list[dict]:
    errors = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "<root>"
        errors.append({"path": location, "message": error["msg"]})
    return errors
