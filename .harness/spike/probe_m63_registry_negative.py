"""M63 S1 负向验证：注册表分派（第三条切片策略）—— 门禁必须真红，且逐字节还原。

被验证的判据（都打在 M63 S1 新加/新改的代码上）：

- **N1** 参数门禁的第三条策略（`_registry_reads`）：handler **函数体**的读取真的参与判据
  ⇒ 往 `appendRow.json` 注入一个谁也没读的参数，必须红。
- **N2 ★ 复发点** 跨命令掩蔽必须已修：`varName` **只有** `_table_get_cell` 的 handler 读，
  把它加进 `appendRow` 的 manifest ⇒ **必须红**。M63 S1 之前这一向是 **PASSED（假绿）**——
  因为 handler 体的读取全落进「通用读取」而被所有命令共享。这一向正是回退 `rglob` 的根据。
- **N3** 注册表解析**不许静默失效**：把注册表变量改名 ⇒ dispatcher 认不出来 ⇒ 必须**硬失败**。
  M63 S1 第一版是**静默降级**（dispatcher 的 `flowDir`/`table` 退回「通用读取」，门禁照样绿），
  被这一向当场抓出——所以它现在是 fail-loud 判据，而不是靠「恰好公共参数相同」侥幸。
- **N4 ★** 错误门禁**不许**把 dispatcher 的框架级错误码归给命令：把那行改回「并上 dispatcher 码」
  ⇒ 必须红（5 条假红）。这一向证明「只切 handler 体」是**必要的判据设计**，不是省事
  （M63 S1 先写错的就是这一版，被门禁当场抓出）。
- **N5** `unsliced` **不是永久豁免**：往 `_table_clear` 的 handler 体塞一个 `ErrorCode.X`
  ⇒ 它自动从 `unsliced` 转为受检，且该码未声明 ⇒ 红。

三自保（都踩过坑）：① 开头哨兵自检，发现哨兵即**硬失败**；② 注入包 `try/finally`；
③ 还原后核 **md5 逐字节相等**（基于原始 `read_bytes`，不经 universal-newline 转换）。

用法：`.venv/Scripts/python.exe .harness/spike/probe_m63_registry_negative.py`
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
SENTINEL = "__m63negver__"

PARAM_GATE = ROOT / ".harness" / "scripts" / "check_param_consumption.py"
ERR_GATE = ROOT / ".harness" / "scripts" / "check_error_contract.py"
APPEND_ROW = ROOT / "commands" / "data" / "table" / "appendRow.json"
PYTHON_WORKER = ROOT / "src" / "rpa_core" / "workers" / "python_worker.py"


def _json_add_prop(prop: str) -> Callable[[str], str]:
    """往 manifest 的 `input_schema.properties` 加一个参数（**合法 JSON 的精准放宽**）。"""

    def mutate(text: str) -> str:
        data = json.loads(text)
        properties = data.setdefault("input_schema", {}).setdefault("properties", {})
        properties[prop] = {"type": "string", "description": SENTINEL}
        return json.dumps(data, ensure_ascii=False, indent=2) + "\n"

    return mutate


def _text_replace(old: str, new: str) -> Callable[[str], str]:
    """文本注入；`old` 必须**恰好命中 1 次**（锚点唯一性自检）。"""

    def mutate(text: str) -> str:
        count = text.count(old)
        if count != 1:
            raise ValueError(f"锚点命中 {count} 次（须恰好 1）：{old[:60]!r}")
        return text.replace(old, new)

    return mutate


CASES: list[tuple[str, Path, Callable[[str], str], Path, str]] = [
    (
        "N1 param·第三条策略真的在判据里（注入无人读的参数）",
        APPEND_ROW,
        _json_add_prop(SENTINEL),
        PARAM_GATE,
        f"data.table.appendRow: 声明了但实现侧从未读取 → {SENTINEL}",
    ),
    (
        "N2 ★跨命令掩蔽已修（getCell 专属的 varName 加进 appendRow）",
        APPEND_ROW,
        _json_add_prop("varName"),
        PARAM_GATE,
        "data.table.appendRow: 声明了但实现侧从未读取 → varName",
    ),
    (
        "N3 注册表改名 ⇒ 解析不许静默失效（fail-loud）",
        PYTHON_WORKER,
        _text_replace("_TABLE_HANDLERS = {", f"{SENTINEL}_TABLE_HANDLERS = {{"),
        PARAM_GATE,
        "找不到引用该注册表的 dispatcher 函数",
    ),
    (
        "N4 ★error 门禁不许并 dispatcher 码（并上即 5 条假红）",
        ERR_GATE,
        _text_replace(
            "        codes = _error_codes(handler.body)\n        if codes:",
            "        codes = _error_codes(handler.body)\n"
            "        codes |= set() if _dispatcher is None else _error_codes(_dispatcher.body)\n"
            "        if codes:",
        ),
        ERR_GATE,
        "manifest.errors 未声明 → CAPABILITY_DENIED, COMMAND_NOT_FOUND",
    ),
    (
        "N5 unsliced 不是永久豁免（handler 体出现 ErrorCode 即自动受检）",
        PYTHON_WORKER,
        _text_replace(
            "def _table_clear(invocation, flow_dir: str, table: str, inputs: dict)"
            " -> CommandResult:\n    obj = _table.load_table(flow_dir, table)",
            "def _table_clear(invocation, flow_dir: str, table: str, inputs: dict)"
            " -> CommandResult:\n"
            f'    return CommandResult.failure(ErrorCode.CAPABILITY_DENIED, "{SENTINEL}")\n'
            "    obj = _table.load_table(flow_dir, table)",
        ),
        ERR_GATE,
        "data.table.clear: 实现会返回但 manifest.errors 未声明 → CAPABILITY_DENIED",
    ),
]

GATES = (PARAM_GATE, ERR_GATE)
MANAGED = sorted({case[1] for case in CASES} | set(GATES))


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def run(gate: Path) -> tuple[int, str]:
    proc = subprocess.run([str(PY), str(gate)], capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


def main() -> int:
    # ① 哨兵自检 + 前置条件
    for path in MANAGED:
        if not path.is_file():
            print(f"REFUSING: 受管文件不存在：{path}")
            return 2
        if SENTINEL in path.read_text(encoding="utf-8"):
            print(f"REFUSING: 受管文件已含哨兵（上次未还原？）：{path}")
            return 2

    for gate in GATES:
        rc, out = run(gate)
        if rc != 0:
            print(f"REFUSING: 基线不绿（{gate.name} rc={rc}）—— 先修基线再做负向验证")
            print(out[-600:])
            return 2
    print("基线绿：check_param_consumption + check_error_contract 均 rc=0\n")

    results: list[tuple[str, bool, int, str, str]] = []
    for name, path, mutate, gate, expect in CASES:
        original_bytes = path.read_bytes()
        before = md5(path)
        try:
            try:
                mutated = mutate(original_bytes.decode("utf-8"))
            except ValueError as exc:
                print(f"REFUSING: {name} —— {exc}")
                return 2
            if mutated.encode("utf-8") == original_bytes:
                print(f"REFUSING: {name} —— 注入没有改变文件")
                return 2
            path.write_text(mutated, encoding="utf-8", newline="\n")
            rc, out = run(gate)
            lines = [line for line in out.strip().splitlines() if line.strip()]
            hit = rc != 0 and expect in out
            results.append((name, hit, rc, expect, lines[-1] if lines else ""))
        finally:
            path.write_bytes(original_bytes)
        after = md5(path)
        if before != after:
            print(f"REFUSING: {path.name} 未逐字节还原（{before} != {after}）")
            return 2

    print()
    all_hit = True
    for name, hit, rc, expect, last in results:
        print(f"{'HIT ' if hit else 'MISS'}  {name}   (rc={rc}，期望含 {expect!r})")
        if not hit:
            all_hit = False
            print(f"       末行：{last}")
    print()
    restored = all(run(gate)[0] == 0 for gate in GATES)
    print(f"还原后复绿：{restored}")
    verdict = "PASS（全中 + 逐字节还原 + 复绿）" if all_hit and restored else "FAIL"
    print(f"RESULT: {verdict}")
    return 0 if all_hit and restored else 1


if __name__ == "__main__":
    raise SystemExit(main())
