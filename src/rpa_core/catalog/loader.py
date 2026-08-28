import hashlib
import json
from collections.abc import Iterator, Mapping
from pathlib import Path
from types import MappingProxyType

from jsonschema.validators import validator_for

from rpa_core.model.command import CommandManifest


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


def load_catalog(root: Path) -> CommandCatalog:
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
