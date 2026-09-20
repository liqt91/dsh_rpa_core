# 工作日志

## 2026-09-20

- **扩展通道诊断可见性（extension-diagnostics-visibility）**：维护者需求——**不打开浏览器就要能看出 bridge 注册没注册、插件装没装**，而不是只显示一句「离线」。能力层新增只读 `extension_installer.channel_diagnostics()`（bridge 注册 × 插件安装/加载 × 浏览器运行 × 当前实例是否加载）→ 稳定 `reason` + 一行中文摘要 + `offline_hint()` 处置建议；GUI 状态栏徽标、插件对话框共用同一诊断；静态体检（bridge + 读 profile）带 30s TTL，5s 轮询不反复读 Secure Preferences。**全程不启动浏览器**。
  - **当场修掉一个误报（真机踩到）**：只按进程 argv `--load-extension` 判「当前实例是否加载扩展」，会把**开发者模式加载**（profile `location==4`、argv 无任何参数）误报成「浏览器在跑却没加载插件」——维护者重载扩展后记录恰好由 `location=8` 变 `location=4`，代码随即误报。改为两条腿 `_extension_loaded`（开发者模式记录 OR 命令行注入）；Windows 判不了返回 `None` → 显示「加载状态未知」，不误报。测试 +18 项（`test_extension_installer.py` +12、新增 `test_gui_ext_badge.py` 6）。FULL GATE PASSED。
- **「插件通道：离线」真因定位（同一晚的上游问题）**：不是 host 坏了，是**当前 Edge 实例压根没加载扩展**——`pgrep -f load-extension` = **0**、`lsof | grep -c dsh_rpa_core/extension` = **0**（对照组 31 证 grep 有效）、65 秒观测 host **零次**出现、端点目录全程为空；profile 记录 `location=8`（命令行注入，从未进 profile）只是历史痕迹。进一步实测：`extension_launch.launch_browser()` **自愈也无效**——浏览器单实例会把新命令行的 `--load-extension` **转交吞掉**（主进程 pid 未变、argv 仍无参数），而 `extension_launch.py` 注释里「自启时不存在既有实例抢参数」的前提在「浏览器在跑但没注入扩展」时**不成立**，属设计缺口。判据与恢复手法已沉淀进 user-level skill `native-messaging-macos`。
- **M28 元素自愈收官三段（S1/S2/S3）**：**S1** 运行期消费 `selector.candidates`（主选择器失效时按稳定性顺序回退并把归因写进证据）；**S2** 执行前预检 + 错误分类——扩展侧先 `scrollIntoView` 再预检（`isConnected` / `disabled` / `aria-disabled` / `inert` / `checkVisibility` / rect 在视口内 / `elementFromPoint` 遮挡），失败回传结构化 `precheck` 翻成 `ELEMENT_COVERED` / `ELEMENT_DISABLED` / `ELEMENT_NOT_VISIBLE`（details 带 `blockedBy`），并定案交互：**主选择器命中但预检不过时不试候选**（换候选 = 换一个元素去点，比失败更危险），杜绝「报成功但点错地方」；**S3** 修两处「manifest 声明了但扩展通道没实现」的漂移——`keyIntervalMs` 真正逐字间隔、`clipboard` 实装（粘贴注入 + 未被接受时显式失败），补 `clickBeforeInput` / `postDelayMs`。仅剩 S4（扩展通道往返数/耗时基线 + MVP 边界文档）。
- **宿主形态改两段式 + 工作台（M27，ADR 0017）**：影刀式首页 + 运行历史/流程库分界面。S1 骨架 → S2 运行历史迁入（全局视图 + 按流程筛选 + 双击打开编辑器看时间线）→ S3 流程管理动作（复制/重命名/删除连带/导入导出）→ S4 工作台运行入口与状态联动 + **影刀式交互细节**（点运行后首页收起 + 右下角弹出运行浮窗）。
- **M24 调试器 + M25 运行历史**：M24 S1 断点契约（`RunControl` 打包「暂停开关 + 断点集合 + 已消费断点 + 单步预算」）、S2–S4 GUI 断点交互（画布编号栏扩出最左断点列，46→58）+ 单步 + 文档；M25 S1 顶层零依赖只读读取器 `run_history.py`、S2+S3 GUI 运行历史 dock（5 列：时间/流程/状态/耗时/错误）+ 文档。
- **「打开网页」冷启动行为定案 B+（维护者拍板）**：裸拉起浏览器 + 冷启动复用空白启动页，取代前一日 A 案（带 URL 拉起 + 探测绑定）；并**不再关闭空白启动页**——`create+close` 的关页动作是「指令以外的操作」且发生在用户眼前，观感比留个空白页更差。连带修三处：`omnibox` 冷启动后 URL 全选高亮、`launch_blank` 复用导致的「打开网页有时候打开两个」、`_commit_pending_edits` 引起的「切换 browserType 保存不及时、有时要保存两次」。
- **GUI 一批闪现/卡顿的根因修复**（均为真机报障，靠插桩/A-B/诊断日志定位，非读码猜测）：fx 指令小框闪现（真因 `_wrap_fx_row` 里的 `QToolButton`，重启后仍复现 → 诊断日志实锤）、参数面板旧控件只 `deleteLater()` 未即时隐藏、首次选中复杂表单 `QScrollArea.setWidget` 布局延迟（cProfile 定位）、无法从左侧指令树拖放指令到画布（三处缺陷叠加）、新建流程空画布无法拖放（`indexAt` 命中区域）、「加循环→删除→点其他指令卡死闪退」加固。
- **指令测试策略定案**（维护者评审）：**定位修正（关键）——按需启用的独立测试，不进默认 harness**（L1 参数矩阵规模会把门禁拖垮）；口径是「测全参数」而非「跑通一次」；用例表 JSON + 覆盖率机器校验；实现顺序刻意 **S2 先行**，让用例表一次纳全新增错误码。
- **其他**：补「打印日志」指令 + 运行日志显示节点输出值（此前 test 流程不知道最后一个节点抓到的数据对不对）；删除 `frontend/` Vue 迁移中间态（32 个已跟踪文件）+ `.trae` 入 gitignore；状态收口（M22 Linux 真机验证**只记录不测试**、BACKLOG 与任务单同步）。

