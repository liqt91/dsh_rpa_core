# 工作日志

## 2026-09-03

- 完成 M17 编辑器进阶：变量补全（${ 引用路径下拉）、全屏画布、运行状态高亮（devserver 新增只读 /api/runs/latest-events 端点读 run_artifacts events.jsonl，画布节点按 node_id 标状态色）；E2E +3；full gate 184 tests。
- rpa-core-runtime 0.1.0 发布 PyPI（撞名 rpa-core → 改名 rpa-core-runtime）；wheel 内含 26 条命令清单；干净环境 pip install 实测可用；WorkBuddy 连接器 init 前置（可安装源）就绪。
- 完成 M14 收口：bsk 执行传输 + 自研 content-script 扩展无缝捕获 + 混合捕获 auto（装扩展网页走插件/没装走 UIA/先回传者胜）+ 桌面 hover 细粒度 + 元素编辑确认对话框 + 元素库「＋捕获」入口（网页/桌面二选）。实机验收：登录态小红书、跨浏览器无缝、混合双通道。feature capture-extension 通过。active → M15 WorkBuddy 连接器。
- 混合捕获实机验收：插件腿（网页 Ctrl+Click）+ UIA 腿（桌面 F9）+ 让位（正文区让位）双通道通过；修 stale-root bug（无 handle 虚拟元素根窗口用 win32 链解析）。并实测证伪「UIA 兜底网页内容」：命中渲染层 Pane、verifyCount=29 不可用，记入 capture-transport.md §2.6（网页正文无 UIA 退路）。
- hover 捕获性能与横扫漏框：(3) 帧节奏与 GDI 泄漏——show_rect 每帧泄漏 inner region 句柄（补 DeleteObject）、rect 未变跳过重建/SetWindowPos、hit 节流 60→30ms 帧间隔 30→15ms；(4) 横扫漏框根因——图标间隙命中大背景 Pane 每帧触发 Progman 全树 DFS（300ms 卡顿帧=整图标滑过），改为 DFS 仅快路径完全失败时兜底，粗容器 hover 直接框粗 rect（响应优先）、捕获瞬间仍全量 DFS 保精度；更正前日结论：浏览器 UI 骨架（TabStrip/Omnibox/工具栏）本就暴露在 UIA 树（此前被 drill 的 handle 防环误杀+skip 列表一刀切拦住）。full gate 159 tests。
- hover 捕获两轮修复：(1) 悬浮框 region API 用 pywin32 不存在的 CreateRectRgn （改 gdi32 ctypes），且异常被循环静默吞掉表现为"没效果"；Ctrl+C 慢取消——queue.get 长 timeout 吞键盘中断，改短切片轮询 + CLI KeyboardInterrupt 即退；(2) 细粒度：_drill_to_leaf 向下钻最深叶子（Label/Text 级）+ UIA 虚拟元素盲区用 win32 窗口链根窗口 + 窗口作用域枚举兜底（Terminal 标签文字/图标、桌面图标），浏览器 UI 区域确认是 Chromium 设计不进 UIA（非缺陷）。full gate 159 tests。
- 完成 M14a bsk 执行传输：`browser.launch transport:"bsk"`（v1.1.0，browserInstanceId + 能力差异声明 CSS only/仅主 frame）；运行期 BskSession（session 映射、取消逐命令检查、close 强制 session stop）；抽能力层 bsk_client.py 统一 bsk 子进程协议（devserver 捕获与运行期执行器共用，消除分叉）；executor 合同 10 项；修复 executors/browser.py 被 PowerShell 写入引入的 BOM。full gate 148 tests。
- 完成 M14.5 CLI 通道对齐（ADR 0006 §6 落地）：`rpa-core catalog`（digest+清单，与 load_catalog 同源）、`capture browser|desktop`（一次性会话；bsk 默认传输 + Ctrl+Click 合成验收、desktop F9/point；save-as+flow 落库流程元素资产；cancel+close 强制）、`elements list|show|verify`；元素校验下沉 model/capture.py（devserver 薄封装）；cli_parity 合同 7 项；README CLI 段更新；full gate 138 tests。

- 通道对齐评估（维护者提议）：CLI 优先原则成立，落地形态修正为「能力层唯一实现 + CLI/devserver 双薄通道」，devserver 复用 = import 能力层而非 spawn 解析 CLI；ADR 0006 §6 增补；CLI 缺口（catalog/capture/elements 子命令）入 BACKLOG「CLI 通道对齐」。

- 安装 Tencent BrowserSkill（bsk 0.1.11 CLI/daemon + Edge/Chrome 商店扩展 0.2.0），doctor 全绿；Chrome 版存在且可用（不走 CDP 端口，不受 Chrome 152 默认 profile 封锁影响）。
- M14 按 spike 结论从"自研扩展"改为 bsk 单扩展路线（任务单重写）：probe 验证 evaluate 注入 picker（elementsFromPoint 变体绕过 bsk ControlOverlay 遮罩）→ **Ctrl+Click 捕获手势**（普通点击穿透不捕获，用户可正常导航）→ 轮询读回 → 回验命中，Edge 152 全链路通过。
- 实装 `BrowserBskCaptureSession`（session start/stop 映射、取消强制 stop、runner 注入可测）+ devserver transport 白名单加 `bsk` + cli 工厂分发；合同测试 8 项。
- 实机验收：Edge 必应搜索框 Ctrl+Click 捕获 `#sb_form_q`（verifyCount=1）落库 `workflows/demo/elements/bingSearchBox.json`，重新导航回验命中；cancel 干净回收 Agent Window。
- 路线决策（ADR 0010）：编辑器保持 web 页面，捕获遮挡实证矩阵（bsk Agent Window 独立/窗口作用域 hit-test 免疫/仅裸屏幕兜底路径会遮挡）；桌面客户端薄壳入 BACKLOG 远期（重估条件三条）；M14 desktop 捕获分支补"先 attach 目标窗口"。
- full gate 131 tests；当前 active：M14（剩余：M14a 执行传输、能力声明、元素库「＋捕获」入口、登录态验收）。

## 2026-09-02

- 修复 2026-08-31 旧条目的编码乱码（上个会话编码问题，从 PROGRESS.md 语义还原）。
- 完成 M11 切片 2-5、M12 美化中文化、M10 元素捕获（详见 PROGRESS.md 对应条目）。
- 完成 M13 编辑器元素库：元素库面板（列表/刷新/插入/删除/结构校验）、selector 字段一键捕获按钮、POST/DELETE/verify 元素端点（含 do_DELETE handler 补齐）；110 tests 全门禁。
- 建立目录约定：workflows=定义、elements=捕获工作数据（独立工作目录 + gitignore）、run_artifacts=运行证据。
  - 注：该条已被 M13.1 取代——elements 不再独立，改随流程入版本库。
- 评估 WorkBuddy 五种入驻形态：定 CLI+Skill 主路径（M15 任务单）；扩展捕获（M14）提前实装。
- 会话收尾：README 中文完整重构（定位/约束/里程碑/布局/CLI/三种形态/门禁）；devserver 孤儿进程排查（后台启动未回收占 8765）；流程组织与前端能力四项答复（运行控制不立项记 backlog、元素库前端捕获入口残缺待 M14 补）；M13.1 落地流程目录化 + 元素即流程资产。
- 当前 active：M14 自研捕获扩展。
- 完成 M13.1 流程目录化与元素即流程资产：每流程一个目录 `workflows/<name>/workflow.json`（WorkflowDirStore），捕获元素作为流程资产存 `<name>/elements/*.json`（可入版本库）；元素端点嵌套 `/api/workflows/{name}/elements[/{el}[/verify]]`，capture `pick` 的 saveAs 需带 flow；去掉全局 elements 根与 CLI --elements；前端元素库按当前流程名加载并即时刷新。运行控制结论记 BACKLOG（最小范围，不立项）。full gate 122 tests。

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
