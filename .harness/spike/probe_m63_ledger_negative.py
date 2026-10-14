"""M63 负向验证：② 改名引用 + ④ `wip` 状态 —— 门禁必须真红，且逐字节还原。

覆盖的判据（本轮改动处）：
- **②** `.harness/BACKLOG.md` → `.harness/commands-plan.md`：`check_docs.py` 的「引用必须存在」
  现在盯着**新名字** ⇒ 指回**旧名**必须红（否则改名等于把这条引用检查绕过去了）。
- **④** `check_tasks.py` 的 `VALID_STATUSES` 新增 `wip`（**放宽**）：必须证明放宽**没有连带**
  破坏两条既有判据 ——「非法值仍被拒」与「`active` 恰好一个」。

三自保（都踩过坑）：
  ① 开头扫受管文件，发现哨兵即**硬失败**（不靠人记性）；
  ② 注入包 `try/finally`，异常也还原；
  ③ 还原后核 **md5 逐字节相等**。

用法：`.venv/Scripts/python.exe .harness/spike/probe_m63_ledger_negative.py`
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
SENTINEL = "__m63negver__"

STATE = ROOT / ".harness" / "STATE.md"
M61 = ROOT / ".harness" / "tasks" / "M61-standalone-runner-packaging.md"
CHECK_TASKS = ROOT / ".harness" / "scripts" / "check_tasks.py"
CHECK_DOCS = ROOT / ".harness" / "scripts" / "check_docs.py"

# (名称, 受管文件, 锚点, 替换为, 门禁脚本, 期望出现的报错子串)
CASES = [
    (
        "N1 ② 改名后仍指向旧名（.harness/BACKLOG.md）",
        STATE,
        ".harness/commands-plan.md",
        ".harness/BACKLOG.md",
        CHECK_DOCS,
        "dead reference",
    ),
    (
        "N2 ④ 非法 status 仍须被拒",
        M61,
        "Status: `planned`",
        f"Status: `{SENTINEL}`",
        CHECK_TASKS,
        "invalid task status",
    ),
    (
        "N3 ④ `wip` 放宽不得破坏「active 恰好一个」",
        M61,
        "Status: `planned`",
        "Status: `active`",
        CHECK_TASKS,
        "active task mismatch",
    ),
]

MANAGED = sorted({case[1] for case in CASES})


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

    base_tasks, out_tasks = run(CHECK_TASKS)
    base_docs, out_docs = run(CHECK_DOCS)
    if base_tasks != 0 or base_docs != 0:
        print("REFUSING: 基线不绿 —— 先修基线，再做负向验证")
        print(out_tasks[-400:], out_docs[-400:])
        return 2
    print("基线绿：check_tasks + check_docs 均 rc=0\n")

    results: list[tuple[str, bool, int, str, str]] = []
    for name, path, old, new, gate, expect in CASES:
        original = path.read_text(encoding="utf-8")
        if original.count(old) != 1:
            print(f"REFUSING: 锚点在 {path.name} 出现 {original.count(old)} 次（须恰好 1）")
            return 2
        before = md5(path)
        try:
            path.write_text(original.replace(old, new), encoding="utf-8", newline="\n")
            rc, out = run(gate)
            lines = [line for line in out.strip().splitlines() if line.strip()]
            hit = rc != 0 and expect in out
            results.append((name, hit, rc, expect, lines[-1] if lines else ""))
        finally:
            path.write_text(original, encoding="utf-8", newline="\n")
        after = md5(path)
        if before != after:
            print(f"REFUSING: {path.name} 未逐字节还原（{before} != {after}）")
            return 2

    rc_tasks, _ = run(CHECK_TASKS)
    rc_docs, _ = run(CHECK_DOCS)
    restored = rc_tasks == 0 and rc_docs == 0

    print()
    all_hit = True
    for name, hit, rc, expect, last in results:
        print(f"{'HIT ' if hit else 'MISS'}  {name}   (rc={rc}，期望含 {expect!r})")
        if not hit:
            all_hit = False
            print(f"       末行：{last}")
    print()
    print(f"还原后复绿：{restored}（check_tasks rc={rc_tasks} / check_docs rc={rc_docs}）")
    verdict = "PASS（全中 + 逐字节还原 + 复绿）" if all_hit and restored else "FAIL"
    print(f"RESULT: {verdict}")
    return 0 if all_hit and restored else 1


if __name__ == "__main__":
    raise SystemExit(main())
