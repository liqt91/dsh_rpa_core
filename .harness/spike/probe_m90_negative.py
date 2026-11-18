"""M90 负向验证：桌面元素详细编辑框的新判据必须真的拦得住破坏。

注入都针对这次新写的判据，期望失败集按推理写、不照抄实际：
1. 模型不带 nodes        → RPC 测试红（桌面编辑退回 JSON 文本框）；
2. path 级恒不选中       → 三个 round-trip / 勾选用例红（祖先链被悄悄丢掉）；
3. 忽略匹配方式冲突      → 冲突用例红（界面显示生效、运行期随机选一个）；
4. compose 写死空 locator → RPC 用例红（候选与未编辑字段被抹掉）；
5. 编辑框不留住 captureId → 静态契约 + 渲染层两条红（重名/重组都拿不到会话）。

所有写回走 write_bytes（write_text 会转 CRLF），注入锚点要求唯一。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"D:\Users\Administrator\Documents\代码\rpa_core")
MAIN = ROOT / ".harness/demo/ppx_probe/app/gui/src/main.js"
API = ROOT / ".harness/demo/ppx_probe/app/api/api.py"
EDITING = ROOT / "src/rpa_core/capture_kit/element_editing.py"
DESKTOP = ROOT / "src/rpa_core/capture_kit/desktop_editing.py"
PY = ROOT / ".venv/Scripts/python.exe"
SENTINEL = "M90-INJECT-SENTINEL"

PICK_CONTRACT = "契约层：桌面编辑也走内核模型与重组，前端不拼 locator"
PICK_RENDER = "渲染层：桌面模型渲染成层级树与属性表，勾选让后端重组 selector"

INJECTIONS = [
    (
        "drop-desktop-nodes", EDITING,
        '        if actual_kind == "desktop":\n'
        '            model["nodes"] = desktop_editing.nodes_from_descriptor(descriptor)\n',
        "        # " + SENTINEL + "\n",
        "rpc", {"test_element_edit_model_desktop_carries_nodes"},
    ),
    (
        "path-never-selected", DESKTOP,
        '            "type": str(step.get("controlType") or ""),\n'
        '            "name": str(step.get("name") or ""),\n'
        '            "selected": True,\n',
        '            "type": str(step.get("controlType") or ""),\n'
        '            "name": str(step.get("name") or ""),\n'
        '            "selected": False,  # ' + SENTINEL + "\n",
        "desktop", {
            "test_round_trip_preserves_candidates_and_unknown_fields",
            "test_compose_honours_attribute_checkboxes",
            "test_compose_honours_level_checkboxes",
        },
    ),
    (
        "ignore-mode-conflict", DESKTOP,
        "    if len(modes) > 1:\n",
        "    if False:  # " + SENTINEL + "\n",
        "desktop", {"test_compose_rejects_conflicting_match_modes"},
    ),
    (
        "compose-empty-locator", API,
        "            return {\"ok\": True, \"selector\": json.dumps(selector, ensure_ascii=False)}\n",
        "            return {\"ok\": True, \"selector\": '{\"locator\": {}}'}  # " + SENTINEL + "\n",
        "rpc", {"test_compose_desktop_rpc_keeps_candidates_and_honours_edits"},
    ),
    (
        "drop-dialog-capture-id", MAIN,
        "  dialogCaptureId = options.captureId || null\n",
        "  dialogCaptureId = null  // " + SENTINEL + "\n",
        "pick", {PICK_CONTRACT, PICK_RENDER},
    ),
]

RUNNERS = {
    "pick": ["node", "scripts/check_element_pick_ui.mjs"],
    "desktop": [str(PY), "-m", "pytest", "tests/unit/test_desktop_editing.py", "-q",
                "--basetemp=D:/Temp/pt_m90neg"],
    "rpc": [str(PY), "-m", "pytest", "apps/xhs-workbench/tests/test_element_edit_rpc.py", "-q",
            "--basetemp=D:/Temp/pt_m90neg"],
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
        if which == "pick" and line.startswith("FAIL "):
            names.add(line[5:].split(" — ")[0].strip())
        elif line.startswith("FAILED "):
            names.add(line.split("::")[-1].split()[0].strip())
    return names


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def main() -> int:
    paths = (MAIN, API, EDITING, DESKTOP)
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
            original = path.read_text(encoding="utf-8")
            saved.setdefault(path, original.encode("utf-8"))
            if original.count(old) != 1:
                print(f"[FAIL] {name}: 锚点出现 {original.count(old)} 次（要求 1）")
                ok = False
                continue
            path.write_bytes(original.replace(old, new, 1).encode("utf-8"))
            try:
                bad = run(which)
            finally:
                path.write_bytes(original.encode("utf-8"))
            got = failed_names(which, bad.stdout)
            tail = bad.stdout.strip().splitlines()[-1:] if bad.stdout.strip() else [""]
            print(f"\n[{name}] rc={bad.returncode} 失败集={sorted(got)}")
            print(f"  期望={sorted(expected)}  {tail[0][:80]}")
            if got != expected:
                print("  [FAIL] 失败集与期望不等")
                ok = False
            if " error" in bad.stdout or bad.returncode not in (0, 1):
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
