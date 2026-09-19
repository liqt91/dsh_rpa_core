# 任务总表

任务状态使用：`planned`（计划中）、`active`（进行中）、`blocked`（受阻）、`done`（已完成）。

## 当前任务

- [ ] **M23 GUI 体验对齐（用户体验 × 对标影刀）**（`active`）
  - 计划：`M23-gui-ux-parity.md`；分析依据：2026-09-17 GUI 代码审计（`src/rpa_core/gui/`）+
    `docs/yingdao-web-cmds-benchmark.md` + `.harness/yingdao-gap-matrix.md`
  - 切 G1 捕获链路（`done` 主体，2026-09-17）：**单入口混合捕获**已落地——唯一「捕获元素」
    按钮接 `HybridCaptureSession`（扩展腿网页/桌面 hover 腿先回传者胜）；捕获时主窗最小化/还原；
    插件离线显式提示；连带修复让位标志断链、离线扩展腿误杀桌面腿、CLI 未 arm 三处存量缺陷。
    **剩余**：捕获后确认对话框对齐 Web（改名/selector 编辑/命中数/同名覆盖保护，当前仅
    QInputDialog 命名）
  - 关联已交付（2026-09-17）：GUI 插件对话框 bridge host 注册入口（`gui-extension-dialog-bridge`）
  - 切 G2 画布交互（`done`，2026-09-18）：多选（ExtendedSelection）+ 批量移动（多 id 拖放，过滤
    被拖祖先的后代、成环守卫、保持相对顺序）+ 批量删除（跳过已选中祖先的后代，状态栏报数量）+
    右键菜单（复制/粘贴/删除/添加否则，可用性 `_canvas_menu_state`）+ Ctrl+F 画布内查找
    （标题/节点 id/命令 id/参数摘要，Enter 循环定位）；新增 `test_gui_canvas_batch` 14 例；FULL GATE PASSED
  - 切 G3 属性面板追平 Web：消费 `x-param-groups` 分组折叠（GUI param_form 仍平铺）+
    输出别名（`output_aliases`/`x-outputs`）编辑 UI + 重试/超时字段（含 unsafe 禁用防呆，
    对齐 Web `retryCountField`）
  - 切 G4 失败定位闭环：运行失败点击错误 → 跳转失败节点 + 结构化错误详情
    （借鉴 Web `startupError` 透出经验）；运行日志加耗时/输出值预览
  - 切片内次级项：变量面板（设计期静态收集 output_aliases/inputs 列表）、菜单栏
    （QMenuBar + 快捷键一览）、卡片摘要按关键字段（url/selector/text）优化
  - 证据：G1 `test_gui_capture` 4 例 + `test_capture_hybrid` +2（full gate 516 passed）；
    插件对话框 `test_gui_panels` +3（full gate 521 passed）；3 个失败均为已知桌面 E2E 焦点抖动

## 后续任务

- [ ] 技术路线（ADR 0016）：GUI 为唯一主力形态——新增能力优先落 GUI；Web 编辑器（devserver）
  `devserver/static/` 冻结演进（不删除、不再补齐 GUI 已有能力）
- [ ] UI、DSH、MCP、调度器和安装器集成（`planned`）——见远期任务

## 远期任务

- [ ] 画布缩放 / 缩略导航（`planned`——树形画布长流程纵深远超影刀自由画布；M23 切片外）
- [ ] 节点禁用/启用（`blocked`——需 AST 增加 `disabled` 字段，属后端契约扩展，不单是 GUI）
- [ ] 断点 / 单步调试（`planned`——影刀核心调试能力；M21 已铺好跨进程暂停通道与
  「暂停即收口 + 从检查点续跑」语义，继续往细粒度走需要运行协议按节点粒度下发暂停）
- [ ] Web 编辑器前端化暂停/继续（`planned`——M21 只做了 PySide6 GUI；ADR 0006 §6 的
  通道对齐要求未落到 devserver HTTP 端点与 `static/app.js`）
- [ ] 运行历史浏览 / 回放（`planned`——run_artifacts 列表入口；影刀有运行记录）
- [ ] 主题切换入口（`planned`——QDarkStyle 深浅 palette 已在依赖，`apply_theme` 固定浅色）
- [ ] 窗口布局记忆（`planned`——dock 开合/宽度 QSettings 持久化）
- [ ] 流程 inputs 声明编辑 UI（`planned`——当前只能手写 JSON；运行对话框只读消费）
- [ ] 元素捕获后截图缩略图（`blocked`——待捕获链路具自动截屏能力；M19 切 C 同源）
- [ ] 编辑器元素截图灯箱 + 上传 + 缩略图（`blocked`——待捕获链路具自动截屏能力；M19 切 C 后置项）
- [ ] 编辑器多 tab 属性表单（`planned`——单命令 schema 字段显著增多（>~8）时按「常规/参数/…」划分；M19 切 E 留接口）
- [ ] UI、DSH、MCP、调度器和安装器集成（`planned`）
  - 逐项放行与否以 ADR 0006 结论为准；操控型 HTTP 推迟不变。

> 已删除条目：原「桌面客户端编辑器薄壳（ADR 0010 重估条件）」——ADR 0016 已定 GUI 为唯一
> 主力形态，该条目（「确认非开发者用户为主力后再立项薄壳」）不再适用。

## 已完成

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
  - 未完成：Linux 全线未真机（`$XDG_RUNTIME_DIR` 回退、`pgrep` 命中待验）

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
