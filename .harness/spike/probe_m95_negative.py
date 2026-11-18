"""M95 负向验证：任务栏壳窗口放行的接线判据必须真拦得住破坏。

注入都按推理写期望集、不照抄实际：
1. 桌面 verifier 不传开关 → 契约测试 + 静态接线红；
2. 壳窗口类名集合清空   → 窗口链单测 + 契约测试 + 静态接线红；
3. 窗口链不接开关       → find_window 单测 + 静态接线红；
4. 枚举阶段丢壳窗       → 静态接线红（FakeAPI 测不到真 win32 枚举）。

写回一律 write_bytes（write_text 会把 LF 转 CRLF），锚点要求唯一，结束核 md5。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

ROOT = Path(r"D:\Users\Administrator\Documents\代码\rpa_core")
FG = ROOT / "src/rpa_core/capture/foreground_window.py"
DV = ROOT / "src/rpa_core/capture/desktop_verification.py"
PY = ROOT / ".venv/Scripts/python.exe"
SENTINEL = "M95-INJECT-SENTINEL"

GATE = "M95 任务栏壳窗口：桌面校验放行，浏览器链不变"
UNIT_TRAY_CHAIN = "test_visible_top_windows_include_desktop_shell_for_taskbar"
UNIT_TRAY_FIND = "test_find_window_include_desktop_shell_reaches_taskbar"
PY_TRAY_VERIFY = "test_verify_finds_element_inside_taskbar_shell_window"

INJECTIONS = [
    (
        "verifier-not-passing-switch", DV,
        "            include_desktop_shell=True,\n",
        "            # " + SENTINEL + "\n",
        ("gate", "py_contract"), {GATE, PY_TRAY_VERIFY},
    ),
    (
        "shell-class-set-empty", FG,
        '_DESKTOP_SHELL_CLASSES = frozenset({"shell_traywnd", "shell_secondarytraywnd"})',
        "_DESKTOP_SHELL_CLASSES = frozenset()  # " + SENTINEL,
        ("gate", "unit", "py_contract"),
        {GATE, UNIT_TRAY_CHAIN, UNIT_TRAY_FIND, PY_TRAY_VERIFY},
    ),
    (
        "chain-drops-switch", FG,
        "    windows = visible_top_windows(api, include_desktop_shell=include_desktop_shell)\n",
        "    windows = visible_top_windows(api)  # " + SENTINEL + "\n",
        ("gate", "unit"), {GATE, UNIT_TRAY_FIND},
    ),
    (
        "enum-drops-shell", FG,
        "            is_shell = str(cls.value or \"\").strip().lower() in _DESKTOP_SHELL_CLASSES\n",
        "            is_shell = False  # " + SENTINEL + "\n",
        ("gate",), {GATE},
    ),
]

RUNNERS = {
    "gate": [str(PY), ".harness/scripts/check_element_pick_contract.py"],
    "unit": [str(PY), "-m", "pytest", "tests/unit/test_foreground_window.py",
             "-q", "--basetemp=D:/Temp/pt_m95neg_unit"],
    "py_contract": [str(PY), "-m", "pytest", "tests/contract/test_desktop_verification.py",
                    "-q", "--basetemp=D:/Temp/pt_m95neg_contract"],
}


def run(which: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update({"TMPDIR": "D:/Temp", "TEMP": "D:/Temp", "TMP": "D:/Temp",
                "PYTHONPATH": str(ROOT / "src")})
    return subprocess.run(
        RUNNERS[which], cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env, timeout=400,
    )


def failed_names(which: str, output: str) -> set[str]:
    if which == "gate":
        return {
            line[5:].split(" — ")[0].strip()
            for line in output.splitlines() if line.startswith("FAIL ")
        }
    return {
        line.split("::")[-1].split()[0].strip()
        for line in output.splitlines() if line.startswith("FAILED ")
    }


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def main() -> int:
    paths = (FG, DV)
    for path in paths:
        if SENTINEL in path.read_text(encoding="utf-8"):
            print(f"[FATAL] {path.name} 已有注入哨兵，先手工清理")
            return 2
    baselines = {p: md5(p) for p in paths}
    print("baseline md5:", {p.name: v[:10] for p, v in baselines.items()})

    ok = True
    for which in RUNNERS:
        base = run(which)
        tail = (base.stdout or base.stderr).strip().splitlines()[-1:]
        print(f"[baseline {which}] rc={base.returncode} {tail[0][:70] if tail else ''}")
        if base.returncode != 0:
            print("[FATAL] 基线不绿，负向验证无意义")
            return 2

    saved: dict[Path, bytes] = {}
    try:
        for name, path, old, new, runners, expected in INJECTIONS:
            original = path.read_text(encoding="utf-8")
            saved.setdefault(path, original.encode("utf-8"))
            if original.count(old) != 1:
                print(f"[FAIL] {name}: 锚点出现 {original.count(old)} 次（要求 1）")
                ok = False
                continue
            path.write_bytes(original.replace(old, new, 1).encode("utf-8"))
            got: set[str] = set()
            codes: list[int] = []
            tails: list[str] = []
            try:
                for which in runners:
                    bad = run(which)
                    codes.append(bad.returncode)
                    got |= failed_names(which, bad.stdout)
                    tails.append(
                        (bad.stdout.strip().splitlines() or [""])[-1]
                        + (" error" if " error" in bad.stdout else "")
                    )
            finally:
                path.write_bytes(original.encode("utf-8"))
            print(f"\n[{name}] rc={codes} 失败集={sorted(got)}")
            print(f"  期望={sorted(expected)}  {tails[-1][:80]}")
            if got != expected:
                print("  [FAIL] 失败集与期望不等")
                ok = False
            if any(code not in (0, 1) for code in codes) or any(
                " error" in tail for tail in tails
            ):
                print("  [FAIL] 出现 error 或异常退出码（环境坏了，不是判据红）")
                ok = False
    finally:
        for path, original in saved.items():
            path.write_bytes(original)

    restored = {p: md5(p) for p in paths}
    print("\nrestored md5:", {p.name: v[:10] for p, v in restored.items()})
    if restored != baselines:
        print("[FAIL] md5 未逐字节还原")
        ok = False
    for which in RUNNERS:
        again = run(which)
        print(f"[after restore {which}] rc={again.returncode}")
        if again.returncode != 0:
            print("[FAIL] 还原后复跑不绿")
            ok = False
    print("M95 NEGATIVE VERIFICATION " + ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