## 2026-09-19

- **M22 macOS 传输层真机验收（Native Messaging S1–S4 完成）**：M20 三个切片的设计在 macOS 侧全部落地并真机验收通过（Windows 侧此前已验）。沉淀 macOS 路径表（写错会恒报离线）与「别拿 Windows 数据乱比」的口径说明。
- **M21 GUI 运行控制进阶 done**：跨进程暂停 + 浏览器会话续接 + 恢复人工确认。动工前先核对「计划的地基假设」与 ADR 是否一致；ADR 0013（统一扩展单通道）连带修正 ADR 0004 的半句过时描述；厘清「三方共用的模块不能放 `runtime/`」。
- **macOS 窗口层级两处平台缺陷**：① 运行时浮窗随主窗口消失——`Qt.Tool` 在 macOS 上 = `NSPanel`，应用失活即被系统隐藏；② 确认框要点 Dock 图标——后台应用的自激活请求被 macOS 忽略。取证手法可复用：零依赖 `ctypes` 调 `objc_msgSend` 向 AppKit 要真相。
- **捕获格式升级落地 + Mac「红框在但 Ctrl+Click 捕获不到元素」**：升级前先做向后兼容性核查（老数据仍可用）；并厘清「序号 ≠ 可持久化标识」——序号只用于展示，持久化另用稳定 id。
- **M10 元素候选落地**：候选采集 + 稳定性排序 + 元素资产落盘（为 M28 S1 的自愈消费铺契约）。
- **调研（未改码）**：`jev-desktop` 不存在（用 GitHub API 逐项核实）；`browser-use/jev-ultrafast` 是真正值得研究的对象；TypeSafe「Jev」对项目的增益评估。

## 2026-09-18

