# M5 API 面决策

状态：`active`

## 目标

runtime 状态机（含 M3 恢复与 M4 暂停）与三类执行器均已通过验收，第一个调用方（CLI）之外开始出现集成需求。本里程碑只做**决策**：确定 rpa_core 的第一个 API 形态并写成 ADR，不引入 FastAPI / 数据库 / UI / MCP（规则 10 的排除清单继续有效，直到 ADR 明确放行）。

## 任务

- [ ] 盘点现有契约的可暴露面：`Orchestrator.start/resume/pause`、`RunHandle`、`RunResult/RunStatus`（含 `paused` / `indeterminate` / `recovery_required`）、`events.jsonl` / `result.json` / `checkpoint.json`、命令 catalog
- [ ] 定义候选调用方及其需求：CLI 之外的第一个真实调用方（如测试脚本、任务编排器、未来的 DSH 插件），列出各自需要的能力与不被允许的能力
- [ ] 评估进程内 Python API（直接 import `rpa_core`）作为第一形态的充分性：事件订阅、取消/暂停入口、artifacts 目录约定
- [ ] 评估是否需要最小 HTTP 服务：论证引入成本、生命周期管理、与单进程 orchestrator 的并发模型冲突点；不预设结论
- [ ] 明确 API 面必须保留的运行约束：用户代码不进 orchestrator 进程、一次运行一份 catalog 快照、终态/驻留状态不折叠、人工恢复门槛（indeterminate / recovery_required）不得被 API 绕过
- [ ] 编写 ADR：第一 API 形态、暴露/不暴露边界、错误传播契约、版本化策略
- [ ] 若决策为"进程内 API 先行"：把 `Orchestrator` / `RunHandle` 的公开签名冻结为契约并补契约测试；若决策为"需要 HTTP"：另立里程碑细化计划，不在本里程碑实现

## 验收标准

- [ ] ADR 已接受并写明决策理由与被否决的备选方案
- [ ] 现有公开入口（`rpa_core.cli`、`rpa_core.runtime.Orchestrator/RunHandle`）的契约测试覆盖关键运行约束
- [ ] 排除清单（FastAPI / DB / UI / MCP / 调度器 / 安装器）是否放行有明确结论
- [ ] 完整 harness 门禁通过

## 范围外

- 实现任何 API 服务器或插件系统
- 认证、多租户、部署
- 调度器与自动恢复策略

## 待定问题

- API 是否需要"提交 workflow 前的 dry-run 编译校验"作为一等能力？
- `events.jsonl` 的订阅模型：轮询文件 vs 进程内回调注册？
- catalog 快照在长驻调用方进程中如何刷新（进程重启 vs 显式 reload）？

## 完成证据

仅在全部验收标准通过后填写。
