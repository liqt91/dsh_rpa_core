# 任务总表

任务状态使用：`planned`（计划中）、`active`（进行中）、`blocked`（受阻）、`done`（已完成）。

## 当前任务

- [ ] M2.1 旧元素库导入器（`active`）
  - 计划：`M2.1-element-importer.md`
  - 完成门槛：旧元素静态导入、类型化 locator 规范化、确定性输出、无 runtime 依赖。

## 后续任务

- [ ] M3 检查点与恢复语义（`planned`）
  - 计划：`M3-recovery.md`
  - 依赖：M1.2 和 M2

## 远期任务

- [ ] M4 暂停与恢复（`planned`）
  - 仅在 M3 的恢复语义验证通过后编写详细计划。
- [ ] API、UI、DSH、MCP、调度器和安装器集成（`planned`）
  - 在架构验证阶段继续排除在范围外。

## 已完成

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
