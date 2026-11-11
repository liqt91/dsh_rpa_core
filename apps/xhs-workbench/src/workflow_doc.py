"""流程文档视图：把 workflow JSON 摊平成「步骤列表」，供控制台的流程面板消费。

设计取向与仓库其他旁路模块一致：**不 import rpa_core 内部模块**（应用层只经 CLI
子进程与文件约定与能力层交互），所以这里自带一份「命令 → 中文名」词表。
词表命中不了就回落命令 id 本身——宁可显示 `browser.xxx`，也不编造标签。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

COMMAND_LABELS = {
    "browser.navigate": "打开网页",
    "browser.waitLoad": "等待加载完成",
    "browser.waitFor": "等待元素出现",
    "browser.input": "输入文本",
    "browser.click": "点击元素",
    "browser.scroll": "滚动页面",
    "browser.getText": "读取元素文本",
    "browser.executeScript": "执行脚本",
    "browser.screenshot": "截图",
    "browser.close": "关闭页面",
    "browser.attach": "附着已开标签页",
    "browser.queryAll": "查询元素",
    "data.writeText": "写入文本文件",
}

# 容器节点：自身没有 command，只有 children。摊平时下钻，不单独成一个步骤。
CONTAINER_TYPES = {"sequence", "if", "loop", "parallel", "try", "branch"}


def command_label(command: str) -> str:
    return COMMAND_LABELS.get(command or "", command or "")


def flatten_steps(document: dict[str, Any]) -> list[dict[str, Any]]:
    """深度优先摊平 root，只输出可执行的 action 节点。"""
    steps: list[dict[str, Any]] = []

    def walk(node: Any, depth: int) -> None:
        if not isinstance(node, dict):
            return
        command = str(node.get("command") or "")
        node_type = str(node.get("type") or "")
        if command:
            steps.append(
                {
                    "index": len(steps) + 1,
                    "id": str(node.get("id") or ""),
                    "type": node_type or "action",
                    "command": command,
                    "label": command_label(command),
                    "depth": depth,
                    "args": node.get("with") or {},
                    "exprModes": node.get("_exprModes") or {},
                    "elementRefs": node.get("elementRefs") or {},
                    "condition": node.get("condition") or node.get("if"),
                }
            )
            return
        for child in node.get("children") or []:
            walk(child, depth + (1 if node_type in CONTAINER_TYPES else 0))

    walk_root = document.get("root") or {}
    if isinstance(walk_root, dict) and walk_root.get("command"):
        walk(walk_root, 0)
    else:
        # root 一般是 sequence 容器，它本身不算一层缩进
        for child in (walk_root.get("children") or []) if isinstance(walk_root, dict) else []:
            walk(child, 0)
    return steps


def describe(path: str | Path) -> dict[str, Any]:
    """读流程文件并给出面板需要的全部字段（含原始文本，供「看源码」用）。"""
    fp = Path(path)
    text = fp.read_text(encoding="utf-8")
    document = json.loads(text)
    if not isinstance(document, dict):
        raise ValueError(f"流程文件顶层必须是对象：{fp}")
    steps = flatten_steps(document)
    return {
        "path": fp.name,
        "id": str(document.get("id") or ""),
        "name": str(document.get("name") or ""),
        "inputs": document.get("inputs") or {},
        "steps": steps,
        "count": len(steps),
        "commands": sorted({s["command"] for s in steps}),
        "raw": text,
    }
