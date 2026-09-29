"""捕获链路计时追踪（诊断用，行为无关）。

背景：维护者报「冷启动捕获，悬浮框立即出、红框迟 5~10s」；暖态实测全路径
~0.68s，慢因在真机冷态（一次性 agent 进程 × Defender/文件缓存），但沙箱无法
复现——只能靠**下次复现时自动落盘的阶段计时**取证。

设计：
- 三侧各自追加写同一文件 ``%TEMP%\\rpa-capture-trace.log``，按 epoch 关联：
  ``gui``（点按/spawn/arm）、``desktop``（agent 子进程 spawn 墙钟）、
  ``agent``（进程内阶段：pythoncom/UIA 懒导入/首帧红框）、``extension``（arm/ack）。
- **只落文件，不走 stderr**：agent 被 ``terminate()``（TerminateProcess）收掉时
  管道缓冲会丢，文件不会。
- 只记时刻、阶段名、进程号与档位，**不带用户数据**（无窗口标题/元素内容）。
- 任何失败都静默：诊断绝不干扰捕获；文件超 5MB 截断重开（防无限增长）。
"""

import json
import os
import tempfile
import time

TRACE_PATH = os.path.join(tempfile.gettempdir(), "rpa-capture-trace.log")
_MAX_BYTES = 5 * 1024 * 1024


def trace(side: str, mark: str, **extra: object) -> None:
    """追加一条计时记录。"""
    try:
        if os.path.exists(TRACE_PATH) and os.path.getsize(TRACE_PATH) > _MAX_BYTES:
            os.truncate(TRACE_PATH, 0)
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "epoch": round(time.time(), 3),
            "pid": os.getpid(),
            "side": side,
            "mark": mark,
        }
        record.update(extra)
        with open(TRACE_PATH, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 - 诊断通道自身绝不能抛
        pass
