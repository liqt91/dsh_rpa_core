"""M21 跨进程运行控制通道（`control.json`）的契约测试。

两层：

- **进程内**：control 原语语义（原子写 / 失效安全 / 重置）与 `watch_control_file`
  对 `RunHandle` 暂停开关的镜像关系；
- **跨进程**：真 spawn `rpa-core run` 子进程，从外部写控制文件触发暂停，覆盖
  「暂停不打断进行中的 attempt」「resume 不重复已完成节点」「残留请求必须先重置」。
"""

import asyncio
import contextlib
import json
import subprocess
import sys
import time
from pathlib import Path

from rpa_core.control_channel import (
    control_path,
    pause_requested,
    read_control,
    request_continue,
    request_pause,
    reset_control,
    watch_control_file,
)

ROOT = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------
# 进程内：control 原语
# --------------------------------------------------------------------------


def test_pause_request_roundtrip_and_reset(tmp_path):
    run_dir = tmp_path / "run"
    assert pause_requested(run_dir) is False  # 目录都不存在 → 无请求，不抛
    assert read_control(run_dir) == {}

    payload = request_pause(run_dir)
    assert payload["pause"] is True
    assert pause_requested(run_dir) is True
    assert read_control(run_dir)["requestedAt"] == payload["requestedAt"]

    request_continue(run_dir)
    assert pause_requested(run_dir) is False

    request_pause(run_dir)
    reset_control(run_dir)
    assert control_path(run_dir).exists() is False
    assert pause_requested(run_dir) is False


def test_control_file_failures_are_fail_safe(tmp_path):
    """读失败一律当「无请求」：控制通道是辅助手段，不许把 run 拖下水。"""
    run_dir = tmp_path / "run"
    path = control_path(run_dir)

    run_dir.mkdir()
    path.write_text("{ not json", encoding="utf-8")
    assert pause_requested(run_dir) is False  # 半写/损坏

    path.write_text("[1, 2]", encoding="utf-8")
    assert pause_requested(run_dir) is False  # 合法 JSON 但结构不符

    path.write_text('{"pause": "true"}', encoding="utf-8")
    assert pause_requested(run_dir) is False  # 类型不符，不做真值强转

    reset_control(tmp_path / "never-existed")  # 不存在也不抛


def test_atomic_write_leaves_no_temp_residue(tmp_path):
    run_dir = tmp_path / "run"
    for _ in range(5):
        request_pause(run_dir)
    leftover = [p.name for p in run_dir.iterdir() if p.name != "control.json"]
    assert leftover == [], f"原子写留下临时文件：{leftover}"


# --------------------------------------------------------------------------
# 进程内：watch_control_file ↔ RunHandle 的镜像关系
# --------------------------------------------------------------------------


class _FakeHandle:
    """只记录 pause/resume 调用次数。"""

    def __init__(self):
        self.paused = 0
        self.resumed = 0

    def pause(self):
        self.paused += 1

    def resume(self):
        self.resumed += 1


async def _until(predicate, budget=3.0):
    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return False


