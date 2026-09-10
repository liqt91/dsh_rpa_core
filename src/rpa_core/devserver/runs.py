"""运行控制（ADR 0011）：devserver spawn `rpa-core run` 子进程做 run host。

隔离边界：本模块只用 subprocess（不 import runtime/executors/workers），
devserver 进程内没有 orchestrator/registry/run 状态——只持有子进程句柄 +
run_id。cancel = 终止子进程（orchestrator 在子进程内落 cancelled 证据）。

run_id 映射：orchestrator 的真实 run_id（UUID）写在子进程 stdout 末尾的
RunResult JSON 里；我们用 `run-<seq>-<pid>` 做对外句柄，stdout 解析出真实
run_id 后映射到 `run_artifacts/<uuid>/` 读证据。
"""

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

from rpa_core.extension_exec import DEFAULT_HUB_URL, HUB_URL_ENV


class RunManager:
    """托管 `rpa-core run` 子进程；句柄用于 cancel，状态从 stdout/run_artifacts 读。"""

    def __init__(self, workflows_root: Path):
        self._workflows_root = workflows_root.resolve()
        self._artifacts = self._workflows_root.parent / "run_artifacts"
        self._procs: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._seq = 0
        # 自研扩展执行通道宿主地址（DevServer.start 后写入真实端口）；
        # 子进程据此回连命令队列，扩展收到命令后在用户真实浏览器里执行。
        self.hub_url = DEFAULT_HUB_URL

    def start(self, workflow_name: str, inputs: dict | None = None) -> dict:
        workflow_path = self._workflows_root / workflow_name / "workflow.json"
        if not workflow_path.is_file():
            raise FileNotFoundError(f"workflow not found: {workflow_name}")
        args = [
            sys.executable, "-m", "rpa_core.cli", "run", str(workflow_path),
            "--artifacts", str(self._artifacts),
        ]
        if inputs:
            args += ["--inputs", json.dumps(inputs, ensure_ascii=False)]
        env = os.environ.copy()
        env[HUB_URL_ENV] = self.hub_url
        proc = subprocess.Popen(
            args,
            env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
            cwd=str(self._workflows_root.parent),
        )
        entry = {"proc": proc, "stdout_lines": [], "stderr_lines": [], "real_run_id": None}
        reader = threading.Thread(
            target=self._read_stdout, args=(proc, entry), daemon=True
        )
        reader.start()
        err_reader = threading.Thread(
            target=self._read_stderr, args=(proc, entry), daemon=True
        )
        err_reader.start()
        with self._lock:
            self._seq += 1
            run_id = f"run-{self._seq}-{proc.pid}"
            self._procs[run_id] = entry
        return {"runId": run_id, "pid": proc.pid}

    def _read_stdout(self, proc: subprocess.Popen, entry: dict) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            entry["stdout_lines"].append(line.rstrip("\n"))
            # 子进程末尾打印 RunResult JSON（含真实 run_id）
            if '"run_id"' in line or '"runId"' in line:
                try:
                    # RunResult 是多行 JSON，单独解析单行不可靠；攒起来最后解析
                    pass
                except Exception:
                    pass
        entry["real_run_id"] = self._parse_real_run_id(entry["stdout_lines"])

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
