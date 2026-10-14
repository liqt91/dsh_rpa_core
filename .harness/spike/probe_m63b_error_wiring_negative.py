"""M63 S7 负向验证：错误门禁的**接线哨兵**与 `unsliced` 豁免台账必须真红，且逐字节还原。

被验证的判据（M63 S7 新加）：

- **N1 ★** 接线哨兵（判定口径第 5 条）：把 `ocr_worker.py` 里 `if command_id == "ocr.detect":`
  的字面量改成别的名字 ⇒ 实现**不再认领**这个 id ⇒ 必须红。
  **这就是 M60 登记、M63 拍板修掉的「第四条同族漏检」的原始复现**（旧判据 `returned ⊆ declared`
  在这一向下 `returned = ∅` ⇒ `missing = ∅` ⇒ **PASS 空过**）。
- **N2** 注册表条目被删（`_TABLE_HANDLERS` 少了 `data.table.getCell`）⇒ 不再进 `unsliced`、
  也没有字面量分支 ⇒ 必须红（同时触发哨兵与台账过期两条）。
- **N3** `unsliced` 台账**自我收紧**（方向②）：往 `_table_clear` 的 handler 体塞一个**已声明**的码
  （`INVALID_INPUT`）⇒ 该命令自动由 `unsliced` 转受检 ⇒ 台账里那条**过期** ⇒ 必须红。
  ★ 特意挑一个**已声明**的码：这样「实现了但没声明」那条判据**不会**顺带报错，
  红色只能来自台账 —— 否则就无法区分是哪条判据打中的。
- **N4 ★ 元验证（对判据本身的验证）**：把新判据关掉（`if not wired:` → `if False and not wired:`）
  后**重做 N1 的注入** ⇒ 门禁必须**变绿**。这证明 N1 的红确实由新判据产生，
  而不是某条既有判据凑巧抓到 —— 「报了绿也要负向验证」的下一步就是「红也要证明是谁报的」。
- **N5** 台账方向①：从 `UNSLICED_DECLARED` 里删掉 `data.table.clear` ⇒ 桶里出现**未声明**
  的命令 ⇒ 必须红（否则「静默出口」照旧）。

三自保（都踩过坑）：① 开头哨兵自检，发现哨兵即**硬失败**；② 注入包 `try/finally`；
③ 还原后核 **md5 逐字节相等**（基于原始 `read_bytes`，不经 universal-newline 转换）。

用法：`.venv/Scripts/python.exe .harness/spike/probe_m63b_error_wiring_negative.py`
"""

from __future__ import annotations

import hashlib
import subprocess
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
SENTINEL = "__m63bnegver__"

ERR_GATE = ROOT / ".harness" / "scripts" / "check_error_contract.py"
OCR_WORKER = ROOT / "src" / "rpa_core" / "workers" / "ocr_worker.py"
PYTHON_WORKER = ROOT / "src" / "rpa_core" / "workers" / "python_worker.py"

UNWIRED_MSG = "在实现里找不到分派点"
STALE_MSG = "声明台账**过期**"
UNDECLARED_MSG = "出现**未声明**的命令"
MISSING_DECL_MSG = "manifest.errors 未声明"


def _text_replace(old: str, new: str) -> Callable[[str], str]:
    """文本注入；`old` 必须**恰好命中 1 次**（锚点唯一性自检）。"""

    def mutate(text: str) -> str:
        count = text.count(old)
        if count != 1:
            raise ValueError(f"锚点命中 {count} 次（须恰好 1）：{old[:60]!r}")
        return text.replace(old, new)

    return mutate


# (名称, 目标文件, 注入, 必须出现的期望片段, 必须**不**出现的片段)
CASES: list[tuple[str, Path, Callable[[str], str], tuple[str, ...], tuple[str, ...]]] = [
    (
        "N1 ★命令从分派器里消失（ocr.detect 字面量改名）⇒ 哨兵必须红",
        OCR_WORKER,
        _text_replace('if command_id == "ocr.detect":', 'if command_id == "ocr.detect-lost":'),
        (UNWIRED_MSG, "ocr.detect"),
        (),
    ),
    (
        "N2 注册表条目被删（data.table.getCell）⇒ 必须红",
        PYTHON_WORKER,
        _text_replace('    "data.table.getCell": _table_get_cell,\n', ""),
        (UNWIRED_MSG, "data.table.getCell"),
        (),
    ),
    (
        "N3 unsliced 台账自我收紧：handler 体开始返回**已声明**的码 ⇒ 必须红（只由台账引起）",
        PYTHON_WORKER,
        _text_replace(
            "def _table_clear(invocation, flow_dir: str, table: str, inputs: dict)"
            " -> CommandResult:\n    obj = _table.load_table(flow_dir, table)",
            "def _table_clear(invocation, flow_dir: str, table: str, inputs: dict)"
            " -> CommandResult:\n"
            f'    return CommandResult.failure(ErrorCode.INVALID_INPUT, "{SENTINEL}")\n'
            "    obj = _table.load_table(flow_dir, table)",
        ),
        (STALE_MSG, "data.table.clear"),
        # 挑的是**已声明**的码，所以「实现了但没声明」那条不许出声——否则分不清是谁打中的
        (MISSING_DECL_MSG,),
    ),
    (
        "N5 台账方向①：桶里出现未声明的命令 ⇒ 必须红",
        ERR_GATE,
        _text_replace('    "data.table.clear": "同上",\n', ""),
        (UNDECLARED_MSG, "data.table.clear"),
        (),
    ),
]

