"""编辑器执行控制：在编辑器里「执行 / 暂停 / 继续 / 停止」一次采集，并把
``events.jsonl`` 增量格式化成执行日志。

与批量 ``run_collection`` 的关系：批量路径是「跑完一次即结束」；编辑器路径支持
暂停后继续，因此有自己的驱动循环，但**终态记录**（快照/告警/监控）完全复用
``_record_outcome``——两条路径的落库语义一致。

控制通道（与 ``src/rpa_core/control_channel.py`` 同一份文件契约，但**不 import**
rpa_core——应用层自己原子写 ``control.json``）：

- 暂停：``control.json`` 写 ``{"pause": true}``，run 进程在下一个节点边界收口成
  ``paused`` 并退出（不是挂起进程）。
- 继续（运行中）：写 ``{"pause": false}`` 撤销尚未收口的暂停请求。
- 继续（已暂停）：``rpa-core resume <workflow> --run-id <run_dir> --artifacts ...``
  起新进程从检查点继续，事件继续写同一个 events.jsonl。
- 停止：``taskkill /T /F`` 杀进程树；workflow 收尾步骤（关标签页）不会执行——
  这是「停止」的明确代价，界面上要写明。

并发纪律：与 ``/api/collect`` 共用 ``ctx.lock``，任何时刻至多一个 run 在飞；
暂停期间锁不释放（resume 之前再来一次采集会与 resume 抢同一个 run_dir）。
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.collector import build_run_command, parse_result
from src.record import archive_screenshot, record_outcome

TERMINAL_STATES = {"finished", "failed", "stopped"}


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _write_control(run_dir: Path, pause: bool) -> None:
    """原子写控制文件：临时名 + os.replace，读侧绝不可能读到半截 JSON。"""
    payload = {"pause": pause, "requestedAt": _utcnow()}
    tmp = run_dir / f"control.json.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    os.replace(tmp, run_dir / "control.json")


def _kill_tree(proc: subprocess.Popen) -> None:
    """Windows 杀进程树：rpa-core 会拉起 native host 子进程，单杀主进程会留孤儿。"""
    if proc.poll() is not None:
        return
    try:
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        try:
            proc.kill()
        except OSError:
            pass


@dataclass
class EditorRun:
    keyword: str
    browser: str
    started_at: str = field(default_factory=_utcnow)
    state: str = "running"  # running / paused / finished / failed / stopped
    proc: Any = None
    run_dir: Path | None = None
    rc: int | None = None
    stop_requested: bool = False
    error: str | None = None
    outcome: Any = None
    lines: list[str] = field(default_factory=list)
    stderr_tail: str = ""
    _log_offset: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def active(self) -> bool:
        return self.state in {"running", "paused"}

    def status(self) -> dict[str, Any]:
        self.drain_log()
        return {
            "state": self.state,
            "keyword": self.keyword,
            "browserType": self.browser,
            "started_at": self.started_at,
            "run_id": self.run_dir.name if self.run_dir else None,
            "pid": self.proc.pid if self.proc is not None else None,
            "rc": self.rc,
            "error": self.error,
            "items": _items_count(self.outcome),
        }

    # ---- 日志（增量读 events.jsonl，格式化追加） ----

    def drain_log(self) -> int:
        """把 events.jsonl 里还没看过的行格式化进 ``self.lines``，返回新增行数。"""
        with self._lock:
            if self.run_dir is None:
                return 0
            path = self.run_dir / "events.jsonl"
            try:
                size = path.stat().st_size
            except OSError:
                return 0
            if size < self._log_offset:
                self._log_offset = 0  # 文件被重写（resume 清检查点），从头读
            try:
                with path.open("rb") as fh:
                    fh.seek(self._log_offset)
                    raw = fh.read()
            except OSError:
                return 0
            self._log_offset += len(raw)
            added = 0
            for line in raw.decode("utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                text = _format_event(event)
                if text:
                    self.lines.append(text)
                    added += 1
            return added


def _items_count(outcome: Any) -> int | None:
    if outcome is None:
        return None
    try:
        raw = outcome.outputs["grabItems"]["outputs"]["result"]
        return len(json.loads(raw)) if isinstance(raw, str) else len(raw or [])
    except (KeyError, TypeError, json.JSONDecodeError):
        return None


def _format_event(event: dict[str, Any]) -> str | None:
    kind = event.get("type")
    node = event.get("node_id") or ""
    payload = event.get("payload") or {}
    ts = str(event.get("timestamp") or "")[11:19]
    if kind == "runStarted":
        return f"[{ts}] ▶ 运行开始"
    if kind == "stepStarted":
        return f"[{ts}] → {node}（{payload.get('command', '')}）"
    if kind == "stepCompleted":
        return f"[{ts}]   ✓ {node} 完成"
    if kind == "stepFailed":
        err = payload.get("error") or {}
        msg = err.get("message") if isinstance(err, dict) else str(err)
        return f"[{ts}]   ✗ {node} 失败：{msg}"
    if kind == "stepRetried":
        return f"[{ts}]   ↻ {node} 第 {payload.get('nextAttempt')} 次尝试"
    if kind == "runFinished":
        return f"[{ts}] ■ 运行结束"
    if kind == "runFailed":
        err = payload.get("error") or {}
        msg = err.get("message") if isinstance(err, dict) else str(err)
        return f"[{ts}] ■ 运行失败：{msg}"
    return None


def start(ctx: Any, keyword: str, browser_type: str) -> EditorRun:
    """开一次编辑器 run。调用方必须先拿到 ``ctx.lock``（HTTP 层负责 409）。"""
    ctx.monitor.register(keyword)
    run = EditorRun(keyword=keyword, browser=browser_type)
    ctx.editor = run
    ctx.monitor.set_running(keyword, "editor-pending")
    threading.Thread(target=_drive, args=(ctx, run, "run"), daemon=True).start()
    return run


def _pump(run: EditorRun, stream: Any, sink: list[str]) -> None:
    """把子进程 stdout/stderr 行追加到 sink（stderr 留尾用于报错）。"""
    try:
        for line in stream:
            sink.append(line.rstrip("\n"))
    except ValueError:
        return


def _run_one_phase(ctx: Any, run: EditorRun, phase: str) -> None:
    """执行一个阶段（run 或 resume），阻塞到进程退出或被杀。"""
    artifacts = ctx.root / "run_artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    before = {p.name for p in artifacts.iterdir() if p.is_dir()} if artifacts.is_dir() else set()
    if phase == "run":
        cmd = build_run_command(
            ctx.workflow,
            run.keyword,
            artifacts,
            binary=str(ctx.cli_binary),
            extra_inputs={
                "browserType": run.browser,
                "workspace": str(artifacts),
                "shotPath": str(artifacts / "results.png"),
            },
        )
    else:
        if run.run_dir is None:
            raise RuntimeError("resume 需要先知道 run_dir")
        cmd = [
            str(ctx.cli_binary), "resume", str(ctx.workflow),
            "--artifacts", str(artifacts), "--run-id", run.run_dir.name,
        ]
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    run.proc = subprocess.Popen(  # noqa: S603
        cmd,
        cwd=str(ctx.repo_root),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    threading.Thread(target=_pump, args=(run, run.proc.stdout, stdout_lines), daemon=True).start()
    threading.Thread(target=_pump, args=(run, run.proc.stderr, stderr_lines), daemon=True).start()

    deadline = time.monotonic() + ctx.timeout
    while run.proc.poll() is None:
        if run.run_dir is None:
            now = {p.name for p in artifacts.iterdir() if p.is_dir()}
            fresh = sorted(now - before)
            if fresh:
                run.run_dir = artifacts / fresh[0]
        if run.stop_requested:
            _kill_tree(run.proc)
            break
        if time.monotonic() > deadline:
            run.stop_requested = True
            _kill_tree(run.proc)
            run.error = f"run 超过 {ctx.timeout}s 未结束"
            break
        run.drain_log()
        time.sleep(0.3)
    run.rc = run.proc.wait()
    run.stderr_tail = "\n".join(stderr_lines[-50:])
    run.drain_log()


def _drive(ctx: Any, run: EditorRun, phase: str) -> None:
    """驱动一个阶段；paused 是「本次驱动结束但不是终态」，终态负责放锁。"""
    try:
        _run_one_phase(ctx, run, phase)
        if run.stop_requested:
            run.state = "stopped"
            ctx.monitor.set_failed(run.keyword, "已被手动停止")
            _finish(ctx, run)
            return
        outcome = _parse_outcome(run)
        if outcome.status == "paused":
            run.outcome = outcome
            run.state = "paused"
            ctx.monitor.run_paused(run.keyword)
            return  # 锁不释放：resume/stop 才真正收尾
        run.outcome = outcome
        record = record_outcome(ctx, run.keyword, outcome)
        run.state = "finished" if record.get("ok") else "failed"
        if not record.get("ok"):
            run.error = str(record.get("error") or outcome.status)
        _finish(ctx, run)
    except Exception as exc:  # noqa: BLE001 - 驱动层兜底：任何异常都要落监控池并放锁
        run.state = "failed"
        run.error = f"{type(exc).__name__}: {exc}"
        try:
            ctx.monitor.set_failed(run.keyword, run.error)
        finally:
            _finish(ctx, run)


def _parse_outcome(run: EditorRun) -> Any:
    if run.run_dir is None:
        raise RuntimeError(
            f"run 未产出 run 目录（rc={run.rc}）。\n--- stderr ---\n{run.stderr_tail}"
        )
    result_path = run.run_dir / "result.json"
    if not result_path.is_file():
        raise RuntimeError(
            f"run 未写出 result.json（rc={run.rc}）。多半是 workflow 中途失败。\n"
            f"--- stderr ---\n{run.stderr_tail}"
        )
    archive_screenshot(result_path.parent.parent, result_path.parent)
    return parse_result(result_path)


def _finish(ctx: Any, run: EditorRun) -> None:
    ctx.lock.release()


def pause(ctx: Any) -> None:
    run = ctx.editor
    if run is None or run.state != "running":
        raise ValueError("没有正在运行中的采集")
    if run.run_dir is None:
        raise ValueError("run 目录还没建立（刚启动），稍等再暂停")
    _write_control(run.run_dir, True)


def resume(ctx: Any) -> None:
    run = ctx.editor
    if run is None:
        raise ValueError("没有可继续的采集")
    if run.state == "running":
        if run.run_dir is None:
            raise ValueError("run 目录还没建立，稍等")
        _write_control(run.run_dir, False)  # 撤销尚未收口的暂停请求
        return
    if run.state != "paused":
        raise ValueError(f"当前状态 {run.state}，不能继续")
    if run.run_dir is None:
        raise ValueError("run 目录丢失，无法 resume")
    run.state = "running"
    ctx.monitor.resume_running(run.keyword, run.run_dir.name)
    threading.Thread(target=_drive, args=(ctx, run, "resume"), daemon=True).start()


def stop(ctx: Any) -> None:
    run = ctx.editor
    if run is None or not run.active:
        raise ValueError("没有正在运行的采集")
    run.stop_requested = True
    if run.proc is not None and run.proc.poll() is None:
        _kill_tree(run.proc)
    if run.state == "paused":
        # 进程已退出（paused 收口），直接收尾
        run.state = "stopped"
        run.error = "已被手动停止"
        ctx.monitor.set_failed(run.keyword, "已被手动停止（处于暂停状态）")
        _finish(ctx, run)
