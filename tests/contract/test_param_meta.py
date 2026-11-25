"""manifest 参数元数据的**等值判据**（M108）。

背景：`docs/manifest-param-config.md` 里那张「缺口表」长期靠人眼维护——哪个字段没有
中文标签、哪个枚举没有中文值、哪条命令没分组，都是「看一眼大概齐」。这类漂移不产生
任何错误，只让界面慢慢退回英文键名，所以必须有机械判据。

三条判据都写成**等值**而不是「覆盖率 ≥ N%」：

1. 每个非 hidden 字段都必须有中文标签（`field_label` 三级回退后 !== 键名）。
   阈值型判据（≥90%）容得下十个字段裸显示英文键名，等于没用。
2. `x-enum-labels` 的键集合**等于** `enum` 值集合。少一个值 ⇒ 那个枚举在界面上显示
   英文原值；多一个值 ⇒ 声明写了已删的枚举，两处都该红。
3. `x-param-groups` 声明的每个键都必须是真实字段，且**每个字段最多被认领一次**——
   重复认领会让它出现在两个组里（渲染两遍同一控件，保存时后者覆盖前者）。

另外三条是「别把扩展声明写到没人读的地方」：

4. 字段级 `x-fx` / `x-python` 只能声明在可求值的字符串字段上（运行期 `resolve_tags`
   对非 str 直接原样返回，声明了也不生效）。
5. manifest 顶层 `x-fx` / `x-python`（`list[str]`）是历史死字段：全仓 0 使用。
   这里钉住「仍为 0」，任何人把它当通道用都会红——那条路没人读。
6. `x-depends` 的受控字段与条件里的控制字段都必须真实存在。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rpa_core.catalog import load_catalog
from rpa_core.gui.i18n import field_label

ROOT = Path(__file__).resolve().parents[2]
CAPS = ROOT / "commands"
HIDDEN_FLAGS = ("hidden", "x-hidden")


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(CAPS)


def _manifests(catalog):
    return sorted(catalog.values(), key=lambda m: m.id)


def _visible_properties(manifest) -> dict[str, dict]:
    props = dict((manifest.input_schema or {}).get("properties") or {})
    return {
        key: dict(value or {})
        for key, value in props.items()
        if not any(dict(value or {}).get(flag) for flag in HIDDEN_FLAGS)
    }


def test_every_declared_field_has_chinese_label(catalog):
    """非 hidden 字段 → 中文标签。缺一条就指名道姓红，不做比例容忍。"""
    missing = [
        f"{manifest.id}.{key}"
        for manifest in _manifests(catalog)
        for key in _visible_properties(manifest)
        if field_label(key, manifest.id) == key
    ]
    assert not missing, (
        "以下字段在参数面板会裸显示英文键名（i18n.js 的 fields / commandFields 里没有标签）："
        + "、".join(missing)
    )


def test_enum_labels_match_enum_values(catalog):
    """`x-enum-labels` 键集合 == `enum` 值集合（双向等值，不是超集）。"""
    broken: list[str] = []
    for manifest in _manifests(catalog):
        for key, declared in _visible_properties(manifest).items():
            values = [str(v) for v in (declared.get("enum") or [])]
            if not values:
                continue
            labels = dict(declared.get("x-enum-labels") or {})
            if not labels:
                broken.append(f"{manifest.id}.{key}: 枚举没配 x-enum-labels（界面显示英文原值）")
                continue
            unknown = sorted(set(labels) - set(values))
            uncovered = sorted(set(values) - set(labels))
            if unknown:
                broken.append(f"{manifest.id}.{key}: x-enum-labels 多出 {unknown}（enum 里没有）")
            if uncovered:
                broken.append(f"{manifest.id}.{key}: x-enum-labels 缺 {uncovered}")
    assert not broken, "\n".join(broken)


def test_param_groups_reference_real_fields_once(catalog):
    """分组声明只能指向真实字段，且一个字段只能属于一个组。"""
    problems: list[str] = []
    for manifest in _manifests(catalog):
        schema = dict(manifest.input_schema or {})
        properties = dict(schema.get("properties") or {})
        groups = schema.get("x-param-groups")
        if not groups:
            continue
        claimed: dict[str, str] = {}
        auto_seen = 0
        for index, group in enumerate(groups):
            label = str(group.get("label") or f"#{index}")
            if group.get("auto"):
                auto_seen += 1
                if auto_seen > 1:
                    problems.append(f"{manifest.id}: 有多个 auto 组（只有第一个会生效）")
                continue
            for key in group.get("fields") or []:
                if key not in properties:
                    problems.append(f"{manifest.id}.{label}: 字段 {key} 未在 properties 里声明")
                    continue
                if key in claimed:
                    problems.append(
                        f"{manifest.id}: 字段 {key} 同时属于「{claimed[key]}」和「{label}」"
                    )
                    continue
                claimed[key] = label
    assert not problems, "\n".join(problems)


def test_expression_flags_never_on_enum_fields(catalog):
    """`x-fx` / `x-python` 不得与 `enum` 同时声明。

    枚举的合法取值是封闭集合，而表达式求值发生在 schema 校验**之前**——把
    `[某变量]` 插进一个 `enum: ["single","double"]` 的字段，几乎必然得到一个不在
    枚举里的值，运行期报 `INVALID_INPUT`。这个入口一旦渲染出来，用户没有理由不点它。

    ★ 不要写成「只能声明在 `type == string` 上」：那是**错**的判据。`resolve_tags`
    对 list/dict 递归、对非 str 原样返回，所以 `type: integer` 的 `row` 写 `[行号]`
    会被替换成数字，这正是它存在的理由。真正无意义的只有封闭枚举。
    """
    wrong: list[str] = []
    for manifest in _manifests(catalog):
        for key, declared in _visible_properties(manifest).items():
            if not (declared.get("x-fx") or declared.get("x-python")):
                continue
            if declared.get("enum"):
                wrong.append(
                    f"{manifest.id}.{key}: 同时声明了表达式能力与 enum="
                    f"{declared.get('enum')}（变量值几乎必然落在枚举外）"
                )
    assert not wrong, "\n".join(wrong)


def test_manifest_level_expression_fields_stay_unused(catalog):
    """顶层 `x-fx` / `x-python` 是死字段：没人读它，用上就是写错层。"""
    users = [manifest.id for manifest in _manifests(catalog) if manifest.x_fx or manifest.x_python]
    assert not users, (
        "顶层 x-fx / x-python 没有消费者（参数面板只读 properties.<字段> 下的声明）："
        + "、".join(users)
    )


def test_depends_references_real_fields(catalog):
    """`x-depends` 两侧的字段都必须真实存在，否则这条联动永远不成立（静默失效）。"""
    problems: list[str] = []
    for manifest in _manifests(catalog):
        schema = dict(manifest.input_schema or {})
        properties = set(schema.get("properties") or {})
        depends = schema.get("x-depends") or {}
        if not isinstance(depends, dict):
            problems.append(f"{manifest.id}: x-depends 必须是对象")
            continue
        for target, condition in depends.items():
            if target not in properties:
                problems.append(f"{manifest.id}: x-depends 的受控字段 {target} 不存在")
            if not isinstance(condition, dict) or not condition:
                problems.append(f"{manifest.id}.{target}: x-depends 条件为空或不是对象")
                continue
            for control in condition:
                if control not in properties:
                    problems.append(f"{manifest.id}.{target}: 条件引用了不存在的控制字段 {control}")
    assert not problems, "\n".join(problems)


def test_depends_condition_values_are_scalars(catalog):
    """条件值必须是**标量**：消费者只实现了「控制字段 == 期望值」。

    `docs/manifest-param-config.md` 里出现过 `{"action": {"in": ["launch"]}}` 这种写法
    （「只支持单字段等值」那句与它自相矛盾）。`_depends_of` 不懂 `in`，于是比较
    `值 == {"in": [...]}` 恒为假——表现不是报错，而是**这一格永远置灰**，用户以为是
    命令不支持这个参数。宁可加载期红，也不要一个看着像功能的死开关。
    """
    problems: list[str] = []
    for manifest in _manifests(catalog):
        schema = dict(manifest.input_schema or {})
        for target, condition in (schema.get("x-depends") or {}).items():
            if not isinstance(condition, dict):
                continue
            for control, expected in condition.items():
                if isinstance(expected, (dict, list)):
                    problems.append(
                        f"{manifest.id}.{target}: 条件 {control} = {expected!r} 不是标量"
                        "（只支持「控制字段 == 某值」，不支持 in / 与或）"
                    )
    assert not problems, "\n".join(problems)
