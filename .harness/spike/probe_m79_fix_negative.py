"""M79 生产修复的负向验证：逐条注入破坏，确认新增判据精确红，再逐字节还原。

覆盖三处 M79 真机暴露出来的接线缺陷：
1. 门面把 metadata.windowHandle 交给默认桌面 verifier；
2. 生产 finder 把 JSON dict 归一化成 DesktopLocator；
3. 显式图像模式下 UIA ElementFromPoint 拒绝访问不阻断 proposal。

跑法：./.venv/Scripts/python.exe .harness/spike/probe_m79_fix_negative.py
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
STATE = ROOT / ".harness" / "spike" / "_m79_nv_state.json"
SENTINEL = "# M79-NV-INJECTED"

T_EXPECTED = (
    "tests/contract/test_unified_verification.py::"
    "test_default_desktop_verifier_receives_captured_window"
)
T_FINDER = (
    "tests/contract/test_desktop_verification.py::"
    "test_default_finder_normalizes_raw_dict_before_executor"
)
T_IMAGE = (
    "tests/contract/test_image_capture_proposal.py::"
    "test_capture_at_explicit_image_survives_uia_access_denied"
)

CASES = [
    {
        "name": "expected_hwnd 接线",
        "path": "src/rpa_core/capture/verification.py",
        "old": "else _default_desktop_verifier(expected_hwnd=_expected_hwnd(element))",
        "new": "else _default_desktop_verifier()",
        "test": T_EXPECTED,
    },
    {
        "name": "dict → DesktopLocator 归一化",
        "path": "src/rpa_core/capture/desktop_verification.py",
        "old": (
            "    resolved = DesktopLocator.model_validate(locator) "
            "if isinstance(locator, dict) else locator\n"
            "    return DesktopExecutor._find(window, resolved)"
        ),
        "new": "    return DesktopExecutor._find(window, locator)",
        "test": T_FINDER,
    },
    {
        "name": "显式图像吞 UIA 拒绝访问",
        "path": "src/rpa_core/capture/desktop_agent.py",
        "old": (
            "        try:\n"
            "            info = _element_from_point(x, y)\n"
            "        except Exception as exc:  # noqa: BLE001 - "
            "仅显式图像模式吞，语义模式绝不吞\n"
            "            _trace(\"agent\", \"image_uia_unavailable\", error=type(exc).__name__)\n"
        ),
        "new": "        info = _element_from_point(x, y)\n",
        "test": T_IMAGE,
    },
]


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def run_test(nodeid: str) -> tuple[int, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = ""
    basetemp = (Path(os.environ.get("TEMP", ROOT)) / "pytest-m79-negative").as_posix()
    proc = subprocess.run(
        [str(PY), "-m", "pytest", "-v", nodeid, f"--basetemp={basetemp}"],
        cwd=ROOT, env=env, capture_output=True, text=True,
        encoding="utf-8", timeout=180, check=False,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def counts(output: str) -> dict[str, int]:
    found: dict[str, int] = {}
    for label, pattern in (
        ("passed", r"(\d+)\s+passed"),
        ("failed", r"(\d+)\s+failed"),
        ("error", r"(\d+)\s+errors?"),
    ):
        match = re.search(pattern, output)
        found[label] = int(match.group(1)) if match else 0
    return found


def write_state(data: dict) -> None:
    STATE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")


def main() -> int:
    results: list[str] = []
    for case in CASES:
        path = ROOT / case["path"]
        original = path.read_bytes()
        digest = md5(original)
        text = original.decode("utf-8")
        assert text.count(case["old"]) == 1, f"{case['name']}: 注入锚点不唯一"
        assert SENTINEL not in text, f"{case['name']}: 上一次注入未清理"

        rc, out = run_test(case["test"])
        base = counts(out)
        if rc != 0 or base.get("passed", 0) < 1 or base.get("failed", 0):
            results.append(f"FAIL|{case['name']}|对照跑未绿 rc={rc} {base}")
            continue

        write_state({
            "path": str(path.relative_to(ROOT)).replace("\\", "/"),
            "md5_before": digest,
            "sentinel": SENTINEL + ": " + case["name"],
        })
        injected = text.replace(case["old"], case["new"]) + f"\n{SENTINEL}: {case['name']}\n"
        try:
            path.write_bytes(injected.encode("utf-8"))
            assert SENTINEL in path.read_text(encoding="utf-8"), "注入未写入"
            rc, out = run_test(case["test"])
            hit = counts(out)
            ok = (
                rc != 0
                and hit.get("failed", 0) == 1
                and hit.get("error", 0) == 0
                and case["test"].split("::")[-1] in out
            )
            results.append(
                f"{'PASS' if ok else 'FAIL'}|{case['name']}|对照={base} 注入={hit} rc={rc}"
            )
        finally:
            path.write_bytes(original)
            restored = path.read_bytes()
            assert md5(restored) == digest, f"{case['name']}: 还原 md5 不一致"
            assert SENTINEL not in restored.decode("utf-8"), f"{case['name']}: 哨兵残留"
            STATE.unlink(missing_ok=True)

    for row in results:
        print(row)
    failed = sum(1 for row in results if row.startswith("FAIL"))
    print(f"M79 FIX NEGATIVE: {len(results) - failed} pass / {failed} fail")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
