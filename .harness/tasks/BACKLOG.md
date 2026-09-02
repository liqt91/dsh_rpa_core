# 任务总表

任务状态使用：`planned`（计划中）、`active`（进行中）、`blocked`（受阻）、`done`（已完成）。

## 当前任务

- [ ] M13 编辑器元素库（`active`）
  - 计划：`M13-element-library.md`
  - 完成门槛：捕获 → 入库 → 插入节点 → 保存全流程在编辑器内闭环；门禁通过。

## 后续任务

- [ ] M11 后续：变量补全、全屏编辑器、运行状态高亮（进阶项，按需另立）

## 远期任务

- [ ] UI、DSH、MCP、调度器和安装器集成（`planned`）
  - 逐项放行与否以 ADR 0006 结论为准；操控型 HTTP 推迟不变。

## 已完成

- [x] M12 编辑器美化与中文化（`done`）
  - 计划：`M12-editor-polish.md`
  - 证据：i18n 映射层 + 防漂移门禁、命令面板两行卡片（滚动条根除）、节点卡片中文重构、属性面板中文 + 术语表、悬浮操作条；99 tests 全门禁。
- [x] M10 元素捕获（`done`）
  - 计划：`M10-capture.md`
  - 证据：桌面 UIA 窗口作用域 hit-test（免疫覆盖层劫持）+ 浏览器 persistent/chrome-inspect-ws 双传输、ElementDescriptor 落库、真实 E2E（浏览器合成点击 + 桌面执行器回验 matchedCount == 1）、M10c 立项设计；105 tests 全门禁。
- [x] M11 编辑器交互升级：结构化树形画布（`done`）
  - 计划：`M11-editor-tree.md`
  - 证据：维护者确认切片 1 拖拽体验；切片 2-5（控制节点表单、多选批量、复制粘贴 id 重映射、快照撤销）；93 tests 全门禁。

## 已完成（早期）

- [x] M9 编辑器 v1：零构建单页（`done`）
  - 计划：`M9-editor.md`
  - 证据：ADR 0008 放行 + `devserver/static/index.html` 单页（命令面板/线性画布/schema 表单/编译回显/打开保存闭环）+ `GET /` 唯一静态路由 + Playwright Chromium E2E 与 3 项合同测试；87 tests + 全门禁。

- [x] M8 设计期服务与编辑器架构决策（`done`）
  - 计划：`M8-devserver.md`
  - 证据：ADR 0007（devserver 隔离边界/捕获契约/待定问题结案）+ `rpa_core.devserver` 骨架（catalog/compile/workflows CRUD + 捕获 501 占位，零新依赖）+ CLI devserver 子命令 + 架构检查隔离断言 + 11 项合同测试；curl 全流程实测；83 tests + 全门禁。S1 验证移入 `M10-capture.md`。

- [x] M7 命令面小扩展（`done`）
  - 计划：`M7-command-surface.md`
  - 证据：win32 timeoutMs 对称（findElement 此前声明未实现一并修复）、data.format fail-fast 模板渲染、UIA COM 繁忙容错；S0 实验确认 Chrome 152 封锁默认 profile CDP；72 tests × 3 轮 + 全门禁。

- [x] M6 API 调用方入门契约（`done`）
  - 计划：`M6-api-usage.md`
  - 证据：`docs/api-usage.md` + `examples/api-usage/` 可运行示例（run/pause/resume 全链路实测）+ README 链接；70 tests + 全门禁通过。

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
