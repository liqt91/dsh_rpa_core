# M6 API 调用方入门契约

状态：`done`

## 目标

ADR 0006 决策进程内 Python API 为第一形态（现有公开面冻结为 API v1）。本里程碑把"调用方视角"的契约固化成文档与可运行示例，让新调用方不读源码即可正确使用四步调用模式与运行约束。

前置依赖：ADR 0006、`tests/contract/test_public_api.py`。

## 任务

- [x] 编写 `docs/api-usage.md`：四步调用模式（load_catalog → compile → Orchestrator → RunHandle/RunResult）、executors registry 组装与 `close()`、artifacts 目录约定、状态机消费指引（含 indeterminate / recovery_required / paused 的人工确认入口）
- [x] 提供可运行的调用方最小示例 `examples/api-usage/`（workflow.json + run_workflow.py），覆盖：正常 run、读取 result.json、暂停 → 恢复
- [x] 在 README 增加 API usage (v1) 一节链接 API 使用文档与示例
- [x] 校验示例与文档中的代码可运行（脚本从零 artifacts 实际执行两次均成功）
- [x] 完整 harness 门禁通过

## 验收标准

- [x] `docs/api-usage.md` 覆盖 ADR 0006 决策的全部调用面，无与签名漂移的代码片段（文档代码即示例脚本同源）
- [x] 最小示例在仓库根目录可直接运行成功（`uv run python examples/api-usage/run_workflow.py`，输出 run #1 succeeded → run #2 paused → run #3 succeeded）
- [x] 完整门禁通过

## 范围外

- HTTP / 插件系统 / 认证
- 回调式事件订阅（ADR 0006 已推迟）
- 教程式长文（保持工程文档粒度）

## 待定问题

- ~~示例放 `examples/` 还是 `docs/` 内嵌代码块？~~ 已结论：`examples/api-usage/` + 文档链接

## 完成证据

- `docs/api-usage.md`：四步模式、执行器选择表、artifacts 约定、状态机消费指引、暂停/恢复语义、硬约束
- `examples/api-usage/workflow.json` + `run_workflow.py`：data-only 全链路（正常 run / 证据读取 / pause → resume），实测输出 `run #1: succeeded → run #2: paused → run #3: succeeded`
- `README.md`：新增 API usage (v1) 一节
- `.gitignore`：示例 artifacts 目录
- 完整门禁通过（`FULL GATE PASSED`，70 tests）