def test_watch_mirrors_pause_then_continue_and_stays_idempotent(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    handle = _FakeHandle()

    async def scenario():
        task = asyncio.create_task(watch_control_file(run_dir, handle, interval=0.02))
        try:
            await asyncio.sleep(0.08)
            assert (handle.paused, handle.resumed) == (0, 0), "初始无请求不应有任何动作"

            request_pause(run_dir)
            assert await _until(lambda: handle.paused == 1)
            assert handle.resumed == 0

            # 状态不变 → 不重复调用（镜像语义，而非每轮无脑 set）
            await asyncio.sleep(0.1)
            assert handle.paused == 1

            # 生效前的撤销：控制文件回到 false，暂停开关被清掉
            request_continue(run_dir)
            assert await _until(lambda: handle.resumed == 1)
            await asyncio.sleep(0.1)
            assert (handle.paused, handle.resumed) == (1, 1)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    asyncio.run(scenario())


def test_watch_without_control_file_does_not_touch_handle(tmp_path):
    """控制文件缺失（绝大多数 run 的常态）时 watcher 完全静默。"""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    handle = _FakeHandle()

    async def scenario():
        task = asyncio.create_task(watch_control_file(run_dir, handle, interval=0.02))
        try:
            await asyncio.sleep(0.15)
            assert (handle.paused, handle.resumed) == (0, 0)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    asyncio.run(scenario())


# --------------------------------------------------------------------------
# 跨进程：真子进程 + 控制文件
# --------------------------------------------------------------------------

PAUSE_WORKFLOW = {
    "schema_version": "1.0",
    "id": "m21-pause",
    "name": "pause via control file",
    "inputs": {},
    "root": {
        "type": "sequence",
        "id": "root",
        "children": [
            {"type": "action", "id": "first", "command": "workflow.sleep",
             "with": {"seconds": 1}},
            {"type": "action", "id": "second", "command": "workflow.sleep",
             "with": {"seconds": 30}},
        ],
    },
}


def _write_workflow(tmp_path: Path) -> Path:
    path = tmp_path / "pause.json"
    path.write_text(json.dumps(PAUSE_WORKFLOW, ensure_ascii=False), encoding="utf-8")
    return path


def _spawn_cli(args: list[str], artifacts: Path):
    """起 CLI 子进程并读回首行 run_id 标记。"""
    proc = subprocess.Popen(
        [sys.executable, "-m", "rpa_core.cli", *args, "--artifacts", str(artifacts)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", cwd=str(ROOT),
    )
    assert proc.stdout is not None
    line = proc.stdout.readline()
    assert line.strip(), "子进程未输出 run_id 标记行"
    return proc, json.loads(line.strip())["run_id"]


def _wait_for(predicate, timeout=20.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _events(run_dir: Path) -> list[dict]:
    path = run_dir / "events.jsonl"
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_pause_via_control_file_stops_at_next_boundary(tmp_path):
    """核心验收：外部进程写控制文件 → run 在下一个节点边界停下并落 paused 证据。

    同时锁定 ADR 0005 的「暂停不打断进行中的 attempt」：请求在第 1 个 action
    执行中途送达，第 1 个 action 必须自然跑完（completedSteps 含它），第 2 个
    必须是「被挡在门外」的那个。
    """
    workflow = _write_workflow(tmp_path)
    artifacts = tmp_path / "artifacts"

    proc, run_id = _spawn_cli(["run", str(workflow)], artifacts)
    run_dir = artifacts / run_id
    try:
        # 等第 1 个 action 真正开始（stepStarted 落盘）再请求暂停
        assert _wait_for(lambda: any(
            e.get("type") == "stepStarted" and e.get("node_id") == "first"
            for e in _events(run_dir)
        )), "第 1 个节点未开始"
        request_pause(run_dir)

        out, err = proc.communicate(timeout=30)
        assert proc.returncode == 1, f"paused 不是成功终态，退出码应为 1；stderr={err}"

        result = _read_json(run_dir / "result.json")
        assert result["status"] == "paused"

        checkpoint = _read_json(run_dir / "checkpoint.json")
        assert checkpoint["completedSteps"] == ["root/first"], (
            "暂停点不对：进行中的 action 应跑完，后续 action 应未执行"
        )

        types = [e["type"] for e in _events(run_dir)]
        assert "pauseRequested" in types and "runPaused" in types
        paused = next(e for e in _events(run_dir) if e["type"] == "runPaused")
        assert paused["payload"]["nodeId"] == "second"
        assert paused["payload"]["completedSteps"] == ["root/first"]
    finally:
        if proc.poll() is None:
            proc.kill()


def test_resume_ignores_stale_pause_request_and_skips_completed(tmp_path):
    """resume 的两件事：① 残留的 pause:true 不得让新进程一启动就再次暂停
    （CLI 的 resume 分支会重置控制文件）；② 不重复执行已完成节点。
    """
    workflow = _write_workflow(tmp_path)
    artifacts = tmp_path / "artifacts"

    # 先正常跑出一个 paused run
    proc, run_id = _spawn_cli(["run", str(workflow)], artifacts)
    run_dir = artifacts / run_id
    try:
        assert _wait_for(lambda: any(
            e.get("type") == "stepStarted" and e.get("node_id") == "first"
            for e in _events(run_dir)
        ))
        request_pause(run_dir)
        proc.communicate(timeout=30)
        assert _read_json(run_dir / "result.json")["status"] == "paused"

        # 人为留下残留请求：没有重置的话，resume 起来会立刻又暂停
        assert pause_requested(run_dir) is True

        resumed, same_run_id = _spawn_cli(
            ["resume", str(workflow), "--run-id", run_id], artifacts
        )
        assert same_run_id == run_id
        try:
            # 第 2 个节点时长 30s，这里等 3s：进程仍在跑就说明没被残留请求立刻按停
            time.sleep(3)
            assert resumed.poll() is None, (
                "resume 后立刻结束——残留的 pause:true 让新进程一启动就暂停了"
            )
            assert pause_requested(run_dir) is False, "resume 未重置控制文件"

            resumed.terminate()
            resumed.communicate(timeout=15)
        finally:
            if resumed.poll() is None:
                resumed.kill()

        # 不重复已完成节点：first 只应出现过一次 stepStarted
        starts = [
            e["node_id"] for e in _events(run_dir)
            if e.get("type") == "stepStarted"
        ]
        assert starts.count("first") == 1, f"已完成节点被重跑：{starts}"
    finally:
        if proc.poll() is None:
            proc.kill()
