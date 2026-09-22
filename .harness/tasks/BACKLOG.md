# 任务总表

任务状态使用：`planned`（计划中）、`active`（进行中）、`blocked`（受阻）、`done`（已完成）。

## 当前任务

- [ ] **M38 指令测试 L1 契约矩阵**（`active`）——浏览器通道 32 条命令 / 180 个变体已入表，
  覆盖率静态校验 `check_command_matrix.py` 已进默认门禁；S2 桌面 36 条、S3 数据/工作流 18 条待建表
  - 计划：`M38-command-matrix.md`

## 后续任务

> 已完成并移出本清单（2026-09-21）：**两套「可见」口径对齐**——扩展侧已统一为
> 单一严格判定 `isElementVisible`（`opacity:0`/视口外/零尺寸均判不可见），`waitFor` 的
> `count` 与执行前预检**复用同一个函数**，并由 `check_precheck_helpers.mjs` 的反漂移断言
> 钉住（禁止再出现第二份可见性判定）。

- [ ] **M31 GUI 跨平台观感诊断（macOS vs Windows）**（`planned`，诊断型——**先诊断，不写代码**）
  - 计划：`M31-gui-crossplatform-audit.md`
  - 由来：维护者在 macOS 上使用 GUI 后反馈「控件样式、字体大小、控件的显示/隐藏行为与 Windows
    不一致」。**当前是主观感受，不能直接立项**——它可能指向三种成本量级完全不同的根因：
    **H1** 本项目单位混用（改代码，小）· **H2** Qt 平台抽象层太薄（换宿主，中）·
    **H3** 前端自绘成本（重写 Web 前端，大）。三者的处置互相排斥。
  - 已读出的静态线索（**假设，待实测验证**）：
    ① `apply_theme` 用 `QFont(family, 9)`（**pt**），而 QSS 里散落 `font-size: 12/13/14px`——
     **同一界面两套单位**，换算依赖 DPI，两端基准不同；
    ② 候选字体表 **Windows 优先**（`Microsoft YaHei` 排第一），macOS 落到 `PingFang SC`——
     两端**字体族不同**，同字号下字面高度与行宽也不同；
    ③ `canvas.py` 行高是硬编码 **46px**、字号是 **pt 相对偏移**（`pointSizeF() - 0.5`），
     两者比例随平台变；`_GUTTER_ERROR_X = 46` 与 `_ROW_HEIGHT = 46` 数值巧合耦合；
    ④ GUI 全模块 **零平台分派**（`sys.platform`/`darwin` 命中 0 次）——而 `cli.py`/`executors/`/
     `capture/`/`local_transport.py` 都有认真分派（M22 甚至处理了 macOS `AF_UNIX` 104 字节上限）。
     **即 GUI 是唯一没做跨平台分派的模块**，属欠账而非技术选择；
    ⑤ `WindowStaysOnTop` 在 macOS 不保证置顶（层级由 WindowServer 管）、`hide()` 与
     `showMinimized()` 在 macOS 语义不同——这类是**系统约束，换 Tauri 也修不掉**。
  - 交付物：表 A（环境数据，两端各一份）· 表 B（15 项控件观感）· 表 C（8 项窗口行为）·
    表 D（把每条差异打成 `[U]单位` / `[F]字体` / `[Q]皮肤` / `[W]系统约束` 四类）。
    **`[U]+[F]+[Q]` 与 `[W]` 的比例就是「要不要迁 Tauri」的量化判据**。
  - 判据：若绝大多数是 `[U]/[F]/[Q]` → Tauri 收益极低（换壳照样要修，甚至以 CSS 单位问题复发）；
    若 `[W]` 占主导 → Tauri 也帮不上（约束在系统层）。诊断结论直接决定下一个里程碑的形态。
  - 注意：诊断**必须两端同 PySide6 版本**（`>=6.7,<7` 可能装到不同 minor，差异会来自 Qt 而非平台）；
    且必须记录 `devicePixelRatio`——Retina 下 `=2`，本身就能解释一部分「看起来不一致」。
