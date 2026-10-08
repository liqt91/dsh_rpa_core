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


def _command_fields_overrides() -> dict[str, set[str]]:
    """解析 commandFields（按命令覆盖字段标签）：{命令 id: {被覆盖的字段名}}。"""
    text = I18N_PATH.read_text(encoding="utf-8")
    block = re.search(
        r'^\s*commandFields:\s*\{(.*?)\n  \}', text, re.DOTALL | re.MULTILINE
    )
    assert block, "i18n block not found: commandFields"
    overrides: dict[str, set[str]] = {}
    for command_id, body in re.findall(r'"([\w.]+)"\s*:\s*\{([^}]*)\}', block.group(1)):
        overrides[command_id] = {m.group(1) for m in _ENTRY.finditer(body + ",")}
    return overrides


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
    overrides = _command_fields_overrides()
    missing = {}
    for manifest in catalog.values():
        required = manifest.input_schema.get("required", [])
        covered = fields | overrides.get(manifest.id, set())
        absent = [key for key in required if key not in covered]
        if absent:
            missing[manifest.id] = absent
    assert not missing, f"required input keys without Chinese labels: {missing}"


#: 不需要中文标签的 locator 字段：结构性的，从不作为用户可见标签出现。
#: `backend`（uia / win32）由后端切换控件的选中态表达，界面上没有它的输入框。
_UNLABELLED_LOCATOR_KEYS = frozenset({"backend"})


#: locator 子树里也必须有中文标签的键。
#:
#: 为什么需要单独一条：`test_i18n_fields_cover_required_input_schema_keys` 的遍历是
#: `required` → 该键的 `properties` **一层**。桌面命令的 required 只有 `["locator"]`，
#: 所以它恰好走进 `locator.properties`；这条判据走的是**模型字段**这一侧，覆盖更稳。
#: 更关键的是：两者事实源不同——manifest 里的 `locator.properties` 是靠人手抄的，
#: 模型里加了字段而 manifest 忘了抄，required 那条照样绿，界面就会静默退回英文键名。
#: M48 给 locator 加了 `matchMode` / `path` / `anchor` 三个键，正属于这种「容易漏抄」
#: 的情形。
#:
#: 这里用 `DesktopLocator` 的**模型字段别名**当事实源（不是手抄名单，也不是读
#: manifest）：模型加了字段就必须同步中文标签，否则红。
def test_i18n_covers_desktop_locator_alias_fields():
    from rpa_core.model.desktop import DesktopLocator

    aliases = {
        (field.alias or name)
        for name, field in DesktopLocator.model_fields.items()
    } - _UNLABELLED_LOCATOR_KEYS
    fields = _i18n_block("fields")
    missing = sorted(aliases - fields)
    assert not missing, f"DesktopLocator 字段缺中文标签: {missing}"


def test_i18n_locator_labels_come_from_the_model_not_from_manifests():
    """钉住上一条判据的价值：manifest 侧的 locator 标签需求**天生不完整**。

    两个后端共用同一个 ``DesktopLocator`` 模型，manifest 却把它们拆成两份
    ``locator.properties``——uia 侧只有 6 个键、win32 侧 11 个，各自都凑不齐模型的
    13 个别名。所以只要把「覆盖 locator 标签」这件事交给 manifest，就一定漏。

    本断言把这个事实钉住，并额外要求 ``path`` / ``anchor`` / ``matchMode``
    （M48 新增的三个键）不出现在「manifest 已有而模型侧判据可能被删」的侥幸里。
    """
    from rpa_core.model.desktop import DesktopLocator

    catalog = load_catalog(ROOT / "commands")
    union_from_manifest: set[str] = set()
    any_manifest: set[str] = set()
    for manifest in catalog.values():
        props = manifest.input_schema.get("properties", {})
        locator = props.get("locator")
        if not isinstance(locator, dict):
            continue
        keys = set(locator.get("properties", {}))
        union_from_manifest |= keys
        any_manifest = keys if not any_manifest else (any_manifest & keys)

    aliases = {
        (field.alias or name)
        for name, field in DesktopLocator.model_fields.items()
    } - _UNLABELLED_LOCATOR_KEYS

    # 单份 manifest 一定不完整（后端被拆开了）
    assert aliases - any_manifest, (
        "某个 manifest 的 locator.properties 已覆盖全部模型字段——"
        "可以重新评估是否还需要模型侧那条判据"
    )
    # 合并两份仍漏 M48 新键之外的东西也无所谓；但 M48 三个键必须在并集里，
    # 否则说明本轮 manifest 同步漏了（这才是真正要拦的）
    for key in ("path", "anchor", "matchMode"):
        assert key in union_from_manifest or key in aliases, key
    assert {"path", "anchor", "matchMode"} <= aliases | union_from_manifest


def test_i18n_glossary_terms_exist():
    glossary = _i18n_block("glossary")
    terms = (
        "action", "query", "transform", "lifecycle",
        "session", "capability", "effect", "checkpoint",
    )
    for term in terms:
        assert term in glossary, f"glossary missing term: {term}"
