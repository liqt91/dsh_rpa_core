# ADR 0014：编辑器宿主形态二 —— 独立桌面客户端的候选方案

- 状态：**方案 E（PySide6 原生重写）已立项（2026-09-15）**；§8 为可行性实证，§9 为决策与前三切片（指令树 / 流程卡片画布 / 参数表单）落地记录，§10 为目标架构。其余候选（A–D）保留备查。
- 日期：2026-09-15
- 关联：ADR 0010（编辑器宿主形态一：保持 Web）、ADR 0008（编辑器 UI 与零构建形态）、ADR 0011（运行控制 proxy）、ADR 0012（devserver 能力层复用）
- 目的：列出「是否/以何种技术给编辑器一个独立桌面窗口」的可选路径，供维护者择定；本文不预设结论。

## 1. 背景

ADR 0010 曾以"捕获遮挡"为唯一诉求否决桌面客户端并列为远期。阅读本文时应注意新的、难以被现有设计规避的诉求：

1. **独立桌面体验**：影刀 / UiPath / Power Automate Desktop 均为桌面应用，用户主观预期 RPA 编辑器是独立程序而非"浏览器里一个标签页"。
2. **程序化窗口控制与全局热键**：捕获元素时希望编辑器能自动隐藏/最小化，并支持全局热键唤起捕获——Web 页面沙盒做不到这两点。
3. **前端规模已过临界**：编辑器自 ADR 0008 后演进（M11 树形画布、M19 面板交互、指令树分组），`app.js` 规模远超 ADR 0010 时代。若走客户端，需明确是保留零构建换宿主，还是借机重写。

## 2. 现状约束（候选都必须守住）

- **ADR 0008 零构建硬边界**：前端零 npm/打包器/CDN，`GET /` 唯一静态路由。
- **后端 Python 优先**：能力层、devserver、executors、worker 均为 Python。
- **运行控制隔离（ADR 0011）**：编辑/devserver 进程内**不承载 run**，运行走 `rpa-core run` 子进程。
- **能力层复用（ADR 0012）**：devserver 是薄通道，`import` 能力层而非 spawn。
- **scope.out 排除**：`installer`（打包/安装器）仍在排除清单。

## 3. 候选方案（仅列选项，优缺自明）

| 方案 | 技术栈 | 前端改动 | 对约束的冲击 | 典型代价 |
|---|---|---|---|---|
| A. pywebview 薄壳 | Python 内嵌系统 WebView2 (Win)/WKWebView (mac)，启动本机 devserver | 零 | 低：新增 `rpa-core editor` 命令 + 新门槛；需解除 ADR 0010 桌面壳排除 | 新增一个第三方 Python 依赖；窗口控制能力由系统 WebView 提供 |
| B. Electron | Node 壳 + Chromium | 零（套壳） | 高：打破零构建、引入 Node/打包链，违背 Python 优先 | 体积巨大、工具链偏离 |
| C. Tauri | Rust 壳 + 系统 WebView | 零（套壳） | 高：违背 Python 优先，引入 Rust 工具链 | 需维护 Rust 侧 |
| D. Qt/PySide QWebEngineView | Python + C++ GUI 壳 | 零（套壳） | 中：较重 GUI 依赖，打包体积大 | 依赖 Qt runtime |
| E. 原生控件重写 | PySide 控件流程 | 全部重写 | 极高：丢零构建、接管全部交互 | 前端生态作废 |

共同点：A–D 都保留「渲染层仍是当前零构建前端」，仅改变宿主（浏览器 → 内置 WebView 控件）；只有 E 是重写。

### 3.1 跨平台矩阵

| 方案 | Windows | macOS | Linux | 说明 |
|---|---|---|---|---|
| A. pywebview 薄壳 | WebView2（Edge 同源，系统自带） | WKWebView（Safari） | GTK WebKit | **同一套 Python 代码**，系统 WebView 组件由 pywebview 抽象；窗口/手势细节各平台有差异 |
| B. Electron | 自带 Chromium | 自带 Chromium | 自带 Chromium | 三平台同捆同一浏览器，最一致，代价是体积与工具链 |
| C. Tauri | WebView2 | WKWebView | WebKitGTK | 壳是 Rust 但跨平台一致 |
| D. Qt/PySide | QtWebEngine | QtWebEngine | QtWebEngine | 三平台同绑定，依赖 Qt runtime |
| E. 原生重写 | 取决技术（如 WPF 仅 Win） | 同 | 同 | 跨平台性**由所选技术决定**，若用 WPF 则单平台 |

