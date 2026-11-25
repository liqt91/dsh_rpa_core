"""元素编辑的**无 UI 语义层**：名称校验、定位校验、编辑模型、层级重组。

边界（ADR 0022 编辑面服务化）：本模块只处理「一份描述符 ↔ 一份编辑模型」的
纯语义，**不碰文件、不碰会话、不碰任何窗口**。工作台（``app/api/api.py``）负责
会话与落盘，win32 编辑框与 PPX 的 HTML 编辑框都消费这里的同一份权威：

- ``validate_name`` —— 入库/改名/删除共用的名字口径（允许中文，挡路径穿越）；
- ``validate_selector`` / ``apply_selector`` —— 定位文本的校验与写回；
- ``build_edit_model`` —— 捕获后编辑框的内容：网页层级树、桌面层级/属性树
    （``desktop_editing``）或旧资产的定位文本；
- ``compose`` / ``desktop_editing.compose_selector`` —— 勾选 → 定位的唯一权威。

前端不感知 UIA / CDP / 对话框实现，只拿模型、提交名字与定位文本、收错误码。
"""

from __future__ import annotations

import json
from typing import Any

from . import desktop_editing
from .selector_tree import compose, levels_from_path, preferred_candidates

NAME_BAD_CHARS = frozenset('/\\:*?"<>|.')
RESERVED_NAMES = frozenset(
    ["con", "prn", "aux", "nul"]
    + [f"com{index}" for index in range(1, 10)]
    + [f"lpt{index}" for index in range(1, 10)]
)


def validate_name(name: Any) -> str | None:
    """名字不合法时返回可直接展示的文案；合法返回 ``None``。"""
    if not isinstance(name, str):
        return "名字不能为空"
    if not name or len(name) > 48 or name != name.strip():
        return "名字不能为空、不能超过 48 字，也不能带首尾空格"
    if any(ch in NAME_BAD_CHARS or ord(ch) < 32 for ch in name):
        return '名字不能含 / \\ : * ? " < > | . 这些字符'
    if name.split(".")[0].lower() in RESERVED_NAMES:
        return "这个名字是系统保留名，换一个"
    return None


def is_valid_name(name: Any) -> bool:
    """``validate_name`` 的布尔口径，给只需要判定的调用点用。"""
    return validate_name(name) is None


def edit_text_for(descriptor: dict[str, Any]) -> str:
    """可编辑的定位文本：网页给 css，桌面给 locator JSON（供编辑框预填）。"""
    selector = descriptor.get("selector") or {}
    if descriptor.get("kind") == "desktop":
        return json.dumps(selector, ensure_ascii=False, indent=2)
    return str(selector.get("css") or "")


def validate_selector(descriptor: dict[str, Any], text: str) -> str | None:
    """定位文本的校验（**不改 descriptor**）：错误文案或 ``None``。"""
    text = (text or "").strip()
    if descriptor.get("kind") == "browser":
        if not text:
            return "选择器不能为空"
        if "\n" in text or "\r" in text:
            return "网页选择器只能是一行"
        return None
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        return f"定位不是合法 JSON：{exc}"
    if not isinstance(parsed, dict) or not isinstance(parsed.get("locator"), dict):
        return '桌面定位必须是含 "locator" 字段的 JSON'
    return None


EDIT_STATE_KEY = "editState"


def saved_edit_state(descriptor: dict[str, Any], part: str) -> list[dict[str, Any]] | None:
    """资产里存的**编辑态勾选快照**（`levels` / `nodes`），形状不对就当没有。

    ★ 没有这份快照时，重开详情只能从捕获期的 `path[i].inCss` 和「首 class 默认勾」
    反推勾选——用户后来在编辑框里取消的层级/class 全部丢失（2026-11-19 现场：
    网页元素全没勾、桌面元素没勾的层级直接消失）。所以勾选状态必须随 css /
    locator 一起落盘，而不是只活在编辑框内存里。

    旧资产没有这个键 ⇒ 返回 None，调用方回退到现有推断（开发期硬切，不做迁移）。
    """
    if not isinstance(descriptor, dict):
        return None
    selector = descriptor.get("selector")
    if not isinstance(selector, dict):
        return None
    state = selector.get(EDIT_STATE_KEY)
    if not isinstance(state, dict):
        return None
    value = state.get(part)
    if not isinstance(value, list) or not value:
        return None
    if not all(isinstance(item, dict) for item in value):
        return None
    return value


