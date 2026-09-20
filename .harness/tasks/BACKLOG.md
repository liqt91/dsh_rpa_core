# 任务总表

任务状态使用：`planned`（计划中）、`active`（进行中）、`blocked`（受阻）、`done`（已完成）。

## 当前任务

- [ ] **M28 元素自愈与执行前预检**（`active`）
  - 计划：`M28-element-self-healing.md`；调研依据：`docs/element-self-healing-plan.md`
  - 已完成（2026-09-20）：**S1** 运行期消费 `selector.candidates`（主选择器失效时按稳定性回退，
    证据记录所用候选）· **S2** 执行前预检与错误分类（`ELEMENT_COVERED`/`DISABLED`/`NOT_VISIBLE`）·
    **S3** 参数漂移修复（`keyIntervalMs` 真正生效、`clipboard` 实装，并补 `clickBeforeInput`/`postDelayMs`）
  - 待做：**S4** 度量基线（扩展通道往返数 + 耗时）+ MVP 边界文档 —— M28 仅剩此片
  - 附（非 M28 切片，2026-09-20 done）：**扩展通道诊断可见性**——状态栏徽标离线时给出
    「bridge 注册 / 插件安装 / 浏览器运行 / 当前实例是否加载」，而不是一句「离线」
  - 不做（已定案）：新增 `mode: "insert"`；后台标签页焦点模拟

## 后续任务

- [ ] **M26 流程 inputs 声明编辑 UI**（`planned`——原为 M25 之后第一项，因维护者定向先做
  M27 工作台、随后做 M28 元素自愈而顺延；计划见 `M26-flow-inputs-editor.md`）
- [ ] 技术路线（ADR 0016）：GUI 为唯一主力形态——新增能力优先落 GUI；Web 编辑器（devserver）
  `devserver/static/` 冻结演进（不删除、不再补齐 GUI 已有能力）
- [ ] UI、DSH、MCP、调度器和安装器集成（`planned`）——见远期任务

## 远期任务

- [ ] **AI 按自然语言生成流程（设计期）**（`planned`）——自然语言 → 流程草稿 AST（catalog 命令 +
  元素资产引用 + 变量别名）→ compiler 静态校验 + M28 预检 → 用户在 GUI 审阅后保存；**运行时
  永不生成代码**（规则 6）。调研结论：jev 的**模型**价值低（托管付费、浏览器通用动作空间、无
  流程 AST），但「受限动作空间 + 观测表 grounding + 执行前校验」的模式价值高；详见
  `docs/element-self-healing-plan.md` §6
- [ ] 后台标签页焦点模拟（`planned`，**待证**——影刀无此设计且我们无 CDP；仅当后台标签页
  可靠性成为实际问题时再评估）
- [ ] 画布缩放 / 缩略导航（`planned`——树形画布长流程纵深远超影刀自由画布；M23 切片外）
- [ ] 节点禁用/启用（`blocked`——需 AST 增加 `disabled` 字段，属后端契约扩展，不单是 GUI）
- [ ] Web 编辑器前端化暂停/继续（`planned`——M21 只做了 PySide6 GUI；ADR 0006 §6 的
  通道对齐要求未落到 devserver HTTP 端点与 `static/app.js`）
- [ ] 主题切换入口（`planned`——QDarkStyle 深浅 palette 已在依赖，`apply_theme` 固定浅色）
- [ ] 窗口布局记忆（`planned`——dock 开合/宽度 QSettings 持久化）
- [ ] 元素捕获后截图缩略图（`blocked`——待捕获链路具自动截屏能力；M19 切 C 同源）
- [ ] 编辑器元素截图灯箱 + 上传 + 缩略图（`blocked`——待捕获链路具自动截屏能力；M19 切 C 后置项）
- [ ] 编辑器多 tab 属性表单（`planned`——单命令 schema 字段显著增多（>~8）时按「常规/参数/…」划分；M19 切 E 留接口）
- [ ] UI、DSH、MCP、调度器和安装器集成（`planned`）
  - 逐项放行与否以 ADR 0006 结论为准；操控型 HTTP 推迟不变。

