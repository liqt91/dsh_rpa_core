"""运行控制（ADR 0011）：devserver spawn `rpa-core run` 子进程做 run host。

隔离边界：本模块只用 subprocess（不 import runtime/executors/workers），
devserver 进程内没有 orchestrator/registry/run 状态——只持有子进程句柄 +
run_id。cancel = 终止子进程（orchestrator 在子进程内落 cancelled 证据）。

run_id 映射：orchestrator 的真实 run_id（UUID）写在子进程 stdout 末尾的
RunResult JSON 里；我们用 `run-<seq>-<pid>` 做对外句柄，stdout 解析出真实
run_id 后映射到 `run_artifacts/<uuid>/` 读证据。

暂停/继续（M21）：暂停信号在 runtime 里是进程内 `asyncio.Event`，跨进程够不着。
run 子进程轮询 `run_artifacts/<uuid>/control.json`（`control_channel`，零依赖、
三方共用）；本模块只负责**写**那个文件，或在 run 已收口时 spawn 一个新进程
`rpa-core resume` 从检查点继续。隔离边界不变：写文件与 subprocess 都不是 import。
"""

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

from rpa_core.control_channel import request_continue, request_pause

# 等真实 run_id 被解析出来的上限：CLI 在 start() 之后立即打印标记行，正常远小于此。
_REAL_RUN_ID_WAIT_SECONDS = 10.0


class RunControlError(RuntimeError):
    """控制请求无法送达（run 未就绪 / 句柄状态不对）。"""


