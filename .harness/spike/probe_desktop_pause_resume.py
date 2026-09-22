"""探针（M38 S2.1）：桌面通道「运行 → 暂停 → 继续」的真实行为。

按本仓纪律：**期望值只能来自实测**。这里要测的是三件事——

1. 真机桌面流程能不能在节点边界被暂停（暂停是否干净收口、`completedSteps` 到哪）；
2. 暂停落地后**跨进程续跑**（`rpa-core resume`，新进程、新执行器实例）会不会丢桌面会话；
3. 如果丢了，报的**确切错误码与 details** 是什么（后续 E2E 的断言要照着写）。

跑法：`uv run python .harness/spike/probe_desktop_pause_resume.py`
（会弹窗抢前台；这在探针里是必要的——测的就是真窗口）
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from rpa_core.control_channel import request_pause  # noqa: E402

TITLE = "RPA Core Desktop Demo"
SLOW_TEXT = "hello rpa, this is the desktop pause probe"
SLOW_INTERVAL_MS = 80  # 41 字 × 80ms ≈ 3.3s 的确定性慢节点（给暂停留出落点）

_CSC = (
    Path(os.environ.get("WINDIR", r"C:\Windows"))
    / "Microsoft.NET"
    / "Framework64"
    / "v4.0.30319"
    / "csc.exe"
)


def log(step: str, payload: object) -> None:
    print(f"[{step}] {payload}", flush=True)


def compile_fixture(target_dir: Path) -> Path:
    exe = target_dir / "RpaCoreDesktopDemo.exe"
    subprocess.run(
        [
            str(_CSC),
            "/nologo",
            "/target:winexe",
            f"/out:{exe}",
            "/r:System.Windows.Forms.dll",
            "/r:System.Drawing.dll",
            str(ROOT / "testapps" / "desktop" / "Program.cs"),
        ],
        check=True,
        capture_output=True,
    )
    return exe


def wait_for_window(title: str, timeout: float = 15.0) -> int:
    find_window = ctypes.windll.user32.FindWindowW
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        hwnd = find_window(None, title)
        if hwnd:
            return int(hwnd)
        time.sleep(0.2)
    raise TimeoutError(f"window did not appear: {title}")


def build_workflow(prefix: str = "desktop.") -> dict:
    """暂停点刻意设在 `typeGreeting` 之后：

    - `findSubmit` 在暂停**之前**完成 → 它的 outputs.elementId 进了快照，但
      `session.elements` 缓存是纯内存的，续跑时能不能用是本次要测的第二个缺口；
    - `attachMain` 在暂停之前 → 续跑后 `clickSubmit` 必须拿回同一个会话（第一个缺口）。
    """
    sid = "${steps.attachMain.outputs.sessionId}"
    return {
        "schema_version": "1.0",
        "id": "desktop-pause-resume-probe",
        "name": "desktop pause/resume probe",
        "inputs": {"title": TITLE},
        "root": {
            "type": "sequence",
            "id": "root",
            "children": [
                {
                    "type": "action",
                    "id": "attachMain",
                    "command": f"{prefix}attachWindow",
                    "with": {"title": "${inputs.title}", "timeoutMs": 5000},
                },
                {
                    "type": "action",
                    "id": "findInput",
                    "command": f"{prefix}findElement",
                    "with": {
                        "sessionId": sid,
                        "locator": {"automationId": "queryInput", "controlType": "Edit"},
                    },
                },
                {
                    "type": "action",
                    "id": "findSubmit",
                    "command": f"{prefix}findElement",
                    "with": {
                        "sessionId": sid,
                        "locator": {"automationId": "submitButton", "controlType": "Button"},
                    },
                },
                {
                    "type": "action",
                    "id": "typeGreeting",
                    "command": f"{prefix}input",
                    "with": {
                        "sessionId": sid,
                        "elementId": "${steps.findInput.outputs.elementId}",
                        "text": SLOW_TEXT,
                        "keyIntervalMs": SLOW_INTERVAL_MS,
                    },
                },
                {
                    "type": "action",
                    "id": "clickSubmit",
                    "command": f"{prefix}click",
                    "with": {
                        "sessionId": sid,
                        "elementId": "${steps.findSubmit.outputs.elementId}",
                    },
                },
                {
                    "type": "action",
                    "id": "findResult",
                    "command": f"{prefix}findElement",
                    "with": {"sessionId": sid, "locator": {"automationId": "resultText"}},
                },
                {
                    "type": "action",
                    "id": "readResult",
                    "command": f"{prefix}getText",
                    "with": {
                        "sessionId": sid,
                        "elementId": "${steps.findResult.outputs.elementId}",
                    },
                },
                {"type": "return", "id": "done", "value": "${steps.readResult.outputs.value}"},
            ],
        },
    }


class CliProcess:
    """跑 `rpa_core.cli` 子进程并实时收集 stdout 行。"""

    def __init__(self, args: list[str]) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "rpa_core.cli", *args],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.lines: list[str] = []
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self.lines.append(line.rstrip("\n"))

    def wait_run_id(self, timeout: float = 30.0) -> str | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for line in list(self.lines):
                stripped = line.strip()
                if stripped.startswith("{") and '"run_id"' in stripped:
                    try:
                        payload = json.loads(stripped)
                    except ValueError:
                        continue
                    if isinstance(payload, dict) and isinstance(payload.get("run_id"), str):
                        return payload["run_id"]
            if self.proc.poll() is not None:
                return None
            time.sleep(0.05)
        return None

    def wait(self, timeout: float = 120.0) -> int:
        self._reader.join(timeout)
        return self.proc.wait(timeout=timeout)

    def tail(self, count: int = 6) -> list[str]:
        return self.lines[-count:]


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def completed_steps(run_dir: Path) -> list[str]:
    return list(read_json(run_dir / "checkpoint.json").get("completedSteps") or [])


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="rpa-desktop-pause-probe-"))
    artifacts = tmp / "artifacts"
    workflow_path = tmp / "workflow.json"
    workflow_path.write_text(
        json.dumps(build_workflow(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log("tmp", tmp)

    exe = compile_fixture(tmp)
    fixture = subprocess.Popen([str(exe)])
    try:
        hwnd = wait_for_window(TITLE)
        log("fixture", {"exe": str(exe), "hwnd": hwnd, "pid": fixture.pid})

        # ---- 1) 跑起来，并在慢节点期间请求暂停 -----------------------------
        run_proc = CliProcess(
            ["run", str(workflow_path), "--artifacts", str(artifacts)]
        )
        run_id = run_proc.wait_run_id()
        if run_id is None:
            log("run", {"error": "未拿到 run_id", "tail": run_proc.tail()})
            return 2
        run_dir = artifacts / run_id
        log("run", {"run_id": run_id, "run_dir": str(run_dir)})

        # 等「慢节点之前那些节点」全部完成再请求暂停：暂停的生效点是节点边界，
        # 慢节点（~3.7s）保证请求一定能落在它之后、clickSubmit 之前。
        # 判据要比**叶子名**：快照里的键是路径键 `root/attachMain`（`_node_path_key`），
        # 第一版直接拿裸名做子集判断 → 永远不成立，循环一路跑到进程退出才 break，
        # 于是暂停请求落在 run 收口之后 111ms（实测）。这个坑值得留着。
        gate = {"attachMain", "findInput", "findSubmit"}
        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline:
            done = {step.rsplit("/", 1)[-1] for step in completed_steps(run_dir)}
            if gate.issubset(done):
                break
            if run_proc.proc.poll() is not None:
                print("[warn] run 在请求暂停前就结束了（判据没打中）", flush=True)
                break
            time.sleep(0.05)
        log("pause-request", {"completedBeforePause": completed_steps(run_dir)})
        log("pause-write", request_pause(run_dir))

        code = run_proc.wait()
        result = read_json(run_dir / "result.json")
        log(
            "paused-result",
            {
                "exit": code,
                "status": result.get("status"),
                "error": result.get("error"),
                "completedSteps": completed_steps(run_dir),
            },
        )

        # ---- 2) 跨进程续跑（新进程、新执行器实例） --------------------------
        resume_proc = CliProcess(
            [
                "resume",
                str(workflow_path),
                "--run-id",
                run_id,
                "--artifacts",
                str(artifacts),
            ]
        )
        resume_code = resume_proc.wait()
        resumed = read_json(run_dir / "result.json")
        log(
            "resumed-result",
            {
                "exit": resume_code,
                "status": resumed.get("status"),
                "returnValue": resumed.get("return_value"),
                "error": resumed.get("error"),
                "completedSteps": completed_steps(run_dir),
            },
        )
        log("resume-stdout-tail", resume_proc.tail(4))

        events = run_dir / "events.jsonl"
        if events.is_file():
            lines = events.read_text(encoding="utf-8").splitlines()
            interesting = [
                line
                for line in lines
                if any(key in line for key in ("runPaused", "runResumed", "stepFailed"))
            ]
            log("events", interesting[-4:])
        return 0
    finally:
        fixture.terminate()
        try:
            fixture.wait(timeout=10)
        except subprocess.TimeoutExpired:
            fixture.kill()
            fixture.wait(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())
