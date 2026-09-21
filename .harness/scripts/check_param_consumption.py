"""门禁：manifest 声明的输入参数必须被实现消费（防「声明了不生效」回归）。

背景：`input_schema` 是命令契约的唯一事实来源——参数声明了，GUI 就会渲染成开关，
用户就会按说明去用。M28 S3 修掉 `browser.input` 的两个参数后，S4 用「参数名是否在实现里
出现过」的启发式复查，又发现 4 处漂移；M29 换成本文件的口径（**按命令切片**）后，
再多挖出一处（`cookieGetAll` 的 `name`/`domain`/`path` 一个都没转发）。

判定口径：

1. 对每条命令，取其 executor 对应的实现文件；
2. AST 解析实现，把 `inputs.get("x")` / `inputs["x"]` 的读取归到**所在分支比较的字面量命令**上
   （`if command == "browser.click":`、`if command in ("a", "b"):`）；
   不在任何命令分支里的读取算「通用读取」（如 `_post_delay` 的 `postDelayMs`、
   `_match_ext_tab` 的 `pattern`/`matchBy`——它们由多个命令共享）；
3. 声明的参数必须落在「该命令分支的读取 ∪ 通用读取」内，否则报错；
4. 执行器里显式声明「扩展通道本期未实现」（分支体返回 `COMMAND_NOT_FOUND`）的命令整体豁免：
   它们的整张参数表都是待实现清单，不是漂移。**实现后自动纳入检查**，不需要改本文件。

已知盲区（如实写在这里，不假装是形式化证明）：

- 静态扫描只认字面量键。实现里按变量取（`inputs[key]`、循环一个 tuple）时看不见——
  所以实现侧要按字面量取值（见 `browser.cookieGetAll` 的过滤器写法）；
- 读取发生在**不带命令字面量的辅助函数**里时，会算进「通用读取」而让所有命令都通过
  （例如 `browser.waitFor` 的 `state` 读在 `_ext_wait_for(inputs, ...)` 里）。收窄办法是
  把这类读取搬进 `if command == ...` 分支，或在此显式登记。

覆盖范围：**扩展通道（executor 以 `browser.` 开头）**——本阶段所有已知漂移都在这里。
桌面通道（`desktop.*`）与 `python.worker` 命令的分派不是 `command == "<id>"` 字面量形状
（走注册表/helper），静态切片不适用，故**跳过并如实打印条数**，其待核对清单见
`.harness/tasks/BACKLOG.md`「桌面/数据通道参数漂移复核」。

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
}

# 显式登记的「已知缺口」：声明了但实现侧尚未消费，且**不在本次收口范围**内。
# 每条都要写清理由与去处；实现后请删除对应条目（门禁会自然把它纳入检查）。
KNOWN_GAPS: dict[str, str] = {}


def _literal_strings(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        out: list[str] = []
        for element in node.elts:
            out.extend(_literal_strings(element))
        return out
    return []


def _command_names(test: ast.AST) -> list[str]:
    """从 `command == "x"` / `command in ("x", "y")` 取出字面量命令名。"""
    if not isinstance(test, ast.Compare) or not isinstance(test.left, ast.Name):
        return []
    if test.left.id != "command":
        return []
    names: list[str] = []
    for op, comparator in zip(test.ops, test.comparators, strict=True):
        if isinstance(op, (ast.Eq, ast.In)):
            names.extend(_literal_strings(comparator))
    return names


def _inputs_reads(nodes) -> set[str]:
    """收集 `inputs.get("x")` / `inputs["x"]` 的字面量键。

    返回 `(键, 该读取节点 id)` 两份信息——节点 id 用于区分「在命令分支里」与「通用读取」。
    """
    reads: set[str] = set()
    for node in nodes:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
                owner = sub.func.value
                if (
                    sub.func.attr == "get"
                    and isinstance(owner, ast.Name)
                    and owner.id == "inputs"
                    and sub.args
                ):
                    reads.update(_literal_strings(sub.args[0]))
            elif (
                isinstance(sub, ast.Subscript)
                and isinstance(sub.value, ast.Name)
                and sub.value.id == "inputs"
            ):
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
                # 覆盖范围外的通道：分派形状不是 command 字面量，静态切片不适用（见模块 docstring）
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

            if command_id in unimplemented_commands or command_id in KNOWN_GAPS:
                exempt += 1
                continue

            missing = sorted(declared - allowed)
            checked += 1
            if missing:
                errors.append(
                    f"{command_id}: 声明了但实现侧从未读取 → {', '.join(missing)}"
                    "（实现它，或从 manifest 删掉；参考 docs/element-mvp-boundaries.md §3）"
                )

    for command_id in KNOWN_GAPS:
        if command_id not in all_command_ids:
            errors.append(f"KNOWN_GAPS 里的 {command_id} 已不存在，请删掉该条目")

    if errors:
        print("PARAM CONSUMPTION CHECK FAILED")
        print("\n".join(f"- {item}" for item in errors))
        return 1

    print(
        f"PARAM CONSUMPTION CHECK PASSED "
        f"({checked} commands checked, {exempt} exempt "
        f"[未实现 {len(unimplemented_commands)} + 已登记缺口 {len(KNOWN_GAPS)}])"
    )
    if unimplemented_commands:
        names = ", ".join(sorted(unimplemented_commands))
        print(f"  未实现命令（整表豁免，实现后自动纳入）：{names}")
    for command_id, reason in sorted(KNOWN_GAPS.items()):
        print(f"  已登记缺口 {command_id}：{reason}")
    if skipped:
        print(f"  跳过 {len(skipped)} 条覆盖范围外命令（桌面/数据通道，见 BACKLOG 待复核清单）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