- [ ] **整页/元素截图**（`planned`）——M29 S3 从 `browser.screenshot` 删掉 `fullPage`/`selector` 后
  留下的能力缺口：`chrome.tabs.captureVisibleTab` 只能截可见区，整页要滚动分段拼接、元素要按 rect
  裁剪，都需要在扩展里解码图像（MV3 service worker 无 `Image`/`FileReader`）→ 走 offscreen document
  或 CDP `Page.captureScreenshot(captureBeyondViewport/clip)`；另需处理 sticky/fixed 元素在分段里的重复
- [ ] **两个死参数的处置**（`planned`）——`browser.closeTabs.browserType` 与
  `browser.waitLoad.state` 声明了但实现未消费（M38 实测；静态门禁因「通用读取」放行）：
  要么实现（前者覆盖路由、后者下发给扩展），要么从 manifest 删除。清账后同步删掉
  `check_command_matrix.py` 的 `KNOWN_DEAD_PARAMS` 条目（该台账会自我收紧）
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

- [x] **M37 closeTabs 增加 `ignoreBeforeUnload`：remove 前注入抑制 beforeunload 弹窗**（`done`，2026-09-21）
  - 计划：`M37-ignore-before-unload.md`
  - 实装：manifest 参数（默认 true 对齐影刀）；Python「显式才转发」（None 不进
    args，缺省语义由扩展 `!== false` 单点兜底）；扩展 remove 前向目标页 MAIN world
    注入清 `window.onbeforeunload`，best-effort——注入失败（chrome:// 等）不阻断
    关闭；`addEventListener` 形式的拦截清不掉、仍弹窗则如实进 `failedTabIds`。
  - 门禁：check_close_ops 29→37 项（先注入后关闭的事件序矩阵 / false 不注入 /
    注入失败不阻断记账 / MAIN world+injectImmediately 反漂移 / manifest default），
    三条负向验证全过；MAIN world 正则切进 closeMany case 切片（全文件正则被
    page.call/eval 的同款注入喂出假绿灯，负向验证实测抓到）。
  - FULL GATE PASSED（收集实测 1086 = M36 后 1083 + 新增 3；契约文件 18→21 项）

- [x] **M36 门禁测试泄漏全局输入：clipboard 用例往维护者前台粘贴 "hi"**（`done`，2026-09-21）
  - 计划：`M36-test-input-leak.md`
  - 根因：M30 S2 的 `test_input_command_shares_the_same_wait_semantics` 只桩了
    `_find`，`desktop.input` clipboard 模式的「写剪贴板 + `send_keys("^v")`」走了
    真实全局路径——每跑一次套件清空一次剪贴板、向前台聚焦的输入框粘贴一次 "hi"。
  - 修复：①肇事用例剪贴板与键击全打桩（断言「调度了粘贴」而非真实效果）；
    ②conftest autouse 守卫 `_block_global_input` 钉死默认测试进程的全局输入面
    （`send_keys`/`desktop_win32.send_keys` 早绑定名/`win32clipboard` 写入口），
    `RPA_DESKTOP_E2E=1` 时让位。负向验证：探针直接调真实 send_keys → 拦红。
  - 教训：打桩边界沿**真实副作用面**划，不沿参数传递面划。FULL GATE PASSED
    （1069 passed / 2 xfailed / 12 skipped；用例数与 M35 持平）

