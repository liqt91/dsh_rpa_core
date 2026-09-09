import re
from typing import Any

from rpa_core.model.workflow import Condition

_REFERENCE = re.compile(r"^\$\{([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\}$")


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
