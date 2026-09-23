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
7. **`knownGap` 的记账**：变体级的 `knownGap` 必须是非空字符串（写清缺口与出路），
   且**标记过的变体不计入**第 2–6 条的任何口径——缺口不许用来凑覆盖率。
8. **`l2` 块的形状**（M38 S4，L2 真机冒烟）：`l2` 必须是对象；`page` 必填且
   `testapps/browser/<page>.html` 必须存在；`expect` 必填非空且**不得含调用类键**
   （`onlyCall`/`calls`/`noCalls`——L2 没有调用记录面，那是 L1 的桩断言，写了
   执行层必违规）；`inputs` 若给必须是对象。`verify` 若给必须是**非空对象列表**，
   每步 `command` 必填且在 catalog 里、`expect` 同样非空且禁含调用类键。

第 6 条与策略原文的「required 缺省必须被拒」**口径不同**，理由：输入 schema 的
`required` 由 **orchestrator** 统一校验（`runtime/orchestrator.py` 用
`Draft202012Validator(manifest.input_schema)`），执行器层只对手写校验的命令报错
（如 `browser.navigate` 的 goto 缺 url）。所以「缺必填必失败」不是执行器层的契约，
把责任压到这里会写出与实现不符的断言。**执行器层真正的责任是「失败路径要显式、
错误码要可诊断」**，第 6 条验的就是它。

### 第 7 条的边界（为什么阈值仍数全部变体）

`MIN_VARIANTS` 数的是**表里的行数**（结构性下限），2–6 条数的是**未被标记的变体**
（行为性覆盖）：一条 `knownGap` 行仍然是一行真实存在的用例，只是它现在证不了产品行为。
真正要防的是「拿缺口盖住整条命令」——那由第 6 条兜住：要求**至少一个未标记的
negative 变体**，所以全表标成 `knownGap` 必然报红。

`knownGap` 的自我收紧不在本文件（静态层看不到「缺口修没修」），而在**执行层**：
标记会变成 `pytest.mark.xfail(strict=True)`，缺口一修那行就 XPASS 转红。
详见 `tests/commands/matrix.py` 的 `knownGap` 一节。

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
TESTAPPS_BROWSER_DIR = ROOT / "testapps" / "browser"

# L2 的 expect 不得出现的调用类键（第 8 条）：L2 是真后端，没有调用记录面——
# 这些键是 L1 假扩展的桩断言，混进 l2.expect 后执行层必然违规（calls=None 判红）。
L2_FORBIDDEN_EXPECT_KEYS = ("onlyCall", "calls", "noCalls")

# 每条命令最少变体数（策略 §5.2 第 4 条）
MIN_VARIANTS = 3

# 尚未建用例表的命名空间：`命名空间 → 理由`。
# **自我收紧**：一旦 `cases/<ns>.json` 出现，这里的条目就必须删除，否则报「台账过期」。
#
# 当前**为空**：M38 S2（2026-09-22）已建 `cases/desktop.json`（UIA 17 条）与
# `cases/desktop_win32.json`（Win32 19 条），86 条命令全部有表。台账机制保留，供后续
# 新增命名空间复用。
PENDING_NAMESPACES: dict[str, str] = {}

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


def _has_known_gap(variant: dict[str, Any]) -> bool:
    """变体是否被标记为已登记的实现缺口（口径见模块 docstring 第 7 条）。"""
    return bool(str(variant.get("knownGap") or "").strip())


def _check_known_gaps(command: str, variants: list[dict[str, Any]]) -> list[str]:
    """第 7 条的记账检查：`knownGap` 必须写明缺口与出路（空字符串等于没记）。"""
    problems: list[str] = []
    for variant in variants:
        if "knownGap" in variant and not _has_known_gap(variant):
            problems.append(
                f"{command}.{variant.get('name')}: knownGap 是空串——必须写清「实测现象 + 出路」，"
                "否则它只是把红盖住的被子"
            )
    return problems


def _check_l2_expect(command: str, name: Any, expect: Any, where: str) -> list[str]:
    """l2 块里一处 expect 的形状校验（主 expect 与 verify 步共用同一口径）。"""
    problems: list[str] = []
    if not isinstance(expect, dict) or not expect:
        problems.append(f"{command}.{name}: {where} 缺失或为空——真机跑完不断言等于白跑")
        return problems
    for key in L2_FORBIDDEN_EXPECT_KEYS:
        if key in expect:
            problems.append(
                f"{command}.{name}: {where} 含调用类键 {key!r}——L2 没有调用记录面"
                "（那是 L1 假扩展的桩断言），写了执行层必违规"
            )
    return problems