补充：本项目跨平台准则为「稳定契约 + 能力感知驱动，不要求每 OS 桌面表现逐像素一致」（AGENTS.md 规则 9）。因此 A/C/D 这类"系统组件有平台差异但契约一致"的形态符合项目定位；B 与 Qt 提供更高一致性但打破 Python 优先或增大依赖。

### 3.2 方案 A（pywebview）与方案 B（Electron）的功能差异

| 能力维度 | A. pywebview 薄壳 | B. Electron |
|---|---|---|
| 渲染引擎 | 系统 WebView：Win=WebView2(≈Chromium Edge)、mac=WKWebView(WebKit) | 自带 Chromium，三平台引擎统一 |
| 前端兼容性 | mac 上 WebKit 对部分新特性/API 支持弱于 Chromium（如个别 CSS、File System Access 等） | 三平台行为一致，前端兼容风险最小 |
| 与现有 Python 能力层集成 | 天然贴合：能力层/devserver 均 Python，壳内 import 即用（ADR 0012） | 需在 Node 侧写 IPC/桥，再多一层技术栈接现有 Python HTTP |
| JS↔宿主双向桥 | pywebview `js_api`（Python↔JS）开箱即用 | ipcMain/preload/ipcRenderer，能力更强但需自建桥代码 |
| 全局热键 | 内置 `hotkey` API | 需第三方（如 globalShortcut 需配合） |
| 系统托盘 | 不原生，需另加 pystray | 原生 Tray |
| 窗口控制（隐藏/置顶/无边框/大小位置） | 支持（pywebview window 属性/方法） | 支持且更精细（frameless+自绘标题栏等） |
| 文件系统直访问（renderer） | 不直连，经 js_api 回 Python（本项目能力层本就在 Python，无损失） | Node 可直读 fs，但本项目也倾向走 HTTP/子进程 |
| 调试 | 依赖系统 WebView DevTools（Win: WebView2 DevTools、mac: Safari inspector） | DevTools 成熟开箱即用 |
| 运行时体积/部署 | 小；Win 依赖系统 WebView2 runtime（Edge 同源，普遍已装） | 大；自带 Chromium 各平台都要几百 MB |
| 技术栈变化 | 仅新增一个 Python 依赖，无 Node 工具链 | 引入 Node 工具链，违背后端 Python 优先 |

结论性事实（不蕴含建议）：A 的差异集中在「mac 前端兼容」与「托盘/桥需额外件」；B 的差异集中在「一致性/细粒度控制」与「体积/技术栈偏移」。两者对本项目"能力层在 Python + HTTP/子进程边界"都成立。

## 4. 触发/重估条件（是否立项的判断依据，由维护者取阈值）

- 独立桌面窗口成为用户明确期待（对标影刀/UiPath 形态）；
- 需要全局热键唤起捕获，或需要编辑器在捕获时程序化隐藏（Web 沙盒做不到）；
- `app.js` 规模已到"零构建维护比换宿主更贵"的临界，需要裁决是重构还是留存。

## 5. 需解除的既有决策（若采纳任一桌面壳方案）

- ADR 0010 §排除确认的"不引入任何桌面壳依赖（pywebview/Electron/Tauri 均暂缓）"；
- 打包/安装器仍属 scope.out，桌面壳第一版只作为内置命令提供，打包单独立项。

## 6. 未决问题（方案选定后再逐一收敛）

- 系统 WebView2 runtime 缺失时是否回退"自动在浏览器打开"。
- 全局热键键位与冲突处理归属（壳层）。
- macOS 窗口控制差异是否纳入第一版。
- 薄壳是否需要前端改造（如标题栏按钮），还是尽量为零。

## 7. 选中方案后的验收口径（任一方案通用，体现 ADR 0011 边界不破）

1. 独立窗口能渲染现有编辑器，可打开/编译/保存流程；
2. `▶ 运行` 仍 spawn `rpa-core run` 子进程（编辑进程不承载 run）；
3. 捕获链路（扩展/混合）行为不变。

## 8. 方案 E（PySide6 原生控件重写）可行性实证（2026-09-15）

