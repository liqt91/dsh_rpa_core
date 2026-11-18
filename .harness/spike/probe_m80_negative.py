"""M80 判据负向验证：逐条注入破坏 → 精确红 → 逐字节还原核 md5。

覆盖五条判据：
1. grab_window 必须带 DPI provenance
2. 没有 DPI 的截图绝不产出 V2 proposal
3. 跨 DPI 必须跑多尺度，不能只跑原尺寸
4. V1 模板必须在 verify_element 用户路径显式拒绝
5. 静态 selector_errors 必须拒绝 V1 模板、接受 V2 image locator
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
STATE = ROOT / ".harness/spike/_m80_nv_state.json"
SENTINEL = "# M80-NV-INJECTED"
BASE_TEMP = "D:/Temp/pytest-m80-nv"

CASES = [
    {
        "name": "grab_window 不再回传 DPI provenance",
        "path": "src/rpa_core/capture/screen_shot.py",
        "old": '        "dpi": dpi_info.get("dpi"),\n        "scale": dpi_info.get("scale"),\n',
        "new": "",
        "test": (
            "tests/contract/test_screen_shot.py::"
            "test_grab_window_returns_origin_and_image_size"
        ),
    },
    {
        "name": "缺 DPI 时 proposal 猜 96/1.0",
        "path": "src/rpa_core/capture/image_proposal.py",
        "old": (
            "    except (KeyError, TypeError, ValueError):\n"
            "        return None\n"
            "    if capture_dpi <= 0 or capture_scale <= 0:\n"
        ),
        "new": (
            "    except (KeyError, TypeError, ValueError):\n"
            "        capture_dpi = 96\n"
            "        capture_scale = 1.0\n"
            "    if capture_dpi <= 0 or capture_scale <= 0:\n"
        ),
        "test": (
            "tests/contract/test_image_capture_proposal.py::"
            "test_image_proposal_refuses_shot_without_dpi_provenance"
        ),
    },
    {
        "name": "跨 DPI 只跑原尺寸（多尺度被摘掉）",
        "path": "src/rpa_core/capture/image_locator.py",
        "old": "    if ratio is None or ratio <= 0 or abs(ratio - 1.0) < 1e-6:\n",
        "new": "    if True:\n",
        "test": (
            "tests/contract/test_image_locator.py::"
            "test_template_cross_dpi_picks_best_scale_and_current_rect"
        ),
    },
    {
        "name": "verify_element 不再拦 V1 模板",
        "path": "src/rpa_core/capture/image_verification.py",
        "old": (
            "        shape_errors = image_locator_errors(locator)\n"
            "        if shape_errors:\n"
            "            return {\"error\": shape_errors[0]}\n"
        ),
        "new": "        shape_errors = []\n",
        "test": (
            "tests/contract/test_image_verification.py::"
            "test_old_template_is_explicit_error_and_never_migrated"
        ),
    },
    {
        "name": "静态校验不再要求 templateSchema",
        "path": "src/rpa_core/capture/image_guard.py",
        "old": (
            "    if request.get(\"templateSchema\") != TEMPLATE_SCHEMA:\n"
            "        errors.append(\"template-schema-requires-recapture\")\n"
        ),
        "new": "    if False:\n        errors.append(\"template-schema-requires-recapture\")\n",
        "test": (
            "tests/unit/test_element_descriptor.py::"
            "test_desktop_selector_rejects_v1_image_template"
        ),
    },
]


def md5(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def restore_pending() -> None:
    if not STATE.exists():
        return
    state = json.loads(STATE.read_text(encoding="utf-8"))
    for rel, info in state["files"].items():
        path = ROOT / rel
        text = path.read_text(encoding="utf-8")
        if SENTINEL not in text:
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
            state["files"][case["path"]] = {
                "original": original,
                "md5": before_md5,
            }
            STATE.write_text(
                json.dumps(state, ensure_ascii=False),
                encoding="utf-8",
                newline="\n",
            )
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
        for rel, info in state["files"].items():
            path = ROOT / rel
            text = path.read_text(encoding="utf-8")
            if SENTINEL in text:
                path.write_text(info["original"], encoding="utf-8", newline="\n")
            now = hashlib.md5(path.read_bytes()).hexdigest()
            assert now == info["md5"], f"还原后 md5 不一致: {rel}"
        if STATE.exists():
            STATE.unlink()

    print("\n".join(results))
    failures = [line for line in results if line.startswith("FAIL")]
    print(f"M80 FIX NEGATIVE: {len(CASES) - len(failures)} pass / {len(failures)} fail")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