def _check_l2_block(
    command: str, variant: dict[str, Any], catalog: dict[str, dict[str, Any]]
) -> list[str]:
    """第 8 条：`l2` 块的形状校验（口径见模块 docstring）。

    没有 `l2` 块的变体合法（L1-only：桩形状整形、通道错误映射、纯下发断言
    在真后端上要么不可诱导、要么不可见），这里只校验**声明了**的块。
    """
    problems: list[str] = []
    l2 = variant.get("l2")
    if l2 is None:
        return problems
    name = variant.get("name")
    if not isinstance(l2, dict):
        problems.append(f"{command}.{name}: l2 必须是对象（page/inputs/expect/verify）")
        return problems
    page = l2.get("page")
    if not isinstance(page, str) or not page.strip():
        problems.append(f"{command}.{name}: l2.page 缺失或为空——驱动不知道把会话建到哪页")
    elif not (TESTAPPS_BROWSER_DIR / f"{page.strip()}.html").is_file():
        problems.append(
            f"{command}.{name}: l2.page={page!r} 没有对应靶页 "
            f"testapps/browser/{page.strip()}.html"
        )
    problems.extend(_check_l2_expect(command, name, l2.get("expect"), "l2.expect"))
    if "inputs" in l2 and not isinstance(l2["inputs"], dict):
        problems.append(f"{command}.{name}: l2.inputs 必须是对象（覆盖 variant.inputs）")
    verify = l2.get("verify")
    if verify is not None:
        if not isinstance(verify, list) or not verify:
            problems.append(f"{command}.{name}: l2.verify 必须是非空列表")
        else:
            for index, step in enumerate(verify):
                where = f"l2.verify[{index}]"
                if not isinstance(step, dict):
                    problems.append(f"{command}.{name}: {where} 必须是对象")
                    continue
                step_command = step.get("command")
                if not isinstance(step_command, str) or step_command not in catalog:
                    problems.append(
                        f"{command}.{name}: {where}.command={step_command!r} 不在 catalog 里"
                    )
                problems.extend(
                    _check_l2_expect(command, name, step.get("expect"), f"{where}.expect")
                )
    return problems


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
    problems.extend(_check_known_gaps(command, variants))
    # 2–6 条只看**未被 knownGap 标记**的变体：缺口不许用来凑覆盖率（模块 docstring 第 7 条）。
    effective = [variant for variant in variants if not _has_known_gap(variant)]
    if not any(variant.get("negative") for variant in effective):
        problems.append(
            f"{command}: 没有任何**未标 knownGap** 的 negative 变体"
            "（失败路径/边界行为未覆盖，或整条命令都被缺口盖住了）"
        )
    variants = effective

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

    # 第 8 条：l2 块形状校验（不依赖 catalog 收录与否，全表扫；verify 步的命令引用要查 catalog）
    l2_count = 0
    for command, spec in sorted(cases.items()):
        for variant in _variants(spec):
            if "l2" in variant:
                l2_count += 1
            problems.extend(_check_l2_block(command, variant, catalog))

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
    # 已登记的实现缺口（变体级 knownGap）逐条列出来——它们是「用例表上真实的红」，
    # 静态层只能记账（执行层用严格 xfail 自我收紧，见模块 docstring 第 7 条）。
    gaps = [
        (command, variant.get("name"), str(variant["knownGap"]))
        for command, spec in sorted(cases.items())
        for variant in _variants(spec)
        if _has_known_gap(variant)
    ]
    print(
        f"COMMAND MATRIX CHECK PASSED（已校验 {checked} 条命令；"
        f"未建表命名空间 {len(PENDING_NAMESPACES)} 个共 {pending} 条命令，"
        f"死参数台账 {dead_params} 项，实现缺口 {len(gaps)} 条，l2 块 {l2_count} 个）"
    )
    for namespace, reason in PENDING_NAMESPACES.items():
        print(f"  待建表：{namespace} —— {reason}")
    for command, items in sorted(KNOWN_DEAD_PARAMS.items()):
        for name, reason in sorted(items.items()):
            print(f"  死参数：{command}.{name} —— {reason}")
    for command, name, reason in gaps:
        print(f"  实现缺口：{command}::{name} —— {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
