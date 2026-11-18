"""计时用例放宽绝对预算后的负向验证：语义未松，仍能抓住真实回归。

M79 收口把两个 50/100ms 级计时用例的绝对预算放宽（机器负载下 calls=0 会假红），
比例关系不变。这里注入两种真实破坏，确认放宽后判据仍能精确红：
1. 工作流 deadline 不再管住 attempt → pause 用例必须红；
2. retry backoff 直接跳过等待 → 第二个 attempt 真的发生，用例必须红。

跑法：./.venv/Scripts/python.exe .harness/spike/probe_timing_budget_negative.py
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
STATE = ROOT / ".harness" / "spike" / "_timing_nv_state.json"
SENTINEL = "# TIMING-NV-INJECTED"

T_PAUSE = (
    "tests/unit/test_pause.py::"
    "test_workflow_deadline_beats_pending_pause_mid_attempt"
)
T_RETRY = (
    "tests/unit/test_runtime_correctness.py::"
    "test_retry_backoff_cannot_exceed_workflow_deadline"
)

CASES = [
    {
        "name": "两层 deadline 都不再管住 attempt",
        "injects": [
            [
                (
                    "            async with asyncio.timeout_at(workflow_deadline):\n"
                    "                await execution"
                ),
                "            await execution",
            ],
            [
                "                timeout=attempt_timeout,",
                "                timeout=max(attempt_timeout, 10.0),",
            ],
        ],
        "test": T_PAUSE,
    },
    {
        "name": "retry backoff 被跳过",
        "injects": [
            [
                (
                    "    async def _sleep_until_retry(\n"
                    "        delay: float, cancellation: asyncio.Event, workflow_deadline: float\n"
                    "    ) -> None:\n"
                    "        remaining = workflow_deadline - time.monotonic()"
                ),
                (
                    "    async def _sleep_until_retry(\n"
                    "        delay: float, cancellation: asyncio.Event, workflow_deadline: float\n"
                    "    ) -> None:\n"
                    "        return\n"
                    "        remaining = workflow_deadline - time.monotonic()"
                ),
            ],
        ],
        "test": T_RETRY,
    },
]

TARGET = ROOT / "src" / "rpa_core" / "runtime" / "orchestrator.py"


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def run_test(nodeid: str) -> tuple[int, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = ""
    basetemp = (Path(os.environ.get("TEMP", ROOT)) / "pytest-timing-negative").as_posix()
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


def main() -> int:
    original = TARGET.read_bytes()
    digest = md5(original)
    text = original.decode("utf-8")
    results: list[str] = []

    try:
        for case in CASES:
            for old, _new in case["injects"]:
                assert text.count(old) == 1, f"{case['name']}: 注入锚点不唯一"
            assert SENTINEL not in text, f"{case['name']}: 上次注入未清理"
            rc, out = run_test(case["test"])
            base = counts(out)
            if rc != 0 or base.get("passed", 0) < 1:
                results.append(f"FAIL|{case['name']}|对照跑未绿 rc={rc} {base}")
                continue

            STATE.write_text(
                json.dumps({"md5_before": digest, "sentinel": SENTINEL}, ensure_ascii=False),
                encoding="utf-8", newline="\n",
            )
            injected = text
            for old, new in case["injects"]:
                injected = injected.replace(old, new)
            injected += f"\n{SENTINEL}\n"
            TARGET.write_bytes(injected.encode("utf-8"))
            try:
                assert SENTINEL in TARGET.read_text(encoding="utf-8"), "注入未写入"
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
                TARGET.write_bytes(original)
                assert md5(TARGET.read_bytes()) == digest, "还原 md5 不一致"
                assert SENTINEL not in TARGET.read_text(encoding="utf-8"), "哨兵残留"
                STATE.unlink(missing_ok=True)
    finally:
        TARGET.write_bytes(original)
        STATE.unlink(missing_ok=True)

    for row in results:
        print(row)
    failed = sum(1 for row in results if row.startswith("FAIL"))
    print(f"TIMING BUDGET NEGATIVE: {len(results) - failed} pass / {failed} fail")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
