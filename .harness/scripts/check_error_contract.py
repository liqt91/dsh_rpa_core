"""门禁：manifest 声明的 `errors` 必须覆盖实现会真正返回的错误码（防「错误码未声明」漂移）。

背景（M30 S3 挖出来的）：`errors` 是调用方判断「这一步可能出什么错」的唯一声明面——
GUI 的错误面板、重试策略、上层工作流的分支都读它。但它是**手写的**，此前没有任何门禁盯着，
于是必然落后于实现：`desktop.click` / `desktop.win32.click` 在 S3 引入 `INVALID_INPUT`
（参数互斥走显式失败）、`desktop.win32.menuSelect` 早就对空 `menuPath` 返回 `INVALID_INPUT`，
三份 manifest 一个都没声明。声明面少一个码，上层就没法为它设计分支——这与 M29/M30 一路在收的
「声明了没用 / 用了没声明」是同一种病，只是长在另一根轴上。

判定口径与 `check_param_consumption.py` 完全对称（复用它的命令切片基础设施）：

1. 对每条命令，取其 executor 对应的实现文件；
2. 按 `command == "<id>"` / `invocation.command_id == "<id>"` / `... in (...)` 的字面量切片，
   收集该命令分支里出现的 `ErrorCode.X`；
3. 要求 `X ∈ manifest["errors"]`，否则报错；
4. **注册表分派**（`_X_HANDLERS = {"<id>": <handler>}`）的命令（`data.table.*` 6 条）：按
   handler 函数体 + dispatcher 函数体切 `ErrorCode.X`。切得出就正常查；**切不出就单列一桶
   `unsliced`、不计入 `checked`** —— 记成 `checked` 而覆盖为零就是「假判据」，和 M63 S1
   修掉参数门禁那个假绿是同一种病。

   **为何不把 dispatcher 的码归给这些命令**（M63 S1 实测）：`_execute_table_command` 的
   `ErrorCode.*` 是**框架级**的（缺 `flowDir` / `KeyError` / 统一异常映射），没有「哪条命令
   会返回到它」的语义。硬归过去 ⇒ 5 条命令要被迫声明**实际不可达**的
   `COMMAND_NOT_FOUND` ⇒ 假红；而假红最终会被人用「加豁免」消掉，豁免一加，真漂移也就
   跟着漏过去了。宁可诚实报 `unsliced 6`。

5. **接线哨兵**（M63 S7 补，用于关闭 M60 登记、M63 拍板修掉的「第四条同族漏检」）：
   每条**非 `skipped`、非豁免、非 `unsliced`** 的命令，必须在实现里**可定位到一个分派点**
   —— `_command_names` 命中的字面量分支，或 `_command_tables` 从模块级命令表
   （`{"<id>": "<JS 方法名>"}`、`("browser.attach", …)` 这类**值不是函数**的表）里收出的 id。
   判不出即 `unwired` 报错。

   ★ **为什么不能退化成「`returned` 非空」**（这是登记时的候选，实测当场证否）：
   80 条 `checked` 里 **53 条的 `returned` 本来就是空的** —— `browser.*` / `desktop.*` 的错误码
   大多由**不带命令字面量的辅助函数**返回（正是下面第一条盲区的形状）。空集 ≠ 没接线：
   硬加会一次造出 **53 条假红**，而假红最终会被人用「加豁免」消掉，真漂移也就跟着漏过去。
   `per_command` 的**键**（分派点被定位到）与它的**值**（切出的码）是两件事，哨兵用的是前者；
   实测口径与假红规模见 `.harness/spike/probe_error_vacuity.py`。
6. `unsliced` 桶**必须与声明台账逐条相符（双向）**：桶里出现未登记的 ⇒ 某条命令**悄悄**
   失去了错误契约检查；登记了但已不在桶里 ⇒ 台账过期。与参数门禁的 `KNOWN_GAPS` 同构，
   所以它只会变短、不会长成长期豁免名单。

只做**单向**要求（实现了但没声明 = 错）。**不反向**要求「声明的每个码都被返回过」：
防御性声明、跨后端兼容的声明都是合理的，反过来卡会逼着人删掉真话。

豁免与参数门禁同一套：分支体里显式返回 `COMMAND_NOT_FOUND` 的「本期未实现」命令整体跳过，
它们的整张契约都还是待实现清单，不是漂移。

已知盲区（如实写在这里，不假装是形式化证明）：

- 错误码由**不带命令字面量的辅助函数**返回时（例如某个命令分支调用的 `_ensure_*`），
  会被算进「无命令归属」而看不见——所以要么把返回点搬进 `if command == ...` 分支，
  要么在 `check_param_consumption.py` 的 docstring 盲区清单里一并登记。
  （这也是判定口径第 5 条**不能**用「`returned` 非空」当哨兵的原因。）
- **单向 = 单向的负向验证**。本门禁只查「实现了但没声明」，因此它的负向验证必须
  打在同一个方向上（往分支里塞一个未声明的码 → 必须报红）。反过来「删掉某个已声明的
  返回点」**不会**报错，那是**设计如此**：声明比实现多是合理的（防御性声明、跨后端兼容），
  反向卡会逼着人删掉真话。M30 S4 曾误用后一种方式做负向验证，得到一个「删了也不红」的
  假结论——记在这里，免得下一个人重复踩。
- **「分派分支被整个拿掉 ⇒ 空过」已由接线哨兵关闭**（M63 S7）。它关掉的是**命令消失**
  （实现不再认领这个 id），**不是**「分支体内的某个返回点被删」——后者仍按上一条放过。
  两者的区别就是「键在不在」与「值空不空」，别在下一轮又混起来。

运行：uv run python .harness/scripts/check_error_contract.py
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

# 复用参数门禁的命令切片基础设施（同一套载体名/字面量口径），避免两份门禁各自漂移。
sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_param_consumption import (  # noqa: E402
    COMMANDS_DIR,
    _command_names,
    _command_tables,
    _executor_files,
    _registry_members,
)

# `unsliced` 的**声明台账**（判定口径第 6 条）。这些命令的 handler 函数体里没有 `ErrorCode.X`
# ——错误码由外层 `_execute_table_command` 的框架级映射统一给出——所以它们的错误契约
# 「不可由静态切片证明」。对这种情形，诚实的表述只有「没查」；把它记成 `checked` 就是假判据。
#
# 为什么非要用台账而不是任其自由增长：`unsliced` 是**静默的出口**，任何命令落进来就再也不受
# 错误契约检查，而门禁照样 PASS。写成台账后两个方向都会红：
#   ① 桶里出现未登记的 ⇒ 某条命令**悄悄**失去了检查（有人把它搬进注册表、或新注册一条）；
#   ② 登记了却不在桶里 ⇒ 台账过期（handler 体真的开始返回码了，它已被自动纳入受检）⇒ 删条目。
# 与 `check_param_consumption.KNOWN_GAPS` 同构：**只会变短**，不会长成长期豁免名单。
UNSLICED_DECLARED: dict[str, str] = {
    "data.table.appendRow": "handler 体无 ErrorCode.X（码由 _execute_table_command 统一映射）",
    "data.table.clear": "同上",
    "data.table.deleteRow": "同上",
    "data.table.exportCsv": "同上",
    "data.table.getCell": "同上",
    "data.table.setCell": "同上",
}


def _error_codes(nodes) -> set[str]:
    """收集一组语句里出现的 `ErrorCode.X` 的 `X`。"""
    codes: set[str] = set()
    for node in nodes:
        for sub in ast.walk(node):
            if (
                isinstance(sub, ast.Attribute)
                and isinstance(sub.value, ast.Name)
                and sub.value.id == "ErrorCode"
            ):
                codes.add(sub.attr)
    return codes


def _analyse_module(path: Path) -> tuple[dict[str, set[str]], set[str], set[str], set[str]]:
    """返回 `(命令 → 分支里返回的错误码, 未实现命令, 注册表分派但切不出码的命令, 命令表 id)`。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    per_command: dict[str, set[str]] = {}
    unimplemented: set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        names = _command_names(node.test)
        if not names:
            continue
        # 只看分支体（不连 `orelse`），理由与负向验证见
        # `check_param_consumption._analyse_module` 的同款注释。
        if "COMMAND_NOT_FOUND" in "\n".join(ast.unparse(statement) for statement in node.body):
            unimplemented.update(names)
        codes = _error_codes(node.body)
        for name in names:
            per_command.setdefault(name, set()).update(codes)

    # 第三条切片策略（注册表分派），与参数门禁**共用**同一套发现逻辑以免两处口径漂移。
    # ★ 只切 handler **函数体**，**不切 dispatcher** —— 这不是省事，是实测结论：
    # dispatcher（`_execute_table_command`）的 `ErrorCode.*` 是框架级的，归过来的话 5 条
    # 命令会立刻被要求声明实际不可达的 `COMMAND_NOT_FOUND` ⇒ 假红（M63 S1 先写错、被本门禁
    # 当场抓出，见 `.harness/spike/probe_m63_registry_negative.py` 的 N4）。
    unsliced: set[str] = set()
    for command_id, (handler, _dispatcher) in _registry_members(tree).items():
        codes = _error_codes(handler.body)
        if codes:
            per_command.setdefault(command_id, set()).update(codes)
        else:
            # handler 体里没有 `ErrorCode.X` ⇒ 这条命令的错误契约**不可由静态切片证明**。
            # 单列 `unsliced`、不计 `checked`：宁可如实报「没查」，也不能报「查过了、没问题」。
            unsliced.add(command_id)
    # 接线证据（判定口径第 5 条）：模块级命令表里的 id。与 `per_command` 的键一起供 main() 用。
    return per_command, unimplemented, unsliced, _command_tables(tree)


