# 工作日志

## 2026-09-02

- 修复 2026-08-31 旧条目的编码乱码（上个会话编码问题，从 PROGRESS.md 语义还原）。
- 完成 M11 切片 2-5（维护者已确认切片 1 拖拽体验）：控制节点属性表单（if 条件/循环/异常/返回）、多选 + 工具栏批量操作（连续性校验）、Ctrl+C/V 子树复制粘贴（全树 id 重映射）、快照式撤销/重做（50 步上限、输入按 focus 粒度入栈）。
- 完成 M12 编辑器美化与中文化（`docs/editor-design.md` 方案落地）：i18n.js 中文映射层 + icons.js 零构建图标、防漂移合同测试（catalog 全量覆盖 + 枚举对齐 + required 字段标签）、命令面板两行卡片（滚动条根除）、节点卡片中文重构、属性面板中文化 + 页脚术语表、悬浮操作条；修复 devserver 413 未排空 body 的 Windows RST 缺口。
- 完成 M10 元素捕获：桌面 UIA 窗口作用域 hit-test（免疫安全软件覆盖层劫持）+ 浏览器 persistent/chrome-inspect-ws 双传输 + picker 注入与 selector 回验 + ElementDescriptor 落库（/api/elements）；browser.launch 扩展 userDataDir；真实 E2E ×2（浏览器合成点击全链路 + 桌面 WinForms 执行器回验命中）。
- 过程修复：devserver 会话注册表 RLock 死锁、agent stdout 编码（cp936→UTF-8 reconfigure）、UIA 首次枚举抖动重试。
- 下一里程碑切至 M13 编辑器元素库（捕获闭环消费端）。

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
