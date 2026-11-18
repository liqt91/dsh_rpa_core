"""桌面元素编辑的**无 UI 语义层**： locator ↔ 影刀式层级/属性模型。

边界与 ``element_editing`` 相同：只做「一份描述符 ↔ 一份编辑模型」的纯语义，
不碰文件、不碰会话、不碰窗口。网页侧由 ``selector_tree`` 负责 css 重组；桌面侧
由本模块负责把勾选后的层级/属性重组回 **selector 字典**（含 ``candidates`` 等
原样字段），两个编辑面共用同一份权威。

模型形状（前后端契约）：

    nodes = [
      {"type": "wnd", "name": "微信", "selected": False, "readonly": True,
       "attributes": [{"field": "className", "label": "类名", "value": "...",
                       "operator": "精确", "required": True, "readonly": True}, ...]},
      {"type": "ToolBar", "name": "导航", "selected": True,
       "attributes": [...]},                      # path 容器级
      {"type": "Button", "name": "发送", "selected": True,
       "attributes": [...]},                      # 目标级（链尾，恒参与）
    ]

**匹配方式只作用于目标级的 ``name`` / ``automationId``**：运行期 ``matchMode``
是 locator 的单个顶层字段（``model.desktop.D2``），path 级逐字段恒等值。因此
path 属性行的 operator 恒为「精确」且不可改；目标级只有 name / automationId
允许切「精确 / 包含 / 正则」，两者若给了不同模式就必须报错（不能悄悄选一个，
那会让另一列在界面上看起来生效、运行期却不生效）。
"""

from __future__ import annotations

from typing import Any

# 属性行字段 → 展示名。白名单：compose 只把这些键写回 locator / step，
# 其余字段（driver / sourceRect / handle / foundIndex …）原样保留但不提供编辑入口。
FIELD_LABELS: dict[str, str] = {
    "controlType": "控件类型",
    "automationId": "自动化ID",
    "name": "名称",
    "className": "类名",
}

# 运行期 D2：matchMode 只对这两个字段生效（controlType 恒等值、className 走等值）。
FUZZY_FIELDS = ("automationId", "name")

MODE_TO_VALUE = {"精确": "exact", "包含": "contains", "正则": "regex"}
VALUE_TO_MODE = {value: label for label, value in MODE_TO_VALUE.items()}

# 由编辑模型接管的 locator 键：compose 时先摘掉再按勾选写回。
_OWNED_KEYS = ("controlType", "automationId", "name", "className", "path", "matchMode")


def _attr(field: str, value: Any, *, operator: str = "精确", required: bool = True,
          readonly: bool = False) -> dict[str, Any]:
    return {
        "field": field,
        "label": FIELD_LABELS[field],
        "value": str(value or ""),
        "operator": operator,
        "required": required,
        "readonly": readonly,
    }


def _attrs_from(source: dict[str, Any], *, fuzzy: str = "精确",
                readonly: bool = False) -> list[dict[str, Any]]:
    """按固定字段顺序产出非空属性行；path 级固定「精确」。"""
    out: list[dict[str, Any]] = []
    for field in ("controlType", "automationId", "name", "className"):
        value = source.get(field)
        if value in (None, ""):
            continue
        out.append(
            _attr(field, value, operator=fuzzy if (fuzzy and field in FUZZY_FIELDS) else "精确",
                  readonly=readonly)
        )
    return out