针对方案 E 做了隔离 demo（不污染主代码与依赖），验证"原生控件能否承载本编辑器的复杂交互与观感"。demo 位于 `.harness/demo/pyside6_demo.py`（独立临时 venv，PySide6-Essentials 6.11，未写入 `pyproject.toml`）。

### 8.1 功能可行性：全部覆盖，多数是原生舒适区

| 编辑器部件 | demo 实现 | 结论 |
|---|---|---|
| 左侧指令树（分组/收起/搜索过滤） | `QTreeWidget` + 过滤 | 原生，零手写交互 |
| 参数表单（manifest `input_schema` → 控件） | 按 type 分发 string/enum/integer/boolean 到 `QLineEdit/QComboBox/QSpinBox/QCheckBox` | schema→控件渲染逻辑可直接平移 |
| 中部流程画布（sequence/if 嵌套树 + 子树拖拽重排） | `QTreeView` + `QStandardItemModel`，`InternalMove` 原生拖拽 | 模型/视图分离恰是画布天然架构，拖拽/折叠/选中框架内建 |
| 素材库（元素树 + 搜索） | `QTreeWidget` | 原生 |
| 数据表格（双击编辑/追加行/删除行/保存） | `QTableView` + `QStandardItemModel` | 原生，标准控件面板是 Qt 舒适区 |

### 8.2 观感：卡片画布"可达但需自绘"，是 E 的主要成本

现有 Web 画布节点是**圆角卡片**（`#canvas li`），含：圆角边框、hover 阴影、选中蓝环、左侧按深度换色的 4px 粗线、行内「拖柄+序号+命令名+等宽参数摘要+类型徽标」、拖拽落点线。demo 用一个 `QStyledItemDelegate` 自绘全部复刻：

- 静态观感可逼近（像素采样核对：白底卡片 + 精确 4 逻辑 px 深度色线 + 选中高亮）；
- **短板**：Qt widgets 无 CSS transition，hover/选中/落点是即时切换，顺滑过渡动画需手写 `QPropertyAnimation`；行内排版精度靠手动微调。

即：卡片的**结构交互框架白送，视觉皮要一份自绘 delegate**。

### 8.3 皮肤生态实测（授权 + 桌面密度两个维度）

| 皮肤 | 授权 | 结论 |
|---|---|---|
| **QDarkStyleSheet** 3.2（DarkPalette/LightPalette 成对） | MIT | 成熟桌面 IDE 深浅双主题，紧凑克制，**符合对标影刀的桌面密度**；demo 默认采用其浅色（LightPalette：底 `#FAFAFA`、边 `#C0C4C8`、选中 `#DAEDFF`） |
| qt-material | MIT | 可用但 Material 规范**以移动/触控为原点**（大间距/卡片/Switch），与桌面工具密度方向相反 |
| PyQt-Fluent-Widgets | **GPL-3** | 观感最新但 GPL 与闭源目标冲突，且需整体换用其控件体系（非贴皮） |
| qlementine | MIT | 是 C++ `QStyle`，需 CMake+Qt6.8 编译，非免构建 QSS |
| BreezeStyleSheets | MIT | 需 SCSS 构建，不分发现成 QSS |
| qss-dracula | Unlicense | 实为 Qt Creator 配色方案，非通用 QSS |
| Ktiseos/qss_themes | **未声明 LICENSE** | 300+ 复古/整活主题，非专业向且授权模糊，不采用 |

事实收敛：免构建、桌面向、授权安全的 QSS 现实可选主要是 **QDarkStyleSheet（MIT）**；自绘卡片不被全局 QSS 命中，需在 delegate 侧自带深/浅调色板（demo 已实现 qlight/qdark/material 三套联动）。另：`QTabWidget::pane` 与 `QTabBar::tab` 边框在 qdark 下叠加会形成不规则黑边，已用局部 QSS（tab 去边框 + pane `top:-1px`）修复——此类皮肤接缝成本需计入。

### 8.4 授权前置（PySide6）

- PySide6/shiboken 双许可 **LGPLv3 / GPLv3 + 商业许可**；闭源分发走 **LGPLv3**。
- 前置条件：不修改 Qt 本体、**动态链接**（pip 安装天然满足）、允许用户重链接、随分发保留许可声明；内部自用负担更轻。规避 LGPL 的替代是 Qt 商业许可，或退回 Tkinter（标准库但撑不起复杂树/拖拽）。
- QDarkStyleSheet（MIT）不引入额外 copyleft。

### 8.5 实证小结（事实，不定案）

