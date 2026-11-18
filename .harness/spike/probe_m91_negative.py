"""M91 负向验证：桌面详细编辑框接线的判据必须真的拦得住破坏。

注入都针对「用户实际走的两条路」上的新接线，期望失败集按推理写、不照抄实际：
1. 控制器不传 nodes        → 契约 + 捕获路径单测红（原生框退回简式 JSON）；
2. 详情 RPC 不传 descriptor → 契约 + 详情路径 RPC 测试红（重组拿不到候选）；
3. 建框时不刷初始预览      → 契约红（界面停在空 JSON，静态调用数必须抓到）。

写回一律 write_bytes（write_text 会把 LF 转 CRLF），锚点要求唯一，结束核 md5。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"D:\Users\Administrator\Documents\代码\rpa_core")
CONTROLLER = ROOT / "src/rpa_core/capture_kit/controller.py"
API = ROOT / ".harness/demo/ppx_probe/app/api/api.py"
DIALOG = ROOT / "src/rpa_core/capture_kit/edit_dialog.py"
PY = ROOT / ".venv/Scripts/python.exe"
SENTINEL = "M91-INJECT-SENTINEL"

WIRED = "原生桌面详细编辑框：两入口接线"
CAPTURE_TEST = "test_captured_desktop_passes_nodes_and_descriptor_to_dialog"
DETAIL_TEST = "test_edit_element_desktop_uses_node_dialog_and_saves_full_selector"

INJECTIONS = [
    (
        "drop-controller-nodes", CONTROLLER,
        '            nodes=model.get("nodes"),\n',
        "            # " + SENTINEL + "\n",
        ("unit", "contract"), {WIRED, CAPTURE_TEST},
    ),
    (
        "drop-detail-descriptor", API,
        '            show_args["descriptor"] = doc\n',
        "            # " + SENTINEL + "\n",
        ("rpc", "contract"), {WIRED, DETAIL_TEST},
    ),
    (
        "drop-initial-preview-refresh", DIALOG,
        "        win32gui.SendMessage(self._name_edit, win32con.EM_SETSEL, 0, -1)\n"
        "        self._refresh_desktop_preview()\n",
        "        win32gui.SendMessage(self._name_edit, win32con.EM_SETSEL, 0, -1)\n"
        "        # " + SENTINEL + "\n",
        ("contract",), {WIRED},
    ),
]

RUNNERS = {
    "contract": [str(PY), ".harness/scripts/check_element_pick_contract.py"],
    "unit": [str(PY), "-m", "pytest", "tests/unit/test_capture_kit.py", "-q",
             "--basetemp=D:/Temp/pt_m91neg"],
    "rpc": [str(PY), "-m", "pytest", "apps/xhs-workbench/tests/test_element_edit_rpc.py",
            "-q", "--basetemp=D:/Temp/pt_m91neg"],
}


def run(which: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update({"TMPDIR": "D:/Temp", "TEMP": "D:/Temp", "TMP": "D:/Temp",
                "PYTHONPATH": str(ROOT / "src")})
    return subprocess.run(
        RUNNERS[which], cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env, timeout=600,
    )


def failed_names(which: str, output: str) -> set[str]:
    names: set[str] = set()
    for line in output.splitlines():
        if which == "contract" and line.startswith("FAIL "):
            names.add(line[5:].split(" — ")[0].strip())
        elif line.startswith("FAILED "):
            names.add(line.split("::")[-1].split()[0].strip())
    return names


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def main() -> int:
    paths = (CONTROLLER, API, DIALOG)
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
        for name, path, old, new, which, expected in INJECTIONS:
            runners = which if isinstance(which, (list, tuple)) else (which,)
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
        for path, raw in saved.items():
            path.write_bytes(raw)

    md5_ok = True
    for path, digest in baselines.items():
        after = md5(path)
        if after != digest:
            print(f"[FAIL] {path.name} 还原后 md5 不一致 {after} != {digest}")
            md5_ok = False
    print("所有文件 md5 逐字节还原一致" if md5_ok else "还原核对失败")
    ok &= md5_ok

    for which in RUNNERS:
        back = run(which)
        print(f"[restored {which}] rc={back.returncode}")
        ok &= back.returncode == 0
    print("\n" + ("ALL PASS" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
