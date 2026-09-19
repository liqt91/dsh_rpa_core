"""跨进程运行控制通道（M21）：暂停请求从外部进程传进 run 子进程。

问题：暂停信号在 runtime 里是进程内的 `asyncio.Event`（ADR 0005），而 GUI 经
`RunManager` spawn `rpa-core run` 子进程执行（ADR 0011），两个进程——GUI 够不着
那个 Event，run 子进程也没有任何外部触发方式。

解法：把控制文件 `run_dir/control.json` 当作 run 的**暂停开关镜像**。run 子进程
起一个轮询任务读它，外部进程（GUI / `rpa-core pause`）写它。

**为什么放在顶层而不是 `runtime/` 下**：这是三方共用的跨进程契约——run 侧轮询
（runtime）、`pause` 子命令（CLI）、`RunManager` 写入（devserver）。devserver 受
架构检查约束不得 import `rpa_core.runtime`（ADR 0011），所以控制通道必须是零
依赖模块（只 import stdlib）才能被三方共用。

选型（文件 vs 端点/命名管道）：
- 零新依赖、跨平台、崩溃后仍可从文件本身查证「谁在什么时候请求过暂停」；
- 不引入 web 端口（ADR 0016），不进 GUI 进程承载 runtime（ADR 0011）；
- 命名管道方案记录在 M21 作为备选：Windows 上语义更脆（无 reader 时写入会阻塞）。

三条硬性约束：

1. **原子写**（临时文件 + `os.replace`）：读侧随时可能在写侧写到一半时读，
   绝不能读到半截 JSON。临时名带 pid，避免同目录并发写相撞。
2. **失效安全**：读失败（不存在 / 半写 / 损坏 / 结构不符）一律当「无请求」。
   控制通道是辅助手段，它的任何故障都不得影响 run 本身的执行。
3. **resume 必须重置**：paused 收口后控制文件里仍留着 `pause: true`，若不清除，
   同一个 run 被 resume 起来会立刻再次暂停。重置放在 CLI 的 resume 分支里做
   （不依赖调用方记得清）。

暂停的**生效点**仍由 ADR 0005 决定：节点边界（action 入口 / 重试循环顶部），
控制通道不改变这一点——它只负责把请求送到 `RunHandle.pause()`。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

CONTROL_FILE_NAME = "control.json"

# 轮询间隔：暂停的生效点本身是节点边界，没必要高频探测。0.25s 让「点暂停」
# 到「被 run 进程看见」的延迟远小于单步耗时，而每秒 4 次 stat+read 的成本可忽略。
DEFAULT_POLL_INTERVAL_SECONDS = 0.25


class PauseSwitch(Protocol):
    """控制通道要操作的最小面。

    只声明这两个方法而不是 import `RunHandle`，是为了让本模块零依赖——否则
    `devserver` 用不了它（架构检查禁止 devserver import `rpa_core.runtime`）。
    """

    def pause(self) -> None: ...

    def resume(self) -> None: ...


def control_path(run_dir: Path) -> Path:
    """run 的控制文件路径（`<artifacts>/<run_id>/control.json`）。"""
    return Path(run_dir) / CONTROL_FILE_NAME


def _write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink()


def request_pause(run_dir: Path) -> dict:
    """写暂停请求（幂等：重复调用即重复覆盖同一内容）。"""
    payload = {"pause": True, "requestedAt": datetime.now(UTC).isoformat()}
    _write_atomic(control_path(run_dir), payload)
    return payload


def request_continue(run_dir: Path) -> dict:
    """撤销尚未收口的暂停请求。

    只对**仍在运行**的 run 有意义：run 已经收口成 `paused` 之后，控制文件不再被
    消费，「继续」必须走 `resume`（从检查点起新进程）。
    """
    payload = {"pause": False, "requestedAt": datetime.now(UTC).isoformat()}
    _write_atomic(control_path(run_dir), payload)
    return payload


def read_control(run_dir: Path) -> dict:
    """容错读控制文件；任何异常形态都返回空 dict（= 无请求）。"""
    try:
        raw = control_path(run_dir).read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        payload = json.loads(raw)
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def pause_requested(run_dir: Path) -> bool:
    return read_control(run_dir).get("pause") is True


def reset_control(run_dir: Path) -> None:
    """清除控制文件（resume 前调用，见模块文档第 3 条）。"""
    with contextlib.suppress(OSError):
        control_path(run_dir).unlink()


async def watch_control_file(
    run_dir: Path,
    handle: PauseSwitch,
    *,
    interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
) -> None:
    """把控制文件的状态镜像到 `handle` 的暂停开关上，直到被取消。

    - `RunHandle` 的暂停开关初始必然是「未请求」（`asyncio.Event` 未 set），因此
      以 `False` 作为「已应用」的初值：文件里已有 `pause: true`（例如调用方在
      watcher 起起来之前就写了）会被立刻应用，不留竞态窗口；
    - 期望状态与已应用状态一致时不重复动作（`Event.set()` 幂等，但少调更干净）；
    - resume 场景下控制文件可能残留 `pause: true`，那会让新进程一启动就暂停——
      所以 `resume` 之前必须 `reset_control()`（CLI 的 resume 分支已在做）；
    - 本任务由调用方在 run 结束后取消；它自身不结束，也不抛异常。
    """
    applied = False
    while True:
        desired = await asyncio.to_thread(pause_requested, run_dir)
        if desired != applied:
            if desired:
                handle.pause()
            else:
                handle.resume()
            applied = desired
        await asyncio.sleep(interval)