> 已删除条目：原「桌面客户端编辑器薄壳（ADR 0010 重估条件）」——ADR 0016 已定 GUI 为唯一
> 主力形态，该条目（「确认非开发者用户为主力后再立项薄壳」）不再适用。

## 已完成

- [x] M27 工作台（首页）+ 编辑器两段式（`done`，2026-09-20）
  - 计划：`M27-workbench-home.md`；决策：ADR 0017
  - 交付：独立工作台窗口 `gui/home.py`（流程库页签：列表含最近运行状态/时间/元素数/修改时间 +
    新建/打开/复制/重命名/删除/导入/导出 + 运行入口与状态轮询；运行历史页签：全局列表 + 按流程筛选 +
    双击在编辑器打开时间线）；`app.open_editor_window` 编辑器单例；`run_gui` 默认开工作台、
    `--workflow X` 仍直开编辑器
  - 能力层：`WorkflowDirStore` 增 delete_flow(purge)/rename_flow/copy_flow/export_flow/import_flow；
    `run_history.purge_runs`（该模块唯一写操作，用于删除流程时清理其运行历史）
  - 决策落地：运行控制只在编辑器；删除流程确认框列出连带范围（元素/数据表格/运行历史）且默认取消；
    正在编辑器打开的流程拒绝删除/重命名
  - 证据：`test_gui_home.py`（7）+ `test_gui_home_management.py`（12）；FULL GATE PASSED

- [x] M25 运行历史浏览与回放（`done`，2026-09-20）
  - 计划：`M25-run-history.md`
  - 交付：顶层零依赖 `run_history.py`（`list_runs` 摘要按时间倒序 + limit、`read_run` 详情带
    inputs/断点调试字段/事件时间线，全程容错——损坏 result 记 unknown、坏事件行跳过）；
    CLI `runs list [--limit]` / `runs show <run_id>`；GUI 底部「运行历史」面板（列表 +
    查看时间线复用运行面板格式化 + 跳到节点 + 用同样输入再跑 + 继续/单步）；
    `RunManager.resume_run` 支持恢复**不由本进程托管**的历史运行（GUI 重启后也能续）
  - 约束遵守：不新增持久化格式、不建数据库（AGENTS 规则 10）、GUI 不承载 runtime（ADR 0011）
  - 证据：`tests/unit/test_run_history.py`（8）+ `tests/contract/test_gui_run_history.py`（5）；
    实机 `rpa-core runs list` 对真实 run_artifacts 输出正常；FULL GATE PASSED

- [x] M24 断点与单步调试（`done`，2026-09-20）
  - 计划：`M24-debugger.md`；决策：ADR 0005 增补「断点与单步（M24 增补）」
  - 交付：`RunControl`（暂停开关 + 断点集合 + 已消费断点 + 单步预算）在递归执行链上传递；
    节点执行前统一判定「用户暂停 / 单步 / 命中断点」；`PauseSignal` 带 reason；检查点新增
    可选字段 `breakpoints` / `consumedBreakpoints` / `pauseReason` / `pausedAtNode`（向后兼容）；
    CLI `run --breakpoints` / `resume --step`；GUI 编号栏红点 + 行首点击切换 + 右键菜单 +
    工具栏/浮窗「单步」+ 命中原因与跳转定位
  - 关键取舍：断点随检查点持久化（resume 是新进程、控制文件会被 reset）；命中即计入
    consumed 防「resume 后同断点反复命中」；单步用「放行本节点 + 下个边界返回 step」
    （初版立刻置暂停位会被同节点内的重试边界检查提前拦下）
  - 证据：新增 `tests/unit/test_debugger_breakpoints.py`（8）与
    `tests/contract/test_gui_breakpoints.py`（7，含真子进程断点运行与单步链路）；
    API v1 契约同步登记；FULL GATE PASSED

