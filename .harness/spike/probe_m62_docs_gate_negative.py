"""M62 负向验证：`check_docs.py` 真的拦得住吗。

三处注入，**都打在门禁实际检查的方向（路径存在性）上**：

| 注入 | 方向 | 期望 |
|---|---|---|
| N1 | 写侧：README 的 set_feature 路径写错 | rc≠0，点名该路径 |
| N2 | 存在性：AGENTS.md 引用不存在的文件 | rc≠0，点名该路径 |
| N3 | 防空转：引用数跌破 `MIN_REFS` | rc≠0，命中 refusing-to-report-green |

N3 是**假绿灯**方向：若防空转自检失效，门禁会在「零覆盖」状态下标 PASS。

每处注入：`assert old in src`（防锚点未命中而静默跳过）→ 跑门禁 → 断言 rc 与输出 →
`try/finally` 逐字节还原 → 核 md5。**不接管道**（SIGPIPE 会打断还原）。
"""

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
GATE = ROOT / ".harness" / "scripts" / "check_docs.py"

README = ROOT / ".harness" / "README.md"
AGENTS = ROOT / "AGENTS.md"

INJECTIONS = (
    (
        "N1 写侧：README 把 set_feature 路径写错",
        README,
        "**`.harness/scripts/set_feature.py <id> true\\|false`**",
        "**`scripts/set_feature.py <id> true\\|false`**",
        "dead reference -> scripts/set_feature.py",
    ),
    (
        "N2 存在性：AGENTS.md 引用不存在的文件",
        AGENTS,
        ".harness/scripts/check_all.py",
        ".harness/scripts/check_ghost_docs.py",
        "dead reference -> .harness/scripts/check_ghost_docs.py",
    ),
    (
        "N3 防空转：_PREFIXES 丢掉 .harness/ ⇒ 引用数跌破 MIN_REFS",
        GATE,
        '    ".harness/",\n    "docs/",',
        '    "docs/",',
        "refusing to report green",
    ),
    (
        "N4 STATE 漂移：STATE.md 的 active_feature 与 project_state 不一致",
        ROOT / ".harness" / "STATE.md",
        "| 当前特性 `active_feature` | `selector-advisor` |",
        "| 当前特性 `active_feature` | `selector-adviser` |",
        "STATE.md stale: `active_feature` row says",
    ),
    (
        "N5 STATE 缺行：删掉 active_plan 那一行",
        ROOT / ".harness" / "STATE.md",
        "| 当前计划 `active_plan` | `.harness/tasks/M52-selector-advisor.md` |\n",
        "",
        "STATE.md missing a table row for `active_plan`",
    ),
)


def digest(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def run_gate() -> tuple[int, str]:
    done = subprocess.run([PY, str(GATE)], cwd=ROOT, capture_output=True, text=True)
    return done.returncode, done.stdout + done.stderr


def main() -> int:
    failures: list[str] = []
    for name, path, old, new, expect in INJECTIONS:
        before = digest(path)
        src = path.read_text(encoding="utf-8")
        assert old in src, f"{name}: anchor not found in {path.name}"
        try:
            path.write_text(src.replace(old, new, 1), encoding="utf-8", newline="\n")
            rc, out = run_gate()
            ok = rc != 0 and expect in out
            print(f"[{'HIT' if ok else 'MISS'}] {name}: rc={rc}")
            if not ok:
                failures.append(f"{name}: rc={rc}, out={out.strip()[:200]!r}")
        finally:
            path.write_text(src, encoding="utf-8", newline="\n")
        after = digest(path)
        if after != before:
            failures.append(f"{name}: restore md5 mismatch {before} -> {after}")

    rc, out = run_gate()
    if rc != 0:
        failures.append(f"post-restore gate not green: rc={rc} {out.strip()[:200]!r}")
    else:
        print(f"post-restore: {out.strip()}")

    if failures:
        print("\nNEGATIVE VERIFICATION FAILED")
        print("\n".join(f"- {item}" for item in failures))
        return 1
    print(f"\nFULL NEGATIVE VERIFICATION PASSED ({len(INJECTIONS)} injections)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
