"""M86 负向验证：无障碍 gate 判据必须拦得住。

两手注入，各自期望失败集（按推理写，不照抄实际）：
1. 去掉「没有 core xref 就不猜」的早退  → test_scan_requires_core_string
                                         + test_scan_filters_candidates_far_from_core_xref
2. 去掉「目标必须在可写段」的检查        → test_scan_rejects_gate_target_in_executable_section

每个注入跑完立刻逐字节还原并核 md5；任何异常走 finally 还原。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"D:\Users\Administrator\Documents\代码\rpa_core")
TARGET = ROOT / "src/rpa_core/capture/accessibility.py"
TESTS = "tests/contract/test_accessibility_gate.py"
PY = ROOT / ".venv/Scripts/python.exe"
SENTINEL = "# M86-INJECT-SENTINEL"

INJECTIONS = [
    (
        "drop-core-xref-early-return",
        "    if not core_xrefs:\n"
        "        return GateScan(None, 0, len(core_xrefs), None)\n",
        "    if False:  " + SENTINEL + "\n"
        "        return GateScan(None, 0, len(core_xrefs), None)\n",
        {
            "test_scan_requires_core_string",
            "test_scan_filters_candidates_far_from_core_xref",
        },
    ),
    (
        "drop-writable-section-check",
        "        if tsec is None or not tsec.writable:\n",
        "        if tsec is None:  " + SENTINEL + "\n",
        {"test_scan_rejects_gate_target_in_executable_section"},
    ),
]


def run_tests() -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update({"TMPDIR": "D:/Temp", "TEMP": "D:/Temp", "TMP": "D:/Temp",
                "PYTHONPATH": str(ROOT / "src")})
    return subprocess.run(
        [str(PY), "-m", "pytest", TESTS, "-q", "--basetemp=D:/Temp/pt_m86"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
        errors="replace", env=env, timeout=600,
    )


def failed_names(output: str) -> set[str]:
    names = set()
    for line in output.splitlines():
        if line.startswith("FAILED "):
            part = line.split("::")[-1]
            names.add(part.split()[0].strip())
    return names


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def main() -> int:
    original = TARGET.read_text(encoding="utf-8")
    if SENTINEL in original:
        print("[FATAL] 源码里有注入哨兵，先手工清理")
        return 2
    baseline_md5 = md5(TARGET)
    print(f"baseline md5={baseline_md5}")

    base = run_tests()
    print(f"对照跑 rc={base.returncode} tail={base.stdout.strip().splitlines()[-1][:80]}")
    if base.returncode != 0:
        print("[FATAL] 基线不绿，负向验证无意义")
        return 2

    ok = True
    try:
        for name, old, new, expected in INJECTIONS:
            if original.count(old) != 1:
                print(f"[FAIL] {name}: 锚点出现 {original.count(old)} 次（要求 1）")
                ok = False
                continue
            # write_bytes：write_text 在 Windows 会把 \n 转 \r\n，整个文件换行被改，
            # 还原后 md5 就不再等于基线（2026-11-17 实测踩到）。
            TARGET.write_bytes(original.replace(old, new, 1).encode("utf-8"))
            try:
                bad = run_tests()
            finally:
                TARGET.write_bytes(original.encode("utf-8"))
            got = failed_names(bad.stdout)
            tail = bad.stdout.strip().splitlines()[-1] if bad.stdout.strip() else ""
            print(f"\n[{name}] rc={bad.returncode} 失败集={sorted(got)}")
            print(f"  期望={sorted(expected)}  {tail[:90]}")
            if got != expected:
                print("  [FAIL] 失败集与期望不等")
                ok = False
            if " error" in bad.stdout:
                print("  [FAIL] 出现 error（收集/环境坏了，不是判据红）")
                ok = False
    finally:
        TARGET.write_bytes(original.encode("utf-8"))

    after_md5 = md5(TARGET)
    if after_md5 != baseline_md5:
        print(f"[FAIL] 还原后 md5 不一致 {after_md5} != {baseline_md5}")
        ok = False
    else:
        print(f"\n还原 md5={after_md5} 一致")
    back = run_tests()
    print(f"还原后复跑 rc={back.returncode}")
    ok &= back.returncode == 0
    print("\n" + ("ALL PASS" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
