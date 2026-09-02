import re
from pathlib import Path

from rpa_core.catalog import load_catalog
from rpa_core.model.command import CommandKind, EffectKind

ROOT = Path(__file__).resolve().parents[2]
I18N_PATH = ROOT / "src" / "rpa_core" / "devserver" / "static" / "i18n.js"

_BLOCK = re.compile(r'(\w+)\s*:\s*\{(.*?)\n  \}', re.DOTALL)
_ENTRY = re.compile(r'"?([\w.()（）-]+)"?\s*:')


def _i18n_block(name: str) -> dict:
    """从 static/i18n.js 提取指定映射表的键集合（只读解析，不执行 JS）。"""
    text = I18N_PATH.read_text(encoding="utf-8")
    match = re.search(rf'^\s*{name}:\s*\{{(.*?)\n  \}}', text, re.DOTALL | re.MULTILINE)
    assert match, f"i18n block not found: {name}"
    return {m.group(1) for m in _ENTRY.finditer(match.group(1))}


def test_i18n_commands_cover_entire_catalog():
    catalog = load_catalog(ROOT / "commands")
    mapping = _i18n_block("commands")
    missing = set(catalog) - mapping
    stale = mapping - set(catalog)
    assert not missing, f"commands missing Chinese display names: {sorted(missing)}"
    assert not stale, f"stale i18n entries for removed commands: {sorted(stale)}"


def test_i18n_kind_and_effect_and_op_keys_match_model():
    assert _i18n_block("kinds") == {k.value for k in CommandKind}
    assert _i18n_block("effects") == {k.value for k in EffectKind}
    assert _i18n_block("ops") == {"eq", "ne", "gt", "gte", "lt", "lte", "contains", "truthy"}


def test_i18n_fields_cover_required_input_schema_keys():
    catalog = load_catalog(ROOT / "commands")
    fields = _i18n_block("fields")
    missing = {}
    for manifest in catalog.values():
        required = manifest.input_schema.get("required", [])
        absent = [key for key in required if key not in fields]
        if absent:
            missing[manifest.id] = absent
    assert not missing, f"required input keys without Chinese labels: {missing}"


def test_i18n_glossary_terms_exist():
    glossary = _i18n_block("glossary")
    terms = (
        "action", "query", "transform", "lifecycle",
        "session", "capability", "effect", "checkpoint",
    )
    for term in terms:
        assert term in glossary, f"glossary missing term: {term}"
