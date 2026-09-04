# 错误码与恢复

rpa-core 运行失败时 stdout 打印 `RunResult`，`error.code` 是稳定错误码。

## 常见错误码

| code | 含义 | 恢复 |
|---|---|---|
| `ELEMENT_NOT_FOUND` | selector/locator 没命中（页面变了或选择器失效） | 用 `rpa-core capture` 重新捕获元素，更新 selector |
| `SESSION_NOT_FOUND` | 浏览器/桌面 session 不存在或已丢失 | 重新 launch（session 不跨进程存活） |
| `TIMEOUT` | 命令超时（waitFor 等） | 调大 timeoutMs 或确认元素存在 |
| `CANCELLED` | 被人工取消 | 可 resume 续跑 |
| `EXECUTOR_FAILED` | 执行器底层失败 | 看 error.message 细节 |
| `CHECKPOINT_FAILED` | 进度持久化失败 | 核对副作用安全性后 resume |

## 运行终态

| status | 含义 | 动作 |
|---|---|---|
| `succeeded` | 成功 | 读 `return_value` / `result.json` 的 outputs |
| `failed` | 失败且结果确定 | 修复后 `resume`（未完成步骤重跑） |
| `cancelled` | 取消 | 可 resume |
| `indeterminate` | 失败且外部结果未知 | **人工核查外部系统**，确认后 `resume --allow-indeterminate` |
| `recovery_required` | checkpoint 持久化失败 | 核对副作用后 resume |
| `paused` | 人工暂停（非终态） | 之后 resume 或显式放弃 |

## resume

```text
rpa-core resume wf.json --run-id <run_id> [--allow-indeterminate]
```

`run_id` 见 `run_artifacts/<run_id>/result.json`。resume 校验 catalog digest（命令目录快照不一致拒绝），`indeterminate` 必须显式 `--allow-indeterminate`（人工确认不可省略）。
