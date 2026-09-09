# 工作日志

## 2026-09-09

- 提交前说明：本次提交含两部分——(a) 此前未提交的 output-aliases 变量别名重构 + UIA 生产侧提速 + devserver SPA fallback/动态静态扫描（详见 PROGRESS 同日 output-aliases / uia-e2e-flakiness-production / spa-fallback-fix 条目）；(b) 本会话的打开网页合并与配套（下述）。工作区另有 Vue 前端迁移中间态（frontend/ 源码、static/assets/ 构建产物、favicon/icons.svg）未启用、未提交。
- **打开/关闭浏览器指令并入打开/关闭网页**（维护者决策，不单独维护浏览器实例）：
  - `browser.launch` 删除（catalog 55→54）；`browser.navigate` v2.0.0 吸收全部启动参数（headless/userAgent/userDataDir/transport/browserInstanceId/keepOpen + x-depends 联动）并自建会话；输出「保存网页对象到」（sessionId，x-outputs primary）+ url + resourceType；`browser.close` 中文名改「关闭网页」
  - executor：navigate 双后端（playwright/bsk）一步 open+goto，导航失败回收本次新建浏览器（规则 11）；顺带修复 `browser.close` 持久上下文分支 `context` 未定义 NameError（存量 bug）
  - 对标影刀「打开网页」补齐：`channel`（浏览器类型 chromium/chrome/msedge）+ `args`（命令行参数），x-depends 限 playwright 传输并透传
  - x-outputs 新增 `hidden` 标记：最终网址/资源类型不渲染别名框（运行值仍可 ${} 手动引用），输出区对齐影刀仅暴露「保存网页对象到」
  - 编辑器（旧版 app.js）：属性面板分「输入参数 / 输出参数」两区（.props-section-in/out 分色）；「输出变量名」升级为按 x-outputs 逐字段别名输入；sessionId 下拉改引 ${别名}；i18n 新增 commandFields 按命令覆盖（args 在 navigate/executeScript 含义不再打架）
  - devserver `/api/catalog` 下发 x-outputs 与扩展字段（executor/risk/capabilities/resources/stability/retryable/default_timeout_seconds）
  - 迁移：examples ×3、workflows ×2、README、WorkBuddy skill 文档、全部相关测试
- **指令清单查看页**：零构建 `static/catalog.html`，实时拉 /api/catalog 渲染 54 条 manifest（分组/搜索/展开收起/kind+effect 徽标/输入输出参数表/x-depends 标注/hidden 灰行/错误码/digest），编辑器工具条加「☰ 指令清单」入口；Playwright 实测无 JS 错误
- 门禁修复（阻塞项收口）：
  - UIA 桌面 e2e 门禁内必抖根治：根因=前置 Chromium 用例占用前台窗口，SendInput 落错窗口；测试侧 `_force_foreground`（AttachThreadInput 绕过前台锁）运行前抢前台，本轮门禁内通过
  - ruff 排除 `scripts/`（一次性影刀抓取脚本，存量 24 处 lint）；补 `test_desktop_contract.py` 缺 `import pytest` 等存量 lint
  - devserver 事故：SO_REUSEADDR 导致 8765 双进程共存，旧进程内存缓存 Vue 版 index.html 致"重启后仍 Vue"；杀 stale 进程恢复，登记端口占用检测候选改进
- full gate passed（54 manifests，37 features）。

## 2026-09-08

- 完成指令优化规划 Phase 1-4（见 PROGRESS 同日多条）：参数补齐 P1-S1~S6（sessionId resourceType 标签、browser.click/input/close 等六参数、editor 按 resourceType 过滤下拉）+ 新指令 18 条×双后端（hover、窗口操作、executeScript/screenshot/select、upload/download/handleDialog/getWindowList 等），累计 55 manifests。
- 完成 Phase 5 用户变量体系 var-system（`passes: true`）：
  - Model `ActionNode.output_name`（pattern `^[A-Za-z_]\w*$`）；runtime `scopes.variables[output_name] = result.outputs`；resolver 支持变量**子路径** `${var_name}`（整 dict）/ `${var_name.field}`（取字段）。
  - 子路径增强决策（维护者确认）：`output_name` 存整个 outputs dict，若只支持单段 `${web_page1}` 会让下游 sessionId 收到 dict 而失败 → 须 `${web_page1.sessionId}`。
  - compiler 静态收集 output_name，前向（declared later）与未知/error_var 越界引用均编译期拦截，与 `${steps.*}` 前向语义一致；保留 catch error_var 词法作用域。
  - editor：节点属性加「输出变量名」；sessionId 下拉对已命名节点显示变量名并引用 `${name}.sessionId`。
  - 新增 resolver 单测 5、compiler 编译测试 3、runtime 集成 1、editor e2e 1。
  - full gate 257 passed；5 桌面 UIA 真实交互 e2e 在本会话报 "window did not appear"（HEAD 基线复现同失败，与本次无关）。