- **macOS「扩展永远离线」根因 + 修复（M22 S4 部分）**：真机首跑暴露——POSIX 端点路径超过 `sun_path` 上限（104 字节），导致 bind 恒失败；同一轮还发现 macOS 上扩展桥契约测试整体是红的（14/16 failed）。修复方案定稿前先补实测（含 socket 上已有 status 握手、哈希后不必再走 sidecar），并明确「生效对象」边界（用户要不要额外操作）。
- **M23 GUI/UX 对齐（G1 剩余 + G2 + G3 slice A）**：捕获后确认对话框 `ElementDialog`（名称可改、selector 可改）；画布多选 + 批量移动/删除 + 右键菜单 + Ctrl+F 查找；`x-param-groups` 分组折叠。G2 期间连着修五处真机缺陷（拖放后点击不收敛、第二次拖放多选顺序颠倒、多浏览器并存只有一个能框选、PySide6 `QStandardItem` 所有权陷阱等）。
- **门禁默认不弹窗**：`check_all.py` 的真实桌面 E2E 改为 `RPA_DESKTOP_E2E=1` 显式开启（默认跑会弹窗抢前台）。

## 2026-09-17

- **M20 Native Messaging 全链路 S0–S8 done**：S0 真机硬门槛（Edge 152 + Chrome 152）→ S1 ADR 0015 + 任务单 → S2 host 子进程 `workers/ext_bridge.py`（由浏览器按需拉起，stdio）→ S3 安装器 native host 注册 → S4 扩展改造（manifest 0.2.0 → **0.3.0** + `nativeMessaging`，Edge 152 真机验收通过）→ S5+S6+S7 执行器/捕获/接线三者一并迁移（耦合，拆开会出现不可用中间态）→ S8 文档 `docs/extension-execution-plan.md` 与收口。
- **产品技术路线定案：GUI 为唯一主力形态（ADR 0016）**：GUI（`rpa-core gui`）新增能力优先且默认落 GUI；Web 编辑器（`devserver`）退为可选形态、`devserver/static/` 冻结演进不删除；GUI 独立运行不需要任何 web 服务器。
- **GUI/Web 功能对齐九切片 + 运行浮窗**：先出差异矩阵（Web 9 功能域 vs GUI）再切分补齐，含控制流参数编辑；运行时悬浮窗对标影刀（开右下小窗、隐藏主窗、实时显示当前步骤）。
- **单入口混合捕获 G1（M23，影刀式「一个按钮，指哪捕哪」）**：GUI 元素面板统一入口，捕获桌面元素与网页元素同一按钮；并给 GUI 插件对话框补齐 **bridge host 注册**入口，修掉「引导装完扩展通道仍离线」的缺口。
- **其他**：pytest 不再抢前台窗口焦点；「编辑未提交就切换」改为自动提交（`test` 流程的 `browserType` 改动不再丢）。

## 2026-09-16

- **修复 WorkBuddy 连接器「连接失败：Python 3.13.14 does not satisfy runtime requirement 3.12」**——根因是 `workbuddy-connector/cli.json` 把 `runtime.version` 写成了**裸版本号** `"3.12"`。裸版本在 specifier 语义下等价于**精确要求**（`==3.12`，`packaging` 甚至直接判 `InvalidSpecifier`），而宿主托管的 Python 只有一个「当前版本」3.13.x（本机 `binaries/python/versions/current` = 3.13.12，Windows 上 default venv = 3.13.14），于是必然不满足——宿主**不会**为单个连接器另装一个 3.12。
  - **证据三条**（均为实测，非推测）：① 宿主版本池只有 3.13.x；② 连接器市场缓存里 41 个 CLI 连接器**一律用范围语法**（40 个 `>=x.y`），唯一的 Python 样本 `emr-query` 写 `">=3.11"`；③ 本包发布 wheel `rpa_core_runtime-0.1.0-py3-none-any.whl` 的 `requires_python = ">=3.12"`。`packaging` 实测：`==3.12` 对 3.13.14 → False，`>=3.12` → True。
  - **修复**：`version` 改 `">=3.12"`（与 wheel metadata 同口径）；`connector-meta.json` 版本 0.1.0 → 0.1.1（配置修复按市场约定递增）；契约 **+2 防回归**——`runtime.version` 必须是范围语法（拒绝裸版本号），且必须与 `pyproject.toml` 的 `requires-python` **逐字一致**（宿主按 cli.json 备解释器、pip 按 wheel metadata 决定能否安装，口径不一致就会出现「能装但不给装」）。
  - **顺带**：`workbuddy-connector.zip` 重新打包——原包是 09-04 快照，仍带 `.cmd` 入口、`win32` 的 init 缺 `install-extension`，**滞后于源文件两个修复**（若此前拿该 zip 提交/安装，那两个修复均未生效）。
  - 排查方法留档：先看**官方市场缓存里同类连接器怎么写**（`~/.workbuddy/connectors-marketplace/connectors/<source>/cli.json`）比逆向宿主可靠得多——宿主 `app.asar`（298MB）用 ripgrep 搜不出任何文案（连 `minWorkbuddyVersion` 这种必然存在的字段名都搜不到）。