- [x] **M35 closeBrowser 语义收敛：移除 scope，直接杀指定浏览器的全部进程**（`done`，2026-09-21）
  - 计划：`M35-closebrowser-kill-all.md`
  - 维护者定案：「没有 scope 的问题，是直接杀某个浏览器的所有进程」。M32 的
    `launchedByUs`/`byProcessName` 两档删除——授权由执行命令本身给出；「我们拉起过」
    的水位判据跨进程（GUI 重启 / resume）必然丢失，保守默认把该关的报成
    `no_launched_process`；只解绑会话的正确工具是 `browser.close`。
  - 变更：manifest 删 `scope`；`browser.py` 删 `_launched_marks`/`_scope_processes`，
    `_close_browser` 直线化，`_list_browser_processes` 删 `startedAt`（唯一消费者消失，
    POSIX etimes 换算简化）；feature `browser-teardown` 标题同步。保留 force/逐进程
    记账/等真退出/幂等 0 匹配/离线可用/GBK 修复/整树终止。
  - 测试 -3 项（2 个 scope 专测 + 1 个参数化用例），契约文件 18 项全绿；
    FULL GATE PASSED（收集实测 1083 项 = 1069 passed / 2 xfailed / 12 skipped）

- [x] **M34 宿主生命周期：扩展宿主空闲自杀，根治 uv run 锁文件**（`done`，2026-09-21）
  - 计划：`M34-host-lifecycle.md`；文档：`docs/extension-channel-baseline.md` §6
  - 触发：`uv run rpa-core gui` 报 `os error 5`（无法删除 `rpa-core-ext-host.exe`）。
    根因：console-script shim（`.exe` launcher）与真 `python.exe` 宿主**都握 exe 句柄**，
    宿主因扩展关闭 stdin 退出后孤儿 shim 偶发残留不退（日志特征：宿主侧日志全结束、
    shim 侧零条目）；而 `uv run` 每次重装项目都要重写 console scripts，exe 被占用即失败。
  - 修复：宿主**空闲自杀**——无客户端连接且空闲超过阈值（默认 1800s，
    `RPA_CORE_HOST_IDLE_EXIT_SECONDS` 可调、`0` 禁用）→ 监视线程置 `_closed` 信号
    accept_loop 自行退出后 `os._exit(0)`；**不调 `shutdown()`**（从监视线程调会在
    `_PipeServer._lock` 上与 accept_loop 死锁）。活动追踪 `_touch()` 覆盖扩展消息/
    客户端接入/有活客户端三种刷新。
  - 实现教训（任务单 §6）：① **合成单线程测试给假绿灯**——`os.close` 宿主自己这端读 fd
    唤不醒阻塞的 `read()`（EOF 要靠写端关闭），据此放弃 `_wake_extension_loop` 机制；
    ② 清理孤儿 shim 时 `taskkill /T` 级联杀掉了活宿主（任务单 §4）。
  - 测试：`test_ext_bridge.py` +10（阈值解析 6 + 集成 4：到点退出/禁用/客户端抑制/
    消息流抑制）；真机观察：穿越阈值后 rc=0 退出。FULL GATE PASSED
    （1072 passed / 2 xfailed / 12 skipped）