- 桌面 UIA e2e 失败根因定位（决定性实验收口）：非沙箱下 python 内 Popen 启动 GUI demo 到 `demo started` 后进程即被 **SIGTERM**（连窗口枚举都未执行）；改为**外部 detached 启动 demo（PowerShell Start-Process）→ 独立 python 进程用 pywinauto 连接**，可稳定枚举主窗口及其控件（queryInput/submitButton/dialogButton/resultText）并做 set_edit_text/click 交互。结论：**本 WorkBuddy 会话内由 python/bash 直接启动 GUI winexe 会触发进程被 SIGTERM**，而非 UIA 探测能力问题（demo 窗口本身可被正常枚举）。该 5 个桌面 e2e 需在能访问桌面的会话（如 opencode cmd agent）补验。
- **桌面 UIA e2e 真根因（用户能访问桌面会话实测，纠正上述"仅 SIGTERM 环境限制"结论）**：
  - 用户在自己可访问桌面的 shell 跑**同样失败**（window did not appear）→ 非单纯环境限制。
  - `_diag_latency.py` 量化：进程内**首次** `Desktop(backend="uia").windows(...)` ~**60s**，之后 ~125ms；而 `DesktopExecutor` 每命令外层 `asyncio.wait_for` 超时 **15s**（desktop.py:66）→ 首启落在命令内必被掐断 → TIMEOUT（`test_uia_attach_unknown_window...` 报 TIMEOUT 而非 ELEMENT_NOT_FOUND 即因此）。
  - **60s 诱因 = 桌面开游戏（炉石传说等）**：全桌面 UIA 枚举会触碰所有顶层窗口 provider，游戏窗口 provider 慢/挂起拖住首启。用户关炉石后执行变快，实证确认。
  - **修复（tests/e2e/test_uia_desktop.py）**：`_wait_for_window` 改用 Win32 `FindWindowW`（0ms，不触发全桌面 UIA 枚举）+ 新增 `_warmup_uia()`（executor 前用无 wait_for 调用提前消耗 UIA 首启）。
  - **验证通过**：关掉炉石后 `test_uia_desktop_vertical_slice` PASS。
  - **生产隐患（待根治）**：executor attachWindow 依赖全桌面 UIA 枚举 + 15s 超时，真实用户桌面开游戏时首个 attach 会超时；根治方向 = FindWindowW 拿 hwnd → `Desktop(backend='uia').window(handle=hwnd)` handle 级 attach。
- var-system 本地提交 `c13bad3`（14 文件，+423/-10，未 push）。

## 2026-09-07

- 浏览器扩展安装全链路实证与方向修正（详见 docs/extension-install.md §6.5.1，PROGRESS 四条）：
  - Chrome/Edge 152 双浏览器对照 + 影刀插件真机重装对比：外部注册表来源放行与否由 manifest update_url 归属决定；§6.5「指 CWS 即可用、无需上架」被真机推翻——未上架本地 CRX 启用后约 30s 被异步商店校验判损坏（disable [1024]）；影刀持久只因 ID 真上架。
  - 根因二连：装不上多为 `extensions.external_uninstalls` 卸载记忆（loader 永久跳过，清除+冷启动即装）；安装入口默认改开发者模式 Load unpacked 引导（源码目录、持久可用，影刀 Chrome 同款）。
  - 扩展安装入口落地（CLI `install-extension` + 编辑器「⇲ 插件」对话框）：per-browser 状态检测（注册表/profile/开发者模式按 `location==4 && path` 匹配）、复制/打开源码目录、安装自动清除卸载屏蔽、--registry 保留上架后路线；ADR 0012（devserver 能力层复用而非子进程代理）。
  - 实测边界：chrome:// 与 edge:// 扩展页无法从外部命令导航（安全设计）→ 对话框第 3 步改纯文字指引；模态框仅「关闭」按钮关闭。