- 方案 E 在**功能与桌面观感上均被证明可行**：标准控件面板（树/表单/表格）原生舒适，卡片画布靠自绘 delegate + QDarkStyleSheet 可达可接受桌面观感（维护者已认可 qlight 默认版）。
- 真实代价有三：**前端资产（`app.js`/`styles.css`）全部重写**、**卡片等非标准视觉需自绘 delegate 并自管深浅色调色板**、**交互动画顺滑度弱于 Web**。
- 收益：独立桌面窗口、全局热键、捕获时程序化隐藏、原生系统集成，且仍在 Python 进程内复用能力层（ADR 0012 语义不变）。
- demo 运行：临时 venv 下 `python .harness/demo/pyside6_demo.py card [qdark|material] panels`（默认 qlight）。是否立项 E 仍按 §4 触发条件由维护者裁决，本草稿不替其决定。

## 9. 决策与第一切片落地（2026-09-15）

维护者裁决采纳**方案 E（PySide6 原生重写）**，以「独立桌面客户端 + 原生观感」为方向，Web 编辑器（ADR 0010）继续保留，二者短期并存。

**依赖形态（守住默认零重依赖）**：PySide6/QDarkStyle 不进默认依赖，声明为 optional extra `gui`（`uv sync --extra gui` 启用）；`uv sync --all-groups` 与 CI 默认不安装 Qt，GUI 测试在缺依赖时整组跳过。

**第一切片（已落地）**：
- `src/rpa_core/gui/`：`app.py`（qlight 皮肤、主窗口、真实 catalog 指令树 + 搜索过滤），包顶层不 import Qt；
- CLI 新增 `rpa-core gui`，延迟导入，缺 extra 时返回结构化 `GUI_EXTRA_MISSING` 与安装提示，不影响其它子命令；
- 指令树数据源是 `load_catalog` 不可变快照（规则 7），按命名空间分组（browser/data/workflow/desktop），不复制命令定义；
- `tests/contract/test_gui_smoke.py`：offscreen 平台 6 例（分组顺序、83 条全量成叶、计数标签、过滤、清空恢复、选中回显）。

**后续切片边界**：中部流程画布（卡片树 + 拖拽）、参数 schema 表单、素材库/数据表格面板、运行控制、i18n 中文名接入；运行仍走 `rpa-core run` 子进程（ADR 0011/0012 边界不破）。

**第二切片（已落地）**：中部流程卡片画布。

- `src/rpa_core/gui/flow_model.py`：Workflow AST → `QStandardItemModel`。容器节点（sequence/if/forEach/try）可展开；if 的 then/else、try 的 catch 以「虚拟组」行呈现（AST 中无对应节点，仅作视觉与拖放容器，`iter_real_nodes` 导出时剔除）。自定义 MIME 仅携带节点 id（`application/x-rpa-flow-node`），自实现 `mimeData/dropMimeData` 完成同模型内移动，并拒绝把节点移入自身后代（防成环）。
- `src/rpa_core/gui/canvas.py`：`CardDelegate(QStyledItemDelegate)` 自绘卡片，复刻 Web 端视觉契约——白底圆角卡片、选中 `#daedff`、左侧 4px 深度色线（depth-0..5 同 Web 谱系）、拖柄、同级序号、粗体命令名、等宽参数摘要（前 2 个参数 + `+N` 计数）、命名空间徽标；虚拟组为浅灰条。
- `app.py` 集成：中栏占位替换为真实画布，`MainWindow.set_workflow` 可装载真实 `workflow.json`，`rpa-core gui <flow>` 支持打开流程；`apply_theme` 增补跨平台中文字体回退（Microsoft YaHei / PingFang SC / Noto Sans CJK SC…），避免缺字渲染成方框。
- `tests/contract/test_gui_canvas.py`：offscreen 10 例（真实流程映射、then/else/catch 虚拟组、参数摘要、同级重排、跨容器移入 then、防成环、flags 拖拽规则、行高 46、非法父节点拒绝）。

**第三切片（已落地）**：参数 schema 表单。

