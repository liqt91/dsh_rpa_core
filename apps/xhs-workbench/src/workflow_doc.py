"""流程文档视图：把 workflow JSON 摊平成「步骤列表」，供控制台的流程面板消费。

设计取向与仓库其他旁路模块一致：**不 import rpa_core 内部模块**（应用层只经 CLI
子进程与文件约定与能力层交互），所以这里自带一份「命令 → 中文名」词表。
词表命中不了就回落命令 id 本身——宁可显示 `browser.xxx`，也不编造标签。
"""

from __future__ import annotations

import json
import re
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

# 各容器节点的子列表键，按 src/rpa_core/model/workflow.py 的真实形状写。
# ★ 不是所有容器都叫 children：if 的分支体在 then / else，try 的捕获体在 catch。
# 键的顺序就是摊平顺序（先「则」后「否则」、先「尝试」后「异常」）。
CONTAINER_BRANCHES: dict[str, tuple[str, ...]] = {
    "sequence": ("children",),
    "forEach": ("children",),
    "if": ("then", "else"),
    "try": ("children", "catch"),
}
# 旧流程里出现过的容器别名仍按 children 下钻，避免在只读面板里整段消失。
CONTAINER_TYPES = frozenset(CONTAINER_BRANCHES) | {"loop", "parallel", "branch"}


def command_label(command: str) -> str:
    return COMMAND_LABELS.get(command or "", command or "")


_PAIR_RE = re.compile(r'^\s*"([^"]+)"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,?\s*$')


def load_command_labels(i18n_js: str | Path) -> dict[str, str]:
    """从 ``gui/i18n.js`` 的 ``commands: { ... }`` 块里解析「命令 id → 中文名」。

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


def flatten_steps(
    document: dict[str, Any], labels: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    """深度优先摊平 root，只输出可执行的 action 节点。

    控制流节点本身不进列表，但它们的**每条分支体**都要下钻；`branch` 记下这条
    action 落在父容器的哪个分支（then / else / catch / children），供面板分组用。
    """
    label_map = labels or COMMAND_LABELS
    steps: list[dict[str, Any]] = []

    def branches_of(node_type: str) -> tuple[str, ...]:
        return CONTAINER_BRANCHES.get(node_type, ("children",))

    def walk(node: Any, depth: int, branch: str = "") -> None:
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
                    "label": label_map.get(command, command),
                    "depth": depth,
                    "branch": branch,
                    "args": node.get("with") or {},
                    "exprModes": node.get("_exprModes") or {},
                    "elementRefs": node.get("elementRefs") or {},
                    "condition": node.get("condition") or node.get("if"),
                }
            )
            return
        for key in branches_of(node_type):
            for child in node.get(key) or []:
                walk(child, depth + (1 if node_type in CONTAINER_TYPES else 0), key)

    root = document.get("root") or {}
    if not isinstance(root, dict):
        return steps
    if root.get("command"):
        walk(root, 0)
        return steps
    # root 一般是 sequence 容器，它本身不算一层缩进
    for key in branches_of(str(root.get("type") or "")):
        for child in root.get(key) or []:
            walk(child, 0, key)
    return steps


def describe(path: str | Path, labels: dict[str, str] | None = None) -> dict[str, Any]:
    """读流程文件并给出面板需要的全部字段（含原始文本，供「看源码」用）。"""
    fp = Path(path)
    text = fp.read_text(encoding="utf-8")
    document = json.loads(text)
    if not isinstance(document, dict):
        raise ValueError(f"流程文件顶层必须是对象：{fp}")
    steps = flatten_steps(document, labels)
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
