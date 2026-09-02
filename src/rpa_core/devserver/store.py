import json
import os
import re
import tempfile
from pathlib import Path

_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


class WorkflowStoreError(ValueError):
    pass


class WorkflowNameError(WorkflowStoreError):
    pass


class WorkflowNotFoundError(WorkflowStoreError):
    pass


class WorkflowStore:
    def __init__(self, root: Path):
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def list(self) -> list[str]:
        return sorted(path.stem for path in self._root.glob("*.json") if path.is_file())

    def read(self, name: str) -> dict:
        path = self._resolve(name)
        if not path.is_file():
            raise WorkflowNotFoundError(f"workflow not found: {name}")
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise WorkflowStoreError(f"workflow file is not valid JSON: {name}") from exc
        if not isinstance(document, dict):
            raise WorkflowStoreError(f"workflow file must contain a JSON object: {name}")
        return document

    def write(self, name: str, document: dict) -> int:
        if not isinstance(document, dict):
            raise WorkflowStoreError("workflow document must be a JSON object")
        path = self._resolve(name)
        encoded = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        handle_fd, tmp_name = tempfile.mkstemp(dir=self._root, suffix=".tmp")
        try:
            with os.fdopen(handle_fd, "wb") as handle:
                handle.write(encoded)
            os.replace(tmp_name, path)
        except OSError:
            Path(tmp_name).unlink(missing_ok=True)
            raise
        return len(encoded)

    def delete(self, name: str) -> None:
        path = self._resolve(name)
        if not path.is_file():
            raise WorkflowNotFoundError(f"workflow not found: {name}")
        path.unlink()

    def _resolve(self, name: str) -> Path:
        if not _NAME.fullmatch(name):
            raise WorkflowNameError(f"invalid workflow name: {name!r}")
        path = (self._root / f"{name}.json").resolve()
        if path.parent != self._root:
            raise WorkflowNameError(f"workflow path escapes root: {name!r}")
        return path
