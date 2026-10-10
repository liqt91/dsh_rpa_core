# 任务总表

任务状态使用：`planned`（计划中）、`active`（进行中）、`blocked`（受阻）、`done`（已完成）。

## 当前任务

- [x] **M38 指令测试 L1 契约矩阵**（`done`）——已入表：浏览器通道 32 条命令 / 180 个变体、
  数据+工作流 18 条 / 88 个变体（S3）；覆盖率静态校验 `check_command_matrix.py` 已进默认门禁
  （`PENDING_NAMESPACES` 只剩 `desktop`）；**S1.2 工作台「指令测试」页签已交付**
  （运行/停止 + 覆盖率校验 + 进度条 + 结果树 + 日志；结果走被测方自己的 JSONL 钩子，不解析 pytest
  输出）；**S3 数据/工作流通道已交付**——插桩点与前两片不同（`PythonWorkerExecutor` **真起子进程**，
  真子进程就是真机，一次测到行为/outputs 形状/磁盘状态三面）、抽公共驱动层 `tests/commands/matrix.py`
  统一 `expect` 语义、新增**执行前安全阀** `tests/commands/guard.py`（这条通道真会
  `deletePath + recursive`，用例表写错就会删到目录外；契约测试已进默认门禁）；**S2.1 已交付**
  （2026-09-22，维护者定案**走真桌面 fixture**）：靶子加非幂等计数器、装配抽成
  `tests/e2e/desktop_fixture.py` 共用、桌面通道「暂停/继续」的真机证据补上（跨进程 `resume`
  续接同一窗口 / 会话与元素都接回 / 零重跑，两处缺口都是探针实测逼出来的，实装
  `base.desktop_sessions_from_scopes` + 两后端 `restore_from_scopes`，契约测试进默认门禁）。
  **S2 已完成（2026-09-22）**：`cases/desktop.json`（UIA 17 条 / 78 变体）+ `cases/desktop_win32.json`
  （Win32 19 条 / 81 变体）+ 两个驱动 + 共享装配 `tests/commands/desktop_harness.py`
  （每变体自建会话跑完即弃、每变体前重新抢前台、`{session}`/`{element:名字}`/`{appTitle}`/`{pid}`/`{handle}`
  占位符物化），`PENDING_NAMESPACES` 清空——**86 条命令全部有表**，且**建表即自动出现在页签里**，
  不用改页面。  **S2 补靶子 + 执行层首跑已完成（2026-09-22，S2.2）**：靶子补上原生菜单栏（`MainMenu`，**不是**
  `MenuStrip`——托管控件 `GetMenu(hwnd)` 拿不到）、ListBox/ComboBox、可拖 Label、只读 Edit；
  两条旧判因当场被推翻（win32 的 `title` 其实可用、`menuSelect` 正路径真机通过）；**159 个变体首次
  上真机**，30 处「读代码猜的期望」逐类校正；新增变体级 `knownGap`（严格 xfail，缺口一修就 XPASS
  转红）与 `expect.outputListContains`（治「枚举被拒 → pywinauto 静默返回空列表」与「真没匹配窗口」
  同形）；**172 个变体 → 170 passed + 2 xfailed / exit 0**（**2026-09-23 订正：桌面两表经尾巴 #1
  加变体后当前真值是 190（UIA 85 / Win32 105），实测 `190 passed / exit 0`**——当日数字见
  M38 任务单 §1.14），四驱动全量 440 项 → 438 passed + 2 xfailed。
  产品侧剩余缺口已逐条转入「后续任务」（`screenshot` 依赖、`select`/`getText` 实现、`className`/
  `controlId`、`getWindowList` 的会话声明与静默空列表）；**S2.3（2026-09-23）清掉其中大半**：
  `screenshot` 加 Pillow、uia `select`/`getText` 修复、win32 `select`/`getSelectedText` 显式失败、
  `controlId` 过滤修活、`getWindowList` 会话声明对齐；**尾巴 #1（win32 原生消息）同日实现完**。
  剩余两条：「className 动态名」（**判因已订正**：哈希段实为机器 + 运行时级常量，不是
  「随编译产物变」；出路待拍板）、「枚举被拒静默空列表」。**S4 L2 真机冒烟已完成
  （2026-09-23，§1.8–§1.12）**：浏览器 32 条命令全部有了 L2 处置——109 个 l2 块
  （`l2` = page/inputs/expect/pre/verify/session），真机 110 passed；过程中修复
  3 个真机才可见的产品 bug（check 三操作反转 / goBack API 不可靠 / timeoutMs
  不进 args）+ 1 处 onTimeout 对称性缺口。closeBrowser 显式不收（杀真进程，
  超出测试可隔离面）。L2 显式开关：`RPA_BROWSER_L2=1`。
  - 计划：`M38-command-matrix.md`

- [x] **M40 GUI 响应性：启动阻塞 / 切页无响应 / 捕获静默等待**（`done`，2026-09-28 第二轮收口）
  - 计划：`M40-gui-responsiveness.md`
  - 触发：维护者三句报障「冷启动很慢，启动后，内部切换标签也很慢，捕获元素第一次点击没有红框
    出现，但再次点击提示已有捕获任务进行中」——归因后是**两个 bug + 一个误导性判据**。
  - ① 启动/切页：`HomeWindow.__init__` → `refresh_flows()` → `list_runs()` 在**关键路径上同步全量
    扫** `run_artifacts/`（且 `refresh_history()` 又扫一遍），窗口等扫完才出现、期间主线程不响应
    输入。真机分段实测合计 ~2.4 s、**切页仅 5.6/15.4/8.1 ms（QTabWidget 本身不慢）**。修法：
    `list_runs` 结果复用 + `defer_refresh=True` + `show()` 后 `QTimer.singleShot(0, …)` + 表格冻结重绘。
  - ② 捕获静默等待：判活只看「端点能连上」，而端点是 host 持有的——残留 host 照样连得上，扩展
    收不到 arm、页面永不出高亮框，桌面腿又在浏览器内容区让位 → 静默等满 90 s。**ack 早在协议里**
    （扩展回 `capture_armed`，host 的 `ext_bridge` 一律 broadcast），只是父端把它丢了。修法：记 ack +
    `pick()` ⓪ 段「已连接但超期未 ack」判死（3 s 宽限 / 30 s 降级预算 / 文案含「未响应」）。
  - ③ 捕获零反馈无出口：主窗已最小化、状态栏不可见、二次点击只拒绝（而 Esc 是腿内部手势）。
    修法：新建 `gui/capture_float.py` 置顶浮窗（两腿分列状态 + 倒计时 + 取消按钮），**第二次点击＝取消**。
  - 顺带修两个静默 bug：`work()` 无保底 emit（pick 抛异常 → `finished` 永不 emit → 会话永不复位、
    主窗永不还原）；桥/会话非一对一（取消后立刻重开，旧线程收尾会清掉新会话）。
  - 门禁：**负向验证 8 处**（探针 `.harness/spike/probe_m40_negative.py`）全部「对照绿 → 注入精确红
    → 逐字节还原」；过程中抓到**两处我自己的判据缺陷**——①首版探针只判 `returncode != 0`（假绿灯机）
    且期望 nodeid 打偏（注入后仍绿，实测出两条 ack 用例的分工）；②一条用例被注入后**挂住**（走到
    真模态对话框）而非干净变红，桩掉对话框才修好。
  - FULL GATE PASSED（见 `.harness/PROGRESS.md` 的 M40 条目）
  - **第二轮（同日追加报障，重开为 active）**：「内部标签切换第一次点击很明显 / 点击元素库第一次
    也会卡一下、关掉重开就好多了 / 重启 gui 再测就不卡了」。① 维护者指认「内部标签」= 工作台三页签
    （全仓 `grep QTabWidget` 只此一处；元素库是**编辑器**的 dock）。② **第一轮的修复只是把阻塞搬了
    位置**：三臂 A/B 实测第一下点页签被推迟 202.3 ms（旧整段 `refresh_flows`）→ 8.9 ms（新分片），
    `none` 地板臂 −1.4 ms——原来阻塞在窗口出现**之前**（用户点不到，感觉是启动慢），defer 之后
    窗口先出现、紧接着那一轮被占住，正好压在用户第一下手上。③ 四条候选（惰性 import 2.6/4.4 ms、
    页签切换 0.1–3 ms、冷磁盘、show 后阻塞段）在同一份数据里逐条排除，只有最后一条命中。
    ④ 「重启就不卡」= **OS 文件缓存**：`list_runs` 本机 157 条/464 文件冷盘 400.2 ms（open 自耗
    274.9 ms）→ 热盘 121.7–141 ms，与第一轮修复无关（第一轮修的是阻塞位置，没减读取量）。
  - 修法：`run_history` 抽出 `iter_run_summaries()`+`sort_runs()`（`list_runs` 同源组合）＋
    `home.start_refresh()` 先廉价填流程库再用零延时 QTimer 链分片扫（6 ms/轮、每轮至少一条）＋
    待读期间运行列显示 `…`（还没读到 ≠ 确实没有）＋ `refresh_flows()` 入口取代进行中的分片
    （否则旧扫描后到覆盖新结果）＋ `run_gui` 接 `start_refresh` ＋ 编辑器 `showEvent` 首显预热元素库
    dock（首开 65.2 ms / 二次 20.3 ms）。
  - 门禁：**负向验证 15 处**（8 → 15，新增 7 处覆盖分片/占位/取代两条入口/接线/预热）全部「对照绿 → 注入
    精确红 → 逐字节还原」。**事故**：探针按「备份目录里有这个文件」自愈，用陈旧备份把本轮已改好的
    `app.py`/`home.py` 盖回 HEAD；改为**哨兵式自愈**（注入处留 `# [M40-NEGATIVE-INJECTED] idx=N`，
    只还原带哨兵的文件）并实测该自愈机制本身。**收尾重跑又抓到两处「注入本身」的缺陷**：① #12 摘掉
    `refresh_flows` 入口的 abort 后用例如样绿——`refresh_flows()` 结尾无条件调 `refresh_history(runs)`，
    被后者的 abort 兜住了；判据改为**中间态**（spy 记下「这一次同步读开始时旧分片是否已死」，取代必须
    发生在新读**之前**）。② #15 锚点从一行中间切一刀，哨兵把原行剩余部分顶到下一行 → 注入语法坏掉；
    锚点改整行取，并加前置检查 `_anchor_ends_at_line_end`（第一次误用「`new` 不以换行结尾」，把 7 处
    合法的删除型/行尾型注入一起判失败，当场改成边界判据）。

