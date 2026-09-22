"""负向验证探针（M38 S2）：证明桌面矩阵这一片的**记账与断言真的承重**。

六向，每向都沿着「这条机制可能失效的方式」注入，跑完逐字节还原：

| 向 | 注入 | 期望 |
|---|---|---|
| ① | 把 `desktop.screenshot` 缺口行的 `knownGap` 改成空白串 | 静态校验器红，点名「knownGap 是空串」 |
| ② | 把 `desktop.win32.screenshot` 的**全部**变体都标成 `knownGap` | 静态校验器红，点名「没有任何未标 knownGap 的 negative 变体」（缺口不许盖住整条命令） |
| ③ | 摘掉 `desktop.screenshot` 两条**非缺口**变体里的 `savePath` | 静态校验器红，点名 `desktop.screenshot.savePath`——证明缺口行**不计入**参数覆盖（缺口不许凑覆盖率） |
| ④ | 反过来：把缺口行的 `knownGap` 整个删掉 | 静态校验器**照样 PASSED**——静态层看不见「缺口修没修」。这条是对照：说明自我收紧只能落在执行层的严格 xfail 上，只靠静态层够不着 |
| ⑤ | 真机跑 `desktop.screenshot` 三个变体，跑两遍：带标记 / 摘标记 | 带标记 → 1 xfailed / exit 0；摘标记 → 那行**真红**且信息里是 `NoneType`——证明被 xfail 的是一条真缺陷，不是把已经绿的用例盖上被子 |
| ⑥ | 把 `desktop.getWindowList` 正路径的锚点（`{appTitle}`）改成不存在的标题，真机跑 | 4 条正路径**全红**，失败信息里有 `outputListContains.windows`——证明新加的列表包含断言**真的被求值**（「判据写了但没人执行」正是假绿灯的成因） |

判据是「红在**预期的那几条**上」，不是「有没有红」（M30 S4：注入点没落在被测分支上时，
注入照样可能与断言无关）。①–④ 只改用例表；⑤⑥ 要真机。

需要真机（⑤⑥）：探针自己设 `RPA_COMMAND_MATRIX=1`，但**不**替你设 `RPA_DESKTOP_E2E=1`
——缺了则 ⑤⑥ 报 SKIP，前四向照跑。

用法：RPA_DESKTOP_E2E=1 uv run python .harness/spike/probe_desktop_matrix_negative.py
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
from collections.abc import Callable
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[2]
PYTHON = str(ROOT / ".venv" / "Scripts" / "python.exe")
CASES = ROOT / "tests" / "commands" / "cases"
CHECKER = ROOT / ".harness" / "scripts" / "check_command_matrix.py"
UIA_DRIVER = "tests/commands/test_desktop_matrix.py"
# 真机筛选关键词（不带点：pytest 的 -k 表达式里点号不是合法标识符字符）。
SCREENSHOT_KEYWORD = "screenshot"
WINDOW_LIST_KEYWORD = "getWindowList"

LINES: list[str] = []


def _run(command: list[str], env_extra: dict[str, str] | None = None) -> tuple[int, str]:
    env = dict(os.environ)
    env.update(env_extra or {})
    proc = subprocess.run(
        command, cwd=str(ROOT), capture_output=True, text=True, env=env,
        encoding="utf-8", errors="replace", check=False,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def run_checker() -> tuple[int, str]:
    return _run([PYTHON, str(CHECKER)])


def parse_outcomes(output: str) -> set[str]:
    """从 `-rA` 的短摘要里切 `(结果, <命令::变体>)`。

    **不能**按「行尾即 nodeid」去匹配：XFAIL 的行在 nodeid 之后还跟着 ` - 理由`
    （实测踩过——第一版正则用 `\\]$` 收尾，于是 xfail 那条被静默漏掉，探针差点把
    「机制正常」报成 BAD）。所以先按 ` - ` 切掉理由后缀，再从 nodeid 里取参数化 id。
    """
    outcomes: set[str] = set()
    for line in output.splitlines():
        text = line.strip()
        outcome, _, rest = text.partition(" ")
        if outcome not in {"PASSED", "FAILED", "XFAIL", "XPASS", "ERROR"}:
            continue
        node = rest.split(" - ", 1)[0].strip()
        marker = "test_command_variant["
        start = node.find(marker)
        if start < 0 or not node.endswith("]"):
            continue
        outcomes.add(f"{outcome} {node[start + len(marker):-1]}")
    return outcomes


def run_device(driver: str, keyword: str) -> tuple[int, set[str], str]:
    """真机跑某个驱动的若干变体，返回 `(退出码, 逐条结果, 原文)`。"""
    code, output = _run(
        [PYTHON, "-m", "pytest", driver, "-p", "no:cacheprovider", "--no-header",
         "-q", "-rA", "-k", keyword],
        env_extra={"RPA_COMMAND_MATRIX": "1", "RPA_DESKTOP_E2E": "1"},
    )
    return code, parse_outcomes(output), output


def mutate(path: pathlib.Path, edit: Callable[[dict[str, Any]], None]) -> bytes:
    """按 JSON 对象改用例表（注入点写起来比字符串替换稳），返回原始字节供还原。

    刻意在**对象层**注入而不是文本层：用例表里 `"negative": true` 这类片段在一条命令里
    会出现多次，字符串替换要么命中不唯一、要么 quietly 改错行——正是负向验证最容易
    自己骗自己的地方。
    """
    original = path.read_bytes()
    data = json.loads(original.decode("utf-8"))
    edit(data)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return original


def restore(path: pathlib.Path, original: bytes) -> bool:
    path.write_bytes(original)
    return path.read_bytes() == original


def variants(data: dict[str, Any], command: str) -> list[dict[str, Any]]:
    return list(data[command]["variants"])


def gap_variants(data: dict[str, Any], command: str) -> list[dict[str, Any]]:
    return [variant for variant in variants(data, command) if variant.get("knownGap")]


def check(label: str, code: int, output: str, want_code: int, keyword: str) -> None:
    hit = keyword in output
    ok = (code == want_code) and hit
    LINES.append(
        f"[{'OK ' if ok else 'BAD'}] {label}：exit={code}（期望 {want_code}）、"
        f"关键词 {'命中' if hit else '未命中'}：{keyword}"
    )
    if not ok:
        LINES.append("      ---- 原文（末 25 行）----")
        for line in output.strip().splitlines()[-25:]:
            LINES.append(f"      {line}")


def main() -> int:
    uia = CASES / "desktop.json"
    win32 = CASES / "desktop_win32.json"

    code, output = run_checker()
    check("基线（未注入）", code, output, 0, "实现缺口 2 条")

    # ① knownGap 只写空白：等于没记（「现象 + 出路」一个字都没有）
    original = mutate(uia, lambda d: [
        variant.update({"knownGap": "   "})
        for variant in gap_variants(d, "desktop.screenshot")
    ])
    try:
        code, output = run_checker()
        check("① knownGap 空串", code, output, 1, "knownGap 是空串")
    finally:
        assert restore(uia, original), "① 未逐字节还原"

    # ② 整条命令被缺口盖住（win32 screenshot 的 4 个变体全标上）
    original = mutate(win32, lambda d: [
        variant.update({"knownGap": "注入：把整条命令盖住"})
        for variant in variants(d, "desktop.win32.screenshot")
    ])
    try:
        code, output = run_checker()
        check(
            "② 整条命令被 knownGap 盖住", code, output, 1,
            "没有任何**未标 knownGap** 的 negative 变体",
        )
    finally:
        assert restore(win32, original), "② 未逐字节还原"

    # ③ 缺口行不计入参数覆盖：摘掉两条非缺口变体的 savePath，剩下的只有缺口行在用这个参数
    original = mutate(uia, lambda d: [
        variant["inputs"].pop("savePath")
        for variant in variants(d, "desktop.screenshot")
        if not variant.get("knownGap")
    ])
    try:
        code, output = run_checker()
        check("③ 缺口行不计入参数覆盖", code, output, 1, "desktop.screenshot.savePath")
    finally:
        assert restore(uia, original), "③ 未逐字节还原"

    # ④ 对照：静态层看不见缺口修没修——删掉标记后照样 PASSED（所以自我收紧只能在执行层）
    original = mutate(uia, lambda d: [
        variant.pop("knownGap") for variant in gap_variants(d, "desktop.screenshot")
    ])
    try:
        code, output = run_checker()
        check("④ 对照：静态层对缺口视而不见", code, output, 0, "实现缺口 1 条")
    finally:
        assert restore(uia, original), "④ 未逐字节还原"

    if os.environ.get("RPA_DESKTOP_E2E") != "1":
        LINES.append("[SKIP] ⑤⑥ 真机那两向没跑：未设 RPA_DESKTOP_E2E=1（前四向已跑完）")
    else:
        # ⑤ 带标记 xfail / 摘标记真红（证明盖的是真缺陷，不是把绿的盖上被子）
        code, outcomes, _ = run_device(UIA_DRIVER, SCREENSHOT_KEYWORD)
        check(
            "⑤a 屏幕缺口基线真机（带标记）", code, str(sorted(outcomes)), 0,
            "XFAIL desktop.screenshot::writes-a-png-into-the-variant-temp-dir",
        )
        original = mutate(uia, lambda d: [
            variant.pop("knownGap") for variant in gap_variants(d, "desktop.screenshot")
        ])
        try:
            code, outcomes, output = run_device(UIA_DRIVER, SCREENSHOT_KEYWORD)
            check("⑤b 摘掉标记后真红", code, output, 1, "NoneType")
            LINES.append(f"      ⑤b 逐条结果：{sorted(outcomes)}")
        finally:
            assert restore(uia, original), "⑤ 未逐字节还原"

        # ⑥ 新断言（列表包含）真的被求值：换一个不存在的锚点标题，4 条正路径必须全红
        code, outcomes, _ = run_device(UIA_DRIVER, WINDOW_LIST_KEYWORD)
        check(
            "⑥a getWindowList 基线真机", code, str(sorted(outcomes)), 0,
            "PASSED desktop.getWindowList::exact-pattern-finds-the-fixture-window",
        )
        original = mutate(uia, lambda d: [
            variant["expect"]["outputListContains"]["windows"].update({"title": "No Such Window"})
            for variant in variants(d, "desktop.getWindowList")
            if "outputListContains" in variant["expect"]
        ])
        try:
            code, outcomes, output = run_device(UIA_DRIVER, WINDOW_LIST_KEYWORD)
            check("⑥b 锚点换掉后正路径全红", code, output, 1, "outputListContains.windows")
            red = sorted(name for name in outcomes if name.startswith("FAILED"))
            LINES.append(f"      ⑥b 红的正是这 4 条：{red}")
        finally:
            assert restore(uia, original), "⑥ 未逐字节还原"

    print("\n".join(LINES))
    bad = [line for line in LINES if line.startswith("[BAD]")]
    print(f"\n{'ALL DIRECTIONS OK' if not bad else f'{len(bad)} DIRECTION(S) FAILED'}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
