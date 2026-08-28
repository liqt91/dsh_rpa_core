# ADR 0002：副作用、重放与幂等契约

- 状态：已接受
- 日期：2026-08-28

## 背景

M1.1 已经实现有界自动重试，但仅使用 `manifest.retryable` 和 executor 返回的 `error.retryable` 判断。这个条件无法回答命令是否可能已经改变外部状态。浏览器点击、页面导航、文件写入和 session 生命周期都可能在“外部操作成功、结果尚未返回”时失败。如果 Runtime 仅凭错误类型重试，可能重复提交表单、重复触发服务端操作或破坏 session 状态。

## 决策

每个 stable command 必须声明一个 `effect` policy，包含三个独立维度：

- `kind`：`pure`、`read`、`idempotent-write`、`unsafe-write`、`session`。
- `replay`：`safe`、`idempotent`、`unsafe`。
- `idempotency`：`none`、`derived`、`required`。

约束如下：

1. 只有 `replay=safe` 或 `replay=idempotent` 的命令可以声明 `retryable=true`。
2. `replay=idempotent` 时，`idempotency` 不能为 `none`。
3. `unsafe-write` 和 `session` 在首版中必须使用 `replay=unsafe`，禁止自动重试。
4. `pure` 和 `read` 使用 `replay=safe`。
5. `idempotent-write` 使用 `replay=idempotent`，其幂等键必须由输入提供或从稳定资源标识派生。
6. Compiler 拒绝 workflow 为 `replay=unsafe` 的 action 配置 `retry_count > 0`。
7. Runtime 再次检查该约束，防止绕过 Compiler 的手工 plan 或未来兼容路径。

`CommandResult.effects` 改为类型化的 `EffectRecord[]`。每条记录至少包含：

- 确定性的 `effectId`；
- `kind`；
- 生命周期 `status`：`prepared`、`committed`、`failed`、`unknown`；
- `resource`；
- 可选 `idempotencyKey`；
- 可选诊断 `details`。

`effectId` 使用 `runId + stepId + attempt + effect index + kind + resource` 派生，保证同一次 attempt 内稳定、不同 attempt 之间不同。它是审计关联 ID，不是 exactly-once token。

首版记录成功结果产生的 `committed` effect。失败发生在外部结果不确定的边界时，executor 后续应返回 `unknown` effect；本里程碑不实现恢复或自动补偿。

## 命令分类

- `browser.launch`：`session / unsafe / none`
- `browser.navigate`：`unsafe-write / unsafe / none`
- `browser.click`：`unsafe-write / unsafe / none`
- `browser.input`：`unsafe-write / unsafe / none`
- `browser.waitFor`：`read / safe / none`
- `browser.getText`：`read / safe / none`
- `browser.queryAll`：`read / safe / none`
- `browser.close`：`session / unsafe / none`
- `data.writeJson`：`idempotent-write / idempotent / derived`，幂等键由规范化输出路径派生。

`browser.navigate` 不声明安全重放，因为导航目标可能通过 GET 或重定向触发服务端副作用。保守分类优先于未经证明的可重试性。

## 后果

- 部分原本标记为 `retryable=true` 的命令会被改为不可自动重试。
- Read effect 也会持久化，因为它是实际执行证据，而不仅是 manifest 推导信息。
- Runtime 可以在持久化恢复实现前阻止最危险的重复副作用。
- 该契约不提供分布式事务、exactly-once、补偿或崩溃恢复保证。
