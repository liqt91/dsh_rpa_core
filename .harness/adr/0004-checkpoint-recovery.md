# ADR 0004：检查点持久化与恢复状态机

- 状态：已接受
- 日期：2026-09-01

## 背景

M1.2 确立了副作用契约：每个命令声明 `effect.kind / replay / idempotency`，失败时 executor 返回 `unknown` effect 表示"外部结果不确定"。Runtime 此前不持久化执行进度：进程崩溃或运行失败后，只能从头重跑整个 workflow， unsafe-write 命令（browser.navigate、browser.click、desktop.click 等）会被无条件重复执行。

M3 需要在不引入 exactly-once、分布式调度或数据库的前提下，给出安全的节点边界检查点与恢复语义。本 ADR 修改 runtime 状态机契约：`RunStatus` 新增 `recovery_required` 与 `indeterminate` 两个终态取值。

## 决策

### 检查点内容与写入时机

checkpoint 为 `version = 1` 的 JSON 文档，写入 `run_dir/checkpoint.json`：

- `version`：格式版本，resume 时校验，非 `1` 视为损坏。
- `workflowId` / `catalogDigest`：resume 时必须与重新编译的 plan 一致。
- `completedSteps`：已持久完成的 action 的路径键（见下）。
- `scopes`：`inputs`、`steps`、`loop` 与 try 错误变量快照；resume 使用快照中的 inputs，不接受新 inputs。
- `returnValue`：仅当 workflow 已执行 `return` 后才是真实返回值；action 边界写 `null`。

完成键使用节点路径而不是裸节点 id：`forEach` 的第 i 次迭代的子节点路径附加 `#i` 段（如 `loop/#0/step`）。这保证 foreach 恢复时按迭代跳过，而不是把后续迭代全部误判为已完成。

写入时机：action 成功且输出校验通过后、`stepCompleted` 事件之前，先原子写 checkpoint（临时文件 + replace）。崩溃窗口语义：

- checkpoint 已写入、事件未写：resume 跳过该 step，事件证据缺失但执行最多一次。
- checkpoint 未写入：resume 重新执行该 step。对 `replay=safe` / `idempotent` 的命令这是契约允许的重放；对 `replay=unsafe` 的命令，重放风险由人工 resume 决定承担。

写入失败（`CheckpointError`）不会静默继续：run 终态记为 `recovery_required`。终态证据（`runFinished` + `result.json`）持久化之后的最终 checkpoint 写入是 best-effort，失败不改变终态，只留下较旧的检查点（见后果）。

### 状态机

`RunStatus` 终态语义扩展：

- `indeterminate`：某 step 失败且其结果携带 `status=unknown` 的 effect 记录，即外部写入结果未知。此时 runtime 绝不自动重试（即使 manifest 声明 `retryable=true`），也绝不判定成功；终态 `indeterminate`，错误载荷保留原始错误码与 unknown effect 证据。这是 M1.2"失败 + unknown effect 记为 failed"契约的修订。
- `recovery_required`：action 边界的 checkpoint 持久化失败。run 证据完整，但进度快照可能落后于实际副作用，恢复需要人工确认。
- `succeeded` / `failed` / `cancelled` / `abandoned`：语义不变。

### Resume 前置检查

`resume(plan, run_id)` 依次校验，任何一步失败即拒绝，不产生新的 run 证据：

1. checkpoint 存在且通过 `version=1` 结构校验（损坏、非对象、缺字段 → `CheckpointError`）。
2. `workflowId` 与 plan 一致。
3. `catalogDigest` 与 plan 一致（catalog 或 workflow 变更后旧检查点作废，必须重跑）。
4. 读取 `result.json`：若上次终态为 `indeterminate` 且未显式传 `allow_indeterminate=True`，抛出 `RecoveryRequiredError`，要求人工确认后才能继续。`result.json` 缺失（真实崩溃场景）或损坏时不阻塞，checkpoint 是恢复进度的权威来源。

### 恢复执行规则

- deadline 不持久化（monotonic 时间跨进程无意义）；resume 以全新 `workflow.timeout_seconds` 预算执行。
- 执行从根节点重新走一遍：路径键在 `completedSteps` 中的 action 直接跳过，不产生命令调用与事件；其余节点正常执行，条件分支依据快照 scopes 重新求值。
- 失败的 run 可以 resume：未完成的 step（含失败的那个）会重新执行。对 unsafe-write 命令，这是人工决策的一部分。
- 浏览器与桌面 session 不跨进程存活：checkpoint 中的 `sessionId` 输出在 resume 后指向已死亡的 session，后续使用它的 step 会以 `SESSION_NOT_FOUND` / `SESSION_LOST` 失败。首版不为 session 做重建，workflow 需要重新 launch 或由人工介入。

### 人工恢复入口

- `Orchestrator.resume(plan, run_id, allow_indeterminate=True)`：显式确认 unknown effect 后继续。
- CLI `resume <workflow> --run-id <id> [--allow-indeterminate]`：对 indeterminate run 提供同一入口。

## 后果

- 安全（safe/idempotent）副作用在节点边界获得 at-most-once；unsafe 副作用仅在"结果已知且已 checkpoint"时获得 at-most-once，其余窗口仍是 at-least-once，由人工 resume 承担。
- `recovery_required` 终态下最终 step 的完成可能未入 checkpoint，resume 可能重放该 step；操作者必须核对副作用安全性。
- try 的 catch 分支若被崩溃打断，resume 时 try 主体全部"已完成"而不再抛错，剩余 catch step 不会补跑；该限制在引入显式 catch 恢复标记前记录为已知缺口。
- 状态机消费方（未来 API/UI）必须处理 `indeterminate` 与 `recovery_required`，不能把它们折叠进 `failed`。
- 本契约不提供 exactly-once、自动补偿或跨机恢复。
