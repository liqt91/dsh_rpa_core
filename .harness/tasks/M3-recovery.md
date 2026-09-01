# M3 检查点与恢复语义

状态：`done`

## 目标

定义并验证安全的节点边界恢复机制，不对结果未知的外部副作用承诺透明重放。

## 决策记录

- 状态机契约见 `.harness/adr/0004-checkpoint-recovery.md`。

## 任务

- [x] 定义持久化内容：`version=1`、`workflowId`、`catalogDigest`、`completedSteps`（路径键，forEach 迭代带 `#i` 段）、`scopes`、`returnValue`
- [x] 定义终态和可恢复状态，包括 `recovery_required` 和 `indeterminate`
- [x] 定义 workflow 或 catalog 版本变化时的行为：resume 校验 workflowId 与 catalogDigest，不匹配即拒绝
- [x] 定义浏览器和桌面 session 丢失后的行为：session 不跨进程存活，恢复后引用死亡 session 的 step 以 `SESSION_NOT_FOUND` / `SESSION_LOST` 失败
- [x] 根据副作用契约定义安全重放规则：仅 checkpoint 已持久化的 action 获得跳过；未持久化的按 replay 契约重放，unsafe 由人工 resume 决定
- [x] 实现原子检查点持久化和损坏检测：临时文件 + replace，`version` 与结构校验，损坏明确抛 `CheckpointError`
- [x] 在 action 完成和证据持久化边界增加崩溃注入测试
- [x] 为结果不确定的副作用提供人工恢复入口：`resume(..., allow_indeterminate=True)` 与 CLI `resume --allow-indeterminate`
- [x] 修改 runtime 状态机契约前编写 ADR（0004）

## 验收标准

- [x] 在安全节点之间崩溃后恢复，不重复执行已经完成的安全副作用（checkpoint 写入后崩溃 → resume 跳过）
- [x] 外部写入结果未知时进入 `indeterminate`，绝不自动判定成功或重试（即使 manifest `retryable=true`）
- [x] Resume 前能够检查 catalog 和 plan 不匹配
- [x] 损坏或不完整的 checkpoint 明确失败
- [x] 完整 harness 和崩溃注入测试通过

## 范围外

- 分布式调度和 worker lease
- Exactly-once 保证
- 暂停和继续 UI
- 自动补偿
- catch 分支被崩溃打断后的补跑（已记录于 ADR 后果）

## 待定问题

- 使用状态快照、事件重放，还是混合模型？（首版采用状态快照）
- 引入数据库持久化前需要怎样的 artifact store 抽象？

## 完成证据

- `src/rpa_core/runtime/checkpoint.py`：版本化 payload、结构校验、原子写入、`RecoveryRequiredError`。
- `src/rpa_core/runtime/orchestrator.py`：路径键完成集合、`resume(plan, run_id, allow_indeterminate)`、`indeterminate` / `recovery_required` 终态、`_run`/`_resume` 共享终态证据路径。
- `src/rpa_core/cli.py`：`resume` 子命令（`--run-id`、`--allow-indeterminate`），checkpoint 错误以退出码 2 报告。
- `tests/unit/test_checkpoint_recovery.py`：7 项测试覆盖事件边界崩溃、checkpoint 边界失败、损坏 checkpoint、digest 不匹配、unknown effect → indeterminate 不重试、人工 ack 恢复、foreach 按迭代恢复。
- 完整门禁通过：48 tests + ruff + architecture + task check（`FULL GATE PASSED`）。