def apply_selector(
    descriptor: dict[str, Any], text: str,
    edit_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把校验过的定位文本写回一份**新**描述符（调用前必须先过 ``validate_selector``）。

    ``edit_state`` 是编辑框的勾选快照：网页 levels / 桌面 nodes。必须与定位文本
    同一次保存写入，否则下次打开详情又只能猜勾选状态。
    """
    result = dict(descriptor or {})
    text = (text or "").strip()
    if descriptor.get("kind") == "browser":
        selector = dict(descriptor.get("selector") or {})
        selector["css"] = text
        # M106 S3：勾选态回喂候选层——用户勾中的属性对应的写法排到前面，
        # 运行期自愈按此顺序 try（第一轮=偏好候选，第二轮=其余）。原生框与
        # Web 框都调本函数，勾选偏好只有这一份权威，不在控件里各写一份。
        levels = edit_state.get("levels") if isinstance(edit_state, dict) else None
        if isinstance(levels, list) and isinstance(selector.get("candidates"), list):
            selector["candidates"] = preferred_candidates(selector["candidates"], levels)
        result["selector"] = selector
    else:
        result["selector"] = json.loads(text)
    if isinstance(edit_state, dict) and edit_state:
        result["selector"][EDIT_STATE_KEY] = edit_state
    return result


def build_edit_model(
    descriptor: dict[str, Any],
    *,
    name: str = "",
    kind: str = "",
    matches: int = 0,
    node_id: str | None = None,
) -> dict[str, Any]:
    """一份描述符 → 编辑框内容。

    ``browser`` 且带 ``selector.path`` 时给**网页层级模型**（影刀式树形编辑，
    前端勾完层级用 ``compose`` 重组 css）；``desktop`` 给**桌面层级/属性模型**
    （``nodes``，重组回 selector 字典）；其余旧资产退化为可编辑定位文本。
    ``info`` 是只读信息行，供两种编辑面共用。
    """
    actual_kind = kind or str(descriptor.get("kind") or "")
    parts = ["网页元素" if actual_kind == "browser" else "桌面元素"]
    if matches:
        parts.append(f"命中 {matches} 个")
    parts.append(f"将绑定到 {node_id}" if node_id else "存为资产（不绑定节点）")
    info = " · ".join(parts)

    path = (descriptor.get("selector") or {}).get("path")
    model: dict[str, Any] = {
        "title": "捕获到元素",
        "name": str(name or ""),
        "kind": actual_kind,
        "info": info,
    }
    if actual_kind == "browser" and isinstance(path, list) and path:
        levels = saved_edit_state(descriptor, "levels")
        # 快照按层级索引对应 path：层数变了就宁可回退推断，别把勾选错位到别的层。
        if levels is None or len(levels) != len(path):
            levels = levels_from_path(path)
        model["levels"] = levels
        model["selector"] = compose(levels)
        model["meta"] = info.replace(" · ", "\r\n")
    else:
        model["selector"] = edit_text_for(descriptor)
        if actual_kind == "desktop":
            nodes = saved_edit_state(descriptor, "nodes")
            model["nodes"] = (
                nodes if nodes is not None
                else desktop_editing.nodes_from_descriptor(descriptor)
            )
        model["meta"] = model["info"].replace(" · ", "\r\n")
    return model


__all__ = [
    "NAME_BAD_CHARS",
    "RESERVED_NAMES",
    "apply_selector",
    "build_edit_model",
    "compose",
    "edit_text_for",
    "is_valid_name",
    "levels_from_path",
    "saved_edit_state",
    "validate_name",
    "validate_selector",
]
