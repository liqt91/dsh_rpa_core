# ADR 0011：devserver 代理型运行控制（子进程 run host）

- 状态：已接受
- 日期：2026-09-03
- 关联：ADR 0006（API 面）、ADR 0007（devserver 隔离）、ADR 0008（编辑器 UI）、M18 任务单、BACKLOG 运行控制条目

## 背景

编辑器（M9~M17）已具备流程编写与元素捕获闭环，但**没有运行入口**——用户要跑流程必须切到 CLI。BACKLOG 的"运行控制"条目（前端 run + cancel + 看 events/result，最小范围）在 M17 收口后立项为 M18。

ADR 0007 的硬约束是 **devserver 进程内不存在 orchestrator / run 状态**。本 ADR 要在不破该约束的前提下放行"从编辑器触发运行"。

## 决策

### 1. run host 放子进程，devserver 只做代理控制

- devserver 进程内**仍然没有 orchestrator、registry、任何 run 状态**。运行由 spawn 的子进程承担：`python -m rpa_core.cli run <workflow> --artifacts <dir>`。
- devserver 只持有**子进程句柄 + run_id**（用于 cancel = 终止子进程、status = 读证据文件），架构边界不破（子进程是独立进程，runtime/executors 不进 devserver 进程）。
- 取消 = 终止子进程（`proc.terminate()`）。这对应运行终态 `cancelled`（orchestrator 在子进程内会落 cancelled 证据）。

### 2. 端点契约（最小范围）

| 端点 | 方法 | 语义 |
|---|---|---|
| `/api/runs` | POST | 启动运行：body `{"workflow": <名>, "inputs": {...}?}` → spawn 子进程，返回 `{runId}`（= artifacts 目录名） |
| `/api/runs/{runId}` | GET | 运行状态：读 `run_artifacts/<runId>/result.json`（在跑时读 events 尾部推断 running） |
| `/api/runs/{runId}/events` | GET | 读 `events.jsonl`（进度订阅 v1 = 轮询/tail，与 ADR 0006 一致） |
| `/api/runs/{runId}/cancel` | POST | 终止子进程（cancel） |

### 3. 隔离与边界（不变式不破）

- devserver 仍只 import `model`/`catalog`/`compiler`，**不 import runtime/executors/workers**（架构检查的 devserver 隔离断言继续覆盖；run host 是 `subprocess` 调用，不是 import）。
- 运行证据仍走既有 `<artifacts>/<run_id>/` 约定（result.json / events.jsonl / checkpoint.json），devserver 只读。
- 与 ADR 0006 的"操控型 HTTP 推迟"的边界：本 ADR 放行的是**设计期编辑器内的代理运行控制**（devserver 起子进程跑 CLI），不是给运行时加操控 HTTP API——运行时仍无 HTTP 操控面。

### 4. 范围外（后置）

- pause/resume（需要 orchestrator 句柄跨进程，子进程模型不支持；后置）
- indeterminate 人工确认对话（CLI resume --allow-indeterminate 已覆盖，前端后置）
- 多并发 run 调度（一次一个即可）

## 后果

- M18 可以启动：编辑器内可 run/cancel/看状态与事件流。
- devserver 隔离不破：架构检查继续断言 devserver 不 import runtime/executors/workers。
- 运行控制的最小闭环（run + cancel + 看结果/事件）达成，pause/resume 与 indeterminate 确认后置。
