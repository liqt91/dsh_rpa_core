# 任务总表

任务状态使用：`planned`（计划中）、`active`（进行中）、`blocked`（受阻）、`done`（已完成）。

## 当前任务

- [ ] M6 API 调用方入门契约（`active`）
  - 计划：`M6-api-usage.md`
  - 完成门槛：`docs/api-usage.md` + 可运行最小示例 + README 链接 + 全门禁。

## 后续任务

- [ ] M7（`planned`）
  - 依调用方实际需求再立项（候选：win32 timeoutMs 对称、data.format 模板化）。

## 远期任务

- [ ] UI、DSH、MCP、调度器和安装器集成（`planned`）
  - 逐项放行与否以 ADR 0006 结论为准；HTTP 已明确推迟并附重估条件。

## 已完成

- [x] M5 API 面决策（`done`）
  - 计划：`M5-api-decision.md`
  - 证据：ADR 0006（进程内 API 先行、HTTP 推迟、排除清单结论）+ 公开签名冻结契约测试 4 项；69 tests + 全门禁通过。

- [x] M4.5 数据命令补齐（`done`）
  - 计划：`M4.5-data-commands.md`
  - 证据：`data.writeText` + `data.limit` 契约与合同测试、百度示例纯文本产物（limit 10）；65 tests + 全门禁通过。

- [x] M2.2 Windows 桌面 UIA 主线验证（`done`）
  - 计划：`M2.2-uia-winforms.md`
  - 证据：WinForms 测试应用全链路 E2E（含对话框 + 双 session、3 连跑一致）、timeoutMs 轮询 + EnumWindows 兜底、manifest 对称合同测试、`docs/desktop_backends.md` 分工文档。
- [x] M4 暂停与继续（`done`）
  - 计划：`M4-pause-resume.md`
  - 证据：ADR 0005 + `RunStatus.PAUSED` + action 边界暂停/恢复契约 + 7 项边界测试；AGENTS.md 规则 8 修订；55 项测试与完整门禁通过。
- [x] M3 检查点与恢复语义（`done`）
  - 计划：`M3-recovery.md`
  - 证据：版本化 checkpoint + 路径键完成集合 + `indeterminate` / `recovery_required` 终态 + 崩溃注入测试；ADR 0004；48 项测试与完整门禁通过。
- [x] M2.1 旧元素库导入器（`done`）
  - 计划：`M2.1-element-importer.md`
  - 证据：静态旧元素盘点 + `LegacyElementImporter` + provenance/diagnostic 模型 + 确定性 fixture 测试完成；全门禁通过。
- [x] M2 Windows 桌面自动化垂直切片（`done`）
  - 计划：`M2-desktop.md`
  - 证据：`desktop.uia` + `desktop.win32` 双后端、Win32 记事本 E2E、合同测试、完整 harness 门禁通过。

- [x] M1.2 副作用契约（`done`）
  - 计划：`M1.2-effects.md`
  - 证据：类型化副作用、重放和幂等 policy、unsafe retry 双层拒绝、真实 E2E effect 证据，以及 30 项测试通过。
- [x] M1.1 Runtime 正确性（`done`）
  - 计划：`M1.1-runtime.md`
  - 证据：稳定错误分类、任务与子进程清理、有界重试、可靠运行证据，以及 24 项测试通过。
- [x] M1.0 确定性浏览器与 Python worker 垂直切片（`done`）
  - 证据：`../PROGRESS.md`
