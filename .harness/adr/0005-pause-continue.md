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

### 跨进程暂停信号（M21 增补，2026-09-19）

本 ADR 首版把暂停限定在进程内，**跨进程暂停信号列为不做**。但 GUI 是主力形态（ADR 0016），而 GUI 经 `RunManager` spawn 子进程执行（ADR 0011）——进程内 `asyncio.Event` 外部够不着，"暂停"这个能力在 GUI 里等于不存在。M21 补齐，语义不变、只补通路：

- **通路**：控制文件 `<artifacts>/<run_id>/control.json`（`rpa_core.control_channel`）。run 子进程起一个轮询任务把文件状态镜像到 `RunHandle.pause()/resume()`；外部进程（GUI / `rpa-core pause`）写文件。选文件而非端点/管道的理由：零新依赖、跨平台、崩溃后仍可从文件查证请求史；且不引入 web 端口（ADR 0016），不让 GUI 进程承载 runtime（ADR 0011）。
- **仍然是"请求"不是"状态"**：生效点照旧是节点边界。真正停下的标志是 `result.json` 落成 `paused`。
- **一次性方向由重置保证**：paused 收口后控制文件仍留 `pause: true`，故 `resume` 前必须重置（CLI 的 resume 分支负责），否则新进程一启动就再次暂停。
- **撤销**：`RunHandle.resume()` 清掉尚未生效的暂停请求（本 ADR「请求不排队、可被后续取消覆盖」的另一半）。run 一旦抛出 `PauseSignal` 进入收口，撤销不再生效——那时"继续"是 `resume`（新进程）。
- **不改变"暂停即收口"**：进程仍然退出、不做进程内挂起、不引入资源租约或心跳。因此"继续"对**浏览器**流程可用（标签页跨进程存活，见 ADR 0004 修订），对**桌面**流程不可用（pywinauto 会话绑定进程）。
- 控制通道的任何故障（文件缺失/半写/损坏）一律按「无请求」处理，不影响 run 本身。

## 后果

- `RunStatus` 消费方必须显式处理 `paused`，不得折叠进 `failed` / `cancelled`；它是唯一"非终态但已落盘证据"的取值。
- 暂停的实时性以节点为界：长 attempt（如长超时命令）会推迟暂停生效点；需要即时停止应使用取消。
- pause/resume 之间系统状态可能变化，workflow 作者应把暂停点当作与崩溃点同等对待（副作用契约完全一致）。
- 不提供调度器自动 resume 或暂停期间资源租约。跨进程暂停信号由 M21 增补（见上一节），
  但仍然只是"把请求送到边界"——不做进程内挂起、不保活 session。

## 断点与单步（M24 增补）

M24 在既有暂停通道上做**最小扩展**，不改变本节任何既有契约（「请求不是状态」、生效点
= 节点边界、`paused` 是持久化驻留状态）：

- **断点 = 声明式的停**：`run --breakpoints <nodeId,...>` 把节点 id 集合交给 run；执行到
  这些节点**之前**收口为 `paused`。与用户暂停共用同一个判定点与同一条收口路径，只是
  触发源不同（`pauseReason = user | breakpoint | step`）。
- **断点随检查点持久化**：resume 是新进程，而控制文件在 resume 前会被 `reset_control()`
  清掉——断点若只放在控制通道就会在「继续」后丢失。因此 `checkpoint.json` 增可选字段
  `breakpoints` / `consumedBreakpoints`（旧检查点缺省为空，向后兼容）。
- **`consumedBreakpoints` 防死循环**：断点是声明式的，命中一次后若不作废，resume 会在
  同一节点反复停下。命中即计入已消费集合（随检查点落盘），resume 恢复后跳过它。
- **单步**：`resume --step` 只执行一个节点，随后在下一个边界以 `pauseReason = step` 停下。
  实现上不是「立刻置暂停位」（那会被同一节点内的重试边界检查提前拦下，导致该节点根本
  没执行），而是「放行本节点 + 登记 step_after，下一个边界返回 step」。
- **边界**：条件断点、日志断点、变量监视、调用栈、运行中热更新断点都不在本期范围。