- **同步远端 26 提交并合并本地 WIP**（扩展版本可见性）：`extension/background.js` 上报插件自身版本 `extVersion`（心跳即携带，不多一次往返），hub 侧 `ExtensionExecHub` 记录并暴露；**hosts 虚增修复**——MV3 首启 alarm 可能早于 instanceId 落位触发心跳，先按浏览器名归档、随后按 instanceId 再归档 → `_hosts` 双记录并存致在线数虚报，现 `instances()` 对「同浏览器已有带 instanceId 记录」的浏览器名占位记录做丢弃。与远端新增的宿主焦点上报（`foc`/`focat`）冲突手工合并并存；前端「⇲ 插件」面板合并安装态 + 在线态 + 插件版本新旧（取该浏览器在线实例中最旧版本与 devserver 基准比对）三维度徽标。
- **GUI 修复：顶层节点删除崩溃**（维护者报障：点卡片尾删除按钮 AttributeError）——根因是 Qt 语义：**顶层 item 的 `parent()` 返回 `None`**（而非 invisibleRootItem），旧 guard「parent() is None and not item.row()」误护顶层首行、放行非首行后 `None.takeRow` 崩溃；改为 `parent or invisibleRootItem` 显式回退 + invisibleRootItem 身份比较。顺带修复远端带入的两处存量测试失败：`test_insert_targeting_leaf_becomes_sibling` 断言同样违背该 Qt 语义；`test_flags_enforce_drag_drop_rules` 依赖 `workflows/test` 草稿恰好有顶层 forEach（被手测改没后 StopIteration），改自带夹具。
- **指令树影刀式卡片化**：新增 `gui/command_palette.py`——`CommandCardDelegate`（叶子=圆角卡片：命名空间彩色图标块取显示名首字符 + 中文名粗体 + 小字命令 id；分组保持轻文本）+ `load_command_display_names`（正则解析 `i18n.js` commands 块，**单一事实来源**不建第二份映射，与 `test_editor_i18n` 门禁锁同口径）；组内排序按 manifest `x-palette-order` 影刀对标序。
- **删除按钮文字化**：手绘垃圾桶图标改为浅灰胶囊 + 红色「删除」文字；`delete_button_rect` 让 paint 与点击热区共用同一矩形（所见即所点），顺带修正 `QRect` 闭区间语义（`right()=left+width-1`）的 1px 偏差。
- **「否则」点删除无效修复**：else-branch 行 `virtual=True`，视图层 `mouseReleaseEvent` 用 `ROLE_IS_VIRTUAL` 一刀切拦截，而模型层 `remove_item` 本就对其例外放行——改为可删性判定**委托模型单源**（`remove_row` 返回 bool），视图不再重复更严的策略。
- **画布左侧编号栏对标影刀**：46px 固定栏（不随缩进移动）承载三件套——①**逻辑行号**（全树前序位置，序号跟随指令本身，收起 if/循环下方行号不变）；②**错误徽标**（红底白 `!`，action 必填参数缺失即标记，数据源自 manifest `input_schema.required`）；③**收起/展开按钮**（`collapse_button_rect` 与热区同源）。**引导线让位**：Qt 按缩进自绘的 branch 引导线会画进编号栏，`setIndentation(0)` 让 branch 区整体消失、缩进改由 delegate 自算。内置示例流程补齐必填参数（默认视图不应自带错误标记）。

## 2026-09-15

