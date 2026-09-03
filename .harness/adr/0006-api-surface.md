# ADR 0006：第一 API 面决策

- 状态：已接受
- 日期：2026-09-01

## 背景

runtime 状态机（ADR 0004 恢复、ADR 0005 暂停）、三类执行器与 25 条命令契约均已通过验收；第一个调用方是 CLI。仓库外开始出现集成诉求：用 Python 驱动 rpa_core 跑 workflow 的脚本、未来的任务编排层与 DSH 插件。AGENTS.md 规则 10 的排除清单（FastAPI / 数据库 / UI / MCP / 调度器 / 安装器）需要在本 ADR 中逐项给出"是否放行"的结论。

## 现有公开面盘点

进程内可直接 import 的稳定入口：

- `rpa_core.runtime.Orchestrator`：`start(plan, inputs)` / `run(plan, inputs)` / `resume(plan, run_id, *, allow_indeterminate)`；`RunHandle`：`cancel()` / `pause()` / `wait()` / `cancel_and_wait()` / `pause_and_wait()`
- `rpa_core.runtime.checkpoint.CheckpointStore`（M3 恢复的进度权威）
- `rpa_core.runtime.events.EventWriter`（append-only events.jsonl）
- `rpa_core.catalog.load_catalog` / `rpa_core.compiler.WorkflowCompiler.compile`（编译与 dry-run 校验已是公开纯函数）
- 证据产物约定：`<artifacts>/<run_id>/` 下的 `events.jsonl`、`result.json`、`checkpoint.json`
- CLI 子命令：`validate` / `run` / `resume`（`--allow-indeterminate`）

状态机取值：`succeeded / failed / cancelled / abandoned / indeterminate / recovery_required / paused`（M3/M4 契约，禁止折叠）。

## 候选调用方与需求

| 调用方 | 需求 | 不需要 |
|---|---|---|
| Python 自动化脚本（首批） | 编译 workflow → run → 读 RunResult / artifacts；失败后 resume；长任务 pause | 高并发、远程调用 |
| 任务编排器（中期） | 批量提交 run、按事件流感知进度、崩溃后按 checkpoint 恢复 | 多租户 |
| DSH 插件 / UI（远期） | 进度订阅、人工确认入口（indeterminate ack） | —— |

## 决策

### 1. 第一形态 = 进程内 Python API（即现有公开面冻结为 API v1）

**不新建 facade 层。** `Orchestrator / RunHandle / load_catalog / WorkflowCompiler` 的现有签名即 API v1 契约，用合同测试冻结：

- 调用方式：调用方自行 `load_catalog(commands) → WorkflowCompiler.compile(workflow, capabilities) → Orchestrator(catalog, registry, artifacts).run(plan)`；四步皆是公开纯函数/类，无隐藏状态。
- 生命周期归调用方：executor registry 的创建与 `close()` 由调用方持有（与 CLI 相同模式）；一个进程一个 registry。
- `RunResult` 与 artifacts 文件是唯一结果通道；`events.jsonl` 是唯一进度通道。

理由：调用方与 runtime 同进程同语言，无序列化/网络边界；现有签名已被 CLI 与 65 项测试实际验证；新增 facade 在只有一个调用方时是纯间接层。

### 2. HTTP 服务：推迟，不放行进当前阶段

否决理由（记录备查，非永久否决）：

- 单进程 asyncio orchestrator 与多请求并发模型存在生命周期冲突（长任务 run、取消、暂停都挂在进程内 task 上），需要 run 注册表 + 进程外可观测性，复杂度未换回当前不存在的需求。
- 需要新增错误映射、认证、部署面，全部落在规则 10 排除清单。
- 触发重估条件：出现**跨进程/跨机**调用方，或编排器需要被非 Python 生态驱动。

### 3. 排除清单结论（规则 10）

- 放行：无新增。进程内 API 本就存在，无需放行动作。
- 继续排除：FastAPI / 任何 HTTP 框架（本 ADR 明确推迟）、数据库（artifacts 文件即存储）、UI、MCP、调度器、安装器——各自需要新的 ADR。

### 4. API 面必须保留的运行约束（合同测试锁定）

1. 状态不折叠：`indeterminate` / `recovery_required` / `paused` 是独立 `RunStatus` 取值，任何入口不得映射为 `failed` / `cancelled`。
2. 人工恢复门槛不可绕过：对 `indeterminate` run，`resume()` 不带 `allow_indeterminate=True` 必须拒绝。
3. catalog 快照：一次 plan 编译绑定一份 digest；resume 校验 digest，不匹配拒绝。
4. 用户代码不进 orchestrator 进程：data/transform 命令只经 `python.worker` 子进程。

### 5. 三个待定问题的结论

- **dry-run 编译校验**：已满足——`WorkflowCompiler.compile` 公开且在 run 前抛出全部静态错误；CLI `validate` 即其薄封装。不新增 API。
- **events 订阅模型**：v1 = 调用方轮询/ tail `events.jsonl`（append-only、seq 单调，已是稳定契约）。进程内回调注册推迟到出现真实长驻调用方时再议（会引入背压问题）。
- **catalog 刷新**：长驻调用方在每次提交新 run 前**重建** catalog/plan（`load_catalog` 是廉价纯函数）；不提供驻留式 reload，避免"一次运行两份契约"的窗口。

## 后果

- API v1 契约 = 冻结签名；后续不兼容变更需要新 ADR 与版本号动作。
- 调用方入门文档（`docs/api-usage.md`）与合同测试落在下一里程碑（M6）。
- HTTP 重估条件已写明，避免"是否该上服务"反复讨论。
- 规则 10 的排除清单除本 ADR 明确推迟的 HTTP 外维持不变。

## §6 通道对齐原则（2026-09-03 增补，维护者提议）

**能力层（rpa_core 包）是唯一能力实现；CLI 与 devserver 是两个薄通道。**

- **CLI 优先**：能力层每项能力必须有 CLI 入口（agent/脚本通道，M15 WorkBuddy 主路径）；devserver 只做人类交互，不得独占能力。
- **devserver 复用方式 = 直接 import 能力层**（现状即如此，架构检查强制隔离），**不是 spawn 解析 CLI 子进程**（stdout 解析脆弱、双进程开销）。唯一例外是运行操控（长任务/取消），子进程隔离是机制选择，见运行控制 backlog 条目。
- 新能力落地顺序：库函数 → CLI 子命令 →（如需人类交互）devserver 端点。
- 当前缺口（CLI 无入口，仅 devserver 有）：`catalog` 摘要、`capture` 交互捕获、`elements` 读写/verify——入 BACKLOG「CLI 通道对齐」条目补齐。