- [x] **M41 捕获高亮「躲着鼠标」/ 启动加载提示**（`done`，2026-09-28）
  - 计划：`M41-capture-affordance-and-startup.md`
  - 触发：维护者两条体验要求「捕获元素的框跟影刀的一样，躲着鼠标，否则影响其他元素的捕捉」＋
    「启动前如果要初始化，可以有个加载提示」。两条都**不是**崩溃类缺陷，是观感与反馈。
    **第二轮澄清**（同日）：第一句的真意是「捕获期的**桌面悬浮窗**在鼠标即将移到它上面时翻到屏幕
    另一侧」，而不是「框线压住指针像素」——故切片是三条（S1 字面理解 / S2 启动卡片 / S3 澄清落点）。
  - ①（S1）红框压鼠标（**纯视觉，不影响命中**）：`_HoverOverlay` 的 region 挖空区是元素 rect 的**内缩**版，
    3px 框线带因此占住元素最外 3px——指针贴边（小元素/格子/窗口边）时框线正好压在指针上。真机取证：
    §3 A/B 证 overlay **不参与** UIA 命中（13 个采样点差异 0/13，先排掉功能面）；§6 把真实指针移到
    采样点上截图判定，改前 **6/13** 点的指针 ±2px 内有红色框线像素（最近距离 0）→ 改后 **0/13**。
    修法：框线带整条移到元素**之外** [2,5)——外移量 = 框线宽 3 + 指针热区 2 = 5（§7 净外移扫描
    0/3/5/8 → 压线 6/2/0/0，精确命中解析解）；浏览器页内框同款几何并**去掉 12% 红填充**
    （填充把元素内容整片染色）。两通道几何完全一致。
  - ②（S2）冷启动「窗口出现前」约 1 秒无任何反馈：分段**首次**采样 ≈976 ms（`load_catalog` 420.5 ms 含
    惰性 import + `build_application` 275.8 ms + 窗口构造 68.5 ms + 首帧 211.3 ms）。**注：3 次取中位
    只有 60.6 ms，据此会得出「提示会闪、没必要」的错误结论**（一次性成本要看首次）。
    修法：新建 `gui/splash.py`（卡片无边框/置顶/**不收焦点**）+ `startup_splash()` contextmanager
    （异常路径也撤卡），`run_gui` 用 `with` 包住 load_catalog → 窗口构造 → `show()`，并把
    `singleShot(0)` 的预热/分片首轮挪到 `app.processEvents()` 之前——首帧与预热都在卡片在场时消化。
  - ③（S3，第二轮澄清的落点）**捕获悬浮窗挡住鼠标路径**：先定性——`CaptureFloatWindow` 是
    `Qt.Tool|WindowStaysOnTop|Frameless` 的**真实窗口**（无 `WS_EX_TRANSPARENT`、不吃穿透），压在
    鼠标路径上时**同时挡住视觉与点击**（用户会「点不中」它下方的元素）；而高亮红框必须框住元素、
    几何上**不可能**「移到另一侧」——澄清的落点只能是这个浮窗。修法：新增**纯函数**
    `capture_float_origin(area, size, cursor)`（默认贴右下角，鼠标落入「默认位置矩形外扩
    `AVOID_PAD=80`」即翻到左上角）；**判定基准恒为默认位置**、与浮窗当前所在处无关 ⇒ 无状态、
    不抖动，「走开自动回位」天然成立；`avoid_cursor()` 取代 `place_bottom_right()`，新增
    `user_positioned`（用户拖过就不再自动挪）；`app.py` 用 **40ms（≈25Hz）** 避让节拍——比 500ms 的
    状态节拍快得多，否则快速甩动的鼠标会追上浮窗（`AVOID_PAD=80` 取「~1500 px/s × 40ms ≈ 60px」下限）。
  - 门禁：**负向验证 12/12（S1+S2）＋ 11/11（S3）**（探针 `.harness/spike/probe_m41_negative.py` 与
    `probe_m41b_negative.py`，支持 pytest 与 node 两类判据的判绿/判红口径）。新判据：
    `tests/unit/test_desktop_overlay_geometry.py`(10) + `tests/contract/test_gui_splash.py`(6) +
    `scripts/check_capture_overlay_geometry.mjs`(27 项，已进 `PURE_FUNCTION_SCRIPTS`)、
    `tests/unit/test_capture_float_geometry.py`(11) + `tests/contract/test_gui_capture.py` 新增 3 项
    （行为 / 用户接管 / 四处接线）。**首轮即抓到 2 处判据缺口**（都是我自己的）：`show_rect` 没有任何
    判据钉住（纯函数全绿也拦不住「执行器没用上」）→ 补接线判据；「泵没泵事件」offscreen 下不可观测
    → 补 monkeypatch 记录器判据。S3 同理：`avoid_cursor` 是否真被节拍与初始定位调用，只能用
    `inspect.getsource` 断言（纯函数正确 ≠ 接上了）。
  - 实测纠正两处想当然：PySide6 `Qt.WindowType.SplashScreen` 枚举**不含** `WindowDoesNotAcceptFocus`
    （契约测试首跑即红）；overlay **不参与** UIA 命中（曾以为会被命中）。
  - 残留（登记不修）：**元素命中粒度**——鼠标贴窗口边界时命中退化为「窗口本身」、框会铺满整窗
    （那是命中语义问题，改框治不了）；页内提示条仍跟元素不跟鼠标（在元素外侧，压不到指针）；
    **`RunFloatWindow`（运行浮窗）不避让**（同样是右下角真实窗口，本轮只做捕获链路）；
    **避让只按主屏几何**（多显示器下即便浮窗被拖到副屏仍按主屏算）；**避让是「翻到对角」而非连续跟随**。
  - **S1 如实交代**：它是第一句话的**字面理解**产物，不是第二轮澄清要的东西。它是探针实测支持的
    改进（框线不再压指针、不吃元素内容），本轮**保留**；若维护者不要这个视觉变化，改动隔离在
    `overlay_bounds` / `overlayBoxRect` 两处，可单独回退。

- [x] **M42 网页捕获：已打开页面不生效 / 捕获后红框残留**（`done`，2026-09-28）
  - 计划：`M42-capture-extension-lifecycle.md`
  - 触发：维护者「捕获网页元素时，不会在已经打开的网页上生效，捕获网页元素后，网页上红框还在」。
  - 同源根因：**声明式 content_scripts 只在页面加载时注入**。扩展（重新）加载之后，已经打开的
    标签页要么没有捕获脚本、要么只剩一个**僵尸**脚本（`chrome.runtime.id` 已消失、
    `sendMessage` 抛 `Extension context invalidated`）。`broadcast()` 此前**只发消息并把失败静默
    吞掉**（注释只提「chrome:// 等受保护页面」），于是 `capture_arm` 永远送不到这些页面 = 「不生效」；
    僵尸仍按自己那份 `armed` 画框，却收不到 `capture_disarm`，而 `capture()` 失败路径又**刻意
    不清框**（只在提示条上说一句）⇒ 红框永久赖在页面上 = 「红框还在」。
  - 修法必须**成对**（只做一侧都无效）：`background.js` 对 `sendMessage` 失败的标签页
    **补注入** `content.js`（仅 http(s)/file、**仅 arm 时**）后重发；`content.js` 的守卫由
    「装过就一律拒绝」换成**实例注册表** `{alive, teardown}`（活实例拦人 / 僵尸先拆干净再接管），
    并加 `onMove` 自愈（上下文没了就不再画框）与失败路径收框（`hideBox` + 保留提示条）。
    顺带堵同族两条：**host 断开兜底撤防**（推送模型没有心跳，丢一次 `capture_disarm` 即永久
    停在捕获态）、**`capture_arm` 先落盘再广播**（补注入的脚本启动时查 `storage.session`）。
  - 判据：新增两条 node 门禁进 `check_all.py`——`check_capture_lifecycle.mjs`（28 项，桩环境整文件
    求值 content.js）+ `check_capture_broadcast.mjs`（23 项，切片 + 送达模型：只有页面有可用脚本
    时 `sendMessage` 才成功）。**顺带把一条弱断言搬了家**：`test_capture_extension.py` 里那两条
    `addEventListener(...)` 源码断言在注册改走 `on(...)` 辅助函数后当场变红——它只能证明「写了
    这行字」，现已由 node 门禁用桩事件真求值（S6–S10）覆盖，Python 侧只留接线。
  - 负向验证 **13/13**（`.harness/spike/probe_m42_negative.py`，content 5 + background 8），
    四条防假绿灯齐备（对照跑 / 失败类型判定**排除脚本自崩的假红** / 挂住判定 / 逐字节还原核 md5）。
  - 残留（登记不修）：**维护者需要重新加载扩展**（版本 `0.4.0` → `0.4.1`）才生效；
    **跨 isolated world 的僵尸脚本拆不掉**（两种 world 模型下行为都正确，但不同 world 时旧实例
    的框要等页面重载才消失，本轮无条件实测 world 归属）；`_BROWSER_CONTENT_CLASSES` 只有
    `Chrome_RenderWidgetHostHWND`，Firefox 的 `MozillaWindowClass` 在 hybrid 下不让位（与本条无关）。

## 后续任务

- [ ] **M52 选择器优选（唯一 · 稳定 · 短）**（`active`，2026-10-10 立项；**S1 已落地**）
  - **S1（2026-10-10）已落地**：桌面 penalty 引擎（`model.desktop.choose_best_locator` /
    `locator_penalty`，字典序唯一硬门）+ `capture_at` 接线（`_pick_best_candidate`）；
    判据 11 条；负向验证 6 注入全命中。判据 `tests/contract/test_desktop_locator_ranking.py`。
  - 计划：`M52-selector-advisor.md`。对标影刀「智能元素」（捕获期挑唯一/稳定/短的选择器）。
  - 本质＝候选生成 × 唯一性实测 × 打分排序，**可确定性实现、不需 LLM**（守 `AGENTS.md` 规则 6）。
  - 与 M51 分界：**M51 = 候选「供给」**（XPath / 候选扩容），**M52 = 候选「优选」**
    （三因子打分 唯一/稳定/长度 + 排序落盘 + 推荐 UI）。软依赖 M51-B；编辑期优选与运行期
    自愈共用同一份排序候选。**未动 `project_state` 排期字段**（`next_milestone` 仍 M51）。

- [x] **里程碑编号口径定案：M48 = 桌面契约一片，M49 = GUI 体验**（`done`，**已拍板**，2026-10-08）
  - **冲突曾存在**：`docs/yingdao-gap-catchup.md` §4 里 M48 = 桌面契约一片（D1+D2+D3）、
    M49 = 系统层 + 新能力立项（E1/D4/E2/E3）；而实际执行的 M49 是「GUI 体验 P0–P3」，
    同号不同内容，且 M48 从未执行。
  - **定案（维护者 2026-10-08 拍板，选「不重编号」）**：**M48 保持「桌面契约一片」原定义**
    ——理由是 2026-09-29 已授权「M48 破戒（locator 可加 path 等字段）」，该授权锚点、
    `M45-editor-ui-l1.md` 的「契约层，M48」引用、`.workbuddy/memory/2026-09-29.md` 的记录
    全部依赖这个编号，重编号会让它们失去锚点。**M49 = GUI 体验 P0–P3**（已落地，不再改动，
    实际执行即成事实口径）。E1/D4/E2/E3 顺延为后续里程碑（编号待排，不占用 M48）。
  - 已同步：`docs/yingdao-gap-catchup.md` §4 表下说明改为定案；本文件 M48 条目去掉「待拍板」。

- [ ] **`classify_offline_reason` 的 `host-not-reachable` 判据与注释不符**（`planned`，2026-09-28
  M40 §4 自挖；本片未顺手改）
  - 注释写的是「扩展加载着**却没端点**」，实现里**根本没有端点这一项**——只要
    `running and extensionLoaded` 就报 `host-not-reachable`。于是「插件装了但没注入」（`extensionInjected=false`）
    与「host 没上线」这两种成因被合并成同一句文案，用户分不清该重开浏览器还是重装插件。
  - 为什么没顺手改：它不是本次报障的现场（M40 已用 ack 判活把用户侧体验与它解耦），而改它要同时
    动判据、文案与既有断言，属独立小片。
  - 证据：M40 任务单 §1.2（本机通道实测 `endpoints:[...]` 存在 + `reason: host-not-reachable`）。

- [ ] **残留 `rpa-core-ext-host.exe` 占着端点**（`planned`，2026-09-28 M40 §4 自挖）
  - 实测 2026-09-28：两个残留 host 进程（8:53 起）持续占着 `rpa_core_ext_*` 端点，导致「端点存在
    但扩展不在」。M40 的 ack 判活已让**用户体验**不受影响（会被判死并如实提示「未响应」），进程侧
    靠 M34 的空闲自杀（30 分钟）收敛。
  - 之所以只登记：它属宿主生命周期，不在本片范围；若后续观察到「残留超过空闲阈值仍不退」，应转
    M34 的机制复查而不是在捕获链路打补丁。

- [x] **「内部切换标签很慢」待维护者指认具体界面**（`done`，2026-09-28 M40 第二轮——**已结案**）
  - 维护者第二轮指认：就是**工作台三个页签**（全仓 `grep QTabWidget` 只此一处；「元素库」是**编辑器**
    的 dock，不是页签）。真正的原因不是页签本身（首次显示实测 5.6 / 15.4 / 8.1 ms），而是第一轮
    `defer_refresh` 把产物扫描**搬到了 `show()` 之后那一轮**，正好压在用户第一下手：三臂实测第一下
    点页签被推迟 **202.3 ms → 8.9 ms**（分片后）。已按根因修，详见 M40 任务单 §10。

- [ ] **运行历史的读取总量没减（冷盘 400 ms 仍是全量读）**（`planned`，2026-09-28 M40 §10 自挖）
  - M40 第二轮改的是「**谁在什么时候读**」（分片，不阻塞输入），**没有减读取量**：`list_runs` 在本机
    157 条运行 / 464 个文件下冷盘 400.2 ms（其中 `open` 自耗 274.9 ms）、热盘 121.7–141 ms。分片把这段
    摊到多轮、输入照常被派发，但用户从启动到「运行状态列全部填上」仍然要等 ~0.3 s（冷盘更久）。
  - 候选做法：按目录 `mtime`/`result.json` 大小做**增量或缓存索引**（只在产物目录变动时重读）。
  - 为什么本片不做：本次报障是「**卡输入**」而不是「填得慢」，且缓存引入失效面（运行产物是**外部**写入
    的目录，`run` 子进程随时会新增），属独立切片，需要先定「谁是失效判据」。
  - 证据：M40 任务单 §10.2（同进程冷→热对 400.2 → 128.2 ms，差异来自文件读取而非解析）。

- [ ] **门禁偶发红：`test_extension_capture_offline_without_endpoint` 间歇失败**（`planned`，待归因）
  - 观测（2026-09-22，M38 S1.2 收口期间）：当天 **5 次全量运行里 1 次**该用例红——
    `pick` 返回 `{'cancelled': True}` 而不是 `{'offline': True}`；其余 4 次全绿。隔离验证：
    单独跑该文件 3 次、只跑 `tests/contract` 2 次、红后全量复跑 1 次（1088 passed / 12 skipped /
    2 xfailed = 1102 项），均稳定绿。
  - 线索：失败的返回体形状与**同一文件前一条用例**打 `/api/capture/browser/cancel` 的响应
    完全一致，所以方向有两个——① cancel 的取消标记跨 session 残留，被下一条用例新开的 session
    消费掉；② 启动瞬间存在**半死的 bridge 端点**（`start` 因此判定为「在线」而不是 offline），
    arm 后端点消失，于是 pick 以 cancelled 收场。当天该次失败前有一次不当的 `taskkill`
    误杀了别的 python 进程，②的可能性因此上升，但**没有复现证据，不下结论**。
  - 为什么要登记：它打在「退出码即门禁结论」这条纪律上——**间歇红最容易被当成无关噪声忽略**，
    而 M38 §4.2 已经证明过「全绿的用例数」与「门禁通过」是两件事。归因前遇到该红，
    请先复跑确认，不要直接改断言放宽（会把真实缺陷一起放掉）。

- [ ] **门禁偶发红：桌面 E2E 记事本切片在整目录运行时失败**（`planned`，待归因；
  **已用干净对照排除与 M38 S2.1 改动相关**）
  - 观测（2026-09-22，M38 S2.1 收口期间）：`RPA_DESKTOP_E2E=1 pytest tests/e2e`（整目录）
    **连跑三次都红在同一处**——`test_windows_desktop.py::test_windows_desktop_vertical_slice`
    （三次进度行都是 `.....F.`，即那三次是**稳定复现**而非随机），而同文件单跑、两两组合、
    本次新增的两个文件单独跑均绿。缺省门禁不含该目录（`--with-desktop-e2e` 才跑）。
  - 取证：pytest 的 `tmp_path` 保留最近三次，失败运行的 `events.jsonl` 还在盘上，配合探针
    `.harness/spike/probe_win32_notepad_slice.py`（含 `--dump <events.jsonl>` 模式，用例本身
    只断言 status、拿不到细节）取出**三个不同失败签名**，全部落在**本次改动未触及的节点**：
    ① `attachMain` / `desktop.win32.attachWindow` → `ELEMENT_NOT_FOUND`
       `details={"title": "无标题 - 记事本", "matchedCount": 0}`。该节点按 title **精确匹配**，
       而 fixture 期望的是**全新未命名**的记事本——怀疑与 Windows 11 记事本「重启恢复上次
       标签页」（标题变成 `test.txt - 记事本`）有关，即**跨运行状态累积**；
    ② `openDialog` / `desktop.win32.menuSelect` 或 `inputPath` / `desktop.win32.input`
       → `EXECUTOR_FAILED` `(0, 'SetForegroundWindow', 'No error message is available')`；
    ③ `attachOpened` / `desktop.win32.attachWindow` → `EXECUTOR_FAILED`
       `Handle <n> is not a vaild window handle`（打开对话框已被 Enter 关掉、句柄已失效）。
    ②③ 都是**真实桌面焦点/时序竞态**，①更像跨运行状态累积。
  - **对照实验（排除与本次改动的相关性）**：从 `HEAD` 建干净 worktree
    （`git worktree add --detach`）并用**同一探针**跑，先确认导入的确是改动前的代码
    （`hasattr(Win32DesktopExecutor, "restore_from_scopes") is False`）。结果：HEAD **14 次红 4 次**
    （①×1、②×1、③×2），本仓库 **14 次红 5 次**（同三款签名）；为进一步排掉「环境随时间漂移」，
    又做**交替 A/B 六轮**（HEAD、本仓库轮流跑）——HEAD 六次红 2 次、本仓库六次**全绿**。
    结论：**既有抖动，与本里程碑 diff 无关**（4/14 vs 5/14 无差别；此前看到的「本仓库红得多」
    是时间聚集造成的假象）。结构上也不该相关：该切片**不走 resume 路径**，本次新增的
    `restore_from_scopes` 在此根本不会被调用，另一处改动只是给 effect 的 `details` 多写一个
    `locator` 键（纯数据）。
  - 线索/候选（**未验证，不下结论**）：① `attachMain` 改按 `processId` 定位（测试侧本就
    `Popen` 拿得到 pid，且下游 `attachDialog`/`attachOpened` 已是按 pid/类名定位），可去掉
    「标题随记事本状态变」这个变量；② `SetForegroundWindow` 返回 0 是 Windows 的前台权限语义，
    可能要在实现层重试或走 `AttachThreadInput`；③ `attachOpened` 前应对句柄做轮询等待。
    **三者修法互相独立，且②③不是用例侧能修的**——先登记，等 M38 S2 真要把桌面通道进 L1
    矩阵时一并处理（届时这片的稳定性就是前提）。
  - 为什么要登记：缺省门禁不含桌面 E2E，**最容易在需要它的时候才发现它抖**；且
    `tests/e2e/test_uia_desktop.py` 的 `force_foreground` 注释早记过「门禁内抖、单跑即过」的
    同类现象，本条可能是同一根因的另一个面。归因前遇到该红，先复跑确认，不要直接改断言或
    放宽 fixture。

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
- [x] **桌面 `screenshot` 的依赖缺口**（`done`，2026-09-23 M38 S2.3 维护者定案**加 Pillow**）——
  `pyproject.toml` 增 `pillow>=10,<12; sys_platform == 'win32'`；`capture_as_image()` 内部就是 PIL，
  加依赖即修复，两后端正路径真机落盘 PNG（实测文件头 `\x89PNG`）。曾评估的 BitBlt 方案
  （零依赖但要自写 GDI 截屏 + PNG 编码）否决。原 `knownGap` 标记已摘除。
- [x] **桌面 `select` / `getSelectedText` / uia `getText` 的实现缺口**（`done`，2026-09-23 M38 S2.3）
  ——uia `select` 改按列表项 `SelectionItemPattern.Select()`（label/value 按列表项文本匹配；
  读侧是 effect details 的 `selectedItem` 读回——**UIA Select 不触发** WinForms 的
  SelectedIndexChanged，状态回显 Label 不是有效读侧，实测 round5）；uia `getText` 改
  ValuePattern 优先（comtypes 只有 `CurrentValue` **属性**，`GetCurrentValue()` 方法不存在，
  实测 AttributeError）；win32 `select`/`getSelectedText` 按「别静默成功」定案改为**显式
  `EXECUTOR_FAILED`**（details 带 operation）。e2e 两条 xfail 已转正。
- [x] **win32 `select` / `getSelectedText` 的原生消息实现**（`done`，2026-09-23 M38 尾巴 #1）
  ——S2.3 的显式失败只是止血；真正的能力走 `LB_SETCURSEL` / `CB_SETCURSEL` +
  `LB_GETCURSEL` / `LB_GETTEXT`（pywinauto 的 ListBox/ComboBox 包装类已封装
  RemoteMemoryBlock 跨进程缓冲区，**不用自己写 VirtualAllocEx**）。实施要点：
  - `_find` 拿到的元素**自动包装**成 `ListBoxWrapper` / `ComboBoxWrapper`（pywinauto 的
    `windowclasses` 正则里就写着 `WindowsForms\d*\.LISTBOX\..*`），直接
    `element.select(...)` 即可，不必手工构造包装对象。
  - **读侧必须显式取索引再判 `>= 0`**：`selected_text()` 内部是
    `item_texts()[selected_index()]`，无选中时 `CB_GETCURSEL` / `LB_GETCURSEL` 返回 -1，
    Python 负索引会把它静默变成**最后一项**（实测不加判断时 ComboBox 回 `'three'`、
    ListBox 回 `'gamma'`，加了才是空串）。
  - **一处原判断被实测推翻**：旧注释写「原生消息同样**不触发** `LBN_SELCHANGE`」——
    原生消息本身确实不发通知，但 pywinauto 的 `select()` 在设完 curs 之后会
    `notify_parent(LBN_SELCHANGE / CBN_SELCHANGE)`（post 一个 WM_COMMAND），
    所以**走包装类的实现会让 UI 真的反应**：实测 `listStatus` 逐步
    `none → list:1:beta → list:2:gamma → list:0:alpha`。这比 uia 侧强（那边的
    `SelectionItemPattern.Select()` 确实不触发 SelectedIndexChanged）。
- [ ] **`desktop.win32` 的 `className` 定位字段名不副实**（`planned`；`controlId` 部分已由
  M38 S2.3 修复，见下）
  - ~~`control_id` 的过滤完全不生效~~（**已修**，S2.3：`_find` 拿到 descendants 后按
    ElementInfo 的 control_id（GetDlgCtrlID）手工补滤——pywinauto 的 children 只读
    class_name/title/control_type，`control_id` 被静默忽略；且包装元素上的 `control_id`
    在 0.6.9 是 deprecated **方法**而非属性，必须走 `element_info.control_id`。用例表以
    「错误 id 排除 title 命中」负路径钉住，停用补滤该行即红，负向验证已做）
  - **判因订正（2026-09-23 尾巴 #1 取证时实测）**：本节此前写 `className` 的哈希段
    「随编译产物变」——**错**。它既不是产物级也不是应用级，而是**机器 + 运行时级常量**：
    实测同一 exe 连跑 3 次、同路径重编译、不同路径/文件名重编译、纯注释改动、真实 IL
    改动（改控件文本）——五轴下哈希段全部恒定；另一个**完全不同的** WinForms 程序拿到的
    也是同一段 `34f5582_r8_ad1`（探针 `.harness/spike/probe_win32_stability.py`、
    `probe_win32_classname_rebuild.py`、`probe_win32_classname_content.py`、
    `probe_win32_classname_other_app.py`）。换机器或换 CLR/WinForms 版本会变，所以仍
    不能硬编码进仓库，但它不是「每次编译都变」。
  - 另一处相应订正：`control_id` 的漂移粒度是**每次进程启动**（同一个 exe 连跑三次拿到
    三个不同值），不是「每次编译」。
  - 真正的麻烦不是「哈希会变」，而是**类的可区分性太粗**：两个 Edit 撞同一个类名，且
    「无窗口文本」的控件（ListBox / ComboBox）在 win32 侧**只能**靠 className /
    controlId 定位——这正好把尾巴 #1 的新能力和它绑在一起（不解决本条的**可用性**，
    尾巴 #1 的能力在实践中够不着）。
  - 出路（**已拍板：选 ③，2026-09-23，已实现**）：① `className` 改**子串/包含**匹配
    ——否决（治不了「用稳定前缀跨机器筛选」的需求）；② 按 `GetClassNameW` 精确化文档口径、
    保持现状（用户必须粘贴含哈希的完整串，跨机器失效）——否决（只是描述现状）；
    **③ 新增 `classNameRe` 字段（动 manifest 契约）——采用**。
  - **实现落地（M38 §1.16）**：`classNameRe` 与等值 `className` **互斥**（等值 vs 正则两种
    语义，同给报 `INVALID_INPUT`），两后端 + 三处 manifest + i18n + 契约测试 + 各 3 条变体。
    模型层 `DesktopLocator.require_identity` 与执行器前置检查双落地；`ValidationError`
    显式映射 `INVALID_INPUT`（原会被 catch-all 吞成 `EXECUTOR_FAILED`）。uia 侧 exact +
    `classNameRe` 走 EnumWindows（`FindWindowW` 只收字面类名）。**该条从「可用性缺口」转正：
    无窗口文本的控件现在能用稳定前缀定位了。**
  - **测试侧**：L1 驱动仍按运行期读回的完整类名注入 `{listBoxClass}` / `{comboBoxClass}`
    占位符（换机器照样跑）；新增的正则变体判据用**实测类名**写——主窗口实测
    `WindowsForms10.Window.8.app.0.34f5582_r8_ad1`（比子控件**多一段窗体序号 `.8`**），
    所以正则写 `WindowsForms10\.Window\.[0-9]+\.app`（探针
    `.harness/spike/probe_win32_target_classes.py`）。
  - **注意**：`title` 是好的（它比的是控件窗口文本，实测 `Submit`/`Count`/`note-ready`
    都唯一命中），别一起误删。
- [ ] **`desktop.getWindowList`：枚举被拒时静默返回空列表**（`等复现`，2026-09-23 维护者
  拍板**不改代码**——见任务单 §1.16 末节）
  （「声明与实现不一致」部分已由 M38 S2.3 修复——`getWindowList` 已从
  `_no_session_commands` 名单移除，声明对齐实现：它需要会话，两后端同改）
  - 静默空列表：本命令走 pywinauto 的 `uia_element_info._get_elements`，那里
    `except (COMError, ValueError): return []`——全桌面枚举被 COM 拒绝时**返回空列表而不是报错**
    （实测的 `0x8001010d` = `RPC_E_CANTCALLOUT_ININPUTSYNCCALL` 就是这条路径，pywinauto 内部吞掉、
    进程继续；注意 pytest ≥5 默认的 faulthandler 会把它渲染成 `Windows fatal exception`，**那是
    噪声不是崩溃**）。于是「枚举失败」与「本机真没有匹配窗口」在 outputs 上完全同形。
  - 现状：已用 `expect.outputListContains`（锚点是靶子窗口标题）把两后端 8 条正路径从形状断言
    变成真判据；探针 `.harness/spike/probe_desktop_window_list.py` 连跑 8×2 次枚举**没撞上**
    （本机稳定 11 / 13 项），所以登记的是「路径存在、本轮未复现」。
  - 可选加固（未定）：枚举失败时**区分**「空」与「被拒绝」，或对 COM 拒绝做一次短重试
    （M7 对同类 `RPC_E_SERVERCALL_RETRYLATER` 已有处置先例）。**S2.3 追记（2026-09-23）**：
    四驱动连跑时 uia 侧 `getWindowList` 前两条出现过**一次** `Desktop operation timed out`
    （15s 默认操作预算被全桌面枚举冷启动吃满；单跑与复跑均过、win32 侧从未红）——
    候选修法多一条：给 `getWindowList` 的 schema 加 `operationTimeoutMs`（需动契约，与
    上面的「区分空与被拒」一起做产品决策）。
  - **拍板（2026-09-23，M38 §1.16）：等复现，不改代码。** 维护者定案——无可复现输入时
    预先改产品代码只能靠猜：把「枚举被拒」改成抛错，反而可能把「本机真没有匹配窗口」
    误判成失败（两态在实现侧同形）。保留上述观测证据，**复现后按当时的现场再决策**
    （届时优先做的仍是「区分两态」，`operationTimeoutMs` 作为独立候选）。
- [x] **两个死参数的处置**（`done`，2026-09-22 维护者定案**删除**，M38 S1.1）——删除「声明」
  而非「能力」：两项从未被消费过（`closeTabs` 的路由由会话绑定的浏览器决定、扩展的
  `tabs.waitLoad` 只收 `tabId`/`timeoutMs`），删掉后行为不变、`KNOWN_DEAD_PARAMS` 已清空
  （台账机制保留给 S2/S3）。对标文档同步更正：影刀「等待网页加载完成」本身也只传
  网页对象 + 超时，故与影刀的差距**没有**扩大
- [ ] **技术路线（ADR 0016）**：GUI 为唯一主力形态——新增能力优先落 GUI；Web 编辑器（devserver）
  `devserver/static/` 冻结演进（不删除、不再补齐 GUI 已有能力）
- [ ] UI、DSH、MCP、调度器和安装器集成（`planned`）——见远期任务

- [ ] **QProcess 型 GUI 用例在「全量套件跑完后」会 30s 超时**（`planned`，2026-09-28 M41 收口实测，
  S4 轮订正描述）
  - 现场：`tests/contract/test_gui_command_matrix.py::test_panel_streams_jsonl_into_rows` 与
    `::test_shutdown_kills_running_child`，两条各有 **30s 固定超时**（分别等「子进程出第一行结果」
    与「子进程起来」）。**红窗内**整文件跑 62–72s → `2 failed`；**红窗外**整文件 4.5–6.2s 全绿。
  - **红窗怎么来的（S4 轮实测，可复现）**：门禁**前**单跑那两条 = `2 passed in 1.25s` →
    跑完一次全量门禁（292s）**后立刻**单跑 = `2 failed in 62.21s` → 再隔几分钟单跑 =
    `2 passed in 1.27s`。⇒ **「跑完一次全量套件」本身就把这两条推入红窗**，随后自行恢复。
    与上午的对比一致：上午 `FULL GATE PASSED` 那轮全量 **177s**，现在 **292–330s**——
    套件变慢与这两条变红**同现**。
  - **不是 M41 改动引入**（对照实验）：把 `app.py` / `capture_float.py` 还原到 HEAD 再跑同组合
    → 同样 `2 failed, 111 passed in 94.14s`；且 pytest 总数 `1203 = 1202 + 1`（本轮新增判据全绿）。
  - **已否证的机制（后人别重走）**：① **不是「子进程启动慢」**——红窗内实测
    `.venv/Scripts/python.exe -c pass` = 0.82–0.93s、`node -e 0` = 0.66s、
    `uv run python -c pass` = 1.32s，`rc` 全 0；② **不是调用口径**——
    `.venv\Scripts\python.exe -m pytest` 与 `uv run pytest` 同绿同红；③ 已排除并发、
    `%TEMP%` 累积、随机顺序插件（未安装）、pytest 版本；六轮二分一度指向
    `test_browser_precheck.py`，但**它自己只花 1.12s**（同一份会话里当过 64s 的「受害者」）；
    `PYTHONPATH` 与 `tests.commands.desktop_harness` 的 import 也被怀疑过，**补了空对照插件后
    同样被推翻**。**教训（2026-09-28 最大一条）**：这条红/绿**随时间开合**，任何「单次实验的
    相关性」都不成立，必须带对照。
  - **S5 轮再否证三条（2026-09-28，都带对照）**：
    ① **不是沙箱删除守卫**（这是首轮与 S4 轮一直挂着的机制）——`sitecustomize.py` 的
    `_check_bulk_delete_guard` 确实「每删除一次就起一个 Node 进程」（阈值
    `CODEBUDDY_SAFE_DELETE_BULK_THRESHOLD=50`，`.lock` 目录锁 2s 超时），但用
    `CODEBUDDY_TOOL_CALL_ID`（守卫的唯一开关）做 **A/B 交替**实测：守卫开 `4385 / 4650 / 5307 ms`
    vs 守卫关 `4510 / 4605 / 5140 ms` —— **无差别**。
    ② **不是进程启动 / 导入 / 收集**：同一窗口内 `python -c pass` = 166–281 ms、
    `import rpa_core.gui.app` = 1004 ms、`pytest --collect-only` = 792–836 ms，**跑完 209s 全量套件后
    依然如此**。
    ③ **不是 Qt 初始化**：`QApplication([])` + import PySide6 = **97 ms**，20 个 QLabel 建+show = **1 ms**。
    同时**不是纯 CPU**（20×(5×10^5) 求和 = 116–223 ms）⇒ 五层基线全稳，慢的是**测试执行期**的某个
    等待路径，不是「机器整体变慢」。
  - **同一份代码、同一条命令的实测分布（S5 轮）**：32 项捕获判据 `3.40s / 4.45s / 4.92s / 5.37s /
    6.73s / 159.6s`；全量套件 `177s / 209s / 292s / 314s / 330s`。**159.6s 与 209s 这两笔否掉了
    S4 轮那条「跑完全量套件就会进红窗」的规则**——这次跑完 209s 的全量后，捕获判据仍是 4.4s（未进红窗）。
    ⇒ 准确表述只能是「**存在会开合的宿主级窗口，且不由仓库代码决定**」，**任何单次测量都不足以归因**。
  - **下一步方向（不在本轮范围）**：用**守候探针**
    `.harness/spike/probe_slow_window_sentinel.sh` 抓窗口——循环探测到金丝雀（这两条）变红，
    同一轮里把五层耗时（CPU / 进程启动 / 收集 / Qt 初始化 / 测试执行）一并记下，判定慢落在哪一层。
    （2026-09-28 首轮守候 25 轮全绿、未抓到窗口 ⇒ 该瞬态**出现频率不高**，需要更长守候或
    在门禁进程内直接埋点。）
  - 候选做法：把「等子进程起来」与「计时」分开（先等起来再开始计时）；或按机器负载自适应超时。
    **但不许用「放宽断言」消红**——那会把真实缺陷一起放掉（M41 §5.2 的既定口径）。
  - 证据：M41 任务单 §5.2（首轮）· §5.3（S4 轮订正）· §5.4（S5 轮三条否证 + A/B 对照）。

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
- [x] **元素「校验」按钮语义错位：GUI 侧只做结构校验，文案却像活体验证**（`done`，2026-09-23
  M39 ① 取处置②「对齐 Web 侧」；元素捕获链路调研发现）
  - 现状：GUI `app.py:_verify_element` → `model/capture.py:selector_errors()`，只判「browser 有
    非空 css / desktop 的 locator 是合法 JSON」，**完全不查页面**；状态栏文案「元素 X 校验通过」，
    按钮 tooltip 无限定语。
  - 对照：**Web 侧同一能力已澄清**——按钮字符「验」+ `title="结构校验"`，devserver
    `verify_element` 返回 `note`:「结构校验；活体验证（命中数）需在捕获会话内完成」。
    → 两形态口径不一致，且 GUI 是弱的那一侧。
  - 为什么算缺陷：影刀的「校验元素」是**活体**的（点击后页面高亮闪烁、报「没有找到任何元素」/
    「已经找到元素」）。从影刀迁来的用户点「校验」看到「通过」，会以为元素在当前页面定位得到，
    实际只证明 JSON 合法——**「以为校验了、其实没校验」正是本项目最在意的静默错误**。
  - **实装（M39 ①）**：按钮文案 `校验` → `结构校验` + tooltip 写明「不连接页面，活体验证在
    捕获时完成」；状态栏消息改成「结构校验通过（仅校验结构与 selector，未验证页面命中）」。
    判据钉在**限定语**上（不只是「包含校验通过」），负向验证 2 处注入各自精确变红。
  - **处置①（做活体）仍未做**——能力已存在（内容脚本动作路径里就有命中数 + 预检），缺的是
    通道：`_capture_element` 在对话框弹出前就 `session.close()`。见 `M39-element-editor.md` §5.1。
- [x] **ElementDialog 不展示已捕获的 candidates 与语义特征（有数据、无界面）**（`done`，2026-09-23
  M39 ②；元素捕获链路调研发现）
  - 现状：`content.js:buildDescriptor` 捕获了 `selector.candidates[]`（含每条 `matchedCount`）与
    `metadata` 的 `role`/`accessibleName`/`placeholder`/`label`/`containerText`/`url`/`title`；
    而 `gui/element_panel.py:_metadata_text` 只展示 tag/id/classes/text/rect（desktop 另加
    controlType/automationId/windowTitle），Web 侧同源（`static/app.js` 的 `metaEl`）。
  - 已确认是**有意**保留不展示（`element_panel.py` 与 `static/app.js` 两处注释都写「selector 里
    还有界面上不展示的键（捕获时收集的 candidates 备选定位），重建会让用户编辑一次就把它们静默
    抹掉」）——即**保住数据**解决了、**暴露数据**没做。
  - 代价：用户不知道自愈能力存在、无法查看/干预候选顺序；`accessibleName`/`label`/`containerText`
    本是「命中多个时人工消歧」最有用的信息，收集了却看不到。
  - **实装（M39 ②）**：确认框加只读候选区块（kind + selector + 实测命中数，`>1` 标「不唯一」）
    与语义特征/页面指纹行；`matchedCount` 缺席如实写「命中未实测」。**同时实测补上一个漏展示**：
    桌面 metadata 的 `className` / `name`（`className` 是 win32 侧定位无窗口文本控件的唯一手段、
    也是 `classNameRe` 的输入）。刻意**不展示** `windowHandle`/`point`（运行期值，摆出来会诱导
    用户粘进 locator）。展示是只读的，写回仍是「以原 selector 为基底覆盖一个键」，candidates 原样保留。
  - **两端同步（M39 收尾，2026-09-23）**：Web 侧 `static/app.js` 的同源展示也补上了——只读区块
    改走新增的纯函数区（`element-display-helpers` 锚点），两端 **17 个字段标签逐字相同**（契约
    测试钉死），渲染行为由 node 门禁 `scripts/check_element_display_helpers.mjs` 按真实捕获载荷
    验。不再存在「同一个元素两端显示不同事实」。
  - **后续（M39 ③）**：候选从「只能看」变成「可选中提升为主定位」（GUI 已做；Web 侧见下一条）。
- [ ] **元素编辑器的 Web 侧未同步（③ 只在 GUI 落地）**（`planned`，2026-09-23 M39 收尾）
  - 现状：GUI 有离线元素编辑器（`gui/element_editor.py`：候选**提升**为主定位 + 桌面 locator
    **字段勾选** + **就地**结构校验）；Web 侧 `openElementDialog` 仍是「selector 单行输入 +
    桌面 locator 手写 JSON」，与捕获确认框共用一套简化界面。
  - **卡点只有一个：草稿校验通道**。GUI 的就地校验直接调 pydantic 模型（`DesktopLocator`），
    浏览器侧够不着。要在 Web 上做同一个「改 → 校验 → 再改」回路，只有两条路：① 加一个
    **不落盘**的草稿校验端点（如 `POST /api/workflows/{flow}/elements/validate`，复用
    `selector_errors` / `_validate_element_document`）；② 在 JS 里再抄一份规则——**② 直接违背
    本项目既有的「判据权威唯一、两套规则必然漂移」结论，不建议**。即「要不要新增这层 HTTP
    接口面」需要维护者拍板，**未擅自决定**。
  - 其余部分落地成本不高：候选提升、按 backend 分的字段集（uia: controlType/automationId/name；
    win32: title/className/classNameRe/controlId）都是纯客户端逻辑，且字段集与执行器消费面的
    一致性可以照 GUI 那条**自维护判据**（AST 扫 `locator.<字段>` ↔ 界面字段表）同法钉死。
  - 参考：`M39-element-editor.md` §6 / §7。
- [ ] **`menuPath` 作为 locator 身份字段：声明与实现不一致**（`planned`，2026-09-23 M39 ③ 实测发现）
  - 事实：`grep -o 'locator\.[a-z_]*'` 逐个核过——**两个执行器都不读 locator 里的 `menuPath`**
    （`desktop.py::_find` 读 automation_id/control_type/name；`desktop_win32.py::_find` 读
    title/class_name/class_name_re/control_id/found_index）。`menuPath` 只作为
    `desktop_win32.menuSelect` 的**命令输入**被读。
  - 为什么算缺陷：`DesktopLocator.require_identity` 把 `menu_path` 算作 **win32 的合法身份字段**
    → 可以写出一个「模型校验通过、执行器永远找不到」的 locator，且没有任何地方报错。
    与 M38 查处的那批「声明与实现不一致」同类。
  - 处置（未定，二选一）：① 让 win32 `_find` 真的消费它（菜单项按路径查找，语义要额外定）；
    ② 把它从模型的身份字段清单里去掉（动作小，但要确认没有存量数据依赖它）。
    ③ 的编辑器已按「不提供没人消费的字段」处理：字段表里没有 `menuPath`，且 locator 里出现它
    会在界面**如实提示**「当前 backend 不消费，保存会移除」（不静默改用户文件）。
- [ ] **元素编辑器 ③-2：活体校验 / DOM 节点树**（`planned`，**待维护者拍板**——2026-09-23 M39 ③）
  - 现状：③-1 的离线编辑器已落地（候选可提升为主定位 / 桌面 locator 字段勾选 / 就地结构校验，
    见 `M39-element-editor.md` §2.3）。**活体校验（打一次真实命中数）仍未做**。
  - 关键事实：**能力已经存在**——内容脚本动作路径里本来就有 `document.querySelectorAll(selector).length`
    与遮挡 / 可见性预检；缺的是**通道**：`app.py::_capture_element` 在 `work()` 的 finally 里
    `session.close()`（disarm + 关全部 bridge 通道），而对话框在那之后才弹出。
  - 三个选项与建议见 `M39-element-editor.md` §5.1（建议 A：把会话生命周期延到对话框关闭后，
    范围限在「捕获确认框内」，先只做「改选择器 → 打一次命中数」）。
  - 连带项（**2026-09-28 更正**）：**Web 侧 DOM 节点树不属此列**——它既不需活通道，也不需新增
    `page.outline` 类 op：`content.js::cssSelectorFor`（L127）**已经在生成**带层级的 CSS 路径
    （逐级 tag + 首类名 + `:nth-of-type`，`join(" > ")`，上限 6 级），节点树就是把它拆开显示、
    逐级勾选后拼回去（见 `M39-element-editor.md` §9.2-1）。**真正需要活通道的只有「预览高亮」与
    「活体校验」两项**。**桌面侧**节点树是另一回事：`_dfs_smallest_at` / `_drill_to_leaf` 已走完
    祖先链但 uia/win32 的 locator 是扁平字段、不支持层级 ⇒ 要动模型 + 执行器（§9.3-7）。
    相似元素成组与 AI 修复元素都是**新命令能力**，另立里程碑。

- [ ] **捕获确认框：字段勾选前置 + 「重新捕获」按钮 + 相对定位/锚点**（`planned`，2026-09-28
  M39 §8 保存界面对标）
  - 现状：确认框对桌面元素给的是**一行 locator JSON**（实测
    `{"backend": "win32", "title": "记事本", "controlId": 3}`），字段勾选这件能力我们有、但放在
    **保存之后**的 `element_editor.py` 里；确认框只有 保存/放弃，没有影刀那样的「重新捕获元素」
    「修复元素」入口（全仓 `grep 重新捕获` 无实现）。
  - 改法（UI 侧，无需新通道）：把 `LOCATOR_FIELDS_BY_BACKEND` + `locator_problems` **复用**进
    `ElementDialog`，让确认框就能勾字段、就地校验；加一个「重新捕获」回调（复用
    `_capture_element` 入口）。相对定位 / 锚点是**模型层新字段**，要单独定 schema，别混在这一片。
  - 证据：`M39-element-editor.md` §8.1（实测渲染）/ §8.4-①。

- [ ] **元素编辑器对齐影刀：P1–P5 分期总纲**（P1 已落地 M44；P2–P5 `planned`，2026-09-28 M39 §9 逐控件复核）
  - **追赶方案 v2（2026-09-28，P1 落地后重排）**：`docs/yingdao-gap-catchup.md`——差距台账按
    交互/语义/通道/契约/系统五层重排 + 里程碑序 M45–M49（先地基后功能：B1 值拷贝决策最先推，
    C1 会话保活是四项共用地基，D1+D2+D3 合并一次契约改动）。外部核查补四条官方事实
    （锚点自动推荐规则、修复元素=AI+引用自动更新、AI 辅助定位是运行期兜底、相似元素=成组+循环指令闭环），
    新增矩阵外缺口「父子元素捕获」（并入 D3）。
  - **P1 done（M44-capture-edit-in-place）**：捕获即编辑（确认框内嵌 `ElementEditorForm`）＋
    `保存并继续` / `重新捕获`（`intent()` 三出口）＋ Web 节点树（勾层级拼回 css，离线版）＋
    网页侧 Ctrl 光标变蓝。判据与负向验证见该任务单；P2 以下仍按分期推进。
  - 触发：维护者给出影刀**真实截图**（桌面 / Web 各一张）并问「以上我们能做吗」。§9.3 是
    15 项的逐条判定矩阵；§9.6 是分期建议；**本轮只调研 + 登记，未改 src**。
  - **结构判断（§9.4）**：差距有一半在**流程位置**——影刀是「捕获即编辑」（控件与捕获同一窗口），
    我方是「捕获确认框 → 保存 → 元素库点编辑」两步 ⇒ P1 先修流程位置，否则逐个补控件的收益被流程打折。
  - **P1**（不动执行器契约）：捕获即编辑 ＋ `保存并继续` ＋ `重新捕获` ＋ Web 节点树与属性表
    （「包含」编译成 `[class*="…"]`，执行器照旧 `querySelector`）＋ **网页侧 Ctrl 光标变蓝**。
  - **P2**（动执行器契约）：桌面节点树层级 locator ＋ 控件级匹配方式。
  - **P3**（生命周期改造，多项共用地基）：会话保活 → 活体校验 / 预览 / 相似元素。
  - **P4**（系统级副作用）：**桌面** Ctrl 光标变蓝（`SetSystemCursor` 换 `OCR_NORMAL`，四条恢复
    路径都要还原，否则光标永久变蓝；Windows-only）。有利条件：桌面 agent **已在轮询
    `GetAsyncKeyState(VK_CONTROL)`**（`desktop_agent.py` L21/L608），触发条件零成本。
  - **P5**（立项）：锚点 / AI 智能定位 / XPath 切换。
  - **一处现状更正（§9.2-2）**：`matchMode`（exact/contains/regex）**不是新能力**，执行器里已有
    （`desktop.py` L142/L233、`desktop_win32.py` L124/L242），i18n 也有标签（L156）；
    但它**只作用于窗口 title**、且只作 `attachWindow` 的**命令参数**暴露，**不是 element locator
    字段**（`desktop.py` L773 写死了这条口径）⇒ 影刀属性表那种「控件级匹配方式」仍需扩执行器。

- [ ] **元素资产是「值拷贝 + 按 css 反查」，改主 css 会让已插入指令静默变差**（`planned`，
  **待产品决策**——2026-09-28 M39 §8 实测）
  - 实测（`M39-element-editor.md` §8.4-②）：节点里存的是值（`app.py::_insert_element` 的
    `holder.args[key] = value`，不含元素名）；资产只被 `browser.py::_element_candidates` 按
    **归一化 css 字符串**建索引反查。于是在元素库里把主定位从 `#q` 改成 `#q2` 之后，节点里仍是
    `#q` 的指令：**主定位不变**（用户以为会跟着变）**且自愈候选变成 0 条**（用户看不到）。
  - 与影刀相反（影刀指令引用元素名，「修复完成后引用处自动更新」）。界面上**没有任何提示**，
    迁来的用户必然误解——正属本项目最忌讳的一类静默错误。
  - 三选一（本轮未擅自定）：① UI 明说「元素库是捕获档案，改它不更新已插入的指令」；
    ② 索引键换成元素名（则节点必须存引用，动流程 schema）；③ 改主 css 时同步更新引用该 css 的节点。

- [ ] **元素库列表侧：搜索 / 多选 / 批量结构校验 + 默认名两端不一致**（`planned`，2026-09-28
  M39 §8 对标）
  - 影刀元素库支持搜索、分组、改名、验证、**批量验证**（Ctrl·Shift 多选，页面改版后一次筛出坏
    元素）；我们只有 列表 + 6 按钮（刷新/捕获/编辑/结构校验/插入参数/删除）。
  - 默认名不一致：GUI `el_{tag}`（`app.py`）/ Web `{tag}_{Date.now()%100000}`（`app.js`），
    desktop 在 Web 侧退化成 `element_*`。属 §7 那类跨端一致性缺陷（同一元素两处不同）。
  - 证据：`M39-element-editor.md` §8.3 第 7/8 行。

- [ ] **桌面腿没有元素自愈，也没有「修复元素」入口**（`planned`，2026-09-28 M39 §8.3 第 11 行）
  - `flow_dir` / `_element_candidates` 只在 `executors/browser.py`；`desktop.py` /
    `desktop_win32.py` 全无资产反查 ⇒ 桌面元素改版后**只能重新捕获**，与影刀「网页/桌面对称修复」
    不对等。
  - 要做的话先定：桌面 locator 的候选从哪来（capture agent 现在不回传备选），以及重试的代价
    （桌面上「换一个控件」比网页更危险）。

- [ ] 编辑器多 tab 属性表单（`planned`——单命令 schema 字段显著增多（>~8）时按「常规/参数/…」划分；M19 切 E 留接口）
- [ ] UI、DSH、MCP、调度器和安装器集成（`planned`）
  - 逐项放行与否以 ADR 0006 结论为准；操控型 HTTP 推迟不变。

> 已删除条目：原「桌面客户端编辑器薄壳（ADR 0010 重估条件）」——ADR 0016 已定 GUI 为唯一
> 主力形态，该条目（「确认非开发者用户为主力后再立项薄壳」）不再适用。

## 已完成

- [x] **M49 GUI 体验 P0–P3**（`done`，2026-09-30）——颜色 token 唯一来源 `gui/theme.py`（AST 扫
  字符串常量 + must-have 名单）· 状态栏徽标图标化（点+文字，同状态不重写 setText）· 尺寸持久化
  `gui/persist.py`（IniFormat/UserScope，坏值回落出厂）· 视图菜单统一五个懒创建 Dock + 恢复默认
  布局 · 知会型弹框降级 `hint.setText`（白名单门禁比正文不比标题）· 往返导航只 close 一次 +
  `gui/motion.py` 过渡动画（`RPA_GUI_ANIMATIONS=0` 可关、失败安全）· 字号像素口径唯一来源
  `gui/fonts.py` + Ctrl+P 命令面板 `gui/palette.py`。计划：`M49-gui-experience.md`。
  23 个负向验证注入全命中并逐字节还原核 md5；pytest 1356 passed / 2 failed（QProcess 沙箱挂账）/
  21 skipped / 2 xfailed；ruff 全绿；静态五组 + 13 个 node 切片全过。提交 `f681f8f`（本地未 push）。

- [x] **M48 桌面契约一片（D1+D2+D3）**（`planned`，**待开工**，2026-10-08 编号定案）——D1 桌面节点树 ·
  D2 控件级 matchMode 扩到 `automationId`/`name` · D3 锚点起步。差距方案 §4 排定；**破戒授权已于
  2026-09-29 获得**（locator 可加 path 等字段）。任务单：`M48-desktop-contract.md`（本轮新建）。

- [x] **M47.8 双浏览器「都闪框」修复（非前台不应答）**（`done`，2026-10-08）——计划：
  `M47.8-verify-dual-browser.md`。真机报障「Chrome 与 Edge 同开同样页面，校验时两个页面
  都闪黄框，但命中数只算 1 个」。根因**本机实测**：host 侧 `ElementVerifier._exchange` 把
  `capture_verify` 广播给**全部在线端点**（端点=一个浏览器实例，本机实测 chrome + msedge 两个），
  故两个浏览器都闪；命中数只算 1 是 `read_loop` 只认首个回传。修法＝扩展自判「是否 OS 级前台」
  （`windows.getLastFocused().focused`），**非前台不闪框**（`silent`）**且延后 120ms 应答**，
  把首个应答让给前台那个。fail-open + 不假超时两条线钉住。EXT_BUILD 0.6.3（**两个浏览器都要重载**）。

- [x] **M47.7 校验取页口径（真前台窗口优先）**（`done`，2026-10-08）——计划：
  `M47.7-verify-target-tab.md`。真机报障「校验元素命中数 0/1 抖动」；trace 实锤**计数抖动
  其实是「打到了哪一页」在抖**（count 0 全来自 explore 页、count 1 全来自 search_result 页，
  交替），根因是 `runVerify` 用 `tabs.query({active:true,lastFocusedWindow:true})` 取页——
  `lastFocusedWindow` 是**窗口**级记忆，GUI 抢焦点/多窗口下会失准。修法＝新纯函数
  `pickVerifyTab()`（真前台窗口 → 最近聚焦兜底 → 任一活跃页）。EXT_BUILD 0.6.2（**须重载扩展**）。

- [x] **M47.2–M47.6 校验元素补丁轮次**（`done`，2026-09-29）——合并记录在任务单
  `M47-element-verify-live.md`：M47.2 verify-timeout 根因（bridge 透传白名单漏
  `capture_verify`，跨端对账判据断根）· M47.3 真机三连修（吞钩子结构加固 / 预览黄框跟随滚动 /
  编辑区空间放开）· M47.4 Ctrl+Click 吞钩子 `SetEvent` 落错 DLL（kernel32 非 user32，AST 归属判据）
  · M47.5 离线提示按「缺哪一环」分支 · M47.5b 良性离线不弹框 · M47.6 中途开浏览器补 arm 守望。

- [x] **M47 / M47.1 校验元素与预览**（`done`，2026-09-29）——计划：`M47-element-verify-live.md` /
  `M47.1-live-preview.md`。改道**按需短连接校验通道**（放弃会话保活），`capture_verify` 信封 +
  `mode`（flash/preview/clear）；feature：`element-verify-live` / `live-preview`。

- [x] **M46 元素引用模型（B1 定稿 + S1 落地）**（`done`，2026-09-29）——计划：
  `M46-element-reference-model.md`，决策 ADR-001。节点双写 `elementRefs` + `with`；运行期从
  `flow_dir/elements` 解析；缺元素回落快照 + `elementRefFallback` 事件；feature：
  `element-reference-model`。

- [x] **M45 编辑界面交互收尾（L1：A1–A4）**（`done`，2026-09-30）——计划：
  `M45-editor-ui-l1.md`。A1 Web 属性表 · A2 R1/R2 残留修复 · A3 默认名三端统一（共享用例表）·
  A4 元素库搜索；feature：`editor-ui-l1`。

- [x] **M44 捕获即编辑 P1**（`done`，2026-09-28）——计划：`M44-capture-edit-in-place.md`。
  确认框内嵌 `ElementEditorForm` + 三出口（保存/保存并继续/重新捕获）+ Web 节点树 +
  网页 Ctrl 光标变蓝；feature：`capture-edit-in-place`。

- [x] **捕获排障与扩展对账**（`done`，2026-09-29–09-30，无独立里程碑号）——冷启动红框延迟归因 ·
  Ctrl+Click 穿透拦截（WH_MOUSE_LL + 专职子进程 `desktop_click_hook.py`）· 钩子卡顿二次架构级修复
  （阻塞 `GetMessageW` 泵 + 命中测试降频）· 扩展过期对账（`EXT_BUILD` 三方一致 + host 落
  `arm_stale`；根因是 Load unpacked「载入即快照」）。

- [x] **M43 捕获 Esc 会话级取消**（`done`，2026-09-28）——feature：`capture-esc-cancel`。

- [x] **M42 扩展与已开页捕获态同步（补注入 + 接管）**（`done`，2026-09-28）——feature：
  `capture-extension-lifecycle`。

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

