"""元素引用解析（M46/B1 引用模型）。

流程节点参数可携带 `elementRefs: {参数键: 元素名}`（与影刀同构：指令存引用，
选择器值只在流程 `elements/` 目录存一份）。运行期在 schema 校验前把引用键的
值替换为元素库的最新值；元素缺失/文件损坏时回落到节点 `with` 里存的快照值
并落 `elementRefFallback` 事件——与自愈候选的「按 css 反查静默失效」相反，
这条路径的回落必须可观测。

写入口径（GUI `_insert_element`）：browser 元素填参数 `selector`（取文档
`selector.css`），desktop 元素填参数 `locator`（取文档 `selector.locator`）。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 参数键 → 元素文档里的取值路径；未知键不解析（回落快照，不算错误——
# 引用映射只会由 GUI 插入路径写入，键集合是封闭的）
_VALUE_BY_KEY = {
    "selector": ("selector", "css"),
    "locator": ("selector", "locator"),
}

# 元素 kind → 承载它的参数键（与 `_VALUE_BY_KEY` 互为反向，两处必须同步）。
# 事实源仍以 `_VALUE_BY_KEY` 为准：`element_kind_for_param_key` 只认能取出定位值的键。
_KEY_BY_KIND = {
    "browser": "selector",
    "desktop": "locator",
}


def param_key_for_element_kind(kind: str) -> str | None:
    """元素 kind → 它应填入的参数键；未知 kind 返回 None。

    GUI 两条写入路径（元素库「插入参数」、参数面板「从元素库选择」）共用这一份，
    避免各自 if/elif 硬编码出第二套权威。
    """
    return _KEY_BY_KIND.get(kind)


def element_kind_for_param_key(key: str) -> str | None:
    """参数键 → 它接受哪类元素；不支持元素引用的键返回 None。

    **以 `_VALUE_BY_KEY` 为准**：取不出定位值的键就不该给「从元素库选」入口，
    否则会出现「面板能选、运行期替换不了」的静默失配。
    """
    if key not in _VALUE_BY_KEY:
        return None
    for kind, mapped in _KEY_BY_KIND.items():
        if mapped == key:
            return kind
    return None


def element_capable_keys() -> frozenset[str]:
    """支持元素引用的参数键集合（参数面板据此决定要不要加选择入口）。"""
    return frozenset(_VALUE_BY_KEY)


@dataclass
class ElementRefResolution:
    """一次解析的结果：resolved=换入了新值，missing=回落快照（含原因归类）。"""

    resolved: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def _usable(value: Any) -> bool:
    """定位值有效 = 非空字符串或非空 dict（desktop locator 是结构化对象）。"""
    if isinstance(value, str):
        return bool(value)
    return isinstance(value, dict) and bool(value)


def element_value_for_key(document: Any, key: str) -> Any | None:
    """从元素文档取参数键对应的定位值；形状不对/为空一律 None（回落快照）。

    返回 str（browser css）或 dict（desktop locator）——与 `_insert_element`
    的写入口径一致：键决定取值路径，值的有效性由这里统一判。
    """
    if not isinstance(document, dict):
        return None
    path = _VALUE_BY_KEY.get(key)
    if path is None:
        return None
    node: Any = document
    for part in path:
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node if _usable(node) else None


def make_element_reader(flow_dir: Path | None) -> Callable[[str], dict | None]:
    """返回 ``name -> dict | None`` 读取器；目录不存在/文件坏一律 None。

    故意不走 WorkflowStore：runtime 不依赖 devserver；且坏文件回落快照比抛错
    更符合「引用失败不炸流程」的语义（回落有事件，不静默）。
    """
    if flow_dir is None:
        return lambda name: None
    root = Path(flow_dir) / "elements"

    def read(name: str) -> dict | None:
        if not isinstance(name, str) or not name:
            return None
        path = root / f"{name}.json"
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return None
        return document if isinstance(document, dict) else None

    return read


def resolve_element_refs(
    command_inputs: dict[str, Any],
    element_refs: dict[str, str] | None,
    reader: Callable[[str], dict | None],
) -> ElementRefResolution:
    """就地把引用键的值替换为元素库最新值；返回解析/回落清单供事件取证。

    回落语义：元素缺失或取不出有效值时**保持快照值不动**（运行不中断），
    由调用方落 `elementRefFallback` 事件。
    """
    resolution = ElementRefResolution()
    if not element_refs:
        return resolution
    for key, name in element_refs.items():
        value = element_value_for_key(reader(name), key)
        if value is None:
            resolution.missing.append(name)
            continue
        command_inputs[key] = value
        resolution.resolved.append(name)
    return resolution
