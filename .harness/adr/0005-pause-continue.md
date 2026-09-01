# ADR 0005：暂停与继续

- 状态：已接受
- 日期：2026-09-01

## 背景

M3 落地了节点边界检查点与恢复（ADR 0004），恢复的触发只有"崩溃 / 失败后人工 resume"。运维上还需要一种**主动的、干净的**停止：操作者预感外部条件将变化（如要登录、要换环境）时，希望在当前命令完成后停下来，之后从断点继续，而不是取消后从头重跑。

本 ADR 修改 runtime 状态机契约：`RunStatus` 新增 `paused`，并修订"每个 run 必达终态"的绝对表述（AGENTS.md 规则 8）。

## 决策

### `paused` 是持久化的驻留状态，不是进程内挂起

`RunHandle.pause()` 只置一个请求标记。生效点永远是 **action 边界**：进行中的 attempt 自然跑完（暂停不打断、不取消任何进行中的命令），随后在下一个节点入口停止。停止时 run 像其他终态一样收口：`pauseRequested` / `runPaused` / `runFinished` 事件、`result.json`（`status=paused`）、最终 checkpoint 全部落盘，run task 结束，执行器持有的 run 内资源随 attempt 语义释放。

首版明确排除进程内挂起（suspend task、保活 session、冻结 deadline）——那需要资源租约和心跳，属于后续里程碑。

### invariant 8 修订

原规则："每个 run 到达终态：succeeded / failed / cancelled / abandoned。" 修订为："**每个 run 到达终态，或到达持久化的 `paused` 状态；`paused` 不是终态，负有后续义务——要么被 resume，要么被显式放弃（abandoned）。**" 运行时自身不引入 TTL 或自动放弃；`abandoned` 的边界不变（仍是显式人工动作的终态）。

### 触发与边界契约

- `pause()` 可在 run 任意时刻调用；请求不排队、可被后续取消覆盖。
- 生效点只有两处：**action 节点入口**（未开始任何校验、事件与命令前）和**重试循环顶部**（每次新 attempt 开始前，含退避后的重试）。控制节点（sequence / if / forEach / try）不触发暂停；已完成节点静默跳过。
- 因此：若剩余动作全部已完成（如恰好在收尾时请求暂停），run 正常 `succeeded`，暂停不落位。
- 暂停不吞错误：暂停请求挂着时某步终局失败，run 仍判 `failed`；步骤被 try/catch 正常处理后继续走到下一个 action 边界才停。
- `pauseRequested` 事件记录被拒之门外 action 的 nodeId，`runPaused` 随附当时已完成步骤清单。

### 优先级：取消 > 暂停 > 超时

- **取消 > 暂停**：两个请求同时挂起，终态 `cancelled`（取消检查在节点入口和 attempt 竞争中都先于暂停）。
- **暂停 > 超时（attempt 层面）**：暂停后不再开始新 attempt，因此不会产生新的 attempt 超时；边界处即使 deadline 已耗尽，也是先判暂停。
- **超时是唯一能打断进行中 attempt 的非取消因素**：workflow deadline 在 attempt 进行中触发时，run 判 `failed(TIMEOUT)`，即使暂停请求已挂起——暂停契约只承诺"边界处干净地停"，不撤销已开始 attempt 的时限。此边界情形是已知且被测试锁定的。

### deadline 与 session

- `paused` 不冻结时间：deadline 只约束执行中的 run；暂停后 run 已结束，无时间语义。
- resume（无论从 failed 还是 paused）按 ADR 0004 重新计费 `workflow.timeout_seconds`。
- session 无保活承诺：暂停期间浏览器 / 桌面 session 可能存活（同进程）也可能死亡（新进程 resume）；恢复后按 ADR 0004 的 session 丢失契约处理（`SESSION_NOT_FOUND` / `SESSION_LOST`）。需要 session 的 workflow 应在暂停后重新 launch。

### 恢复

`resume()` 对 `paused` run 无特殊门槛——完全复用 ADR 0004 的五道门（结构校验、workflowId、catalogDigest、indeterminate 人工确认、恢复执行）。`indeterminate` 与 `recovery_required` 的门槛对一切 resume 生效，不因来源是 paused 而放宽。

## 后果

- `RunStatus` 消费方必须显式处理 `paused`，不得折叠进 `failed` / `cancelled`；它是唯一"非终态但已落盘证据"的取值。
- 暂停的实时性以节点为界：长 attempt（如长超时命令）会推迟暂停生效点；需要即时停止应使用取消。
- pause/resume 之间系统状态可能变化，workflow 作者应把暂停点当作与崩溃点同等对待（副作用契约完全一致）。
- 不提供调度器自动 resume、跨进程暂停信号或暂停期间资源租约。