- `src/rpa_core/gui/param_form.py`：`ParamForm` 把 `manifest.input_schema`（JSON Schema dict）渲染为原生控件——string+enum 为 `QComboBox`（首项「未设置」，`x-enum-labels` 提供中文显示，itemData 存实际值）；string 为 `QLineEdit`（空 = 未设置，default 进 placeholder）；integer/number 为带校验器的 `QLineEdit`（空 = 未设置）；boolean 为 `QCheckBox`；array/object 等复合类型降级为单行 JSON 文本（非法 JSON 在收集时抛 ValueError）；`type: ["string","null"]` 取首个非 null 类型。必填字段 label 带 `*`，description 进 tooltip。
- `flow_model.py` 新增 `ROLE_ARGS_RAW` 与 `ArgsHolder`：item 携带原始 with 参数 dict 的 Python 对象引用（不经 QVariantMap 转换，避免键序重排/类型丢失）；编辑参数时整体替换 holder。
- `app.py` 右栏集成：画布选中 action 卡片即显示「滚动表单 + 应用参数」按钮，应用后 item 的参数与摘要同步更新（delegate 自动重绘）；容器/返回节点显示对应占位提示；参数修改目前仅在内存中，保存回 workflow.json 属后续切片。
- `tests/contract/test_gui_param_form.py`：offscreen 12 例（enum 未设置项与中文标签、初始值选中、控件类型、收集跳过未设置、integer 类型、JSON 数组往返与非法 JSON、nullable 类型列表、必填星号、主窗口占位、选中出表单+应用回写、容器/返回提示、非法 JSON 应用进状态栏）。

## 10. 目标架构：GUI 内嵌 ExtHub，Web 退化为形态之一（2026-09-15）

方案 E 立项后需澄清终态：**原生 GUI 与 Web 编辑器不是替换关系，而是同一能力层上的两个宿主形态**。

### 10.1 devserver 现有三类负载（事实）

`server.py` 当前把三组关注点装在同一条顺序路由链中：

1. **编辑器宿主面（给人）**：`GET /` 与 `static/` 静态资产；`/api/catalog`、`/api/compile`、`/api/workflows*`（含 `elements`、`table`）、`/api/runs*`、`/api/env/status`。
2. **扩展命令通道（给浏览器扩展）**：`/api/ext/*`（`_route_extension_exec`，扩展借 loopback 执行桌面命令，是 run 子进程回连的一等公民通道）。
3. **捕获通道（给扩展与捕获器）**：`/api/capture/*`（desktop/browser 捕获、`extension/<action>` 回传）。

### 10.2 终态形态

- **原生 GUI（默认桌面形态）**：编辑器交互全部在进程内完成，`import` 复用 `DevServerApp` 能力层方法（ADR 0012 语义不变），**不经 HTTP 自取数据**；运行仍 spawn `rpa-core run` 子进程（ADR 0011 不破）。但浏览器扩展只认 HTTP loopback 契约，GUI **无法取代第 2、3 类负载**——因此 GUI 进程内必须内嵌一个仅服务扩展的 loopback 网关（下称 ExtHub）：只挂 `/api/ext/*` 与 `/api/capture/*`，不挂静态页与编辑器 API。
- **Web 编辑器（零安装形态）**：保留现有 `rpa-core editor` 全量挂载（三组路由齐全），退化为「不想装 GUI / 临时在浏览器打开」的形态之一；零构建边界（ADR 0008）继续有效。

即：能力层只有一份；差异只在「哪些路由被挂载、编辑器交互走进程内调用还是 HTTP」。

### 10.3 路由拆分切片（未来切片，本文只定边界，不在本切片实现）

现状 `_route_api` 的 if 链混装三组路由，无法选择性挂载。拆片方向：

- 将路由按关注点重组为可独立选择的路由组（契约：路径、方法、JSON 负载、错误码全部不变，仅改分发组织）：
  - `editor` 组：static + `/api/catalog`、`/api/compile`、`/api/workflows*`、`/api/runs*`、`/api/env/status`；
  - `extension` 组：`/api/ext/*`；
  - `capture` 组：`/api/capture/*`。
- `DevServer` 增加挂载选择（构造参数或启动入口）：`editor` 形态挂 editor+extension+capture（等价现状）；GUI 内嵌 ExtHub 仅挂 extension+capture。
- `set_extension_hub_url` 等既有接线不变；端口仍只绑 `127.0.0.1`。

**验收口径**：① 拆分后现有 devserver 契约测试零修改全绿（契约不变的回归保证）；② GUI 形态下浏览器扩展经 ExtHub 执行命令、回传捕获成功；③ Web 形态行为与现状逐项一致；④ 未装 `gui` extra 时不影响 `editor`/`run` 任何路径。