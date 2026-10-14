"""只读探针：把 89 条命令按错误门禁的现行口径**逐条摊开**，看哪些命令的 `returned` 是空的。

动机（BACKLOG 登记的「第四条同族漏检」）：`check_error_contract.py` 的判据是单向包含
`returned ⊆ declared`，所以「命令的分派分支整个拿掉 ⇒ `returned = ∅` ⇒ `missing = ∅` ⇒ PASS」。
候选加固是「要求非豁免命令 `returned` 非空」，但**加之前必须先查清现状**：
若现在就有命令合法地 `returned = ∅`，硬加就会立刻造出一批假红。

本探针只读、不改任何文件。**随仓提交**（`.harness/spike/probe_*.py` 是只读证据件；
`_` 前缀的那些才是本机 scratch）——因为「登记时的候选被实测证否」这件事，
比结论本身更值得留给下一个人：否则同样的 `returned 非空` 会被人再想一次、再试一次。
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".harness" / "scripts"))

import check_error_contract as ec  # noqa: E402
from check_param_consumption import COMMANDS_DIR, _command_tables, _executor_files  # noqa: E402


def _module_dict_keys(tree) -> set[str]:
    """模块级 dict 字面量的**字符串键**（含值不是函数的那些，如 `_EXT_PAGE_METHODS`）。"""
    keys: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            for key in node.value.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    keys.add(key.value)
    return keys


def main() -> int:
    analysis: dict[str, tuple[dict[str, set[str]], set[str], set[str], set[str]]] = {}
    dict_keys_cache: dict[str, set[str]] = {}
    table_ids_cache: dict[str, set[str]] = {}
    unimplemented_all: set[str] = set()
    unsliced_all: set[str] = set()
    # (命令, executor, 实现文件数, 声明码, 切出的码, 有字面量分派点, 在命令表里, 在松口径里)
    rows: list[tuple[str, str, int, set[str], set[str], bool, bool, bool]] = []

    for directory in sorted(COMMANDS_DIR.iterdir()):
        if not directory.is_dir():
            continue
        for manifest_path in sorted(directory.rglob("*.json")):
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            command_id = manifest.get("id") or manifest_path.stem
            executor = str(manifest.get("executor") or "")
            files = _executor_files(executor)
            declared = set(manifest.get("errors") or [])
            returned: set[str] = set()
            located = False
            in_table = False
            in_raw_dict = False
            for path in files:
                key = str(path)
                if key not in analysis:
                    analysis[key] = ec._analyse_module(path)
                    _tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                    # 门禁的**实际**口径（被命令载体消费过的容器）
                    table_ids_cache[key] = _command_tables(_tree)
                    # 更松的诊断口径（模块级 dict 的键，不问是否被消费）——只用来对照
                    dict_keys_cache[key] = _module_dict_keys(_tree)
                per_command, unimplemented, module_unsliced = analysis[key][:3]
                returned |= per_command.get(command_id, set())
                # ★ 与「returned 非空」是**两回事**：`per_command` 的**键**是「分派点被定位到」，
                #   值可能是空集（分支体里没有 ErrorCode.X，错误码由辅助函数返回）。
                located = located or command_id in per_command
                in_table = in_table or command_id in table_ids_cache[key]
                in_raw_dict = in_raw_dict or command_id in dict_keys_cache[key]
                unimplemented_all |= unimplemented
                unsliced_all |= module_unsliced
            rows.append(
                (command_id, executor, len(files), declared, returned, located, in_table, in_raw_dict)
            )

    checked: list[tuple[str, str, set[str], set[str], bool, bool, bool]] = []
    exempt: list[str] = []
    unsliced: list[str] = []
    skipped: list[str] = []
    for command_id, executor, nfiles, declared, returned, located, in_table, in_raw_dict in rows:
        if not nfiles:
            skipped.append(f"{command_id}({executor})")
        elif command_id in unimplemented_all:
            exempt.append(command_id)
        elif command_id in unsliced_all:
            unsliced.append(command_id)
        else:
            checked.append((command_id, executor, declared, returned, located, in_table, in_raw_dict))

    vacuous = [item for item in checked if not item[3]]
    unlocated = [item for item in checked if not item[4]]
    only_table = [item for item in checked if not item[4] and item[5]]
    only_raw_dict = [item for item in checked if not item[4] and not item[5] and item[6]]

    print(f"总命令 {len(rows)} 条 = checked {len(checked)} + unsliced {len(unsliced)} "
          f"+ exempt {len(exempt)} + skipped {len(skipped)}")
    print()
    print(f"① checked 里 returned == ∅ 的：{len(vacuous)} 条")
    print("   → 候选「要求 returned 非空」的假红规模；空集≠没接线（错误码走辅助函数）")
    print()
    print(f"② checked 里没有字面量分派点（`if command ==` / 注册表）的：{len(unlocated)} 条")
    for command_id, executor, _declared, returned, _located, in_table, in_raw_dict in unlocated:
        print(f"    {command_id:<34} executor={executor:<18} returned={sorted(returned)} "
              f"命令表口径={in_table} 松口径={in_raw_dict}")
    print()
    print(f"③ 其中「靠命令表（被载体消费过的容器）才够定位」的：{len(only_table)} 条")
    print("   → 这就是接线哨兵放进 `_command_tables` 之后，② 的假红规模")
    for command_id, *_rest in only_table:
        print(f"    {command_id}")
    print(f"④ 只在更松口径（模块级 dict 键，不问是否被消费）下才定位的：{len(only_raw_dict)} 条")
    print("   → 应为 0：若不为 0，说明门禁口径与「随便一个 dict 的键」不是一回事（那是有意的）")
    for command_id, *_rest in only_raw_dict:
        print(f"    {command_id}")
    print()
    print("unsliced:", ", ".join(sorted(unsliced)) or "(无)")
    print("exempt  :", ", ".join(sorted(exempt)) or "(无)")
    print("skipped :", ", ".join(sorted(skipped)) or "(无)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