class RunManager:
    """托管 `rpa-core run` 子进程；句柄用于 cancel/pause/continue，状态从
    stdout/run_artifacts 读。"""

    def __init__(self, workflows_root: Path):
        self._workflows_root = workflows_root.resolve()
        self._artifacts = self._workflows_root.parent / "run_artifacts"
        self._procs: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._seq = 0

    def start(
        self,
        workflow_name: str,
        inputs: dict | None = None,
        breakpoints: list[str] | None = None,
    ) -> dict:
        """起一个 run 子进程；`breakpoints`（M24）为节点 id 列表，命中即暂停。"""
        workflow_path = self._workflows_root / workflow_name / "workflow.json"
        if not workflow_path.is_file():
            raise FileNotFoundError(f"workflow not found: {workflow_name}")
        args = [
            sys.executable, "-m", "rpa_core.cli", "run", str(workflow_path),
            "--artifacts", str(self._artifacts),
        ]
        if inputs:
            args += ["--inputs", json.dumps(inputs, ensure_ascii=False)]
        if breakpoints:
            args += ["--breakpoints", ",".join(breakpoints)]
        return self._spawn(args, workflow_name)

    def _spawn(
        self, args: list[str], workflow_name: str, *, real_run_id: str | None = None
    ) -> dict:
        """起一个 run 子进程并登记句柄。

        `real_run_id` 已知时（resume：UUID 来自参数）直接置位就绪事件，调用方
        立刻就能写控制文件 / 读证据。
        """
        env = os.environ.copy()
        proc = subprocess.Popen(
            args,
            env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
            cwd=str(self._workflows_root.parent),
        )
        entry = {
            "proc": proc,
            "stdout_lines": [],
            "stderr_lines": [],
            "real_run_id": real_run_id,
            "workflow": workflow_name,
            "real_run_id_ready": threading.Event(),
        }
        if real_run_id is not None:
            entry["real_run_id_ready"].set()
        reader = threading.Thread(target=self._read_stdout, args=(proc, entry), daemon=True)
        reader.start()
        err_reader = threading.Thread(target=self._read_stderr, args=(proc, entry), daemon=True)
        err_reader.start()
        with self._lock:
            self._seq += 1
            run_id = f"run-{self._seq}-{proc.pid}"
            self._procs[run_id] = entry
        return {"runId": run_id, "pid": proc.pid, "runIdReal": real_run_id}

    # ---- 跨进程控制（M21） --------------------------------------------------

    def _wait_real_run_id(self, entry: dict, timeout: float = _REAL_RUN_ID_WAIT_SECONDS):
        entry["real_run_id_ready"].wait(timeout)
        return entry.get("real_run_id")

    def _entry_and_real(self, run_id: str) -> tuple[dict, str]:
        entry = self._procs.get(run_id)
        if entry is None:
            raise KeyError(run_id)
        real = self._wait_real_run_id(entry)
        if not real:
            raise RunControlError(f"run 尚未就绪（未解析出真实 run_id）：{run_id}")
        return entry, str(real)

    def pause(self, run_id: str) -> dict:
        """请求暂停运行中的 run（写控制文件；生效点是下一个节点边界）。"""
        _entry, real = self._entry_and_real(run_id)
        request_pause(self._artifacts / real)
        return {"runId": run_id, "runIdReal": real, "pauseRequested": True}

    def continue_run(self, run_id: str) -> dict:
        """继续：仍在运行 → 撤销尚未生效的暂停请求；已收口 → 从检查点起新进程。

        同一个按钮覆盖两种处境，界面上不必让用户分辨——差别只是「暂停还没落地」
        还是「已经落成 paused」。
        """
        entry, real = self._entry_and_real(run_id)
        if entry["proc"].poll() is None:
            request_continue(self._artifacts / real)
            return {"runId": run_id, "resumed": "pause-cancelled"}
        return self.resume(run_id)

    def resume(
        self,
        run_id: str,
        *,
        allow_indeterminate: bool = False,
        step: bool = False,
    ) -> dict:
        """spawn `rpa-core resume` 从一个已收口 run 的检查点继续。

        `allow_indeterminate` 是 ADR 0004 的第 4 道人工确认门：上次终态为
        `indeterminate`（外部写入结果未知）时，只有用户显式确认「可能重复执行
        未确认的副作用」才置位——默认拒绝，由调用方（GUI 对话）决定。
        `step`（M24）单步：只执行一个节点后在下一个边界再次暂停。
        """
        entry, real = self._entry_and_real(run_id)
        if entry["proc"].poll() is None:
            raise RunControlError(f"run 仍在运行，不能 resume：{run_id}")
        workflow_name = entry["workflow"]
        workflow_path = self._workflows_root / workflow_name / "workflow.json"
        if not workflow_path.is_file():
            raise FileNotFoundError(f"workflow not found: {workflow_name}")
        args = [
            sys.executable, "-m", "rpa_core.cli", "resume", str(workflow_path),
            "--run-id", real, "--artifacts", str(self._artifacts),
        ]
        if allow_indeterminate:
            args.append("--allow-indeterminate")
        if step:
            args.append("--step")
        return self._spawn(args, workflow_name, real_run_id=real)

    def resume_run(
        self,
        workflow_name: str,
        run_id: str,
        *,
        step: bool = False,
        allow_indeterminate: bool = False,
    ) -> dict:
        """恢复一个**不由本进程托管**的历史运行（M25 运行历史）。

        与 `resume` 的区别：不需要本进程先 spawn 过该 run（GUI 重启后、或从运行
        历史面板里挑一个 paused 的旧运行继续/单步）。前置条件由 run 侧检查——
        检查点缺失/工作流不匹配时 `rpa-core resume` 会以退出码 2 + 结构化错误收场。
        """
        workflow_path = self._workflows_root / workflow_name / "workflow.json"
        if not workflow_path.is_file():
            raise FileNotFoundError(f"workflow not found: {workflow_name}")
        args = [
            sys.executable, "-m", "rpa_core.cli", "resume", str(workflow_path),
            "--run-id", run_id, "--artifacts", str(self._artifacts),
        ]
        if allow_indeterminate:
            args.append("--allow-indeterminate")
        if step:
            args.append("--step")
        return self._spawn(args, workflow_name, real_run_id=run_id)

    # ---- 子进程输出 ---------------------------------------------------------

    def _read_stdout(self, proc: subprocess.Popen, entry: dict) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            entry["stdout_lines"].append(line.rstrip("\n"))
            # CLI 在启动 run 后立即打印单行 {"run_id": ...} 标记（RunResult 之前），
            # 提前解析出真实 run_id，运行中即可读 events.jsonl（悬浮窗/事件流实时显示）
            if entry.get("real_run_id") is None and '"run_id"' in line:
                try:
                    payload = json.loads(line.strip())
                except json.JSONDecodeError:
                    payload = None
                if isinstance(payload, dict) and isinstance(payload.get("run_id"), str):
                    entry["real_run_id"] = payload["run_id"]
                    entry["real_run_id_ready"].set()
        if entry.get("real_run_id") is None:
            entry["real_run_id"] = self._parse_real_run_id(entry["stdout_lines"])
        # 流结束仍未解析出（启动即失败等）：解除等待，让控制请求尽早报错而不是干等
        entry["real_run_id_ready"].set()

    def _parse_real_run_id(self, lines: list[str]) -> str | None:
        """从子进程 stdout 末尾的多行 RunResult JSON 提取 run_id。"""
        text = "\n".join(lines)
        # RunResult 是最后一个 JSON 对象，run_id 字段在其中
        idx = text.rfind('"run_id"')
        if idx < 0:
            return None
        try:
            # 从最近的 '{' 开始解析
            start = text.rfind("{", 0, idx)
            while start >= 0:
                try:
                    payload = json.loads(text[start:])
                    if isinstance(payload, dict) and "run_id" in payload:
                        return payload["run_id"]
                except json.JSONDecodeError:
                    start = text.rfind("{", 0, start)
        except Exception:
            pass
        return None

    def _real_run_id(self, entry: dict) -> str | None:
        return entry.get("real_run_id")

    def cancel(self, run_id: str) -> dict:
        entry = self._procs.get(run_id)
        if entry is None:
            raise KeyError(run_id)
        proc = entry["proc"]
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        return {"runId": run_id, "cancelled": True}

    def _read_stderr(self, proc: subprocess.Popen, entry: dict) -> None:
        """必须持续读取：不读会写满管道缓冲导致子进程阻塞，且启动失败的原因全在 stderr。"""
        assert proc.stderr is not None
        for line in proc.stderr:
            entry["stderr_lines"].append(line.rstrip("\n"))

    def _startup_error(self, entry: dict) -> dict:
        """子进程未产出任何结果就退出（编译/校验失败）时的原因摘要。

        `rpa-core run` 编译失败会以结构化 JSON 打到 stderr（见 cli.py），
        这里优先取它；取不到就退回 stderr 尾部原文（比如未捕获异常）。
        """
        lines = [line for line in entry.get("stderr_lines", []) if line.strip()]
        message = None
        for line in reversed(lines):
            stripped = line.strip()
            if not stripped.startswith("{"):
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and payload.get("message"):
                message = str(payload["message"])
                break
        return {"message": message, "tail": lines[-12:]}

    def status(self, run_id: str) -> dict:
        entry = self._procs.get(run_id)
        if entry is None:
            raise KeyError(run_id)
        proc = entry["proc"]
        running = proc.poll() is None
        result = None
        real = self._real_run_id(entry)
        if real:
            result_file = self._artifacts / real / "result.json"
            if result_file.is_file():
                result = json.loads(result_file.read_text(encoding="utf-8"))
        payload = {
            "runId": run_id,
            "running": running,
            "exitCode": proc.poll(),
            "result": result,
        }
        # 没有结果且非零退出 = 启动即失败（编译/校验/加载失败），把 stderr 的原因带出来
        if result is None and not running and proc.returncode not in (0, None):
            payload["startupError"] = self._startup_error(entry)
        return payload

    def events(self, run_id: str) -> dict:
        entry = self._procs.get(run_id)
        if entry is None:
            raise KeyError(run_id)
        events = []
        real = self._real_run_id(entry)
        if real:
            events_file = self._artifacts / real / "events.jsonl"
            if events_file.is_file():
                for line in events_file.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line:
                        events.append(json.loads(line))
        return {"runId": run_id, "events": events}

    def close(self) -> None:
        with self._lock:
            entries = list(self._procs.values())
            self._procs.clear()
        for entry in entries:
            proc = entry["proc"]
            if proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:
                    pass