- [x] M23 GUI 体验对齐（`done`，2026-09-20）——G1–G5 全部完成
  - 计划：`M23-gui-ux-parity.md`；分析依据：2026-09-17 GUI 代码审计 + `docs/yingdao-web-commands-benchmark.md`
    + `.harness/yingdao-gap-matrix.md`
  - 交付：G1 单入口混合捕获（+ 平台退化修正 / macOS 手势与窗口层级）；G2 画布交互（多选/批量移动删除/
    右键菜单/Ctrl+F）；G3 属性面板追平 Web（分组折叠/输出别名/重试超时防呆）；G4 失败定位闭环
    （跳转失败节点/结构化错误/日志耗时与输出值）；次级项（变量面板/菜单栏/卡片摘要）
  - G5 维护者实测反馈批次（9 项，2026-09-20）：打开网页重复标签页、地址栏全选高亮、切换浏览器类型
    保存两次才生效、指令树拖不进画布、删除后点其他指令崩溃隐患、参数面板残影、fx 指令小框闪现
    （真因：无父级 QToolButton 被 setVisible 当顶层窗口）、首次点复杂指令卡顿、新增「打印日志」
    `data.log` + 运行日志显示输出值
  - 诊断沉淀：`RPA_GUI_DEBUG=1` 窗口 Show 监听（`debug_log.install_window_show_watch`）+
    `.harness/demo/` 可复用 GUI 交互/拖拽诊断脚本
  - 验收：维护者确认真实平台观感与交互 ok；`gui-ux-parity` 通过；FULL GATE PASSED

- [x] M21 GUI 运行控制进阶：暂停/继续 + 恢复人工确认（`done`，2026-09-19）
  - 计划：`M21-run-control-advanced.md`（含「实施结果」与计划修正说明）
  - 决策：ADR 0005 新增「跨进程暂停信号（M21 增补）」；ADR 0004 就地修订浏览器会话
    跨进程表述；ADR 0011 §4 两条「后置」标记为已补齐；手册 `docs/gui-run-control.md`
  - 交付：顶层零依赖 `control_channel.py`（控制文件 `control.json` + 轮询镜像到
    `RunHandle`）、`RunHandle.resume()`（撤销未生效请求）、`rpa-core pause`、
    `RunManager.pause/continue_run/resume(allow_indeterminate=)`、GUI 工具栏+浮窗
    「暂停/继续」与两种终态的确认框、**浏览器会话跨进程续接**
    （`session_bindings_from_scopes` + `restore_from_scopes` 恢复钩子）
  - 证据：真机（macOS+Edge 153）`navigate → 暂停 → resume → reload + getText` 全部作用在
    暂停前那个 `tabId` 上、已完成节点零重跑；新增 24 项测试（7 控制通道含真子进程 +
    9 会话续接 + 8 GUI 暂停链路含「不确认绝不恢复」）

- [x] M22 macOS/Linux 传输层真机验证（`done` —— macOS 侧完成，2026-09-19；Linux 仍未真机）
  - 计划：`M22-crossplatform-transport.md`；决策：ADR 0015（§6 平台差异、§7 平台验证状态）
  - 证据：macOS + Edge 153 + 扩展 0.3.1 完成 S1–S3 真机 —— 端点 `/tmp/rpa_core-501/rpa_core_ext/`
    （0700、属主=euid、pathBytes=72 ≤ 103）、`status()` 报 online、`env-status` 的 edge 五项全 true、
    host 单进程保活 **11h23m** 且零断连、扩展 reload 与浏览器退出**均即时回收 host 并删除端点
    socket 文件**（无残留）、`browser.navigate` `succeeded`（1760ms、`transport: "extension"`）、
    扩展 ID「发现=推导=manifest 放行」三者一致；S4 文档口径更正（§1.2 加平台验证状态表并显式
    标注 bsk 时代旧句失效、ADR 0015 加 §7、README 补 macOS manifest 路径与 host 生命周期语义、
    修「`browser.navigate` 不再暴露」歧义）+ launcher/`pgrep` 路径表核对（16 个 Helper 零误判）
  - 未完成：Linux 全线未真机（`$XDG_RUNTIME_DIR` 回退、`pgrep` 命中待验）——
    **维护者定案（2026-09-20）：只记录不测试**（无 Linux 环境）；待有 Linux 真机时按
    任务单「未完成」清单复验