MANAGED = sorted({case[1] for case in CASES} | {ERR_GATE, OCR_WORKER, PYTHON_WORKER})


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def run_gate() -> tuple[int, str]:
    proc = subprocess.run([str(PY), str(ERR_GATE)], capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


def _guarded(mutations: dict[Path, Callable[[str], str]]) -> tuple[int, str, list[str]]:
    """对若干文件施加注入、跑门禁、无论如何还原；返回 `(rc, 输出, 还原异常列表)`。"""
    originals = {path: path.read_bytes() for path in mutations}
    problems: list[str] = []
    try:
        for path, mutate in mutations.items():
            try:
                mutated = mutate(originals[path].decode("utf-8"))
            except ValueError as exc:
                return -1, f"REFUSING: {exc}", problems
            if mutated.encode("utf-8") == originals[path]:
                return -1, "REFUSING: 注入没有改变文件", problems
            path.write_text(mutated, encoding="utf-8", newline="\n")
        rc, out = run_gate()
    finally:
        for path, raw in originals.items():
            path.write_bytes(raw)
    for path, raw in originals.items():
        if path.read_bytes() != raw:
            problems.append(f"{path.name} 未逐字节还原")
    return rc, out, problems


def main() -> int:
    # ① 哨兵自检 + 前置条件
    for path in MANAGED:
        if not path.is_file():
            print(f"REFUSING: 受管文件不存在：{path}")
            return 2
        if SENTINEL in path.read_text(encoding="utf-8"):
            print(f"REFUSING: 受管文件已含哨兵（上次未还原？）：{path}")
            return 2

    rc, out = run_gate()
    if rc != 0:
        print(f"REFUSING: 基线不绿（rc={rc}）—— 先修基线再做负向验证")
        print(out[-600:])
        return 2
    print("基线绿：check_error_contract rc=0\n")

    results: list[tuple[str, bool, int, tuple[str, ...], str]] = []
    for name, path, mutate, expects, forbids in CASES:
        before = md5(path)
        rc, out, problems = _guarded({path: mutate})
        if problems:
            print(f"REFUSING: {name} —— {'；'.join(problems)}")
            return 2
        if md5(path) != before:
            print(f"REFUSING: {name} —— {path.name} 未逐字节还原")
            return 2
        hit = (
            rc != 0
            and all(fragment in out for fragment in expects)
            and not any(fragment in out for fragment in forbids)
        )
        last = [line for line in out.strip().splitlines() if line.strip()]
        results.append((name, hit, rc, expects + tuple(f"!{f}" for f in forbids), last[-1] if last else ""))

    # N4 ★ 元验证：关掉新判据后，N1 的注入必须**不再红**（否则 N1 的红不是它造成的）
    meta_rc, meta_out, meta_problems = _guarded(
        {
            ERR_GATE: _text_replace("            if not wired:\n", "            if False and not wired:\n"),
            OCR_WORKER: _text_replace(
                'if command_id == "ocr.detect":', 'if command_id == "ocr.detect-lost":'
            ),
        }
    )
    if meta_problems:
        print(f"REFUSING: N4 元验证 —— {'；'.join(meta_problems)}")
        return 2
    meta_ok = meta_rc == 0
    print()
    for name, hit, rc, expects, last in results:
        print(f"{'HIT ' if hit else 'MISS'}  {name}   (rc={rc}，期望含 {expects})")
        if not hit:
            print(f"       末行：{last}")
    print(
        f"{'HIT ' if meta_ok else 'MISS'}  N4 ★元验证：关掉哨兵后同一注入必须**变绿**"
        f"   (rc={meta_rc})"
    )
    if not meta_ok:
        tail = [line for line in meta_out.strip().splitlines() if line.strip()]
        print(f"       末行：{tail[-1] if tail else ''}")

    print()
    restored = run_gate()[0] == 0
    print(f"还原后复绿：{restored}")
    all_hit = all(item[1] for item in results) and meta_ok
    verdict = "PASS（全中 + 元验证 + 逐字节还原 + 复绿）" if all_hit and restored else "FAIL"
    print(f"RESULT: {verdict}")
    return 0 if all_hit and restored else 1


if __name__ == "__main__":
    raise SystemExit(main())
