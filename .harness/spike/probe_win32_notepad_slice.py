"""探针：复跑 examples/windows-desktop 切片并 dump 失败节点与事件流。

背景（2026-09-22）：`RPA_DESKTOP_E2E=1 pytest tests/e2e`（整目录 / 多文件组合）里
`test_windows_desktop_vertical_slice` 会红成 `assert 'failed' == 'succeeded'`，
但该用例单跑即过。本探针用于把「失败在哪一步、错误码是什么」取出来——用例本身
只断言了 status，拿不到细节。

用法（在仓库根）：

    RPA_DESKTOP_E2E=1 .venv/Scripts/python.exe .harness/spike/probe_win32_notepad_slice.py

注意：会真的启记事本、弹窗、抢焦点。
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from rpa_core.catalog import load_catalog  # noqa: E402
from rpa_core.compiler import WorkflowCompiler  # noqa: E402
from rpa_core.executors import ExecutorRegistry, Win32DesktopExecutor  # noqa: E402
from rpa_core.model.workflow import Workflow  # noqa: E402
from rpa_core.runtime import Orchestrator  # noqa: E402


def _kill_notepad() -> None:
    subprocess.run(
        ["taskkill", "/F", "/IM", "notepad.exe"],
        capture_output=True,
        check=False,
    )


async def run(runs_dir: Path):
    workflow = Workflow.model_validate_json(
        (ROOT / "examples" / "windows-desktop" / "workflow.json").read_text(encoding="utf-8")
    )
    inputs = {"filePath": str(ROOT / "test.txt")}

    _kill_notepad()
    proc = await asyncio.to_thread(subprocess.Popen, ["notepad.exe"])
    try:
        catalog = load_catalog(ROOT / "commands")
        plan = WorkflowCompiler(catalog).compile(workflow, {"desktop.control"})
        registry = ExecutorRegistry({"desktop.win32": Win32DesktopExecutor()})
        try:
            runner = Orchestrator(catalog, registry, runs_dir)
            return await runner.run(plan, inputs)
        finally:
            await registry.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        _kill_notepad()


def _dump(result, runs_dir: Path) -> None:
    print("=" * 72)
    print(f"platform   = {sys.platform}")
    print(f"RPA_DESKTOP_E2E = {os.environ.get('RPA_DESKTOP_E2E')!r}")
    print(f"status     = {result.status.value}")
    print(f"return     = {result.return_value!r}")
    print(f"error      = {json.dumps(result.error, ensure_ascii=False, indent=2, default=str)}")

    events_path = runs_dir / result.run_id / "events.jsonl"
    print(f"events     = {events_path}")
    if not events_path.is_file():
        print("(no events file)")
        return
    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line]
    print("-" * 72)
    for event in events:
        etype = event.get("type")
        if etype == "stepStarted":
            print(f"  start  {event.get('nodeId')}  ({event.get('commandId')})")
        elif etype == "stepSucceeded":
            print(f"  ok     {event.get('nodeId')}  durationMs={event.get('durationMs')}")
        elif etype == "stepFailed":
            print(f"  FAIL   {event.get('nodeId')}  ({event.get('commandId')})")
            print(json.dumps(event, ensure_ascii=False, indent=4, default=str)[:4000])
        else:
            print(f"  {etype}  {json.dumps({k: v for k, v in event.items() if k != 'type'}, ensure_ascii=False, default=str)[:400]}")


def dump_events_file(path: Path) -> None:
    """`--dump <events.jsonl>`：只解析已存在的运行目录，不再碰桌面。

    用途：pytest 里 `tmp_path` 会保留最近三次，失败运行的 events.jsonl 还在盘上，
    用它把「哪个节点失败、错误码是什么」取出来（用例本身只断言了 status）。
    """
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    for event in events:
        etype = event.get("type")
        node_id = event.get("node_id")
        payload = event.get("payload") or {}
        if etype in ("stepAttemptStarted", "stepCompleted", "stepFailed", "stepStarted"):
            print(f"{etype:20} {str(node_id):22} "
                  f"{json.dumps(payload, ensure_ascii=False, default=str)[:1200]}")
        elif etype == "runFinished":
            print(f"{etype:20} {'':22} {json.dumps(payload, ensure_ascii=False, default=str)[:1200]}")
        else:
            print(f"{etype:20} {str(node_id):22}")


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--dump":
        dump_events_file(Path(sys.argv[2]))
        return 0
    runs_dir = Path(tempfile.mkdtemp(prefix="probe-win32-notepad-"))
    result = asyncio.run(run(runs_dir))
    _dump(result, runs_dir)
    return 0 if result.status.value == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