- **桌面 GUI 从零打通到编辑闭环（切片 2-5）**：
  - **切片2 流程卡片画布**：`flow_model.py` 把 Workflow AST 映射到 `QStandardItemModel`（虚拟组行承载 AST 无节点的分支，`iter_real_nodes` 导出时剔除）；自定义 MIME 只携节点 id，自实现 `mimeData`/`dropMimeData` 做同模型移动，拒绝移入自身后代防成环。`canvas.py` 的 `CardDelegate` 自绘复刻 Web 视觉：白底圆角卡片、选中 `#daedff`、4px 深度色线、拖柄/同级序号/粗体命令名/等宽参数摘要/命名空间徽标。
  - **切片3 参数表单**：`param_form.py` 按 manifest `input_schema` 生成原生控件（enum → QComboBox 且真值存 itemData、array/object → 单行 JSON、必填标 `*`）；新增 `ROLE_ARGS_RAW` + `ArgsHolder` 存 Python 对象引用，避开 QVariantMap 的键序重排与类型丢失。
  - **切片4 编辑闭环**：`model_to_workflow` 以节点 raw 为模板、只替换结构键（`with`/`children`/`then`/`else`/`catch`），GUI 不编辑的字段原样保留；保存前 `Workflow.model_validate` 校验，失败只进状态栏不写盘；`rowsInserted`/`rowsRemoved` 置脏 + 标题 `•` 前缀 + closeEvent 保存确认。顺带修正 SAMPLE 的 `itemVar` 错键（规范键为 `item_var`/`error_var`）。
  - **切片5 节点增删**：`allocate_node_id` 生成全模型唯一 id；`insert_command(command_id, target)` 按容器/叶子的落点规则插入；左树双击新增、Delete 删除（根节点与虚拟组受保护）。
  - 新增「新建 / 打开」（Ctrl+N / Ctrl+O）文件入口与 `--workflow` CLI 透传。
- **GUI 三处交互硬修的诊断链**（都靠插桩/A-B 对照定位，非读代码猜测）：① **拖拽全程放不下**——`dragEnterEvent` 先用「无效 parent」探测模型能力，旧实现对无效 parent 一律 False，拖拽从入口即被整体拒绝（模型级单测直接调 `dropMimeData`，故未暴露）；② **拖拽松手后卡片消失**——`dropMimeData` 逻辑零 bug，真因是 Qt `InternalMove` 下 `startDrag` 在 `QDrag.exec()` 返回后再 `clearOrRemove()` 删一遍源行，而模型已用 `takeRow + insertRow` 原子移动完毕，旧索引必然删错；重写 `FlowTreeView.startDrag` 跳过基类清理；③ **（Windows）拖不动**——`cli.py` 顶层 `from rpa_core.executors import ...` 触发 pywinauto 模块加载期 `CoInitialize(COINIT_MULTITHREADED)`，随后 `QApplication` 的 `OleInitialize(STA)` 被拒 → OLE 拖放整体失效（stderr 有 `OleInitialize() failed: COM error 0x80010106`）；改为延迟导入，并把 orchestrator 的 executor 引用降为 TYPE_CHECKING 守卫。
- **if 结构影刀式扁平化 + 结束标记行落点修正**：
  - **落点 bug**（维护者实测：灰色 `结束 循环` 下方竟还能插进一个缩进的「返回」）——end-bracket 是容器的最后一个 child，凡按 `rowCount()` 追加都落到结束行**下方**，画面上在容器外、AST 里仍在容器内，视觉与语义相悖。新增 `_real_child_insert_row`，插入/同模型移动/落点解析统一走它，补 3 条回归用例。
  - **顺带挖出 PySide6 所有权陷阱（SIGSEGV 级）**：`QStandardItem.insertRow(row, item)` **不转移所有权**（只有 C++ 签名为 `QList<QStandardItem*>` 的 list/tuple 形式才转移），调用方丢弃返回值会让 Python 引用归零 → C++ 对象被 GC 销毁 → 该行变 `None`，再往前走就是 `exit 139`。最小矩阵实测后全部改 `[new_item]` 并留注释。
  - **扁平化**：去掉「则执行 / 否则执行」两层虚拟分组，改为 if 直属 then 子节点 + 「否则」指令行 + 「结束 如果」；`_branch_insert_row` 以标记行为界切 then / else。
  - **「否则」按需化**（维护者口径：else 是单独指令、可拖放、有需要就加、默认不加）：`build_item` 只在 AST 有 else 段时才插该行，无 else 的 if 只剩 then + 「结束 如果」；它是**真指令**（可选中、可拖、可删，删掉即取消分支且其下节点自然并入 then 段、不丢节点）；无 AST id 却要参与拖拽定位，故用合成 id `@else:<if_id>`；拖动边界限定**同一个 if 内**（跨 if 迁移会静默改写两个 if 的分支归属，几乎不可能是用户意图）；左树新增固定「流程控制」组作为添加入口，`_apply_filter` 支持显示名 + 命令 id 双匹配。
