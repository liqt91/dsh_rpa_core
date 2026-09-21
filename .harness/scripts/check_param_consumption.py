"""门禁：manifest 声明的输入参数必须被实现消费（防「声明了不生效」回归）。

背景：`input_schema` 是命令契约的唯一事实来源——参数声明了，GUI 就会渲染成开关，
用户就会按说明去用。M28 S3 修掉 `browser.input` 的两个参数后，S4 用「参数名是否在实现里
出现过」的启发式复查，又发现 4 处漂移；M29 换成本文件的口径（**按命令切片**）后，
再多挖出一处（`cookieGetAll` 的 `name`/`domain`/`path` 一个都没转发）。

判定口径：

1. 对每条命令，取其 executor 对应的实现文件；
2. AST 解析实现，把输入读取（`inputs.get("x")` / `inputs["x"]` / `invocation.inputs[...]`）
   归到**所在分支比较的字面量命令**上（`if command == "browser.click":`、
   `if invocation.command_id == "data.writeText":`、`if command in ("a", "b"):`）；
   不在任何命令分支里的读取算「通用读取」（如 `_post_delay` 的 `postDelayMs`、
   `_match_ext_tab` 的 `pattern`/`matchBy`、`_resolve_output_path` 的 `workspace`——
   由多个命令共享）；
3. 声明的参数必须落在「该命令分支的读取 ∪ 通用读取」内，否则报错；
4. 执行器里显式声明「本期未实现」（分支体返回 `COMMAND_NOT_FOUND`）的命令整体豁免：
   它们的整张参数表都是待实现清单，不是漂移。**实现后自动纳入检查**，不需要改本文件。

命令载体与输入容器的形状按通道而异，本文件两处都接受：

| 通道 | 命令变量 | 输入容器 |
|---|---|---|
| `browser.*` | `command`（局部别名） | `inputs` |
| `desktop.*` / `desktop.win32.*` | `command` | `inputs` |
| `python.worker` | `invocation.command_id` | `invocation.inputs` |

已知盲区（如实写在这里，不假装是形式化证明）：

- 静态扫描只认字面量键。实现里按变量取（`inputs[key]`、循环一个 tuple）时看不见——
  所以实现侧要按字面量取值（见 `browser.cookieGetAll` 的过滤器写法）；
- 读取发生在**不带命令字面量的辅助函数**里时，会算进「通用读取」而让所有命令都通过
  （例如 `browser.waitFor` 的 `state` 读在 `_ext_wait_for(inputs, ...)` 里）。收窄办法是
  把这类读取搬进 `if command == ...` 分支，或在此显式登记；
- 走**注册表分派**的命令（`python_worker._TABLE_HANDLERS[invocation.command_id]` 对应的
  `data.table.*`、以及 `python.evalExpression`）目前**不在 catalog 里**，因此不参与检查；
  将来若登记为命令，需要给本文件加「按 handler 函数切片」的第三条策略。

覆盖范围：**四类通道全部**——`browser.*`（Playwright/扩展）、`desktop.*`（UIA）、
`desktop.win32.*`、`python.worker`（`data.*` / `workflow.sleep`），共 78 条命令。

> **历史更正**：M29 时期本文件只登记了 `"browser."` 一条前缀，其余通道被跳过，而 docstring 与
> BACKLOG 把跳过归因为「桌面/数据通道的分派不是 `command == "<id>"` 字面量形状」。
> **该归因是错的**：`desktop.py`、`desktop_win32.py`、`python_worker.py` 全都是字面量比较，
> 只是后者的载体名是 `invocation.command_id`。真实原因就是「当时没登记」。M30 S1 泛化载体名后
> 四类通道全部纳入，**跳过 0 条**。教训记在这里：门禁「跳过」的输出带着一个归因，
> 而归因错了会让人以为这里没法机器校验、于是继续靠人工复查。

运行：uv run python .harness/scripts/check_param_consumption.py
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMMANDS_DIR = ROOT / "commands"

# 覆盖范围：executor 前缀 → 实现文件（读取可能分布在多个文件里，按并集算「通用读取」）
EXECUTOR_FILES = {
    "browser.": ("src/rpa_core/executors/browser.py",),
    "desktop.uia": ("src/rpa_core/executors/desktop.py",),
    "desktop.win32": ("src/rpa_core/executors/desktop_win32.py",),
    "python.worker": ("src/rpa_core/workers/python_worker.py",),
}

# 命令变量名（裸名或属性名）——各通道的载体不同，见 docstring 的形状表
COMMAND_CARRIERS = {"command", "command_id"}
# 输入容器名（裸名或属性名）
INPUT_CARRIERS = {"inputs"}

# 显式登记的「已知缺口」：`命令 → (尚未消费的参数, 清账说明)`。
# **按参数登记，不按整条命令**——整条豁免会让「已登记命令上新冒出来的死参数」也被放过。
# 每条必须写清由哪个里程碑哪一片清掉；M30 收口的判据就是这个台账清零。
#
# 台账**会自我收紧**：某个登记参数一旦被真的消费（或被从 manifest 删掉），条目就过期，
# 门禁会报「台账过期」并要求删除——所以它只会变短，不会变成长期豁免名单。
#
# M30 S4 清空最后一条（`desktop.attachWindow` 的 `className`）：
# uia 侧 `_find_windows_by_title` 现已接 `class_name` 参数（exact 走 FindWindowW 的
# lpClassName + GetClassNameW 复核，contains/regex 走枚举回调里的等值比较），
# 与 win32 侧 `_filter_windows` + className 过滤口径对齐。
KNOWN_GAPS: dict[str, tuple[tuple[str, ...], str]] = {}


def _literal_strings(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        out: list[str] = []
        for element in node.elts:
            out.extend(_literal_strings(element))
        return out
    return []


def _is_carrier(node: ast.AST, names: set[str]) -> bool:
    """`command` / `invocation.command_id` 两种形状都算载体。"""
    if isinstance(node, ast.Name) and node.id in names:
        return True
    return isinstance(node, ast.Attribute) and node.attr in names


def _command_names(test: ast.AST) -> list[str]:
    """从 `command == "x"` / `invocation.command_id == "x"` / `... in (...)` 取字面量命令名。"""
    if not isinstance(test, ast.Compare) or not _is_carrier(test.left, COMMAND_CARRIERS):
        return []
    names: list[str] = []
    for op, comparator in zip(test.ops, test.comparators, strict=True):
        if isinstance(op, (ast.Eq, ast.In)):
            names.extend(_literal_strings(comparator))
    return names


def _inputs_reads(nodes) -> set[str]:
    """收集 `inputs.get("x")` / `inputs["x"]` / `invocation.inputs[...]` 的字面量键。"""
    reads: set[str] = set()
    for node in nodes:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
                if (
                    sub.func.attr == "get"
                    and _is_carrier(sub.func.value, INPUT_CARRIERS)
                    and sub.args
                ):
                    reads.update(_literal_strings(sub.args[0]))
            elif isinstance(sub, ast.Subscript) and _is_carrier(sub.value, INPUT_CARRIERS):
                reads.update(_literal_strings(sub.slice))
    return reads


def _analyse_module(path: Path) -> tuple[dict[str, set[str]], set[str], set[str]]:
    """返回 `(命令 → 该命令分支里的读取键, 通用读取键, 未实现命令)`。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    per_command: dict[str, set[str]] = {}
    inside_ids: set[int] = set()
    unimplemented: set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        names = _command_names(node.test)
        if not names:
            continue
        reads = _inputs_reads(node.body)
        # 「本期未实现」的命令：分支体里显式返回 COMMAND_NOT_FOUND
        if "COMMAND_NOT_FOUND" in ast.unparse(node):
            unimplemented.update(names)
        for name in names:
            per_command.setdefault(name, set()).update(reads)
        for statement in node.body:
            for sub in ast.walk(statement):
                inside_ids.add(id(sub))

    inside: set[str] = set()
    outside: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Call, ast.Subscript)):
            keys = _inputs_reads([node])
            if not keys:
                continue
            (inside if id(node) in inside_ids else outside).update(keys)
    return per_command, outside, unimplemented


