#!/usr/bin/env python3
"""装到别处之后，技能还知道自己原来在哪。

为什么需要这一层：技能是拷进宿主的技能目录的（`~/.dsh/skills/workflow-studio`），
而它要用的东西都在源仓库里 —— `src/rpa_core`（CLI 本体）、`editor/`（PPX 窗口工程）、
`extension/`（浏览器插件）。相对路径在安装态全部失效，位置又不能在运行期靠猜。

所以由安装器在装的时候把这些**实测**到的绝对路径写进 `runtime.json`；
本模块是唯一的读法，接受三种来源，优先级从高到低：

    1. 环境变量   RPA_CORE_HOME / RPA_CORE_PYTHON / STUDIO_RUNTIME（临时改道、CI 用）
    2. runtime.json（安装器写的）
    3. 自身向上找 src/rpa_core/cli.py（源码树里直接跑，开发态）

第 3 条是给开发态兜底的：在仓库里 `python workbuddy-suite/.../studio.py` 时并不存在
runtime.json，行为必须和以前完全一样。三条都不成立时返回 None —— 让调用方去报
「找不到」，而不是拿一个假路径继续跑。

只用标准库：studio.py 的前提是「装不装东西都能跑」。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent  # scripts/
SKILL_DIR = HERE.parent  # workflow-studio/
RUNTIME_FILE = SKILL_DIR / "runtime.json"

# 判定「这是源码树根」的标记：rpa_core 的 CLI 入口 + 本套件自己的目录。
# 两个都要求，避免把一个恰好有 src/ 的无关目录当根。
SOURCE_MARKERS = ("src/rpa_core/cli.py", "workbuddy-suite")
# 向上找几级够用：scripts → workflow-studio → skills → workbuddy-suite → 仓库根
MAX_WALK_UP = 6


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load() -> dict[str, Any]:
    """runtime.json 的内容；没有就是空字典（不抛异常）。"""
    override = os.environ.get("STUDIO_RUNTIME")
    if override:
        return _read_json(Path(override).expanduser())
    return _read_json(RUNTIME_FILE)


def is_source_root(path: Path | None) -> bool:
    if not path:
        return False
    try:
        return all((path / marker).exists() for marker in SOURCE_MARKERS)
    except OSError:
        return False


def find_source_root() -> Path | None:
    """仓库根：环境变量 → runtime.json → 自身向上找。都没有就 None。"""
    for raw in (os.environ.get("RPA_CORE_HOME"), load().get("source_root")):
        if not raw:
            continue
        candidate = Path(str(raw)).expanduser()
        if is_source_root(candidate):
            return candidate

    for parent in (HERE, *HERE.parents)[:MAX_WALK_UP]:
        if is_source_root(parent):
            return parent
    return None


def python_exe() -> Path:
    """能 import rpa_core 的解释器；没记录时用当前解释器。

    注意 studio.py 只用标准库，谁跑都行；rc.py 必须用它，否则 import 会缺依赖。
    所以这里优先信 runtime.json，而不是 sys.executable。
    """
    for raw in (os.environ.get("RPA_CORE_PYTHON"), load().get("python")):
        if raw:
            candidate = Path(str(raw)).expanduser()
            if candidate.is_file():
                return candidate
    return Path(sys.executable)


def describe() -> dict[str, Any]:
    """给 doctor / 报错用的快照：解析到什么都列出来，不做真假判断。"""
    data = load()
    root = find_source_root()
    return {
        "runtime_file": str(RUNTIME_FILE) if RUNTIME_FILE.is_file() else None,
        "source_root": str(root) if root else None,
        "python": str(python_exe()),
        "rpa_core": data.get("rpa_core") or {},
        "resolved_by": "runtime.json"
        if RUNTIME_FILE.is_file()
        else ("env" if os.environ.get("RPA_CORE_HOME") else ("source-tree" if root else "none")),
    }
