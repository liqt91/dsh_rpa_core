# M6 API 调用方入门契约

状态：`active`

## 目标

ADR 0006 决策进程内 Python API 为第一形态（现有公开面冻结为 API v1）。本里程碑把"调用方视角"的契约固化成文档与可运行示例，让新调用方不读源码即可正确使用四步调用模式与运行约束。

前置依赖：ADR 0006、`tests/contract/test_public_api.py`。

## 任务

- [ ] 编写 `docs/api-usage.md`：四步调用模式（load_catalog → compile → Orchestrator → RunHandle/RunResult）、executors registry 组装与 `close()`、artifacts 目录约定、状态机消费指引（含 indeterminate / recovery_required / paused 的人工确认入口）
- [ ] 提供可运行的调用方最小示例（`examples/api-usage/run_workflow.py` 之类，或文档内嵌完整脚本），覆盖：正常 run、读取 result.json、失败后 resume 的最小路径
- [ ] 在 README 的 Development 一节链接 API 使用文档
- [ ] 校验示例与文档中的每段代码可运行（脚本实际执行一次）
- [ ] 完整 harness 门禁通过

## 验收标准

- [ ] `docs/api-usage.md` 覆盖 ADR 0006 决策的全部调用面，无与签名漂移的代码片段
- [ ] 最小示例在仓库根目录可直接运行成功
- [ ] 完整门禁通过

## 范围外

- HTTP / 插件系统 / 认证
- 回调式事件订阅（ADR 0006 已推迟）
- 教程式长文（保持工程文档粒度）

## 待定问题

- 示例放 `examples/` 还是 `docs/` 内嵌代码块？（倾向 `examples/api-usage/` + 文档链接）

## 完成证据

仅在全部验收标准通过后填写。
