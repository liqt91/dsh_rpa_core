# 工作日志

## 2026-09-02

- 修复 2026-08-31 旧条目的编码乱码（上个会话编码问题，从 PROGRESS.md 语义还原）。
- 完成 M11 切片 2-5、M12 美化中文化、M10 元素捕获（详见 PROGRESS.md 对应条目）。
- 完成 M13 编辑器元素库：元素库面板（列表/刷新/插入/删除/结构校验）、selector 字段一键捕获按钮、POST/DELETE/verify 元素端点（含 do_DELETE handler 补齐）；110 tests 全门禁。
- 建立目录约定：workflows=定义、elements=捕获工作数据（独立工作目录 + gitignore）、run_artifacts=运行证据。
- 评估 WorkBuddy 五种入驻形态：定 CLI+Skill 主路径（M15 任务单）；扩展捕获（M14）提前实装。
- 当前 active：M14 自研捕获扩展。

## 2026-09-01

- 完成 M3 检查点与恢复：版本化原子 checkpoint、路径键完成集合、`resume(plan, run_id)` 五道恢复门槛、`indeterminate` / `recovery_required` 终态与人工确认入口（含 CLI resume）、7 项崩溃注入测试，ADR 0004。
- 完成 M4 暂停与继续：`RunStatus.PAUSED` 驻留状态、action 边界暂停契约（取消 > 暂停 > 超时）、`pauseRequested` / `runPaused` 事件、resume 复用 M3 门槛、AGENTS.md 规则 8 修订，ADR 0005。
- 完成 M2.2 UIA 主线验证：WinForms 测试应用全链路 E2E（含对话框双 session、3 连跑一致）、`timeoutMs` 轮询与 EnumWindows 兜底、双后端 manifest 对称合同测试、`docs/desktop_backends.md`。
- 新增百度真实站点示例：搜索结果标题落盘纯文本；`browser.launch` 扩展 `userAgent`、python worker 修复相对路径解析。
- 完成 M4.5 数据命令：`data.writeText`（text/lines）与 `data.limit`（前 N 条截断）入册，catalog 26 条。
- 完成 M5 API 面决策（ADR 0006）：进程内 Python API 冻结为 v1、HTTP 推迟并附重估条件、公开签名契约测试。
- 完成 M6 API 调用方入门契约：`docs/api-usage.md` + `examples/api-usage/` 可运行示例（run → 证据 → pause → resume）。
- 完成 M7 命令面小扩展：win32 `timeoutMs` 对称、`data.format` fail-fast 模板渲染、UIA COM 繁忙轮询容错。
- S0/S1 捕获调研：实测 Chrome 152 封锁默认 profile CDP（136+ 上游策略）；调研 Panerelay（chrome.debugger + Native Messaging 桥）与 Playwright MCP（持久 profile 默认方案印证）；发现 `chrome://inspect` 用户授权调试开关（9222 监听但 `/json/*` 404，需显式 WebSocket URL，S1 验证中）。
- 定稿浏览器捕获传输方案 `docs/capture-transport.md`：持久 profile 主路线 + chrome://inspect / Panerelay / MCP 扩展 / 自研扩展降级链，S1 验证协议待执行。
- 全程门禁通过（最新 72 tests × 3 轮，18 features），工作区分批提交并推送。

## 2026-08-31

- 完成 `desktop.uia` 与 `desktop.win32` 双后端桌面切片：Win32 记事本 E2E 与合同测试通过；UIA 主线保留待 WinForms 验证（后由 M2.2 完成）。
- 新增 `LegacyElementImporter`：旧元素静态盘点文档、provenance/diagnostic 模型与确定性导入测试；导入器仅处理静态数据，不触及 rpa_script runtime。
- 补充项目总览 HTML（project_overview.html）和系统上下文图（docs/system_context.puml），便于快速理解仓库结构与调用链。
- 完成全量门禁验证：`uv run python .harness/scripts/check_all.py`。
