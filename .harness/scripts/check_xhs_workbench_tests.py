"""门禁：小红书工作台应用层单测（xhs-workbench 独占域）。

默认 `pytest` 只收 `testpaths = ["tests"]`，扫不到 `apps/xhs-workbench/tests/`，
所以这台应用层的 51 个用例一直没进全门禁。本闸门专门跑它，并带两层哨兵防止
「看起来绿、其实没跑」：

1. 接线哨兵：check_all.py 里摘掉本闸门那一行，本闸门就不再进全门禁
   （检查对自身路径的引用恰好 1 次，仿 check_branch_scope.py）。
2. 跑测哨兵：必须真的收集到 >= EXPECTED_MIN_TESTS 条用例、且关键接线测试
   ``test_run_live_actually_calls_the_verdict_function`` 在其中 PASSED。
   该测试断言 ``run_live`` 真消费了 ``evaluate_run`` —— 只测纯函数、没接线的
   实现会让它红，从而挡住「判据函数改了却没被真机路径用到」的恒绿假判据。

运行：uv run python .harness/scripts/check_xhs_workbench_tests.py
（也可被 check_all.py 直接调用；不依赖浏览器，纯单测，秒级。）
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEST_DIR_REL = "apps/xhs-workbench/tests"
CHECK_ALL_REL = ".harness/scripts/check_all.py"
WIRING_RE = re.compile(
    r'str\(ROOT / "\.harness" / "scripts" / "check_xhs_workbench_tests\.py"\)'
)
# 当前应用层用例数（含 test_workflow_contract + test_live_verdict）。设下限而非
# 精确值：加测试仍绿，删/漏收集则红。
EXPECTED_MIN_TESTS = 50
# 接线哨兵：这张测试证明真机路径（`run_live`）真的调用了判据函数 `evaluate_run`。
SENTINEL_TEST = "test_run_live_actually_calls_the_verdict_function"


def check_wiring(errors: list[str]) -> None:
    """接线哨兵：摘掉 check_all.py 里那一行，本闸门就不再进全门禁。"""
    path = ROOT / CHECK_ALL_REL
    if not path.is_file():
        errors.append(f"缺 {CHECK_ALL_REL}")
        return
    hits = WIRING_RE.findall(path.read_text(encoding="utf-8"))
    if len(hits) != 1:
        errors.append(f"{CHECK_ALL_REL}: 接线命中 {len(hits)} 次（须恰好 1）")


def enumerate_tests() -> tuple[int, bool, str]:
    """只收集不执行，返回 (用例总数, 哨兵测试是否被收集到, 输出尾部)。

    ``pyproject.toml`` 的 ``addopts = "-q"`` 下 ``--collect-only`` 按「每行一个文件、
    末尾 ``: N`` 计数」的格式输出（不是逐条 ``::name``），所以总数用 ``: (\\d+)`` 求和；
    哨兵则用 ``-k <名>`` 单独收集，确认恰好命中 1 条——不依赖具体输出排版。
    """
    base = [
        sys.executable,
        "-m",
        "pytest",
        TEST_DIR_REL,
        "--collect-only",
        "-q",
        "-p",
        "no:cacheprovider",
    ]
    full = subprocess.run(
        base, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    per_file = [int(n) for n in re.findall(r": (\d+)$", full.stdout, re.MULTILINE)]
    total = sum(per_file)

    sentinel_proc = subprocess.run(
        [*base, "-k", SENTINEL_TEST],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    sentinel_counts = [int(n) for n in re.findall(r": (\d+)$", sentinel_proc.stdout, re.MULTILINE)]
    sentinel_ok = sentinel_proc.returncode == 0 and sum(sentinel_counts) == 1

    tail = (full.stdout + full.stderr)[-1500:]
    return total, sentinel_ok, tail


def run_tests() -> tuple[int, str]:
    """真正执行应用层单测，返回 (returncode, 输出尾部)。"""
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        TEST_DIR_REL,
        "-q",
        "-p",
        "no:cacheprovider",
    ]
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode, (proc.stdout + proc.stderr)[-1500:]


def main() -> int:
    errors: list[str] = []
    check_wiring(errors)

    total, sentinel_ok, enum_tail = enumerate_tests()
    if total < EXPECTED_MIN_TESTS:
        errors.append(
            f"收集到 {total} 条用例，低于下限 {EXPECTED_MIN_TESTS} —— "
            f"疑似路径错/未收集/用例被删。尾部：\n{enum_tail}"
        )
    if not sentinel_ok:
        errors.append(
            f"接线哨兵测试 {SENTINEL_TEST} 没被恰好收集到 1 条 —— "
            f"run_live→evaluate_run 的接线无法证明，判据可能是恒绿假判据。"
        )

    run_rc, run_tail = run_tests()
    if run_rc != 0:
        errors.append(f"执行阶段退出码 {run_rc}（0 才表示全过）。尾部：\n{run_tail}")

    if errors:
        print("XHS WORKBENCH TESTS CHECK FAILED")
        for item in errors:
            print(f"- {item}")
        return 1
    print(
        f"XHS WORKBENCH TESTS CHECK PASSED "
        f"(collected={total}, sentinel={SENTINEL_TEST}, wiring ok)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