- [x] **M32 浏览器收尾：关标签页与终止浏览器进程**（`done`，2026-09-21）
  - 计划：`M32-browser-teardown.md`
  - 兑现 M29 S3 留下的那句话（「缺口属独立命令」）。两条命令**都是独立命令**不是
    `browser.close` 的新参数：`close` 是会话生命周期（`effect=session`，语义是解绑、
    不碰用户浏览器），关标签/终止进程是**对用户浏览器的破坏性操作**（`unsafe-write`）——
    风险等级、声明面、默认策略三者全不同。
  - `browser.closeTabs`：显式 `tabIds` / `all=true`（当前窗口全部）**二选一**（`oneOf` +
    互斥校验）；扩展侧 `tabs.closeMany` **逐项记账**（`closedTabIds`/`failedTabIds`），
    不是一个布尔；关掉当前会话所属标签页时会话随之解绑。
  - `browser.closeBrowser`：`scope` 参数对齐影刀形态——`launchedByUs`（**默认保守**，
    只杀本执行器拉起过的实例）/ `byProcessName`（按名全杀，含用户自开窗口）。
    **默认保守是硬要求**：判据「哪些是我们拉起的」在没有记录时无法从外部推断，默认全杀
    会让一次普通流程收尾把用户手上正在填的表单一起关掉。
  - **根因修复**：`launch_browser` 此前 fire-and-forget，无任何「我们启动过它」的记录——
    这正是该能力此前无法实现、只能退化成按名全杀的原因。现记 `_launched_marks` 水位，
    且记在「等到插件上线」之后（失败的拉起不记水位，否则事后会去杀用户的浏览器）。
    保守默认 + 无记录时**刻意不静默成功**（报 `reason=no_launched_process` 并指向出口）。
  - 顺手修掉两个真 bug：`tasklist` 的 GBK 编码（异常藏在 subprocess 读线程里）、
    `os.kill(pid, 0)` 在 Windows 上不可用作存活探测。
  - 新门禁 `scripts/check_close_ops.mjs`（28 项断言）——「逐项记账 / `all` 严格 `=== true` /
    空 `tabIds` 不退化成全关」这类语义**只有扩展侧能证明**：Python 桩测的是接口形状，
    一个 `Promise.all` 一把梭的实现能过全部 Python 测试，却会在真机上把「关了 2/3」
    报成「全关了」。负向验证 3 例（第 2 例第一次是假绿灯，已补用例并记教训）。
  - 真机未覆盖：终止动作未对真实浏览器执行（会关掉维护者手头窗口）；POSIX 侧解析未在
    mac 真机跑过。**剩余缺口**：`closeTabs` 的 `all=true` 不区分「我们创建的」与「用户自己的」
    标签页（与 `byProcessName` 同级杀伤），要做需在 `tabs.create` 时记 tabId。

- [x] **M26 流程 inputs 声明编辑 UI**（`done`，2026-09-21）
  - 计划：`M26-flow-inputs-editor.md`；口径文档：`docs/flow-inputs.md`
  - 结果：**feature_list 56/56 全部 `passes=true`，无未完成 feature**。
  - 真实形状（开工核实）：`Workflow.inputs` 是 `dict[str, Any]`，语义为 **`{名称: 默认值}`
    扁平映射**——**没有** `type`/`required`/`description`。故只做**两列**；
    加那三个字段是形状变更（要动 `${inputs.<名>}` 文法），属 ADR 级决定，未夹带。
  - S1 能力层 `model/inputs.py`：读取宽松（历史非法声明也要能打开）/ 保存严格；
    名称 `[A-Za-z_]\w*` **不含点号**（点号是 `${...}` 路径分隔符 → 带点名字「声明在册但
    永远引用不上」，静默失败比报错更坏）；默认值文本 `sort_keys=True` 规范化；
    并接入 `WorkflowCompiler.compile`，保证校验真拦得住。
  - S2 `gui/inputs_dialog.py`：两列表格，**校验在「确定」之前且不产出半成品**；
    只写 `_workflow_meta["inputs"]` 不碰文件（落盘走既有保存链路，脏标记一致）。
  - S3 联动：三个消费方本就都读 `_workflow_meta["inputs"]`，故 S3 的实质是**证明**联动
    成立——用跨层一致性（补全产出的 `inputs.<名>` 必须被 `compiler._REFERENCE` 匹配）
    加「声明→引用→编译通过」端到端；并补 M25 边界：**声明不是运行输入的过滤器**。
  - S4 文档 `docs/flow-inputs.md` + 测试 115 项 + **负向验证 4 处**。

