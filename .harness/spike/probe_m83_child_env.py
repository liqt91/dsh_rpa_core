"""M83 真机探针：宿主只改 ``sys.path`` 时，桌面捕获子进程还能不能起来。

复现 2026-11-17 维护者报障的宿主形态：PPX 工作台用 ``sys.path.insert`` 挂 ``src/``，
进程环境里没有 ``PYTHONPATH``。修复前 ``DesktopCaptureSession`` spawn 的 agent
一律 ``ModuleNotFoundError`` 秒退（界面表现为「微信/桌面没有框选」）。

用法（**必须用 ppx 那套解释器**，.venv 是 editable 安装、在哪儿都能 import，
拿它测等于恒绿）::

    "C:/Users/Administrator/AppData/Roaming/uv/tools/ppx-py/Scripts/python.exe" \\
        .harness/spike/probe_m83_child_env.py

判据（任一不满足即失败）：
  1. 子进程没有在 spawn 后立刻死掉（``error`` 为空）；
  2. agent 侧 trace 里出现 ``main_enter`` 且 ``mode == "hover"``；
  3. ``pick`` 走的是**超时**正常返回（模板里的 3 秒预算），不是 crash_info。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

TRACE = Path(os.environ.get("TEMP") or "D:/Temp") / "rpa-capture-trace.log"


def _trace_tail_hover_enters(offset: int) -> list[dict]:
    if not TRACE.exists():
        return []
    rows: list[dict] = []
    with TRACE.open(encoding="utf-8", errors="replace") as fh:
        fh.seek(offset)
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return [
        r
        for r in rows
        if r.get("side") == "agent"
        and r.get("mark") == "main_enter"
    ]


def main() -> int:
    print("解释器:", sys.executable)
    print("PYTHONPATH 环境变量:", os.environ.get("PYTHONPATH") or "(空)")
    from rpa_core.capture.desktop import DesktopCaptureSession

    offset = TRACE.stat().st_size if TRACE.exists() else 0
    session = DesktopCaptureSession(
        hover=True, hybrid=True, image=True, timeout_seconds=3
    )
    print("available:", session.available)
    payload = session.pick(timeout_seconds=8)
    session.close()

    fails: list[str] = []
    if payload.get("error"):
        fails.append(f"子进程失败：{payload.get('error')} / {payload.get('stderrTail')}")
    if not payload.get("timeout"):
        fails.append(f"pick 未走超时分支：{payload}")

    enters = _trace_tail_hover_enters(offset)
    modes = [e.get("mode") for e in enters]
    if "hover" not in modes:
        fails.append(f"agent 没进 hover 模式（main_enter.mode={modes}）")

    print("pick 结果:", {k: payload.get(k) for k in ("timeout", "error")})
    print("agent main_enter:", modes)
    if fails:
        print("\nFAIL")
        for item in fails:
            print(" -", item)
        return 1
    print("\nPASS：桌面腿在「只改 sys.path」的宿主里正常启动")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
