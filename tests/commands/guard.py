"""指令测试用例表的**路径安全阀**：表里的路径不许越出本变体的临时目录。

## 为什么需要它

数据 / 工作流通道的命令会真的写文件、删文件（`data.deletePath` 带 `recursive` 就是
`shutil.rmtree`）。用例表是**数据**，数据会写错——一个手滑写成维护者真实目录的
`workspace`，跑起来就是真删。所以驱动在执行任何命令**之前**先过这道阀门：把 `inputs` 与
`setup` 里所有路径类值按**实现自己的解析规则**（相对路径拼 `workspace` / `flowDir`）
解析成绝对路径，任何落在本变体临时目录之外的值直接判失败。

它放在 `tests/commands/` 而不是门禁脚本里：判据依赖「本次运行把 `{tmp}` 物化到了哪」，
只有驱动知道；但 `tests/contract/test_command_matrix_paths_guard.py` 会**在默认门禁里**
验证它两个方向都真的管用（误杀合法用例比不拦更糟，所以正向也要测）。

## 与用例表里的越权负路径不冲突

那些负路径用「在本变体目录内、但不在 workspace / flow dir 内」的绝对路径
（如 `{tmp}/escape.txt`）触发 `CAPABILITY_DENIED`——既证明防线生效，又永远不碰真实文件。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# 路径类参数（值要按实现规则解析后落在变体目录内）
PATH_KEYS = ("workspace", "path", "flowDir")


def resolve_like_implementation(raw: str, root: Path) -> Path:
    """按实现的解析规则把路径值变绝对：相对路径拼 `root`（workspace / flowDir）再规范化。"""
    candidate = Path(raw)
    return candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()


def confined_violations(value: Any, base: Path, root: Path, where: str = "inputs") -> list[str]:
    """递归找出越出 `base` 的路径类值（`root` 是相对路径的解析基准）。"""
    problems: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in PATH_KEYS and isinstance(item, str):
                resolved = resolve_like_implementation(item, root)
                if resolved != base and base not in resolved.parents:
                    problems.append(
                        f"{where}.{key}: {item!r} 解析为 {resolved}，越出本变体临时目录 {base}"
                    )
                continue
            problems.extend(confined_violations(item, base, root, f"{where}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            problems.extend(confined_violations(item, base, root, f"{where}[{index}]"))
    return problems


def assert_confined(variant: dict[str, Any], base: Path) -> None:
    """安全阀：用例表（`inputs` 与 `setup`）里的路径只许落在本变体临时目录内。"""
    inputs = variant.get("inputs") or {}
    root = base
    for key in PATH_KEYS:
        if isinstance(inputs.get(key), str):
            root = resolve_like_implementation(inputs[key], base)
            break
    problems = confined_violations(inputs, base, root)
    # `setup` 的文件由驱动自己写盘，同样不许越界（相对路径一律按 base 解析）
    setup = variant.get("setup") or {}
    problems.extend(confined_violations(setup.get("files") or [], base, base, "setup.files"))
    problems.extend(
        confined_violations(
            [{"path": item} for item in setup.get("dirs") or []], base, base, "setup.dirs"
        )
    )
    if setup.get("table"):
        problems.extend(
            confined_violations([{"path": setup["table"].get("path")}], base, base, "setup.table")
        )
    assert not problems, "\n".join(
        ["用例表的路径越出本变体临时目录（安全阀拦下，未执行任何命令）：", *problems]
    )


__all__ = ["PATH_KEYS", "assert_confined", "confined_violations", "resolve_like_implementation"]
