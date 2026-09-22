"""指令测试覆盖率校验（M38）：用例表与 catalog 是否对齐。

## 为什么这个校验器**在默认门禁里**，而用例本身不在

`docs/command-testing-strategy.md` §4 原本把这套覆盖率校验也归为「按需」。2026-09-22
维护者定案把它拆开：

- **静态层（本文件）**：只读用例表 JSON 与 manifest，**不执行任何命令**，毫秒级。
  它是「防用例表随时间腐烂」的唯一机器防线——新增命令不补用例、参数改了没人改表，
  都在这里报红。属于每次提交都该跑的东西。
- **执行层（`tests/commands/`）**：全量参数矩阵，几百个变体、要起进程跑，
  按需启用（`RPA_COMMAND_MATRIX=1`）。

## 校验口径（6 条）

1. **命令覆盖**：catalog 里每条命令都要在用例表里出现（未建表的命名空间见台账）；
2. **参数覆盖**：`input_schema.properties` 的**每个参数**至少被一个变体显式设置过；
3. **枚举覆盖**：枚举参数的**每个取值**至少出现一次；
4. **边界覆盖**：声明了 `minimum`/`maximum` 的参数至少出现两个不同的值；
5. **最少变体数**：每条命令的变体数不低于阈值（`MIN_VARIANTS`，缺省 3）；
6. **负路径**：每条命令至少有 1 个标记 `negative: true` 的变体（失败路径或边界行为）。

第 6 条与策略原文的「required 缺省必须被拒」**口径不同**，理由：输入 schema 的
`required` 由 **orchestrator** 统一校验（`runtime/orchestrator.py` 用
`Draft202012Validator(manifest.input_schema)`），执行器层只对手写校验的命令报错
（如 `browser.navigate` 的 goto 缺 url）。所以「缺必填必失败」不是执行器层的契约，
把责任压到这里会写出与实现不符的断言。**执行器层真正的责任是「失败路径要显式、
错误码要可诊断」**，第 6 条验的就是它。

## 台账（会自我收紧）

- `PENDING_NAMESPACES`：尚未建用例表的命名空间（按命名空间登记 + 理由）。
- `KNOWN_DEAD_PARAMS`：**已入表但参数确实未被消费**的登记项（按参数登记）。
  两者都是**一次性欠账**：条目一旦不再需要（命名空间建了表 / 参数被覆盖 / 参数已从
  manifest 删除），本校验器立即报「台账过期」并要求删除——所以它们只会变短。
  当前 `KNOWN_DEAD_PARAMS` 为空（S1 的两项已由维护者定案删除）。

运行：uv run python .harness/scripts/check_command_matrix.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
COMMANDS_DIR = ROOT / "commands"
CASES_DIR = ROOT / "tests" / "commands" / "cases"

# 每条命令最少变体数（策略 §5.2 第 4 条）
MIN_VARIANTS = 3

# 尚未建用例表的命名空间：`命名空间 → 理由`。
# **自我收紧**：一旦 `cases/<ns>.json` 出现，这里的条目就必须删除，否则报「台账过期」。
PENDING_NAMESPACES: dict[str, str] = {
    "desktop": "M38 S2：桌面通道（UIA 17 + Win32 19）——需真实桌面 fixture，另起切片",
    "data": "M38 S3：数据通道 17 条（python.worker，零漂移但输出契约未覆盖）",
    "workflow": "M38 S3：工作流通道 1 条（workflow.sleep）",
}

# 已入表、但参数确实未被该命令消费的登记项：`命令 → {参数: 说明}`。
# 这些是**实测发现的声明-实现偏差**（静态门禁 `check_param_consumption.py` 因「通用读取」
# 放行：读取发生在不带命令字面量的辅助函数里，于是所有命令都被判为已消费）。
# 自我收紧：参数一旦在用例表里出现，条目即过期。
#
# 目前**为空**：M38 S1 实测到的两项（`browser.closeTabs.browserType`、
# `browser.waitLoad.state`）已由维护者定案「删除」，S1.1 已从 manifest 移除——
# 它们从没被消费过，删掉的是「声明」而不是「能力」。台账机制保留，供后续通道（S2/S3）复用。
KNOWN_DEAD_PARAMS: dict[str, dict[str, str]] = {}


def _load_catalog() -> dict[str, dict[str, Any]]:
    """读全部 manifest：`命令 id → manifest`（含 input_schema）。"""
    catalog: dict[str, dict[str, Any]] = {}
    for path in sorted(COMMANDS_DIR.rglob("*.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        command_id = str(manifest.get("id") or "")
        if not command_id:
            continue
        catalog[command_id] = manifest
    return catalog


def _load_cases() -> dict[str, dict[str, Any]]:
    """合并 `tests/commands/cases/*.json`（每个文件一个命名空间）。"""
    merged: dict[str, dict[str, Any]] = {}
    for path in sorted(CASES_DIR.glob("*.json")):
        merged.update(json.loads(path.read_text(encoding="utf-8")))
    return merged


def _variants(spec: dict[str, Any]) -> list[dict[str, Any]]:
    return list(spec.get("variants") or [])


def _check_bounds(name: str, spec: dict[str, Any], values: list[Any]) -> str | None:
    """有 minimum/maximum 的参数至少要有两个不同取值（合法值 + 边界/越界值）。"""
    has_bound = "minimum" in spec or "maximum" in spec
    if has_bound and len(set(values)) < 2:
        return (
            f"{name}: 声明了 min/max 但变体里只有一个取值（{values}）——"
            "至少要有「合法值 + 边界/越界值」两个变体"
        )
    return None


def _check_command(
    command: str, manifest: dict[str, Any], spec: dict[str, Any]
) -> list[str]:
    problems: list[str] = []
    schema = manifest.get("input_schema") or {}
    properties: dict[str, Any] = schema.get("properties") or {}
    variants = _variants(spec)

    # 「本期未实现」的命令（执行器对本命令返回 COMMAND_NOT_FOUND）：整张参数表都是待实现
    # 清单，不是覆盖率缺口——阈值降到 1，且不要求参数/枚举/边界覆盖（那些字段还没接线）。
    # 「确实未实现」这件事由**执行层用例**保证（它断言 COMMAND_NOT_FOUND），本文件不重复。
    if spec.get("unimplemented"):
        if not variants:
            problems.append(f"{command}: 标记 unimplemented 但一个变体都没有")
        return problems

    if len(variants) < MIN_VARIANTS:
        problems.append(f"{command}: 变体数 {len(variants)} < 阈值 {MIN_VARIANTS}")
    if not any(variant.get("negative") for variant in variants):
        problems.append(f"{command}: 没有任何 negative 变体（失败路径/边界行为未覆盖）")

    dead = KNOWN_DEAD_PARAMS.get(command, {})

    # -- 参数覆盖 + 枚举覆盖 + 边界覆盖 --------------------------------------
    for name, param_spec in sorted(properties.items()):
        if not isinstance(param_spec, dict):
            param_spec = {}
        seen = [
            variant.get("inputs", {}).get(name)
            for variant in variants
            if name in (variant.get("inputs") or {})
        ]
        if not seen:
            if name in dead:
                continue  # 已登记的死参数（静态门禁抓不到的那类声明-实现偏差）
            problems.append(
                f"{command}.{name}: 没有任何变体显式设置该参数（若实现确实未消费，"
                f"请登记到 KNOWN_DEAD_PARAMS 并写清处置）"
            )
            continue
        if name in dead:
            problems.append(
                f"台账过期：{command}.{name} 已被覆盖，请从 KNOWN_DEAD_PARAMS 删除"
            )
        enum_values = param_spec.get("enum")
        if isinstance(enum_values, list):
            for value in enum_values:
                if value not in seen:
                    problems.append(
                        f"{command}.{name}: 枚举值 {value!r} 从未出现在任何变体里"
                    )
        bounds = _check_bounds(f"{command}.{name}", param_spec, seen)
        if bounds:
            problems.append(bounds)

    # -- 台账过期检查（未覆盖但已登记的项已在上面的 continue 分支处理） --------
    for name in dead:
        if name not in properties:
            problems.append(
                f"台账过期：{command}.{name} 已不在 manifest 里，请从 KNOWN_DEAD_PARAMS 删除"
            )
    return problems


def main() -> int:
    catalog = _load_catalog()
    cases = _load_cases()
    problems: list[str] = []
    checked = 0

    # 命名空间台账自我收紧
    for namespace, reason in PENDING_NAMESPACES.items():
        if (CASES_DIR / f"{namespace}.json").exists():
            problems.append(
                f"台账过期：cases/{namespace}.json 已存在，请从 PENDING_NAMESPACES 删除"
                f"（原登记理由：{reason}）"
            )

    for command, manifest in sorted(catalog.items()):
        namespace = command.split(".")[0]
        if namespace in PENDING_NAMESPACES:
            continue
        spec = cases.get(command)
        if spec is None:
            problems.append(f"{command}: 用例表里没有这条命令")
            continue
        checked += 1
        problems.extend(_check_command(command, manifest, spec))

    for command in sorted(cases):
        if command not in catalog:
            problems.append(f"{command}: 用例表里有，但 catalog 里没有（命令已删或改名？）")

    pending = sum(
        1 for command in catalog if command.split(".")[0] in PENDING_NAMESPACES
    )
    if problems:
        print("COMMAND MATRIX CHECK FAILED")
        for problem in problems:
            print(f"  - {problem}")
        print(
            f"（已校验 {checked} 条命令；待建表命名空间 {len(PENDING_NAMESPACES)} 个"
            f"共 {pending} 条命令已登记）"
        )
        return 1

    dead_params = sum(len(items) for items in KNOWN_DEAD_PARAMS.values())
    print(
        f"COMMAND MATRIX CHECK PASSED（已校验 {checked} 条命令；"
        f"未建表命名空间 {len(PENDING_NAMESPACES)} 个共 {pending} 条命令，"
        f"死参数台账 {dead_params} 项）"
    )
    for namespace, reason in PENDING_NAMESPACES.items():
        print(f"  待建表：{namespace} —— {reason}")
    for command, items in sorted(KNOWN_DEAD_PARAMS.items()):
        for name, reason in sorted(items.items()):
            print(f"  死参数：{command}.{name} —— {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