- [x] **M30 桌面通道命令参数漂移收口**（`done`，缺陷等级，2026-09-21）
  - 计划：`M30-desktop-param-drift.md`
  - 立项第一件事是**纠正 M29 留在 BACKLOG 的归因**：原先写「桌面/数据通道分派不是
    `command == "<id>"` 字面量形状，静态切片不适用」——**错的**。三类实现都是字面量
    （`desktop.py` / `desktop_win32.py` 用 `command ==`，`python_worker.py` 用
    `invocation.command_id ==`，只是载体名不同）；真实原因是当时门禁只登记了 `browser.` 前缀。
    泛化载体名后**四类通道 78 条命令全部可切片、跳过 0 条**，一次挖出 12 条命令的参数漂移。
  - 进度：**S1–S5 全部完成**。106 项契约测试（13+64+29）；`KNOWN_GAPS` 清零；
    FULL GATE PASSED（925 passed / 2 xfailed / 12 skipped）。
  - 数据通道结论：`python.worker` 12 条命令**零漂移**（`data.*` 与 `workflow.sleep` 全部
    参数都有真实读取）——原条目里「`data.*` 读取方式也需纳入方法设计」已由 S1 的载体泛化解决。
  - S3 追加发现（不在原审计清单里，已在片内修掉）：① 双击写 `click_input(click_count=2)` 而该参数
    不存在 → 一直 `TypeError`；② 辅助键写 `pywinauto.keyboard.key_down(...)` 而该函数不存在 →
    一直 `AttributeError`。两者都是**参数被读了、读完调用的 API 是错的**，参数消费门禁结构上查不出
    （已写进代码注释与 `docs/desktop_backends.md`，作为该门禁的已知盲区）。
  - S3 新增门禁：`.harness/scripts/check_error_contract.py`——manifest 的 `errors` 必须覆盖实现会返回的
    错误码（只做单向要求，不反向卡防御性声明）。统计口径下只有 3 条命令缺声明（全部 `INVALID_INPUT`），
    已补齐；负向验证 2 例全红。
  - S3 未收（已登记，属独立切片）：`simulateHuman` 归一化四端不一致——执行器 `bool(inputs.get(...))`
    把 `"simulateHuman": "false"` 当 true、把 `null` 当 false，扩展侧是
    `String(raw ?? "").trim().toLowerCase() !== "false"`。统一会牵动 `browser.py` 与
    `scripts/check_click_helpers.mjs` 的反漂移断言；现由 `xfail(strict=True)` 钉住（统一后会 xpass 报红）。
  - S4 追加发现：① `attachWindow` 的**必填口径两后端不同**（uia 的 `title` 是 `required`、win32 不是），
    已统一为「都可省 + 一个筛选条件都不给则 `INVALID_INPUT`」（原先会退化成「枚举全桌面 →
    `ELEMENT_AMBIGUOUS`」，把输入错误伪装成「窗口不唯一」）。② **exact 路径不报歧义**：
    `FindWindowW` 只返回第一个句柄，故同标题同类名的多窗口静默附着第一个——**有意取舍**（exact 的价值
    是绕开全桌面 UIA 枚举，慢 provider 可达 ~60s），要歧义检测请用 `matchMode=contains`。
    这条与 S1/S3 同源：**声明面与实现面的偏差要逐条写清，而不是留成「用户自己会发现」**。
  - S4 教训（门禁负向验证的方向）：`check_error_contract.py` 是**单向**门禁，其负向验证必须打在
    「注入未声明的码」这个方向上。用「删掉已声明的返回点」验证不会报红（声明比实现多是设计允许的），
    容易被误读成「门禁失效」——**这比不验证更危险**。

