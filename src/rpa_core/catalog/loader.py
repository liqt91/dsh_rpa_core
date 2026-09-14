import hashlib
import json
from collections.abc import Iterator, Mapping
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType

from jsonschema.validators import validator_for

from rpa_core.model.command import CommandManifest

# 目录指纹：manifest 路径 + mtime_ns + 字节数。
CatalogSignature = tuple[tuple[str, int, int], ...]


class CommandCatalog(Mapping[str, CommandManifest]):
    def __init__(self, commands: dict[str, CommandManifest], digest: str):
        self._commands = MappingProxyType(dict(commands))
        self.digest = digest

    def __getitem__(self, key: str) -> CommandManifest:
        return self._commands[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._commands)

    def __len__(self) -> int:
        return len(self._commands)


def _manifest_signature(root: Path) -> CatalogSignature:
    """命令目录指纹：只做 rglob 与 stat，不读文件内容。

    重复加载一份 83 条 manifest 的目录，成本几乎全在解析与 JSON Schema 自检；
    指纹只碰元数据，因此「先比指纹」比「重新解析」便宜几个数量级，却同样能
    保证 manifest 的新增、改写、删除都会改变指纹。
    """
    entries: list[tuple[str, int, int]] = []
    for path in root.rglob("*.json"):
        stat = path.stat()
        entries.append((str(path), stat.st_mtime_ns, stat.st_size))
    entries.sort()
    return tuple(entries)


@lru_cache(maxsize=64)
def _load_catalog_snapshot(root: Path, signature: CatalogSignature) -> CommandCatalog:
    """解析并自检目录下的全部 manifest，返回不可变快照（规则 7）。

    `signature` 只参与缓存键、不参与解析：调用方必须先算出它。任何 manifest
    改动都会改变指纹并触发重载，因此这里不会出现「改了 manifest 却仍读到旧快照」
    的窗口。异常不进缓存（`lru_cache` 语义），目录后续补齐即可正常加载。
    """
    commands: dict[str, CommandManifest] = {}
    canonical: list[dict] = []

    for path in sorted(root.rglob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        manifest = CommandManifest.model_validate(raw)
        if path.stem != manifest.id.rsplit(".", 1)[-1]:
            raise ValueError(f"Manifest id {manifest.id!r} does not match file {path.name}")
        if manifest.id in commands:
            raise ValueError(f"Duplicate command id: {manifest.id}")
        validator_for(manifest.input_schema).check_schema(manifest.input_schema)
        validator_for(manifest.output_schema).check_schema(manifest.output_schema)
        commands[manifest.id] = manifest
        canonical.append(manifest.model_dump(mode="json"))

    if not commands:
        raise ValueError(f"No command manifests found under {root}")

    encoded = json.dumps(canonical, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return CommandCatalog(commands, digest)


def clear_catalog_cache() -> None:
    """丢弃已缓存的快照，强制下一次 `load_catalog` 重新读盘并校验。

    生产路径不需要它（指纹已经能感知 manifest 变化）；测试需要用它区分
    「同一进程内复用快照」与「独立重读是否仍然一致」两种断言。
    """
    _load_catalog_snapshot.cache_clear()


def load_catalog(root: Path) -> CommandCatalog:
    """加载命令目录，目录内容自上次加载起未变则复用同一份快照。"""
    root = Path(root)
    return _load_catalog_snapshot(root, _manifest_signature(root))
