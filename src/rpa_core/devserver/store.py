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
    """平铺存储：<root>/<name>.json（元素资产、平铺文件场景）。"""

    def __init__(self, root: Path, *, create: bool = True):
        self._root = root.resolve()
        if create:
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
        return self._write_atomic(self._resolve(name), document)

    def delete(self, name: str) -> None:
        path = self._resolve(name)
        if not path.is_file():
            raise WorkflowNotFoundError(f"workflow not found: {name}")
        path.unlink()

    def _write_atomic(self, path: Path, document: dict) -> int:
        encoded = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        self._root.mkdir(parents=True, exist_ok=True)
        handle_fd, tmp_name = tempfile.mkstemp(dir=self._root, suffix=".tmp")
        try:
            with os.fdopen(handle_fd, "wb") as handle:
                handle.write(encoded)
            os.replace(tmp_name, path)
        except OSError:
            Path(tmp_name).unlink(missing_ok=True)
            raise
        return len(encoded)

    def _resolve(self, name: str) -> Path:
        if not _NAME.fullmatch(name):
            raise WorkflowNameError(f"invalid workflow name: {name!r}")
        path = (self._root / f"{name}.json").resolve()
        if path.parent != self._root:
            raise WorkflowNameError(f"workflow path escapes root: {name!r}")
        return path


class WorkflowDirStore(WorkflowStore):
    """目录存储：每流程一个目录 <root>/<name>/workflow.json。

    list 只统计含 workflow.json 的子目录（可含元素资产目录 elements/ 等附属物）。
    delete 移除 workflow.json，目录若变空则一并删除。
    """

    def list(self) -> list[str]:
        return sorted(
            path.name
            for path in self._root.iterdir()
            if path.is_dir() and (path / "workflow.json").is_file()
        )

    def read(self, name: str) -> dict:
        return super().read(name)

    def write(self, name: str, document: dict) -> int:
        if not isinstance(document, dict):
            raise WorkflowStoreError("workflow document must be a JSON object")
        path = self._resolve(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        return self._write_atomic(path, document)

    def delete(self, name: str) -> None:
        path = self._resolve(name)
        if not path.is_file():
            raise WorkflowNotFoundError(f"workflow not found: {name}")
        path.unlink()
        try:
            path.parent.rmdir()
        except OSError:
            pass

    def directory(self, name: str) -> Path:
        """返回流程目录（不要求已存在），供读取其附属资产。"""
        if not _NAME.fullmatch(name):
            raise WorkflowNameError(f"invalid workflow name: {name!r}")
        folder = (self._root / name).resolve()
        if folder.parent != self._root:
            raise WorkflowNameError(f"workflow path escapes root: {name!r}")
        return folder

    def _resolve(self, name: str) -> Path:
        return self.directory(name) / "workflow.json"
