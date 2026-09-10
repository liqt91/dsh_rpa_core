import json
import re
from typing import Any

from rpa_core.model.workflow import Condition

_REFERENCE = re.compile(r"^\$\{([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\}$")
# fx 模式标签：[name] / [name.field]，可与普通文本混排拼接
_TAG = re.compile(r"\[([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\]")


class ReferenceError(ValueError):
    pass


def _lookup(path: str, scopes: dict[str, Any]) -> Any:
    variables = scopes.get("variables", {})
    # 1) 整路径命中变量名（${var_name} → 变量值整体）
    if path in variables:
        return variables[path]
    # 2) 变量名 + 子路径（${var_name.field.sub} → 从变量值 dict 走子路径）
    if variables:
        head, dot, tail = path.partition(".")
        if dot and head in variables:
            value: Any = variables[head]
            for part in tail.split("."):
                if isinstance(value, dict) and part in value:
                    value = value[part]
                else:
                    raise ReferenceError(f"Unknown reference: {path}")
            return value
    parts = path.split(".")
    if parts[0] not in scopes:
        raise ReferenceError(f"Unknown reference: {path}")
    value: Any = scopes[parts[0]]
    for part in parts[1:]:
        if isinstance(value, dict) and part in value:
            value = value[part]
        else:
            raise ReferenceError(f"Unknown reference: {path}")
    return value


def resolve(value: Any, scopes: dict[str, Any]) -> Any:
    if isinstance(value, str):
        match = _REFERENCE.fullmatch(value)
        if match:
            return _lookup(match.group(1), scopes)
        if value.startswith("${") and value.endswith("}"):
            raise ReferenceError(f"Invalid reference syntax: {value}")
        return value
    if isinstance(value, list):
        return [resolve(item, scopes) for item in value]
    if isinstance(value, dict):
        return {key: resolve(item, scopes) for key, item in value.items()}
    return value


def _tag_to_text(found: Any) -> str:
    if isinstance(found, str):
        return found
    if isinstance(found, (dict, list)):
        return json.dumps(found, ensure_ascii=False)
    if found is None:
        return ""
    if isinstance(found, bool):
        return "true" if found else "false"
    return str(found)


def resolve_tags(value: Any, scopes: dict[str, Any]) -> Any:
    """fx 模式：把 [name] / [name.field] 标签替换为变量值，支持与文本混排拼接。

    例："输出日志：这是变量[web_page1]，表示一个网页引用"
    """
    if isinstance(value, str):
        stripped = value.strip()
        # 整串仍是 ${...} 时按既有语义处理，保留原类型（不强制转字符串）
        if _REFERENCE.fullmatch(stripped):
            return _lookup(_REFERENCE.fullmatch(stripped).group(1), scopes)

        def _replace(match: re.Match) -> str:
            return _tag_to_text(_lookup(match.group(1), scopes))

        return _TAG.sub(_replace, value)
    if isinstance(value, list):
        return [resolve_tags(item, scopes) for item in value]
    if isinstance(value, dict):
        return {key: resolve_tags(item, scopes) for key, item in value.items()}
    return value


def resolve_with_modes(
    inputs: dict[str, Any],
    modes: dict[str, str] | None,
    scopes: dict[str, Any],
) -> dict[str, Any]:
    """按字段表达式模式解析输入：fx=标签语法，其余=既有 ${} 引用语法。"""
    modes = modes or {}
    resolved: dict[str, Any] = {}
    for key, value in inputs.items():
        if modes.get(key) == "fx":
            resolved[key] = resolve_tags(value, scopes)
        else:
            resolved[key] = resolve(value, scopes)
    return resolved


def evaluate(condition: Condition, scopes: dict[str, Any]) -> bool:
    left = resolve(condition.left, scopes)
    right = resolve(condition.right, scopes)
    operations = {
        "eq": lambda: left == right,
        "ne": lambda: left != right,
        "gt": lambda: left > right,
        "gte": lambda: left >= right,
        "lt": lambda: left < right,
        "lte": lambda: left <= right,
        "contains": lambda: right in left,
        "truthy": lambda: bool(left),
    }
    return bool(operations[condition.op]())