def nodes_from_descriptor(descriptor: dict[str, Any]) -> list[dict[str, Any]]:
    """一份桌面描述符 → 层级节点模型（窗口 → 容器级 → 目标级）。

    窗口级不进 ``locator.path``：执行器 ``_narrow_by_path`` 明确「根窗口之下」，
    把窗口级塞进去会让每一级收窄为空。这里只把它作为**只读信息行**展示
    （``readonly=True / selected=False``），compose 永不使用它的属性。
    """
    selector = descriptor.get("selector") or {}
    locator = selector.get("locator") or {}
    metadata = descriptor.get("metadata") or {}
    nodes: list[dict[str, Any]] = []

    if metadata.get("windowTitle") or metadata.get("className"):
        window_attrs: list[dict[str, Any]] = []
        if metadata.get("windowTitle"):
            window_attrs.append({
                "field": "windowTitle", "label": "窗口标题",
                "value": str(metadata["windowTitle"]),
                "operator": "精确", "required": True, "readonly": True,
            })
        window_attrs.extend(_attrs_from(
            {"className": metadata.get("className")}, readonly=True
        ))
        nodes.append({
            "type": "wnd",
            "name": str(metadata.get("windowTitle") or ""),
            "selected": False,
            "readonly": True,
            "attributes": window_attrs,
        })

    for step in locator.get("path") or []:
        if not isinstance(step, dict):
            continue
        attrs = _attrs_from(step)
        if not attrs:
            continue
        nodes.append({
            "type": str(step.get("controlType") or ""),
            "name": str(step.get("name") or ""),
            "selected": True,
            "attributes": attrs,
        })

    fuzzy = VALUE_TO_MODE.get(locator.get("matchMode"), "精确")
    nodes.append({
        "type": str(locator.get("controlType") or ""),
        "name": str(locator.get("name") or ""),
        "selected": True,
        "attributes": _attrs_from(locator, fuzzy=fuzzy),
    })
    return nodes


def _collect(node: dict[str, Any], *, allow_fuzzy: bool) -> tuple[dict[str, str], set[str]]:
    """勾选的属性 → (字段字典, 非精确模式集合)。校验失败由调用方抛错。"""
    fields: dict[str, str] = {}
    modes: set[str] = set()
    for row in node.get("attributes") or []:
        if not isinstance(row, dict) or row.get("readonly") or not row.get("required"):
            continue
        field = row.get("field")
        if field not in FIELD_LABELS:
            raise ValueError(f"不支持的属性字段：{field}")
        value = str(row.get("value") or "").strip()
        if not value:
            continue
        fields[field] = value
        operator = str(row.get("operator") or "精确")
        if operator not in MODE_TO_VALUE:
            raise ValueError(f"不支持的匹配方式：{operator}")
        if allow_fuzzy and field in FUZZY_FIELDS and operator != "精确":
            modes.add(operator)
    return fields, modes


def compose_selector(descriptor: dict[str, Any], nodes: list[dict[str, Any]]) -> dict[str, Any]:
    """层级勾选 → **完整 selector 字典**（保留 ``candidates`` 与未编辑字段）。

    ``descriptor`` 提供原始 selector：未出现在编辑模型里的键（图像 driver、
    sourceRect、handle、foundIndex、candidates…）一律原样留住——编辑框只改
    它展示的字段，不能把捕获期实测出来的候选分数悄悄抹掉。
    """
    if not isinstance(nodes, list) or len(nodes) < 1:
        raise ValueError("桌面层级为空")

    selector = descriptor.get("selector") or {}
    original = selector.get("locator") or {}
    if not isinstance(original, dict):
        raise ValueError("桌面定位数据不对")
    locator = {key: value for key, value in original.items() if key not in _OWNED_KEYS}

    target = nodes[-1]
    fields, modes = _collect(target, allow_fuzzy=True)
    if not fields:
        raise ValueError("目标节点至少要勾选一个属性")
    if len(modes) > 1:
        raise ValueError("名称与自动化ID 的匹配方式要一致（运行期只有一个匹配方式）")
    locator.update(fields)
    if modes:
        locator["matchMode"] = MODE_TO_VALUE[next(iter(modes))]

    path: list[dict[str, str]] = []
    for node in nodes[:-1]:
        if node.get("readonly") or not node.get("selected"):
            continue
        step_fields, _ = _collect(node, allow_fuzzy=False)
        if step_fields:
            path.append(step_fields)
    if path:
        locator["path"] = path

    result = dict(selector)
    result["locator"] = locator
    return result


__all__ = [
    "FIELD_LABELS",
    "FUZZY_FIELDS",
    "MODE_TO_VALUE",
    "VALUE_TO_MODE",
    "compose_selector",
    "nodes_from_descriptor",
]
