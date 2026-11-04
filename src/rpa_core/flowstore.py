import csv
import io
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

    # ---- 流程管理动作（M27 S3，工作台） -----------------------------------
    def delete_flow(self, name: str, *, purge: bool = True) -> None:
        """删除流程。

        `purge=True`（工作台默认）连同流程目录下的一切附属资产（elements/、data/ 等）
        整目录删除；`purge=False` 保持旧行为（只删 workflow.json，目录空则删）。
        """
        import shutil

        directory = self.directory(name)
        if not (directory / "workflow.json").is_file():
            raise WorkflowNotFoundError(f"workflow not found: {name}")
        if purge:
            shutil.rmtree(directory)
            return
        self.delete(name)

    def rename_flow(self, name: str, new_name: str) -> None:
        """重命名流程目录（名称校验与写入同口径；目标已存在则拒绝）。"""
        if not _NAME.fullmatch(new_name or ""):
            raise WorkflowNameError(f"invalid workflow name: {new_name!r}")
        source = self.directory(name)
        if not (source / "workflow.json").is_file():
            raise WorkflowNotFoundError(f"workflow not found: {name}")
        target = self.directory(new_name)
        if target.exists():
            raise WorkflowStoreError(f"workflow already exists: {new_name}")
        source.rename(target)

    def copy_flow(self, name: str, new_name: str) -> None:
        """复制流程（含附属资产）为新名称；目标已存在则拒绝。"""
        import shutil

        if not _NAME.fullmatch(new_name or ""):
            raise WorkflowNameError(f"invalid workflow name: {new_name!r}")
        source = self.directory(name)
        if not (source / "workflow.json").is_file():
            raise WorkflowNotFoundError(f"workflow not found: {name}")
        target = self.directory(new_name)
        if target.exists():
            raise WorkflowStoreError(f"workflow already exists: {new_name}")
        shutil.copytree(source, target)

    def export_flow(self, name: str, target_dir: Path) -> Path:
        """把流程（含附属资产）导出到目标目录；目标已存在且非空则拒绝。"""
        import shutil

        source = self.directory(name)
        if not (source / "workflow.json").is_file():
            raise WorkflowNotFoundError(f"workflow not found: {name}")
        target = Path(target_dir)
        if target.exists() and any(target.iterdir()):
            raise WorkflowStoreError(f"target directory is not empty: {target}")
        shutil.copytree(source, target, dirs_exist_ok=True)
        return target

    def import_flow(self, name: str, source: Path) -> None:
        """从外部目录（或单个 workflow.json）导入为流程 `name`；目标已存在则拒绝。"""
        import shutil

        if not _NAME.fullmatch(name or ""):
            raise WorkflowNameError(f"invalid workflow name: {name!r}")
        source_path = Path(source)
        if source_path.is_file():
            if source_path.name != "workflow.json":
                raise WorkflowStoreError("导入需要 workflow.json 或其所在目录")
            source_dir = source_path.parent
        else:
            source_dir = source_path
        if not (source_dir / "workflow.json").is_file():
            raise WorkflowStoreError(f"缺少 workflow.json：{source_dir}")
        target = self.directory(name)
        if target.exists():
            raise WorkflowStoreError(f"workflow already exists: {name}")
        shutil.copytree(source_dir, target)

    def _resolve(self, name: str) -> Path:
        return self.directory(name) / "workflow.json"


class TableStore:
    """流程数据表格存储：<root>/<name>/data/table.json（单表，流程附属资产）。

    表文件与运行时 data.table.* 命令共用同一契约（workers/data_table.py），
    编辑器在此读写 schema 与行数据。
    """

    def __init__(self, workflow_store: WorkflowDirStore):
        self._workflows = workflow_store

    def _path(self, flow: str) -> Path:
        return self._workflows.directory(flow) / "data" / "table.json"

    def _table_root(self, flow: str) -> Path:
        return self._workflows.directory(flow) / "data"

    def read(self, flow: str) -> dict:
        """读取表对象；表文件不存在返回缺省空表结构。"""
        path = self._path(flow)
        if not path.is_file():
            return {"schema_version": 1, "columns": [], "rows": [], "updated_at": ""}
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise WorkflowStoreError(f"flow table is not valid JSON: {flow}") from exc
        if not isinstance(document, dict):
            raise WorkflowStoreError(f"flow table must be a JSON object: {flow}")
        return document

    def write(self, flow: str, document: dict) -> int:
        """整表原子写（列 schema + 行数据），复用 WorkflowStore._write_atomic。"""
        if not isinstance(document, dict):
            raise WorkflowStoreError("flow table document must be a JSON object")
        self._table_root(flow).mkdir(parents=True, exist_ok=True)
        return self._workflows._write_atomic(self._path(flow), document)

    def clear(self, flow: str) -> dict:
        """清空行数据保留列 schema，返回清除后的表对象。"""
        document = self.read(flow)
        document["rows"] = []
        self.write(flow, document)
        return document

    def to_csv_bytes(self, flow: str) -> bytes:
        """按列顺序导出 CSV（utf-8-sig 带 BOM）。

        运行时序列化在 workers/data_table.py 独立实现；此处为编辑器导出在
        devserver 侧自含实现（devserver 隔离禁列表禁止 import workers）。
        """
        return table_to_csv(self.read(flow))


def table_to_csv(document: dict) -> bytes:
    """把表对象序列化为 CSV 字节（utf-8-sig）。列顺序按 columns，行按列 key。"""
    columns = document.get("columns") or []
    rows = document.get("rows") or []
    keys = [str(col.get("key", col.get("label", ""))) for col in columns]
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    if keys:
        writer.writerow(
            [str(col.get("label", key)) for key, col in zip(keys, columns, strict=True)]
        )
    for row in rows:
        writer.writerow([_table_csv_cell(row.get(key)) for key in keys])
    return buffer.getvalue().encode("utf-8-sig")


def _table_csv_cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)