- **两处遗留清理**：`gui/app.py` 的 `# noqa: N802（Qt 命名）` 因**中文括号被 ruff 当成 code 列表的一部分**而判 `Invalid noqa directive`（说明移到上一行、行尾只留 code）；`tests/contract/test_extension_installer.py` 的私钥夹具从**模块级**读取改为 `lru_cache` 惰性 + `PermissionError` 降级为 `pytest.skip`——模块级读取一旦撞上沙箱敏感内容审批不可用，会让**整个文件**在收集阶段 ERROR（表现为大批 "ERROR at setup"）。
- **门禁**：GUI 契约 69 例全绿（canvas/node_edit/save_roundtrip/param_form/smoke）；全量 pytest 0 failed 0 error；ruff / 架构（50 files、83 manifests）/ tasks（44 features）全过。

## 2026-09-14

- **每流程一张数据表格**（横空对齐影刀「数据表格」）：`data.table.*` 6 条命令落地（getCell/setCell/appendRow/deleteRow/clear/exportCsv），行数据**跨运行累积保留**、`data.table.clear` 显式清空；worker 原子读改写 + CSV(BOM) + 列 key 寻址；devserver 增 `TableStore` 与 `/api/workflows/<flow>/table` 四端点。
- **影刀对齐第二批**：`.harness/yingdao-gap-matrix.md` 差距矩阵后选定「文件/CSV/文本 + 流程延时」方向，落地 6 条纯 worker 指令（零外部依赖、单测全覆盖）：`data.readText` / `data.appendText` / `data.fileExists` / `data.deletePath` / `data.datetimeNow` / `workflow.sleep`。
- **`data.setVar` 补「变量类型」**（对标影刀「设置变量」的类型格式化）：enum `string/number/boolean/object/array`（默认 string，向后兼容），worker `_coerce_by_type` 按类型格式化取值；二轮按维护者意见去掉变量名下拉与 `auto`（写新名=定义、写已有名=覆盖）。
- **浏览器三条平台无关缺陷修复 + macOS 可用性**：① 恢复 `resolve_session_id`（显式 > 最近激活 > 唯一会话）并把 attach/listPages 移入「不依赖既有会话」集合；② 修输出契约漂移——执行器在 outputs 里多带 manifest 未声明的 `sessionId`，在 `additionalProperties: false` 下命中运行期 `INVALID_OUTPUT`；③ `browser_user_data_dirs()` 补 darwin 分支、扩展启动路径表按平台拆分，macOS 从「静默失效」修到 Edge 四项检测全 true。
- **门禁耗时治理**：画像先行（cProfile + `--durations`）证明耗时几乎全在 `load_catalog`——83 条 manifest 每次读盘 + 解析 + 166 次 JSON Schema 自检，沙箱里单次 5–6.6s，叠加各 fixture 后把门禁推到 **12m39s**；加**快照指纹缓存**后大幅下降。
- **全量门禁跨平台排查**：证伪「Windows 专用指令导致卡住」的猜测——macOS 上完整 pytest 337 passed / 13 skipped / 0 failed，13 个 skip 全是 Windows-only 用例的正常守卫。
- **左侧指令树默认收起**（维护者：指令多不方便找）：语义分组首次渲染即折叠、状态记忆、搜索时强制全展开。

## 2026-09-11

