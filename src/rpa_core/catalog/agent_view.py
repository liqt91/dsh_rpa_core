"""catalog 的 AI 语义投影（tool card）：LLM 生成 workflow.json 的能力事实源。

设计文档（docs/agent-native-rpa-product-design.md §2.2/§9.1）的「按意图索引的
派生视图」：原始 catalog 按后端分组对人清晰、对 AI 是歧义源——`desktop.*` 与
`desktop.win32.*` 是同一动作的两个后端。本模块把「选哪个后端」从模型决策里拿掉：

- 分组键 = (id 首段, id 末段)；`desktop.win32.click` 首段就是 `desktop`，
  自然与 `desktop.click` 并进同一组，无需映射表；
- **同组内按语义负载聚类**：除 id/executor/implementation 外逐字段全等才合并。
  `implementation.handler` 内嵌 executor 名，是后端身份，随 backends 走；
  其余字段（schema/outputs/errors/effect/…）有分歧就**不合并**——第一版实测
  17 对里 4 对 input_schema 有差（如 attachWindow 的 win32 多一个可选 handle）、
  7 对 x-outputs 有差，「同一语义」是待审计的假设不是事实，fail-closed；
- 有分歧的组各自成条目（semantic_id 用完整命令 id 保全局唯一），并在顶层
  `divergent_groups` 报出差异字段——这份审计清单本身就是投影器的产出之一；
- 每个语义能力带：参数契约（类型/枚举/中文 description/默认值）、最小可用 example
  （从 input_schema 现算，不硬编码）、元素参数标记（消费 runtime 的引用契约，
  由调用方注入键集，本模块不复制）、errors、effect；
- catalog digest 原样透传——AI 生成的工作流绑定这份快照（catalog_version 闸门）。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from rpa_core.catalog.loader import CommandCatalog
from rpa_core.model.command import CommandManifest

# 随后端走的 manifest 字段：id/executor 直接进 backends，
# implementation.handler 内嵌 executor 名，同样是后端身份。
# 语义负载 = model_dump 去掉这三个字段后的**全部**剩余内容——后续往 manifest
# 加字段自动进入比较面，不会出现「新字段静默逃过差异检测」的盲区。
_PER_BACKEND_FIELDS = ("id", "executor", "implementation")

# 产出卡片挑选的字段（产出面 ≠ 比较面：比较面恒为全量语义负载）。
_CARD_FIELDS = (
    "version",
    "kind",
    "risk",
    "stability",
    "effect",
    "capabilities",
    "resources",
    "errors",
    "default_timeout_seconds",
    "retryable",
    "x_outputs",
)

# 元素引用的说明（与 runtime/element_refs.py 的 M52 S6 引用模型同口径）：
# 被引用参数有意不写进 with，值只存在于流程 elements/ 目录。
_ELEMENT_REFS_NOTE = (
    "element_params 里的参数通过节点的 elementRefs: {参数名: 元素名} 绑定，"
    "值不写进 with；运行期从流程 elements/ 目录解析，解析不到即 ELEMENT_REF_MISSING"
)


def _domain_of(command_id: str) -> str:
    return command_id.split(".", 1)[0]


def _action_of(command_id: str) -> str:
    return command_id.rsplit(".", 1)[-1]


def _semantic_payload(manifest: CommandManifest) -> dict[str, Any]:
    """manifest 里「与后端无关」的全部内容——同语义判定的比较面。"""
    payload = manifest.model_dump(mode="json")
    for key in _PER_BACKEND_FIELDS:
        del payload[key]
    return payload


def _differing_fields(a: CommandManifest, b: CommandManifest) -> list[str]:
    """两个 manifest 除后端绑定字段外的差异字段名（诊断用）。"""
    pa, pb = _semantic_payload(a), _semantic_payload(b)
    return sorted(k for k in set(pa) | set(pb) if pa.get(k) != pb.get(k))


def _placeholder(prop: dict[str, Any]) -> Any:
    """按 schema 现算一个最小占位值；枚举优先用声明的默认值。"""
    if prop.get("enum"):
        return prop.get("default", prop["enum"][0])
    prop_type = prop.get("type")
    if prop_type == "integer" or prop_type == "number":
        return prop.get("minimum", 1)
    if prop_type == "boolean":
        return True
    if prop_type == "array":
        return []
    if prop_type == "object":
        return {}
    return "<string>"


def _minimal_example(input_schema: dict[str, Any]) -> dict[str, Any]:
    """required 字段的最小调用示例；可选字段一律不进（空 = 未设置 = 不写 with）。"""
    properties = input_schema.get("properties") or {}
    example = {
        key: _placeholder(properties[key])
        for key in sorted(input_schema.get("required") or [])
        if key in properties
    }
    unknown = set(input_schema.get("required") or []) - set(properties)
    if unknown:
        raise ValueError(f"input_schema 的 required 引用了不存在的属性：{sorted(unknown)}")
    return example


def _param_card(key: str, prop: dict[str, Any], required: bool) -> dict[str, Any]:
    card: dict[str, Any] = {"type": prop.get("type", "any"), "required": required}
    if "description" in prop:
        card["description"] = prop["description"]
    if "default" in prop:
        card["default"] = prop["default"]
    if prop.get("enum"):
        labels = prop.get("x-enum-labels") or {}
        card["enum"] = [
            {"value": value, "label": labels.get(str(value), str(value))}
            for value in prop["enum"]
        ]
    return card


def _jsonable(value: Any) -> Any:
    """manifest 字段 → JSON 可序列化值（effect 是模型、errors 是 StrEnum 列表）。"""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [item.value if hasattr(item, "value") else item for item in value]
    return value


def _backends(cluster: list[CommandManifest]) -> list[dict[str, Any]]:
    return [
        {
            "id": manifest.id,
            "executor": manifest.executor,
            "handler": manifest.implementation.handler
            if hasattr(manifest.implementation, "handler")
            else None,
        }
        for manifest in cluster
    ]


def build_agent_view(
    catalog: CommandCatalog,
    element_param_keys: Iterable[str] = (),
) -> dict[str, Any]:
    """把不可变 catalog 快照投影成按语义分组的 tool card。

    `element_param_keys` 由调用方注入（CLI 从 runtime.element_refs 取），
    本模块不维护元素参数键集，避免与引用契约出现第二份事实源。
    """
    element_keys = frozenset(element_param_keys)
    groups: dict[tuple[str, str], list[CommandManifest]] = {}
    for command_id in sorted(catalog):
        manifest = catalog[command_id]
        groups.setdefault((_domain_of(command_id), _action_of(command_id)), []).append(
            manifest
        )

    commands: list[dict[str, Any]] = []
    divergent_groups: list[dict[str, Any]] = []
    for (domain, action), manifests in sorted(groups.items()):
        # 组内按语义负载聚类：全等才合并，有分歧就各自成条目并上报。
        clusters: list[list[CommandManifest]] = []
        for manifest in manifests:
            for cluster in clusters:
                if _semantic_payload(cluster[0]) == _semantic_payload(manifest):
                    cluster.append(manifest)
                    break
            else:
                clusters.append([manifest])
        if len(clusters) > 1:
            divergent_groups.append(
                {
                    "group": f"{domain}.{action}",
                    "semantic_ids": [cluster[0].id for cluster in clusters],
                    "differing_fields": sorted(
                        {
                            field
                            for index in range(1, len(clusters))
                            for field in _differing_fields(
                                clusters[0][0], clusters[index][0]
                            )
                        }
                    ),
                }
            )
        for cluster in clusters:
            base = cluster[0]
            payload = _semantic_payload(base)
            schema = base.input_schema
            properties = schema.get("properties") or {}
            required = set(schema.get("required") or [])
            commands.append(
                {
                    # 组内只有一个簇时用合并名（如 desktop.click）；有分歧的簇
                    # 用首个后端的完整命令 id，保证 semantic_id 全局唯一。
                    "semantic_id": (
                        f"{domain}.{action}" if len(clusters) == 1 else base.id
                    ),
                    "backends": _backends(cluster),
                    **{
                        field: _jsonable(payload[field])
                        for field in _CARD_FIELDS
                        if field in payload
                    },
                    "params": {
                        key: _param_card(key, properties[key], key in required)
                        for key in sorted(properties)
                    },
                    "element_params": sorted(element_keys & set(properties)),
                    "example": _minimal_example(schema),
                }
            )

    domains: dict[str, int] = {}
    for command in commands:
        domain = command["semantic_id"].split(".", 1)[0]
        domains[domain] = domains.get(domain, 0) + 1
    return {
        "catalog_digest": catalog.digest,
        "manifest_commands": len(catalog),
        "semantic_commands": len(commands),
        "divergent_groups": divergent_groups,
        "domains": domains,
        "element_refs_contract": _ELEMENT_REFS_NOTE,
        "commands": commands,
    }