- 完成 M19 编辑器交互补强（对照隔壁 rpa_script 仿影刀编辑器，仅借鉴不搬码）：
  - A 面板可拖分栏 + localStorage 记忆；B 元素库移入底部可拖高 dock（横向网格，方向修正上拖增高）；F 捕获自动刷新（2s 轮询 + visibilitychange 门控 + 元素名集合差集，仅变化重渲染）；G 运行参数对话框（顶层 inputs 按声明类型渲染）+ 运行中 beforeunload 拦截；D selector 字段「从元素库选」下拉 + kind 徽标。
  - 技术栈决策：切片 C/D/E 前立决策点——实测 app.js 1886→~2050 行线性增长、零范式回归、字段密度 1-6，**继续 vanilla 不迁 React**（迁移固定成本高：重写 + 产物入库 + 破零外链 ADR）。
  - 切 C（元素截图灯箱）后置：捕获链路现无截图产物、stdlib 无截图/压缩 → 移 BACKLOG 远期 blocked；切 E（多 tab 属性表单）留接口：字段多（>~8）再按「常规/参数/…」划分。
  - 收口：M19 任务单 done、feature editor-interactions passes、project_state completed_milestone=M19；full gate 250 tests。

## 2026-09-06

- 完成 extension-installer：扩展静默安装双通道（外部扩展注册表 HKCU + ExtensionInstallForcelist 策略，UAC 提权 HKLM 降级），逆向影刀 6.2.23 实锤 external_registry_loader 通道；Edge --pack-extension + pem 持久化、纯 stdlib DER 推扩展 ID；devserver 托管 update-manifest XML + CRX；CLI `install-extension`（默认外部注册表 / --policy / --remove）；合同 24 项（fake winreg）+ 真机实测。详见 PROGRESS 2026-09-06 行、docs/extension-install.md。

## 2026-09-05

- 完成 WorkBuddy 连接器合规审查：对照 open.workbuddy.cn/docs/connector 修 cli.json win32 入口（rpa-core.cmd→rpa-core.exe，干净 venv 实证 pip 只生成 .exe）+ SKILL.md frontmatter 必填字段；合同 +2 防回归。详见 PROGRESS 2026-09-05 行。

## 2026-09-04

- keepOpen 实机验证：bsk run 结束后 session 仍存活、Agent Window 保留（对照：非 keepOpen 正常回收）。
- 自研执行层评估（M19 立项前）：核实 BrowserSkill 为 MIT 开源（可 fork/复用，但工程为 Rust daemon + WXT/TS 扩展，与本仓库零构建 vanilla + Python/stdlib 取向冲突——取其模式与协议而非搬工程）；确认「MV3 扩展 + chrome.debugger 可信 CDP」为三家（bsk / Playwright MCP 扩展 / Panerelay）收敛共识。
- 维护者决策：走彻底自研路线（扩展兼任执行，单插件），仅支持独立窗口执行、bsk 退出运行时路径；执行通道须用 WebSocket（MV3 SW 无活动连接 30s 被回收，常驻 WS 兼作保活+低延迟命令通道）。
- M19 计划定稿：单插件（捕获+执行）+ 常驻 broker daemon（`rpa-core broker`，默认 ws://127.0.0.1:52801，lock 自启/防双实例，同 bsk daemon 模式）+ 引入 websockets 依赖；扩展 chrome.debugger 驱动独立窗口；Python 侧 ExtensionExecSession 抄 browser_bsk 命令表。待批准后落 `.harness/tasks/M19-self-exec.md`。
## 2026-09-03

- browser.launch 加 keepOpen（bsk 传输）：流程跑完不 session.stop Agent Window，留给 bsk daemon 持有（空闲超时兜底）供人工继续操作/登录/人审；显式 browser.close 仍停；实机验证 run 结束后 session 仍存活。full gate 192 tests。
- 完成 M18 运行控制：ADR 0011 放行 devserver 代理型运行控制（子进程 run host，进程内仍无 orchestrator/run 状态）；/api/runs start/status/events/cancel + CLI run --inputs；前端 ▶运行/■取消/事件流面板轮询。full gate 190 tests。
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