- [x] M20 扩展通道迁移 Native Messaging（`done`）
  - 计划：`M20-native-messaging.md`；决策：ADR 0015
  - 证据：S0 真机四项（Edge+Chrome：SW 保活 15s 心跳零空洞 / reload 与完全退出回收 /
    重连 ≤0.4s / unpacked ID 推导与预注册）+ S1 传输层（命名管道 overlapped + Unix socket，
    15 单测）+ S2 host（stdio↔端点中继，9 合同）+ S3 安装注册（三平台 + ID 双路 + CLI，13 单测）
    + S4 扩展改造（native port + 串行化，真机捕获回传）+ S5/S6/S7（执行器/捕获/接线全量切换，
    客户端 7 合同 + 捕获 8 合同）+ S8 文档/前端路由收尾；真机 `browser.navigate` 端到端 succeeded；
    FULL GATE PASSED；feature `native-messaging-bridge` 通过

- [x] M19 编辑器交互补强（`done`）
  - 计划：`M19-editor-interactions.md`
  - 证据：面板可拖分栏+记忆 / 元素库底部 dock / 捕获自动刷新 / 运行参数+离开警告 / selector 从元素库选；技术栈决策=继续 vanilla；切 C 截图后置、切 E 留接口；full gate 通过

- [x] M18 运行控制（`done`）
  - 计划：`M18-run-control.md`
  - 证据：ADR 0011 子进程 run host；/api/runs start/status/events/cancel；前端 ▶运行/■取消/事件流面板；190 tests 全门禁。
- [x] M17 编辑器进阶（`done`）
  - 计划：`M17-editor-advanced.md`
  - 证据：变量补全/全屏画布/运行状态高亮（/api/runs/latest-events）；184 tests 全门禁。
- [x] M15 WorkBuddy 连接器入驻（`done`）
  - 计划：`M15-workbuddy-connector.md`
  - 证据：CLI auth/status/unauth 三件套 + statusMatch 契约；workbuddy-connector/ 目录（meta type:cli + cli.json runtime python + icon.svg + skills/rpa-automation/SKILL.md + references 三件套）；修真实部署缺口（commands/ 打进 wheel + 包内优先解析）；干净 venv 本地路径安装验证；181 tests 全门禁。
- [x] M16 混合捕获 auto 模式（`done`，由 M14 HybridCaptureSession 覆盖核心后关闭）
  - 证据：M14 混合捕获实机验收（插件腿网页 Ctrl+Click + UIA 腿桌面 F9 + 让位）；剩余 DPI 坐标换算边界降级为已知边界。
- [x] M14 浏览器捕获（bsk 执行 + 自研扩展捕获 + 混合捕获 + 桌面 hover）（`done`）
  - 计划：`M14-extension.md`
  - 证据：bsk 执行传输（M14a）+ content-script 扩展无缝捕获 + HybridCaptureSession + 桌面 hover 细粒度钻取 + 元素编辑确认 + 「＋捕获」入口；实机验收（登录态小红书、跨浏览器无缝、混合双通道）；175 tests 全门禁。
- [x] M14.5 CLI 通道对齐（`done`）
  - 计划：`M14.5-cli-parity.md`
  - 证据：`catalog`/`capture browser|desktop`/`elements list|show|verify` 子命令与 devserver 同权；元素校验下沉能力层；cli_parity 合同 7 项；138 tests 全门禁。
- [x] M13.1 流程目录化与元素即流程资产（`done`）
  - 计划：`M13.1-flow-dir-assets.md`
  - 证据：每流程一个目录 `<name>/workflow.json` + 元素资产 `<name>/elements/*.json`（可入版本库）；元素端点嵌套 `/api/workflows/{name}/elements[/{el}[/verify]]`；capture pick saveAs 需 flow；前端元素库按当前流程；122 tests 全门禁。
- [x] M13 编辑器元素库（`done`）
  - 计划：`M13-element-library.md`
  - 证据：元素库面板 + 插入到节点 + 删除 + 结构校验（verify）+ selector 捕获按钮；POST/DELETE/verify 端点；110 tests 全门禁。

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