def main() -> int:
    errors: list[str] = []
    checked = 0
    exempt = 0
    skipped: list[str] = []
    unsliced: list[str] = []
    unwired: list[str] = []
    analysis: dict[str, tuple[dict[str, set[str]], set[str], set[str], set[str]]] = {}
    unimplemented_commands: set[str] = set()
    unsliced_commands: set[str] = set()

    for directory in sorted(COMMANDS_DIR.iterdir()):
        if not directory.is_dir():
            continue
        # ★ `rglob`：`commands/data/table/` 是第二层，一层 `glob` 会让那 6 条命令压根不进目录。
        for manifest_path in sorted(directory.rglob("*.json")):
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            command_id = manifest.get("id") or manifest_path.stem
            executor = str(manifest.get("executor") or "")
            files = _executor_files(executor)
            if not files:
                # 覆盖范围外 = 没登记实现文件映射（**不是在给「不适用」下结论**）
                skipped.append(f"{command_id}({executor})")
                continue

            declared = set(manifest.get("errors") or [])
            returned: set[str] = set()
            # 接线证据：`if command == ...` 分支的**键**（切出的码为空也算）或模块级命令表的 id。
            # 注意与 `returned` 的区别——`returned` 空是**常态**（错误码走辅助函数），
            # 而「两个证据都没有」才说明实现根本不认识这个 id。
            wired = False
            for path in files:
                key = str(path)
                if key not in analysis:
                    analysis[key] = _analyse_module(path)
                per_command, unimplemented, module_unsliced, table_ids = analysis[key]
                returned |= per_command.get(command_id, set())
                if command_id in per_command or command_id in table_ids:
                    wired = True
                unimplemented_commands |= unimplemented
                unsliced_commands |= module_unsliced

            if command_id in unimplemented_commands:
                exempt += 1
                continue

            if not wired:
                # 判定口径第 5 条：实现里找不到这个 id ⇒ 命令从分派器里消失了（或压根没接线）。
                # 这是错误契约这一轴上唯一能看见「没接线」的位置（M60 登记、M63 修掉的漏检）。
                unwired.append(command_id)
                continue

            if command_id in unsliced_commands:
                # 注册表分派、切不出按命令归属的返回码 ⇒ **不计 checked**（见 docstring 第 4 条）。
                unsliced.append(command_id)
                continue

            checked += 1
            missing = sorted(returned - declared)
            if missing:
                errors.append(
                    f"{command_id}: 实现会返回但 manifest.errors 未声明 → {', '.join(missing)}"
                    "（补进 errors，或改掉实现里那个返回点）"
                )

    # 判定口径第 6 条：`unsliced` 桶与声明台账**双向**相符。
    undeclared = sorted(set(unsliced) - set(UNSLICED_DECLARED))
    stale = sorted(set(UNSLICED_DECLARED) - set(unsliced))

    if errors or unwired or undeclared or stale:
        print("ERROR CONTRACT CHECK FAILED")
        for item in errors:
            print(f"- {item}")
        if unwired:
            print(
                "- 在实现里找不到分派点（命令从分派器里消失，或压根没接线）："
                f"{', '.join(sorted(unwired))}"
                "（查实现在哪条分支/哪张表里认领这个 id；"
                "确属未实现就按 COMMAND_NOT_FOUND 显式登记）"
            )
        if undeclared:
            print(
                "- unsliced 出现**未声明**的命令（它们不再受错误契约检查）："
                f"{', '.join(undeclared)}"
                " ⇒ 要么补进 UNSLICED_DECLARED 并写明理由，要么把 handler 改成可切片的形状"
            )
        if stale:
            print(
                "- unsliced 声明台账**过期**（这些命令已不在桶里 ⇒ handler 体已能切片）："
                f"{', '.join(stale)} ⇒ 删掉台账条目"
            )
        return 1

    total = checked + exempt + len(skipped) + len(unsliced)
    print(
        f"ERROR CONTRACT CHECK PASSED "
        f"({checked} checked / {len(unsliced)} unsliced / {exempt} exempt / "
        f"{len(skipped)} skipped = {total} 条命令；"
        f"豁免明细：未实现 {len(unimplemented_commands)} 条整表)"
    )
    if unsliced:
        names = ", ".join(sorted(unsliced))
        print(f"  unsliced {len(unsliced)} 条（注册表分派 handler 体切不出 ErrorCode，**未检查**）")
        print(f"    {names}")
    if unimplemented_commands:
        names = ", ".join(sorted(unimplemented_commands))
        print(f"  未实现命令（整体豁免，实现后自动纳入）：{names}")
    if skipped:
        # 覆盖范围外 = 没有登记实现文件映射。**不要**给它编归因（M29 的教训）。
        skip_names = ", ".join(skipped)
        print(f"  跳过 {len(skipped)} 条（无实现文件映射，需在 EXECUTOR_FILES 登记）：{skip_names}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