- [x] M29 浏览器命令参数漂移收口（`done`，2026-09-21）
  - 计划：`M29-browser-command-param-drift.md`；口径与门禁：`docs/element-mvp-boundaries.md` §3
  - 处置 **7 处**「声明了不生效」+ 更正 1 处文档记录错误：**实装** `click.simulateHuman`（`false`=
    最短路径 `el.click()`，仅普通左键单击）、`click.clickPosition`（random 随机点裁剪进「元素 ∩ 视口」，
    且**遮挡预检用同一个点**——此前预检看中心、事件坐标恒 0）、`modifiers` 的 `Ctrl`/`Win`
    （与扩展里比的 `"Control"`/`"Meta"` 对不上，勾了等于没勾）；**补传导** `cookieGetAll` 的
    `name`/`domain`/`path` 过滤器（此前一个都没转发 → 浏览器级全量）与四个 cookie 命令的 `tabId`
    （从未传 → 空 url 调 Chrome API），并按 Chrome 真实语义改正「支持子串匹配」的错误说明；
    **删除** `screenshot.fullPage`/`selector`（`captureVisibleTab` 只能截可见区；裁剪/拼接需解码图像，
    MV3 SW 无 `Image`/`FileReader`）与 `close.forceKill`/`ignoreUnload`（close=本地解绑）；
    **更正** `waitFor` 四态「`hidden`≡`detached`」的记录错误（代码本来是对的）。
  - **门禁**：`.harness/scripts/check_param_consumption.py` 进全门禁——AST 按 `command == "<id>"`
    切片，声明参数必须被该命令分支读取或属通用读取；返回 `COMMAND_NOT_FOUND` 的未实现命令整表豁免
    （实现后自动纳入）；**负向验证**：临时插假开关立刻红。覆盖 27 条扩展通道命令，范围外 48 条如实
    打印跳过条数。
  - 证据：`scripts/check_click_helpers.mjs`（38）+ `check_precheck_helpers.mjs`（+4）+
    `test_browser_input_params.py`（3→6）；FULL GATE PASSED

- [x] M28 元素自愈与执行前预检（`done`，2026-09-21）
  - 计划：`M28-element-self-healing.md`；调研依据：`docs/element-self-healing-plan.md`
  - **S1**（2026-09-20）运行期消费 `selector.candidates`：主选择器失效时按稳定性顺序回退，
    命中的候选与顺位进执行证据（不改命令参数、不写工作流文件——候选仍以元素资产为单一事实来源）
  - **S2**（2026-09-20）执行前预检与错误分类：扩展侧 `scrollIntoView` → `isConnected`/禁用/
    `<inert>`/`checkVisibility`/rect/视口内/`elementFromPoint` 遮挡，失败以结构化 `precheck`
    回传并翻成 `ELEMENT_COVERED`/`ELEMENT_DISABLED`/`ELEMENT_NOT_VISIBLE`（details 带 blockedBy）；
    **主选择器命中但预检不过时不试候选**（换候选＝换一个元素点，比失败更危险）；drag 一并统一
  - **S3**（2026-09-20）参数漂移修复：`keyIntervalMs` 真正逐字间隔、`clipboard` 实装（粘贴注入 +
    未被接受时显式失败），补 `clickBeforeInput`/`postDelayMs`
  - **S4**（2026-09-21）度量与边界文档：传输层唯一计数点 `ChannelMetrics`（ops/statusProbes/
    roundTrips/retries/channelMs/byOp）→ 每步并进 `CommandResult.diagnostics.extension` 随
    checkpoint 落库；耗时改用 `perf_counter`（`monotonic` 在 Windows 上粒度 15.6ms，会把常见命令
    记成 0ms）；基线文档 `docs/extension-channel-baseline.md`（每命令信封数表由测试机器校验 +
    真实 bridge 实测耗时 + 合法变化/回归判别）与边界文档 `docs/element-mvp-boundaries.md`
    （iframe/shadow DOM/canvas/不可信事件/上传下载/原生对话框/新标签页/嵌套滚动/截图口径/
    `:hover` 待验证 + 参数漂移清单）
  - 附（非 M28 切片，2026-09-20 done）：**扩展通道诊断可见性**——状态栏徽标离线时给出
    「bridge 注册 / 插件安装 / 浏览器运行 / 当前实例是否加载」，而不是一句「离线」
  - 不做（已定案）：新增 `mode: "insert"`；后台标签页焦点模拟
  - 证据：`test_browser_channel_metrics.py`（40）+ `test_browser_precheck.py`（10）+
    `test_browser_element_fallback.py` + `test_ext_bridge.py::test_channel_round_trip_baseline`；
    FULL GATE PASSED

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

