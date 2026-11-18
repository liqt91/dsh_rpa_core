"""M83 负向验证：把「子进程导入路径」与「桌面腿失败可见」两个判据逐个拆掉。

纪律同 probe_m82_negative.py：锚点唯一自检 → 注入 → 集合相等比对 → finally 逐字节
还原核 md5；只用 `-v` 解析 failed/error。

期望失败集**按推理写**。这里有个容易写错的地方：拆掉「记录 failure」不仅打中
`test_desktop_spawn_failure_is_recorded_and_surfaced`，还会连带打中
`test_status_truncates_long_reason`（它同样依赖 failure 被记下来）。
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGETS = [
    "tests/contract/test_procenv.py",
    "tests/contract/test_hybrid_desktop_failure.py",
]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
LOG = Path("D:/Temp/m83_negative.log")

PROCENV = "src/rpa_core/procenv.py"
HYBRID = "src/rpa_core/capture/hybrid.py"
ENGINE = "src/rpa_core/capture_kit/engine.py"

INJECTIONS = [
    (
        "child_env 不再把导入根前置（等于回到报障前）",
        PROCENV,
        "    if all(os.path.normcase(p) != wanted for p in existing):\n"
        "        existing.insert(0, root)\n",
        "    if False:\n"
        "        existing.insert(0, root)\n",
        {"FAILED::test_child_env_prepends_root_when_absent"},
    ),
    (
        "桌面腿的真故障不再被记下",
        HYBRID,
        '                if desktop_failure.get("error"):\n'
        "                    # 只记**真故障**（子进程秒退、读不到元素）；单纯 ``timeout`` 是\n"
        "                    # 「用户还没按热键」的正常等待，浮窗不能因此报红。\n"
        "                    self._desktop_failure = desktop_failure\n",
        "                pass\n",
        {
            "FAILED::test_desktop_spawn_failure_is_recorded_and_surfaced",
            "FAILED::test_status_truncates_long_reason",
        },
    ),
    (
        "状态文案不再读桌面腿的失败原因（回到「照样报就绪」）",
        ENGINE,
        '    failure = getattr(session, "desktop_failure", None) or {}\n'
        '    reason = str(failure.get("error") or "").strip()\n',
        '    failure = getattr(session, "desktop_failure", None) or {}\n'
        '    reason = ""  # 注入：永不读 failure\n',
        {
            "FAILED::test_desktop_spawn_failure_is_recorded_and_surfaced",
            "FAILED::test_status_truncates_long_reason",
        },
    ),
    (
        "反向注入：无故障时也报「起不来」（判据得容不下假阳性）",
        ENGINE,
        "    if reason:\n",
        "    if True:\n",
        {"FAILED::test_plain_timeout_is_not_a_failure"},
    ),
]

_FAILED_RE = re.compile(r"^(FAILED|ERROR) (\S+?)::(\S+?)(?: |$)", re.MULTILINE)


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _run_suite() -> set[str]:
    completed = subprocess.run(
        [str(PYTHON), "-m", "pytest", *TARGETS, "-v", "--no-header", "-p", "no:cacheprovider"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = completed.stdout + completed.stderr
    LOG.write_text(output, encoding="utf-8")
    reds: set[str] = set()
    for kind, _file, name in _FAILED_RE.findall(output):
        reds.add(f"{kind}::{name.split('[')[0]}")
    return reds


def main() -> int:
    failures: list[str] = []
    for label, rel, old, new, expected in INJECTIONS:
        path = ROOT / rel
        original = path.read_text(encoding="utf-8")
        before = _md5(path)
        occurrences = original.count(old)
        if occurrences != 1:
            failures.append(f"{label}: 锚点出现 {occurrences} 次（要求唯一），拒绝注入")
            continue
        try:
            path.write_text(original.replace(old, new, 1), encoding="utf-8", newline="\n")
            reds = _run_suite()
        finally:
            path.write_text(original, encoding="utf-8", newline="\n")
            restored = _md5(path) == before
        if not restored:
            failures.append(f"{label}: 还原后 md5 不一致")
            break
        if reds != expected:
            failures.append(f"{label}: 期望 {sorted(expected)} 实际 {sorted(reds)}")
            continue
        print(f"OK  {label}: 精确红 {len(reds)} 条")

    reds = _run_suite()
    if reds:
        failures.append(f"还原后仍有红：{sorted(reds)}")
    else:
        print("OK  还原后全绿")

    if failures:
        for item in failures:
            print("FAIL", item)
        return 1
    print("M83 NEGATIVE VERIFICATION PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
