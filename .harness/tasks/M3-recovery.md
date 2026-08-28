# M3 检查点与恢复语义

状态：`planned`

## 目标

定义并验证安全的节点边界恢复机制，不对结果未知的外部副作用承诺透明重放。

## 任务

- [ ] 定义持久化内容：plan、catalog digest、scopes、控制栈、attempt、deadline 和副作用状态。
- [ ] 定义终态和可恢复状态，包括 `recovery_required` 和 `indeterminate`。
- [ ] 定义 workflow 或 catalog 版本变化时的行为。
- [ ] 定义浏览器和桌面 session 丢失后的行为。
- [ ] 根据副作用契约定义安全重放规则。
- [ ] 实现原子检查点持久化和损坏检测。
- [ ] 为 action 完成和证据持久化边界增加崩溃注入测试。
- [ ] 为结果不确定的副作用提供人工恢复入口。
- [ ] 修改 runtime 状态机契约前编写 ADR。

## 验收标准

- [ ] 在安全节点之间崩溃后恢复，不重复执行已经完成的安全副作用。
- [ ] 外部写入结果未知时进入 `indeterminate`，绝不自动判定成功或重试。
- [ ] Resume 前能够检测 catalog 或 plan 不匹配。
- [ ] 损坏或不完整的 checkpoint 明确失败。
- [ ] 完整 harness 和崩溃注入测试通过。

## 范围外

- 分布式调度和 worker lease。
- Exactly-once 保证。
- 暂停和继续 UI。
- 自动补偿。

## 待定问题

- 使用状态快照、事件重放，还是混合模型？
- 引入数据库持久化前需要怎样的 artifact store 抽象？

## 完成证据

仅在全部验收标准通过后填写。
