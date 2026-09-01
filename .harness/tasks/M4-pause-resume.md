# M4 暂停与继续

状态：`done`

## 目标

在 M3 检查点与恢复语义之上，提供人工触发、节点边界的暂停（pause）与继续（continue）能力，不引入 UI、调度器或跨进程信号协议。

前置依赖：M3 已通过（`.harness/adr/0004-checkpoint-recovery.md`）。

## 决策记录

- 契约见 `.harness/adr/0005-pause-continue.md`；AGENTS.md 规则 8 已同步修订（`paused` 为持久化驻留状态，非终态）。

## 任务

- [x] 修改 runtime 状态机契约前编写 ADR，明确：`paused` 是持久化驻留状态（落盘证据、进程内不驻留）、与 invariant 8 的关系（规则 8 修订为"终态或 persisted paused"）、`abandoned` 边界不变
- [x] 定义暂停触发契约：`RunHandle.pause()` 仅在 action 入口与每次新 attempt 前生效，不中断进行中的 attempt；控制节点不触发
- [x] 定义暂停与取消、workflow timeout 的优先级：取消 > 暂停（同挂起判 cancelled）；暂停 > 超时 = 不再开始新 attempt；进行中 attempt 被 workflow deadline 打断时判 failed(TIMEOUT)（已知边界，测试锁定）
- [x] 定义暂停期间的 deadline 语义：`paused` 后 run 已结束无时间语义；resume 按 ADR 0004 重新计费 `workflow.timeout_seconds`
- [x] 定义 session 行为：无保活承诺，恢复后按 M3 session 丢失契约（`SESSION_NOT_FOUND` / `SESSION_LOST`）
- [x] 实现 `paused` 状态、`pauseRequested` / `runPaused` 事件与 `result.json` 证据
- [x] 实现 resume 对 paused run 的继续路径，复用 M3 五道门校验
- [x] 增加暂停边界测试：边界停止、进行中 attempt 自然完成、取消优先、全部已完成时 succeeded、deadline 打断、checkpoint 门槛复用
- [x] `RunStatus` 消费方契约：`paused` 独立取值，不折叠进 failed / cancelled

## 验收标准

- [x] pause 请求后不再开始新的 attempt；进行中的 attempt 自然完成，证据一致（`stepCompleted` 存在、后续节点无 `stepStarted`）
- [x] 暂停后 run 可通过 resume 继续，副作用契约与 M3 完全一致（`root/stepOne` 跳过、`stepTwo` 补跑）
- [x] M3 门槛对 paused run 同样生效（损坏 checkpoint resume 明确失败）
- [x] 完整 harness 与崩溃注入测试通过

## 范围外

- 暂停/继续 UI、调度器、自动恢复策略
- 跨进程暂停信号传输协议
- 自动补偿与 exactly-once
- 进程内挂起（suspend task、session 保活、deadline 冻结）

## 待定问题

- `paused` 长期不 resume 是否需要 TTL 自动转 `abandoned`？（无调度器，暂不引入）
- session 在暂停期间保活（需要资源租约）留待后续里程碑评估。

## 完成证据

- `src/rpa_core/runtime/orchestrator.py`：`PauseSignal`、`RunHandle.pause()/pause_and_wait()`、action 入口 + attempt 循环顶暂停检查、`paused` 终态路径（`pauseRequested` / `runPaused` 事件 + `result.json`）。
- `src/rpa_core/model/runtime.py`：`RunStatus.PAUSED`。
- `.harness/adr/0005-pause-continue.md` + AGENTS.md 规则 8 修订。
- `tests/unit/test_pause.py`：7 项测试（边界停止、自然完成、resume 续跑、首节点前暂停、全完成时 succeeded、取消优先、deadline 边界、checkpoint 门槛复用）。
- 完整门禁通过：55 tests + ruff + architecture + task check（`FULL GATE PASSED`）。
