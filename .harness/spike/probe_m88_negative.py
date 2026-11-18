"""M88 负向验证：xref 扫描的 memchr 重写不能悄悄改变判定结果。

背景：`rip_xrefs_to` 原来是逐字节 Python 循环，在 196 MB 的 Weixin.dll 上要
14.5 秒，正好卡在 hover 第一帧上（用户侧「点捕获假死十几秒」）。改成
`bytes.find` 定位 `lea` 操作码后 1.17 秒，但这条路径的输出是**要写进别人进程的
内存地址**——判据必须拦得住「为了快而扫漏/扫错」。

两个注入，期望失败集按推理写：
1. `_LEA_OPCODE` 换成不存在的操作码 → 一个 xref 都找不到 → core_xrefs 为空
   → 扫描直接放弃 ⇒ test_scan_finds_gate_in_writable_section 红；引用点用例
   同样一个都找不到 ⇒ test_rip_xrefs_report_instruction_start 一并红。
2. `has_rex` 恒 False → REX 指令被按 6 字节形式再解析一次（位置差 1）
   ⇒ 只有 test_rip_xrefs_report_instruction_start 红：disp 偏移在两种形式下
     数学等价（pos+2==rex+3），gate 仍能定位，所以「找得到 gate」那条不会红——
     这正是需要单独钉住返回值的原因。

每个注入跑完立刻逐字节还原并核 md5；异常走 finally 还原。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"D:\Users\Administrator\Documents\代码\rpa_core")
ACCESS = ROOT / "src/rpa_core/capture/accessibility.py"
TESTS = "tests/contract/test_accessibility_gate.py"
PY = ROOT / ".venv/Scripts/python.exe"
SENTINEL = "# M88-INJECT-SENTINEL"

INJECTIONS = [
    (
        "lea-opcode-typo",
        '_LEA_OPCODE = b"\\x8d"\n',
        '_LEA_OPCODE = b"\\x8e"  ' + SENTINEL + "\n",
        {"test_scan_finds_gate_in_writable_section",
         "test_rip_xrefs_report_instruction_start"},
    ),
    (
        "drop-rex-detection",
        "                has_rex = rex >= 0 and 0x40 <= raw[rex] <= 0x4F\n",
        "                has_rex = False  " + SENTINEL + "\n",
        {"test_rip_xrefs_report_instruction_start"},
    ),
]


def run_tests() -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update({"TMPDIR": "D:/Temp", "TEMP": "D:/Temp", "TMP": "D:/Temp",
                "PYTHONPATH": str(ROOT / "src")})
    return subprocess.run(
        [str(PY), "-m", "pytest", TESTS, "-q", "--basetemp=D:/Temp/pt_m88neg"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
        errors="replace", env=env, timeout=600,
    )


def failed_names(output: str) -> set[str]:
    names = set()
    for line in output.splitlines():
        if line.startswith("FAILED "):
            names.add(line.split("::")[-1].split()[0].strip())
    return names


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def main() -> int:
    original = ACCESS.read_text(encoding="utf-8")
    if SENTINEL in original:
        print("[FATAL] 源码里已有注入哨兵，先手工清理")
        return 2
    baseline = md5(ACCESS)
    print(f"baseline md5={baseline}")

    base = run_tests()
    tail = base.stdout.strip().splitlines()[-1] if base.stdout.strip() else ""
    print(f"对照跑 rc={base.returncode} tail={tail[:80]}")
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
            # write_bytes：write_text 在 Windows 会把 \n 转成 \r\n，还原后 md5 就对不上。
            ACCESS.write_bytes(original.replace(old, new, 1).encode("utf-8"))
            try:
                bad = run_tests()
            finally:
                ACCESS.write_bytes(original.encode("utf-8"))
            got = failed_names(bad.stdout)
            tail = bad.stdout.strip().splitlines()[-1] if bad.stdout.strip() else ""
            print(f"\n[{name}] rc={bad.returncode} 失败集={sorted(got)}")
            print(f"  期望={sorted(expected)}  {tail[:90]}")
            if got != expected:
                print("  [FAIL] 失败集与期望不等")
                ok = False
            if " error" in bad.stdout:
                print("  [FAIL] 出现 error（环境坏了，不是判据红）")
                ok = False
    finally:
        ACCESS.write_bytes(original.encode("utf-8"))

    after = md5(ACCESS)
    if after != baseline:
        print(f"[FAIL] 还原后 md5 不一致 {after} != {baseline}")
        ok = False
    else:
        print(f"还原 md5={after} 一致")

    back = run_tests()
    print(f"还原后复跑 rc={back.returncode}")
    ok &= back.returncode == 0
    print("\n" + ("ALL PASS" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
