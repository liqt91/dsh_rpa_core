"""GUI 消费 ``devserver/static/i18n.js`` 的唯一入口（M54 S1）。

- ``i18n.js`` 是**全仓唯一一份**中文化事实源（Web 编辑器 / 指令树 / 参数面板共用）；
  本模块只做「读 + 解析 + 缓存」，**不新增任何中文映射表**——在 Python 再抄一张就是
  第二套权威：加一个命令要改两处，且漂移无症状（M54 之前 ``CONDITION_OP_LABELS``
  就是手抄 ``ops`` 块，见 G4）。
- 解析失败一律**回退英文原文**（与 Web ``app.js`` 的 ``fieldLabel`` 同口径），
  不假装有中文——缺标签应当看得见，而不是被静默替换。
- ``fields`` 块内是扁平 ``键: "值"``；``commandFields`` 是嵌一层命令 id 的对象，
  故用**括号计数法**取块（容忍块内嵌套）。``fields`` 里有一处重复键 ``text``
  （JS 对象字面量后者覆盖前者），本模块的解析结果与浏览器行为一致，属既有小瑕疵。

字段标签的三级回退（与 Web ``fieldLabel`` 逐字同口径，最后套 GUI 有意分歧覆盖）：:

    commandFields[命令 id][键]  →  fields[键]  →  键名原文  →  GUI_LABEL_OVERRIDES[键]
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

_I18N_PATH = Path(__file__).resolve().parent.parent / "devserver" / "static" / "i18n.js"

# 键可带引号（"browser.navigate"）或不带（sessionId），值一律双引号字符串
_KV = re.compile(r'"?([A-Za-z_$][\w.$-]*)"?\s*:\s*"((?:[^"\\]|\\.)*)"')
_COMMAND_OPEN = re.compile(r'"([\w.]+)"\s*:\s*\{')

# 与 Web 显示口径的**有意分歧**（维护者 2026-10-12 拍板「跟影刀」，见 M54 方案 §六）。
# 影刀把元素槽位一律叫「操作目标」，不区分浏览器/桌面；Web 侧分别是「选择器」/「定位器」。
# 只登记**确实不一致**的键，并由 tests/contract/test_gui_i18n.py 钉住取值——
# 防止有人把它悄悄改回与 Web 一致（那就等于口径无声漂移，用户又说「怎么还带着选择器」）。
GUI_LABEL_OVERRIDES: dict[str, str] = {
    "selector": "操作目标",
    "locator": "操作目标",
}


def _balanced(text: str, open_index: int) -> str:
    """取 ``text[open_index] == "{"`` 起、到配对 ``}`` 之间的**内容**（跳过字符串内括号）。"""
    depth = 0
    start = open_index + 1
    i = open_index
    in_str = False
    quote = ""
    while i < len(text):
        char = text[i]
        if in_str:
            if char == "\\":
                i += 2
                continue
            if char == quote:
                in_str = False
        elif char in "\"'":
            in_str = True
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:i]
        i += 1
    return ""


def _block(text: str, name: str) -> str:
    """取顶层块 ``name: { ... }`` 的内容；找不到返回空串。"""
    match = re.search(rf"^\s*{name}\s*:\s*\{{", text, re.MULTILINE)
    return _balanced(text, match.end() - 1) if match else ""


def _parse_flat(content: str) -> dict[str, str]:
    """解析扁平 ``键: "值"`` 表。"""
    return {m.group(1): m.group(2) for m in _KV.finditer(content)}


def _parse_command_fields(content: str) -> dict[str, dict[str, str]]:
    """解析 ``commandFields``：``{ "命令 id": { 键: "值" } }``。"""
    result: dict[str, dict[str, str]] = {}
    for match in _COMMAND_OPEN.finditer(content):
        result[match.group(1)] = _parse_flat(_balanced(content, match.end() - 1))
    return result


@lru_cache(maxsize=1)
def _load() -> dict[str, Any]:
    """读一次 ``i18n.js`` 并解析出四个块；任何读取失败都回退空表。"""
    try:
        text = _I18N_PATH.read_text(encoding="utf-8")
    except OSError:
        return {}
    return {
        "commands": _parse_flat(_block(text, "commands")),
        "fields": _parse_flat(_block(text, "fields")),
        "commandFields": _parse_command_fields(_block(text, "commandFields")),
        "ops": _parse_flat(_block(text, "ops")),
    }


def load_command_display_names() -> dict[str, str]:
    """命令 id → 中文显示名（指令树/命令面板用）。"""
    return dict(_load().get("commands") or {})


def load_field_labels() -> dict[str, str]:
    """字段键名 → 中文标签（公共键部分）。"""
    return dict(_load().get("fields") or {})


def load_command_field_labels() -> dict[str, dict[str, str]]:
    """命令 id → {字段键名: 该命令专属中文标签}。"""
    return {k: dict(v) for k, v in (_load().get("commandFields") or {}).items()}


def load_op_labels() -> dict[str, str]:
    """条件操作符 → 中文标签（``ops`` 块）。"""
    return dict(_load().get("ops") or {})


def field_label(key: str, command_id: str | None = None) -> str:
    """字段中文标签：命令级覆盖 → 公共键 → 键名原文 → GUI 有意分歧覆盖。"""
    data = _load()
    specific = (data.get("commandFields") or {}).get(command_id or "", {})
    if key in specific:
        resolved = specific[key]
    else:
        resolved = (data.get("fields") or {}).get(key, key)
    return GUI_LABEL_OVERRIDES.get(key, resolved)
