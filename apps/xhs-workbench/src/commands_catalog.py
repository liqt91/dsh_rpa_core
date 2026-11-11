"""指令目录：把仓内 ``commands/**/*.json`` 清单摊成编辑器左侧「指令栏」和
右侧「属性表单」要用的数据。

边界纪律：应用层**不 import rpa_core 内部模块**，清单本来就是 JSON 文件契约，
中文名从 ``src/rpa_core/gui/i18n.js`` 做文本级解析（同样是文件契约）。
缺标签一律回落命令 id 本身——宁可显示英文，也不编造。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

GROUP_LABELS = {
    "browser": "浏览器",
    "data": "数据",
    "workflow": "流程",
    "desktop": "桌面",
    "desktop_win32": "桌面（Win32）",
    "ocr": "OCR",
}

_PAIR_RE = re.compile(r'^\s*"([^"]+)"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,?\s*$')


def load_command_labels(i18n_js: str | Path) -> dict[str, str]:
    """从 ``i18n.js`` 的 ``commands: { ... }`` 块里解析「命令 id → 中文名」。

    该文件的结构稳定：顶层段缩进 2 空格（``commands:`` / ``fields:`` …），
    命令条目缩进 4 空格。按缩进切分，只收 commands 段内 ``"a.b": "中文"`` 的行。
    文本级解析是刻意选择：应用层不 import rpa_core，JS 也不能 import。
    """
    try:
        text = Path(i18n_js).read_text(encoding="utf-8")
    except OSError:
        return {}
    labels: dict[str, str] = {}
    in_commands = False
    for line in text.splitlines():
        if re.match(r"^\s{2}commands:\s*\{", line):
            in_commands = True
            continue
        if in_commands and re.match(r"^\s{2}[a-zA-Z_]+:", line):
            break  # 下一个顶层段开始，commands 段结束
        if not in_commands:
            continue
        m = _PAIR_RE.match(line)
        if m and "." in m.group(1):
            labels[m.group(1)] = m.group(2).replace('\\"', '"').replace("\\\\", "\\")
    return labels


def _field_view(key: str, schema: dict[str, Any]) -> dict[str, Any]:
    prop_type = schema.get("type")
    if isinstance(prop_type, list):
        prop_type = "any" if "null" in prop_type else prop_type[0]
    enum = schema.get("enum")
    return {
        "key": key,
        "type": prop_type or "string",
        "description": str(schema.get("description") or ""),
        "default": schema.get("default"),
        "fx": bool(schema.get("x-fx")),
        "python": bool(schema.get("x-python")),
        "multiline": bool(schema.get("x-multiline")),
        "enum": [
            {"value": v, "label": str((schema.get("x-enum-labels") or {}).get(v, v))}
            for v in enum
        ]
        if isinstance(enum, list)
        else None,
    }


def describe_manifest(manifest: dict[str, Any], labels: dict[str, str]) -> dict[str, Any]:
    """单条 manifest → 面板/表单视图。"""
    command = str(manifest.get("id") or "")
    group = command.split(".", 1)[0]
    schema = manifest.get("input_schema") or {}
    required = schema.get("required") or []
    fields = [
        dict(_field_view(k, v), required=k in required)
        for k, v in (schema.get("properties") or {}).items()
    ]
    return {
        "id": command,
        "label": labels.get(command, command),
        "group": group,
        "groupLabel": GROUP_LABELS.get(group, group),
        "kind": str(manifest.get("kind") or "action"),
        "order": int(manifest.get("x-palette-order") or 999),
        "implemented": str(manifest.get("stability") or "") != "unimplemented",
        "required": required,
        "fields": fields,
    }


def load_commands(commands_dir: str | Path, labels: dict[str, str]) -> list[dict[str, Any]]:
    """扫 ``commands/<组>/*.json``，按组 + palette 顺序排序。坏文件跳过并计数。"""
    root = Path(commands_dir)
    out: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*.json")):
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(manifest, dict) or not manifest.get("id"):
            continue
        out.append(describe_manifest(manifest, labels))
    out.sort(key=lambda c: (c["group"], c["order"], c["id"]))
    return out
