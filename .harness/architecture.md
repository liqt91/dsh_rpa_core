# Architecture

## Dependency direction

```text
model
  ^
  |-- catalog
  |-- executors
  |-- compiler <- catalog
  |-- runtime  <- compiler + catalog + executors
  |-- devserver <- compiler + catalog (design-time only, never runtime)
  `-- cli      <- runtime + compiler + catalog + executors + devserver

workers are subprocess entry points and may depend on model only.
```

Allowed package dependencies:

| Package | May import |
|---|---|
| `model` | standard library, Pydantic |
| `catalog` | `model` |
| `compiler` | `model`, `catalog` |
| `executors` | `model` |
| `runtime` | `model`, `catalog`, `compiler`, `executors` |
| `devserver` | `model`, `catalog`, `compiler` (ADR 0007: never `runtime`/`executors`) |
| `workers` | `model` |
| `cli` | all public packages |

共享低层模块（不属于任何层，仅 stdlib + 平台 API）：

| Module | 职责 | 使用者 |
|---|---|---|
| `local_transport` | 本地端点（Windows 命名管道 / POSIX Unix 域套接字）与长度前缀 JSON 帧 | `workers.ext_bridge`、`extension_exec`、`capture.extension` |
| `extension_exec` | 扩展执行通道协议与执行器侧客户端（ADR 0015：Native Messaging） | `executors`、`devserver`（状态/诊断） |

`workers.ext_bridge` 是浏览器按需拉起的 host 子进程（Native Messaging stdio ↔ 本地端点），
只依赖 stdlib + `local_transport`，不触达 catalog/runtime。

> **平台覆盖**：`local_transport` 的两条分支里，Windows 命名管道与 POSIX Unix 域套接字
> **均已真机验证**（后者 2026-09-19 于 macOS），**Linux 仍未真机**（仅单测/设计）。
> 逐部件验证状态见 `docs/extension-install.md` §1.2 与 ADR 0015 §7 —— 改这条通道前先看那两张表，
> 别把「实现存在」当成「已验证」。

## Runtime flow

```text
workflow.json
  -> Workflow model
  -> Compiler validates commands, ids, references, capabilities
  -> ExecutionPlan pins a catalog version
  -> Orchestrator creates RunContext
  -> Executor receives CommandInvocation
  -> Executor returns CommandResult
  -> Orchestrator writes events and step outputs
```

## Ownership

- Model owns protocol types only.
- Catalog owns manifest loading and immutable snapshots.
- Compiler owns static validation and execution-plan creation.
- Runtime owns orchestration, scopes, cancellation, timeout, and event persistence.
- Executors own external-system interaction but not workflow state.
- Workers isolate high-risk or user-provided execution from the orchestrator.

New top-level packages or reversed dependencies require an ADR.
