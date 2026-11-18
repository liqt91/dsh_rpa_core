"""M81 判据负向验证：逐条注入破坏 → 精确红 → 逐字节还原核 md5。

覆盖本轮新增/改动的接线判据：
1. usedScale 命中即回（不能只在要 --shot 时才给）
2. --shot 必须真的产出截图回执
3. 门面 to_dict 必须把 usedScale 穿给 CLI/GUI
4. 截图缺窗口原点时只能丢回执，不能硬造一个偏移错的框
5. 命中框必须是「屏幕矩形 - 窗口原点」而不是加法
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = ROOT / ".venv/Scripts/python.exe"
STATE = ROOT / ".harness/spike/_m81_nv_state.json"
SENTINEL = "# M81-NV-INJECTED"
BASE_TEMP = "D:/Temp/pytest-m81-nv"

_T_IMAGE = "tests/contract/test_image_verification.py"

CASES = [
    {
        "name": "usedScale 不再无条件回",
        "path": "src/rpa_core/capture/image_verification.py",
        "old": '            "usedScale": float(match.used_scale),\n',
        "new": "",
        "test": f"{_T_IMAGE}::test_image_verify_hit_shape_matches_desktop",
    },
    {
        "name": "--shot 不再产出截图回执",
        "path": "src/rpa_core/capture/image_verification.py",
        "old": (
            "            shot_meta = _shot_from(shot, rect)\n"
            "            if shot_meta:\n"
            '                result["shot"] = shot_meta\n'
        ),
        "new": "            shot_meta = None\n",
        "test": f"{_T_IMAGE}::test_image_verify_want_shot_returns_used_scale_and_box",
    },
    {
        "name": "门面 to_dict 吞掉 usedScale",
        "path": "src/rpa_core/capture/verification.py",
        "old": (
            '        used_scale = self.raw.get("usedScale")\n'
            "        if isinstance(used_scale, (int, float)) and not isinstance(used_scale, "
            "bool):\n"
            '            out["usedScale"] = float(used_scale)\n'
        ),
        "new": "        used_scale = None\n",
        "test": f"{_T_IMAGE}::test_facade_passes_used_scale_through_to_dict",
    },
    {
        "name": "缺窗口原点也硬造 shot",
        "path": "src/rpa_core/capture/image_verification.py",
        "old": (
            '        origin = shot.get("windowOrigin")\n'
            "        if not isinstance(origin, (list, tuple)) or len(origin) != 2:\n"
            "            return None\n"
        ),
        "new": (
            '        origin = shot.get("windowOrigin") or [0, 0]\n'
            "        if False:\n"
            "            return None\n"
        ),
        "test": f"{_T_IMAGE}::test_image_verify_want_shot_without_origin_keeps_hit_and_skips_shot",
    },
    {
        "name": "命中框改用加法（原点符号反了）",
        "path": "src/rpa_core/capture/screen_shot.py",
        "old": '    return {"x": left - ox, "y": top - oy, "width": width, "height": height}\n',
        "new": '    return {"x": left + ox, "y": top + oy, "width": width, "height": height}\n',
        "test": f"{_T_IMAGE}::test_image_verify_want_shot_returns_used_scale_and_box",
    },
]


def restore_pending() -> None:
    if not STATE.exists():
        return
    state = json.loads(STATE.read_text(encoding="utf-8"))
    for rel, info in state["files"].items():
        path = ROOT / rel
        if SENTINEL not in path.read_text(encoding="utf-8"):
            continue
        path.write_text(info["original"], encoding="utf-8", newline="\n")
        now = hashlib.md5(path.read_bytes()).hexdigest()
        assert now == info["md5"], f"还原后 md5 不一致: {rel}"
    STATE.unlink()


def run_test(nodeid: str) -> tuple[int, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = ""
    env["TMPDIR"] = "D:\\Temp"
    env["TEMP"] = "D:\\Temp"
    env["TMP"] = "D:\\Temp"
    try:
        proc = subprocess.run(
            [str(PY), "-m", "pytest", "-v", nodeid, f"--basetemp={BASE_TEMP}"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=180,
        )
        return proc.returncode, proc.stdout + proc.stderr
    except subprocess.TimeoutExpired as exc:
        return 124, f"TIMEOUT: {exc}"


def counts(output: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for key in ("passed", "failed", "error", "errors", "skipped"):
        match = re.search(rf"\b(\d+)\s+{key}\b", output)
        if match:
            result[key] = int(match.group(1))
    return result


def main() -> int:
    restore_pending()
    results: list[str] = []
    state: dict[str, dict] = {"files": {}}

    try:
        for case in CASES:
            path = ROOT / case["path"]
            original = path.read_text(encoding="utf-8")
            assert SENTINEL not in original, f"哨兵未清理: {case['path']}"
            assert original.count(case["old"]) == 1, f"锚点不唯一: {case['name']}"

            rc, out = run_test(case["test"])
            base = counts(out)
            if rc != 0 or base.get("passed", 0) < 1:
                results.append(f"FAIL|{case['name']}|对照跑未绿 rc={rc} {base}")
                continue

            before_md5 = hashlib.md5(path.read_bytes()).hexdigest()
            state["files"][case["path"]] = {"original": original, "md5": before_md5}
            STATE.write_text(
                json.dumps(state, ensure_ascii=False), encoding="utf-8", newline="\n"
            )
            # ★ 必须**每次迭代就地还原**：同一个文件可能有多个注入点，若只在整轮
            # finally 里还原，第二个注入点会踩到第一个留下的哨兵（M81 实锤）。
            try:
                path.write_text(
                    original.replace(case["old"], case["new"]) + f"\n{SENTINEL}\n",
                    encoding="utf-8",
                    newline="\n",
                )
                assert SENTINEL in path.read_text(encoding="utf-8")

                rc, out = run_test(case["test"])
                got = counts(out)
                failed = got.get("failed", 0)
                errors = got.get("error", 0) + got.get("errors", 0)
                ok = rc != 0 and failed == 1 and errors == 0
                results.append(
                    f"{'PASS' if ok else 'FAIL'}|{case['name']}|rc={rc} "
                    f"failed={failed} errors={errors} passed={got.get('passed', 0)}"
                )
                if not ok:
                    results.append(out[-1200:])
            finally:
                path.write_text(original, encoding="utf-8", newline="\n")
                assert hashlib.md5(path.read_bytes()).hexdigest() == before_md5, (
                    f"还原后 md5 不一致: {case['path']}"
                )
    finally:
        for rel, info in state["files"].items():
            path = ROOT / rel
            if SENTINEL in path.read_text(encoding="utf-8"):
                path.write_text(info["original"], encoding="utf-8", newline="\n")
            now = hashlib.md5(path.read_bytes()).hexdigest()
            assert now == info["md5"], f"还原后 md5 不一致: {rel}"
        if STATE.exists():
            STATE.unlink()

    print("\n".join(results))
    failures = [line for line in results if line.startswith("FAIL")]
    print(f"M81 NEGATIVE: {len(CASES) - len(failures)} pass / {len(failures)} fail")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
