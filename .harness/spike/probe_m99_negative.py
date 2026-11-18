"""M99 负向验证：桌面编辑框「层级展示不全」的判据必须真的拦得住。

注入 5 处，逐条比对**失败集是否等于期望集**（不是「有几条红」），最后
逐字节还原并复跑——只绿不算，还要能证明是谁报的红。
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
DIALOG = ROOT / "src" / "rpa_core" / "capture_kit" / "edit_dialog.py"
SENTINEL = "M99NEG"

OPEN_TOP = "test_node_view_opens_on_target_when_chain_exceeds_screen"
FALLBACK = "test_node_view_falls_back_to_last_when_nothing_selected"
FITS = "test_node_view_keeps_top_when_chain_fits_screen"
SETUP = "test_setup_node_scroll_keeps_initial_offset"
CLAMP_HIGH = "test_clamp_node_scroll[99-16-6]"
CLAMP_LOW = "test_clamp_node_scroll[-3-16-0]"

INJECTIONS = [
    (
        "initial-view-fixed-top",
        "        scroll = _clamp_node_scroll(target - _NODE_ROWS + 1, len(self._nodes))\n",
        f"        scroll = 0  # {SENTINEL}\n",
        # 接线用例也会红：它断言 _node_scroll == 6，固定 0 直接把它打掉。
        {OPEN_TOP, FALLBACK, SETUP},
    ),
    (
        # 选中行不跟目标走：属性面板会显示窗口属性，而不是目标自己的。
        # 不能注入 ``target = 0``——后面按 selected 回扫会把它覆盖掉，注入等
        # 于没生效（第一次跑就吃到了这个亏）。
        "return-first-row",
        "        return target, scroll\n",
        f"        return 0, scroll  # {SENTINEL}\n",
        {OPEN_TOP, FALLBACK, FITS, SETUP},
    ),
    (
        "clamp-no-upper",
        "    return max(0, min(int(scroll or 0), max(0, int(total) - _NODE_ROWS)))\n",
        f"    return max(0, int(scroll or 0))  # {SENTINEL}\n",
        # 不足一屏那条也会红：去掉上界后 5 不再被夹回 0。
        {CLAMP_HIGH, "test_clamp_node_scroll[5-4-0]"},
    ),
    (
        "clamp-no-lower",
        "    return max(0, min(int(scroll or 0), max(0, int(total) - _NODE_ROWS)))\n",
        f"    return min(int(scroll or 0), max(0, int(total) - _NODE_ROWS))  # {SENTINEL}\n",
        # 负数偏移会让「层数不足一屏」那条的 scroll == 0 失败。
        {CLAMP_LOW, FITS},
    ),
    (
        "setup-resets-zero",
        "        self._node_scroll = _clamp_node_scroll(self._node_scroll, len(self._nodes))\n",
        f"        self._node_scroll = 0  # {SENTINEL}\n",
        {SETUP},
    ),
]

RUNNER = [
    str(PY), "-m", "pytest", "tests/unit/test_capture_kit.py",
    "-q", "--basetemp=D:/Temp/pt_m99neg",
]


def run() -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update({
        "TMPDIR": "D:/Temp", "TEMP": "D:/Temp", "TMP": "D:/Temp",
        "PYTHONPATH": str(ROOT / "src"),
    })
    return subprocess.run(
        RUNNER, cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env, timeout=900,
    )


def failed_names(output: str) -> set[str]:
    return {
        line.split("::")[-1].split()[0].strip()
        for line in output.splitlines()
        if line.startswith("FAILED ")
    }


def md5(path: pathlib.Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def main() -> int:
    text = DIALOG.read_text(encoding="utf-8")
    if SENTINEL in text:
        print("[FATAL] 源码里残留上次注入的哨兵，先手动清理")
        return 2

    baseline_md5 = md5(DIALOG)
    print("baseline md5:", baseline_md5[:12])

    base = run()
    print(f"[baseline] rc={base.returncode} "
          f"{(base.stdout or '').strip().splitlines()[-1:]}")
    if base.returncode != 0:
        print("[FATAL] 基线不绿，负向验证无意义")
        return 2

    ok = True
    for name, old, new, expected in INJECTIONS:
        original = DIALOG.read_text(encoding="utf-8")
        if original.count(old) != 1:
            print(f"[FAIL] {name}: 锚点出现 {original.count(old)} 次（要求 1）")
            ok = False
            continue
        DIALOG.write_bytes(original.replace(old, new, 1).encode("utf-8"))
        try:
            bad = run()
        finally:
            DIALOG.write_bytes(original.encode("utf-8"))
        got = failed_names(bad.stdout)
        print(f"\n[{name}] rc={bad.returncode} 失败集={sorted(got)}")
        print(f"  期望={sorted(expected)}")
        if got != expected:
            print("  [FAIL] 失败集与期望不等")
            ok = False
        if bad.returncode not in (0, 1) or " error" in bad.stdout:
            print("  [FAIL] 出现 error 或异常退出码（环境坏了，不是判据红）")
            ok = False

    restored = md5(DIALOG)
    print("\nrestored md5:", restored[:12])
    if restored != baseline_md5:
        print("[FAIL] md5 未逐字节还原")
        ok = False
    again = run()
    print(f"[after restore] rc={again.returncode}")
    if again.returncode != 0:
        print("[FAIL] 还原后复跑不绿")
        ok = False
    print("M99 NEGATIVE VERIFICATION " + ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
