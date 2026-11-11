"""采集入口与结果解析：拼 `rpa-core run` 命令，读 run_artifacts 的 result.json。

这里不实现任何浏览器逻辑。执行通道是能力层的扩展单通道（Native Messaging），
工作台只负责给 workflow 喂 inputs、给 CLI 指定 artifacts，再把终态翻译成
「成功 / 失败 / 需人工核查 / 暂停」四类。

状态语义来自 docs/api-usage.md：六种终态不得折叠。特别地 indeterminate 与
recovery_required 是「外部结果未知」，要进告警而不是进采集失败日志。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_BINARY = "rpa-core"

STATUS_GROUPS = {
    "succeeded": "ok",
    "failed": "failed",
    "cancelled": "failed",
    "indeterminate": "needs_attention",
    "recovery_required": "needs_attention",
    "paused": "paused",
}


@dataclass(frozen=True)
class RunOutcome:
    run_id: str
    status: str
    group: str
    outputs: dict[str, Any]
    error: Any


def build_run_command(
    workflow: str | Path,
    keyword: str,
    artifacts: str | Path,
    *,
    binary: str = DEFAULT_BINARY,
    extra_inputs: dict[str, Any] | None = None,
) -> list[str]:
    """一次采集 = 一个 run。inputs 走 CLI 的 --inputs JSON 字符串。"""
    inputs: dict[str, Any] = {"keyword": keyword}
    if extra_inputs:
        inputs.update(extra_inputs)
    return [
        binary,
        "run",
        str(workflow),
        "--inputs",
        json.dumps(inputs, ensure_ascii=False),
        "--artifacts",
        str(artifacts),
    ]


def parse_result(path: str | Path) -> RunOutcome:
    """读终态 result.json；缺字段/未知状态不粉饰成成功。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("result.json 顶层必须是对象")
    status = str(data.get("status", ""))
    return RunOutcome(
        run_id=str(data.get("run_id", "")),
        status=status,
        group=STATUS_GROUPS.get(status, "unknown"),
        outputs=data.get("outputs") or {},
        error=data.get("error"),
    )


def step_output(outcome: RunOutcome, step_id: str, key: str) -> Any:
    """取 result.outputs.<step_id>.outputs.<key>；缺任一层都抛，不返回 None 兜底。"""
    try:
        return outcome.outputs[step_id]["outputs"][key]
    except (KeyError, TypeError) as exc:
        raise KeyError(f"result.json 里取不到 {step_id}.outputs.{key}") from exc