- **浏览器执行/捕获收敛到自研扩展单通道**（维护者：只保留自研插件）：先移除 BrowseSkill(bsk) 执行与捕获链路（删 `bsk_client.py`、`executors/browser_bsk.py`、`capture/browser_bsk.py` 及专项测试），随后**彻底移除 playwright**（pyproject 删依赖 + `uv.lock` 重锁）——执行器纯扩展化，扩展离线时做前置预检 <1s 显式失败（`channel_offline`，不回退、不白等），一批命令改用扩展的 DOM/页面原语。
- **navigate「打开网页」对标影刀多轮收敛**：新增必选 `browserType`（msedge/chrome，多扩展宿主按标识路由——`_hosts` map + 心跳剔除 + 目标离线快速失败）；`channel` 收敛为**仅 playwright 通道生效**并删 firefox/webkit（实现走 `chromium.launch(channel=...)` 只支持 chromium 家族，事实不可用故不下发）；参数面板从 transport×channel 两层嵌套改为「单一下拉 + 通道透明」；session 绑定宿主浏览器避免多浏览器串台；**输出浏览器实例唯一 id + tabId**（同一 profile 多窗口 = 同一实例，`chrome.storage.local` 持久化 `rpaInstanceId`）；url 可省略协议（`_ensure_scheme`，缺失补 https）。
- **移除扩展通道的 token 配对机制**（维护者：配对多余）：删掉 token 存储/TOFU/`_require_extension_token`、`/api/capture/extension/token` 路由、background.js 的 `getToken` 与 `X-Capture-Token` 头、popup 配对/重置与前端配对步骤；hybrid 改为**默认启用**（不再依赖已配对）。
- **命令列表与参数顺序对标影刀**：新增 manifest 顶层 `x_palette_order` 整数字段，30 条 `browser.*` 按 benchmark 文档的影刀编号写入。

## 2026-09-10

- **表达式双模式落地（x-fx 体系后端消费）**——用户定案：fx 用方括号标签、py 直写变量名且支持写回、sessionId 默认会话；其余 UX 建议均不采纳：
  - **fx 标签语法**：`[name]` / `[name.field]` 替换变量值，支持文本混排拼接（「输出日志：这是变量[webpage1]」），弥补 `${}` 全串匹配不能拼接的硬伤；整串 `${}` 保持既有语义向后兼容；未定义标签运行报 INVALID_REFERENCE（不静默）。
  - **py 模式**：worker 新增 `python.evalExpression`（子进程 exec/eval，与 data.* 同一隔离模型，规则 5/6 不破）；用户 Python 可直写变量名（`webpage1.url`）、可赋值；**赋值变量由 worker 回传、orchestrator 合并进 scopes.variables（规则 3 不破），跨节点可用**（端到端实证：n3 py 赋值 total → n4 fx 引用 [total]）。同节点多 py 字段共享命名空间；`__builtins__` 等内部键过滤；string schema 字段自动 str 化。
  - **sessionId 默认会话**：45 条命令移除 input required 的 sessionId（output required 输出契约保留）；`resolve_session_id`（显式 > 最近激活 > 唯一会话，多会话无记录报错不猜测）接入 browser（playwright/bsk 双轨）/desktop/desktop_win32——「打开网页」后的后续命令可全部省略 sessionId。
  - **编辑器**：Vue 版 PropsPanel 重做 fx 交互（textarea + [标签] chip 实时预览 + 光标处插入）并把 expr_modes 持久化到 node._exprModes（原先存组件 ref 切节点即丢）；旧版 app.js sessionId 下拉空值=默认会话；纠正此前误判（Vue 版实读字段级 x-fx，源码无回归——基于过期构建产物的判断）。
  - **架构检查**：check_architecture 对 workers/ 豁免 eval/exec 扫描（规则 5 语义：动态求值限定在子进程执行面，orchestrator 仍全面禁止），附注释。
  - **事故与修复**：vite build `emptyOutDir: true` 清空了 outDir——**删掉了线上 app.js/i18n.js/styles.css/catalog.html 并把 index.html 覆盖成 Vue 版**；git checkout 恢复全部线上文件，vite.config 改 `emptyOutDir: false` + 注释警示（迁移中间态严禁清空）；构建后 index.html 仍会被入口输出覆盖，需 git checkout 恢复（已知操作约束）。前端 node_modules 多处空壳损坏（rolldown/picomatch 等 junction 缺失），移走全量重装后构建通过。
- 指令清单 UX 分析报告（docs/command-ux-analysis.html）：54 条命令实测（参数中位数 3、必填 2、45/54 需 sessionId）；两个 P0（变量能力已实现但 x-fx 仅 3 字段暴露、无插值且静默失败）——本日三项落地即针对其中 P0-1/P0-2 与 sessionId P1。
- 新增测试：resolver 标签 9、python 表达式/端到端 15、resolve_session_id 6、browser 合同 2。

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
