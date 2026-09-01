# API 使用契约（v1）

依据：ADR 0006（进程内 Python API 为第一形态，公开面冻结为 API v1）。
契约测试：`tests/contract/test_public_api.py`。本文所有代码与 `examples/api-usage/run_workflow.py` 一致并可运行。

## 四步调用模式

```python
import asyncio
import json
from pathlib import Path

from rpa_core.catalog import load_catalog
from rpa_core.compiler import WorkflowCompiler
from rpa_core.executors import ExecutorRegistry, PythonWorkerExecutor
from rpa_core.model.runtime import RunStatus
from rpa_core.model.workflow import Workflow
from rpa_core.runtime import Orchestrator

REPO = Path(__file__).resolve().parents[1]

async def main() -> int:
    # 1) 加载命令目录（不可变快照 + digest）——每次提交 run 前重建即可
    catalog = load_catalog(REPO / "commands")

    # 2) 编译：静态校验引用 / 能力 / unsafe-retry，产出冻结的 ExecutionPlan
    workflow = Workflow.model_validate_json(
        (REPO / "examples/api-usage/workflow.json").read_text(encoding="utf-8")
    )
    plan = WorkflowCompiler(catalog).compile(workflow, {"workspace.write"})

    # 3) 组装执行器（调用方持有生命周期，结束时 close）
    registry = ExecutorRegistry({"python.worker": PythonWorkerExecutor()})
    try:
        orchestrator = Orchestrator(catalog, registry, artifacts_dir)

        # 4) 运行：await 到 RunResult；inputs 会合并覆盖 workflow 默认输入
        result = await orchestrator.run(plan, inputs={"workspace": str(artifacts_dir)})
        if result.status is not RunStatus.SUCCEEDED:
            print(json.dumps(result.error, ensure_ascii=False, indent=2))
            return 1
    finally:
        await registry.close()

asyncio.run(main())
```

要点：

- **编译即 dry-run**：所有能提前发现的错误（未知命令、坏引用、能力缺失、unsafe 重试）都在 `compile` 抛出；进入 run 后不再做发现式解析。
- **inputs 合并**：`run(plan, inputs)` 的键覆盖 workflow 的同名默认输入；编译期只校验 workflow 声明的键。
- **一个进程一个 registry**：executor 持有浏览器/桌面/子进程资源，`close()` 释放；不要为每个 run 新建 registry。

## 执行器选择

| registry 键 | 覆盖命令 | 依赖 |
|---|---|---|
| `browser.playwright` | `browser.*` | playwright + 本地 Chromium |
| `desktop.uia` / `desktop.win32` | `desktop.*` | Windows + pywinauto |
| `python.worker` | `data.*` | 无外部依赖 |

只需要 data 命令的 workflow 不必注册浏览器/桌面执行器。

## 运行证据（artifacts 约定）

每次 run 落盘 `<artifacts>/<run_id>/`：

| 文件 | 内容 | 消费方式 |
|---|---|---|
| `result.json` | 终态 `RunResult`（状态、起止时间、outputs、返回值、error、digest） | 直接 JSON 读取；**runFinished + result.json 都落盘 run 才算成功** |
| `events.jsonl` | 追加式事件流（seq 单调） | 进度订阅 v1 = 轮询/tail 此文件（ADR 0006 结论） |
| `checkpoint.json` | 节点边界进度快照 | 恢复由 `resume()` 内部消费，调用方一般不直接读写 |

## 状态机消费指引

| 终态/驻留 | 含义 | 调用方动作 |
|---|---|---|
| `succeeded` | 全部完成 | 读 `result.outputs` / `return_value` |
| `failed` | 命令失败/超时且结果确定 | 读 `error`；修复后可 `resume`，未完成步骤会重跑 |
| `cancelled` | 调用了 `cancel()` | 已完成步骤保留，可 resume 续跑 |
| `indeterminate` | 失败且外部结果未知 | **人工核查外部系统**；确认后 `resume(plan, run_id, allow_indeterminate=True)` |
| `recovery_required` | checkpoint 持久化失败 | 核对副作用安全性后 `resume` |
| `paused` | 人工暂停（非终态） | 之后必须 `resume` 或显式放弃 |

硬约束（ADR 0006，契约测试锁定）：以上状态**不得折叠**为 `failed`/`cancelled`；`indeterminate` 的确认开关不可省略；一次 plan 绑定一份 catalog digest，resume 自动校验；用户代码只经 `python.worker` 子进程执行。

## 暂停与恢复

```python
handle = orchestrator.start(plan, inputs={...})
handle.pause()                      # 请求暂停：仅在下一个 action 入口 / 新 attempt 前生效
paused = await handle.wait()        # 进行中的命令会自然跑完，随后证据落盘（status=paused）

resumed = await orchestrator.resume(plan, paused.run_id).wait()
```

- `pause()` 不打断进行中的命令；需要立即停止用 `cancel()`（取消 > 暂停）。
- resume 复用同一套门槛：checkpoint 结构校验、workflowId、catalogDigest、`indeterminate` 人工确认；deadline 以全新 `workflow.timeout_seconds` 重新计费。
- 浏览器 / 桌面 session 不跨进程存活：恢复后引用旧 `sessionId` 的步骤会以 `SESSION_NOT_FOUND` / `SESSION_LOST` 失败，需要重新 launch。

## 可运行示例

`examples/api-usage/run_workflow.py`（配合 `examples/api-usage/workflow.json`）：

```text
run #1: succeeded
evidence status: succeeded
hello.txt: hello from rpa_core
run #2: paused
run #3: succeeded
```

覆盖：正常 run → 读 result.json → 暂停 → 恢复。运行方式：

```powershell
uv run python examples/api-usage/run_workflow.py
```
