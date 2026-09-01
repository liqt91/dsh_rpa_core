# M5 API 面决策

状态：`done`

## 决策记录

- ADR 0006（已接受）：`.harness/adr/0006-api-surface.md`
- 主方案：进程内 Python API 先行——现有公开面冻结为 API v1，不建 facade；HTTP 推迟（重估条件已写明）；排除清单除 HTTP 外维持不变。

## 目标

runtime 状态机（含 M3 恢复与 M4 暂停）与三类执行器均已通过验收，第一个调用方（CLI）之外开始出现集成需求。本里程碑只做**决策**：确定 rpa_core 的第一个 API 形态并写成 ADR，不引入 FastAPI / 数据库 / UI / MCP（规则 10 的排除清单继续有效，直到 ADR 明确放行）。

## 任务

- [x] 盘点现有契约的可暴露面：`Orchestrator.start/resume/pause`、`RunHandle`、`RunResult/RunStatus`（含 `paused` / `indeterminate` / `recovery_required`）、`events.jsonl` / `result.json` / `checkpoint.json`、25 条命令 catalog（见 ADR 0006 盘点节）
- [x] 定义候选调用方及其需求：Python 自动化脚本（首批）/ 任务编排器（中期）/ DSH 插件与 UI（远期），ADR 0006 需求表
- [x] 评估进程内 Python API 作为第一形态的充分性：结论为充分——公开签名冻结为 API v1，不新建 facade
- [x] 评估是否需要最小 HTTP 服务：推迟，重估条件 = 跨进程/跨机调用方或非 Python 生态驱动需求
- [x] 明确 API 面必须保留的运行约束：状态不折叠、人工恢复门槛不可绕过、catalog 快照绑定、用户代码仅走子进程
- [x] 编写 ADR 0006：第一 API 形态、暴露/不暴露边界、错误传播契约（RunResult + artifacts 为唯一结果通道）、版本化策略（冻结签名，不兼容变更需新 ADR）
- [x] 进程内先行：`Orchestrator` / `RunHandle` 公开签名以 `tests/contract/test_public_api.py` 冻结；三个待定问题（dry-run / events 订阅 / catalog 刷新）在 ADR 中全部给出结论

## 验收标准

- [x] ADR 0006 已接受并写明决策理由与被否决的备选方案（HTTP 论证入档）
- [x] 现有公开入口契约测试覆盖关键运行约束（签名冻结 + RunStatus 全集 + RecoveryRequiredError 出口）
- [x] 排除清单是否放行有明确结论：无新增放行；HTTP 推迟并给出重估条件
- [x] 完整 harness 门禁通过

## 范围外

- 实现任何 API 服务器或插件系统
- 认证、多租户、部署
- 调度器与自动恢复策略

## 待定问题

- ~~dry-run 编译校验~~ 已结论：`WorkflowCompiler.compile` 即 dry-run，无需新增
- ~~events 订阅模型~~ 已结论：v1 文件轮询，回调注册推迟
- ~~catalog 刷新~~ 已结论：调用方每次提交前重建，不做驻留 reload

## 完成证据

- `.harness/adr/0006-api-surface.md`
- `tests/contract/test_public_api.py`：4 项契约测试（Orchestrator/RunHandle 签名冻结、RunStatus 8 值全集、RecoveryRequiredError 公开出口）
- 完整门禁通过（`FULL GATE PASSED`，69 tests）
