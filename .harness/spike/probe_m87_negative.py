"""M87 负向验证：唤醒重试的判据必须拦得住回退。

七个注入，各自期望失败集（按推理写，不照抄实际）：
1. 节流改回「每进程只一次」        → test_rewakes_after_gate_closed_by_target
                                    + test_trace_does_not_flood
2. 删掉 hit-test 之前的唤醒        → test_wake_runs_even_when_hit_test_raises
3. 删掉「不可恢复失败不再重试」     → test_hopeless_pid_is_not_retried
4. 删掉「救活后强制重画」           → test_hover_repaints_when_tree_revived_while_mouse_still
5. 唤醒返回值恒 True               → test_returns_true_only_when_gate_was_written
6. already_active 也 poke          → test_activate_skips_poke_in_steady_state
7. agent 恒传 poke=True            → test_steady_state_passes_poke_false

每个注入跑完立刻逐字节还原并核 md5；任何异常走 finally 还原。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"D:\Users\Administrator\Documents\代码\rpa_core")
AGENT = ROOT / "src/rpa_core/capture/desktop_agent.py"
ACCESS = ROOT / "src/rpa_core/capture/accessibility.py"
TESTS = "tests/contract/test_accessibility_wake_retry.py"
PY = ROOT / ".venv/Scripts/python.exe"
SENTINEL = "# M87-INJECT-SENTINEL"

# (名字, 目标文件, 锚点, 注入, 期望失败集)
INJECTIONS = [
    (
        "throttle-back-to-once",
        AGENT,
        "    if not force and now - _WAKE_LAST.get(pid, 0.0) < _WAKE_INTERVAL:\n",
        "    if not force and pid in _WAKE_LAST:  " + SENTINEL + "\n",
        {"test_rewakes_after_gate_closed_by_target", "test_trace_does_not_flood"},
    ),
    (
        "drop-prewake-before-hit-test",
        AGENT,
        "        if probe_root:\n"
        "            _maybe_wake_accessibility(probe_root)\n",
        "        if probe_root:\n"
        "            pass  " + SENTINEL + "\n",
        {"test_wake_runs_even_when_hit_test_raises"},
    ),
    (
        "drop-hopeless-marking",
        AGENT,
        "        if result.reason in _WAKE_HOPELESS_REASONS:\n"
        "            _WAKE_HOPELESS.add(pid)\n",
        "        if result.reason in _WAKE_HOPELESS_REASONS:\n"
        "            pass  " + SENTINEL + "\n",
        {"test_hopeless_pid_is_not_retried"},
    ),
    (
        "drop-force-repaint",
        AGENT,
        "                if wake_root and _maybe_wake_accessibility(wake_root):\n"
        "                    force_hit = True\n",
        "                if wake_root and _maybe_wake_accessibility(wake_root):\n"
        "                    pass  " + SENTINEL + "\n",
        {"test_hover_repaints_when_tree_revived_while_mouse_still"},
    ),
    (
        "return-true-on-already-active",
        AGENT,
        '    return result.reason == "activated"\n',
        "    return True  " + SENTINEL + "\n",
        {"test_returns_true_only_when_gate_was_written"},
    ),
    (
        "always-poke-when-active",
        ACCESS,
        "        if poke:\n            poke_provider(hwnd)\n",
        "        if True:  " + SENTINEL + "\n            poke_provider(hwnd)\n",
        {"test_activate_skips_poke_in_steady_state"},
    ),
    (
        "agent-always-pokes",
        AGENT,
        "        result = accessibility.activate(hwnd, poke=_WAKE_STATE.get(pid) is not True)\n",
        "        result = accessibility.activate(hwnd, poke=True)  " + SENTINEL + "\n",
        {"test_steady_state_passes_poke_false"},
    ),
    (
        "reuse-stale-leaf-after-revive",
        AGENT,
        "                    if (\n                        not revived\n",
        "                    if (\n                        True  " + SENTINEL + "\n",
        {"test_capture_key_drops_stale_leaf_when_tree_revived"},
    ),
]


def run_tests() -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update({"TMPDIR": "D:/Temp", "TEMP": "D:/Temp", "TMP": "D:/Temp",
                "PYTHONPATH": str(ROOT / "src")})
    return subprocess.run(
        [str(PY), "-m", "pytest", TESTS, "-q", "--basetemp=D:/Temp/pt_m87neg"],
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
    files = (AGENT, ACCESS)
    originals = {p: p.read_text(encoding="utf-8") for p in files}
    for p in files:
        if SENTINEL in originals[p]:
            print(f"[FATAL] {p.name} 里有注入哨兵，先手工清理")
            return 2
    baseline = {p: md5(p) for p in files}
    for p in files:
        print(f"baseline {p.name} md5={baseline[p]}")

    base = run_tests()
    last = base.stdout.strip().splitlines()[-1] if base.stdout.strip() else ""
    print(f"对照跑 rc={base.returncode} tail={last[:80]}")
    if base.returncode != 0:
        print("[FATAL] 基线不绿，负向验证无意义")
        return 2

    ok = True
    try:
        for name, target, old, new, expected in INJECTIONS:
            original = originals[target]
            if original.count(old) != 1:
                print(f"[FAIL] {name}: 锚点在 {target.name} 出现 "
                      f"{original.count(old)} 次（要求 1）")
                ok = False
                continue
            # write_bytes：write_text 在 Windows 会把 \n 转 \r\n，还原后 md5 就不等于基线。
            target.write_bytes(original.replace(old, new, 1).encode("utf-8"))
            try:
                bad = run_tests()
            finally:
                target.write_bytes(original.encode("utf-8"))
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
        for p in files:
            p.write_bytes(originals[p].encode("utf-8"))

    for p in files:
        after = md5(p)
        if after != baseline[p]:
            print(f"[FAIL] {p.name} 还原后 md5 不一致 {after} != {baseline[p]}")
            ok = False
        else:
            print(f"还原 {p.name} md5={after} 一致")

    back = run_tests()
    print(f"还原后复跑 rc={back.returncode}")
    ok &= back.returncode == 0
    print("\n" + ("ALL PASS" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