def _executor_files(executor: str) -> tuple[Path, ...]:
    for prefix, relatives in EXECUTOR_FILES.items():
        if executor.startswith(prefix):
            return tuple(ROOT / relative for relative in relatives)
    return ()


def main() -> int:
    errors: list[str] = []
    checked = 0
    exempt = 0
    skipped: list[str] = []
    gap_params_total = 0
    stale_gaps: list[str] = []
    all_command_ids: set[str] = set()
    unimplemented_commands: set[str] = set()
    analysis: dict[str, tuple[dict[str, set[str]], set[str], set[str]]] = {}

    for directory in sorted(COMMANDS_DIR.iterdir()):
        if not directory.is_dir():
            continue
        for manifest_path in sorted(directory.glob("*.json")):
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            command_id = manifest.get("id") or manifest_path.stem
            all_command_ids.add(command_id)
            executor = str(manifest.get("executor") or "")
            files = _executor_files(executor)
            if not files:
                # 覆盖范围外 = 没登记实现文件映射（**不是在给「不适用」下结论**，见 docstring）
                skipped.append(f"{command_id}({executor})")
                continue

            declared = set((manifest.get("input_schema") or {}).get("properties") or {})
            if not declared:
                continue

            allowed: set[str] = set()
            for path in files:
                key = str(path)
                if key not in analysis:
                    analysis[key] = _analyse_module(path)
                per_command, generic, unimplemented = analysis[key]
                allowed |= per_command.get(command_id, set()) | generic
                unimplemented_commands |= unimplemented

            if command_id in unimplemented_commands:
                exempt += 1
                continue

            # 台账只豁免**逐个登记的参数**，该命令的其余参数照查
            gap_params, gap_reason = KNOWN_GAPS.get(command_id, ((), ""))
            gap_params_total += len(gap_params)
            obsolete = sorted(set(gap_params) - declared)
            if obsolete:
                stale_gaps.append(
                    f"{command_id}: 台账登记的 {', '.join(obsolete)} 已不在 manifest 中，"
                    f"请从台账删掉这几个参数名（{gap_reason}）"
                )

            missing = sorted(declared - allowed - set(gap_params))
            checked += 1
            if missing:
                errors.append(
                    f"{command_id}: 声明了但实现侧从未读取 → {', '.join(missing)}"
                    "（实现它，或从 manifest 删掉；参考 docs/element-mvp-boundaries.md §3）"
                )
            # 台账过期判据：登记的参数实现侧已经读到了 → 该条目该删了
            consumed_gaps = sorted(set(gap_params) & allowed)
            if consumed_gaps:
                stale_gaps.append(
                    f"{command_id}: 台账里登记的 {', '.join(consumed_gaps)} 已被实现消费，"
                    f"请删掉该条目（{gap_reason}）"
                )

    for command_id in KNOWN_GAPS:
        if command_id not in all_command_ids:
            stale_gaps.append(f"KNOWN_GAPS 里的 {command_id} 已不存在，请删掉该条目")

    errors.extend(stale_gaps)

    if errors:
        print("PARAM CONSUMPTION CHECK FAILED")
        print("\n".join(f"- {item}" for item in errors))
        return 1

    total = checked + exempt + len(skipped)
    print(
        f"PARAM CONSUMPTION CHECK PASSED "
        f"({checked} checked / {exempt} exempt / {len(skipped)} skipped = {total} 条命令；"
        f"豁免明细：未实现 {len(unimplemented_commands)} 条整表 + 台账 {len(KNOWN_GAPS)} 条命令"
        f"共 {gap_params_total} 个参数)"
    )
    if unimplemented_commands:
        names = ", ".join(sorted(unimplemented_commands))
        print(f"  未实现命令（整表豁免，实现后自动纳入）：{names}")
    for command_id, (gap_params, reason) in sorted(KNOWN_GAPS.items()):
        print(f"  台账 {command_id}：{'/'.join(gap_params)} —— {reason}")
    if skipped:
        # 覆盖范围外 = 没有登记实现文件映射。**不要**再给它编归因：
        # M29 就是这么把「当时没登记」写成了「分派不是字面量形状」，误导了一轮。
        skip_names = ", ".join(skipped)
        print(f"  跳过 {len(skipped)} 条（无实现文件映射，需在 EXECUTOR_FILES 登记）：{skip_names}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
