# ADR 0022：小红书专用工作台作为旁路应用层

状态：**定案（2026-11-11 维护者拍板）**

日期：2026-11-11
关联：ADR 0015（Native Messaging 单通道）、ADR 0020（旁路形态退役门）、
`docs/branch-ownership.md`、`.harness/branch-scope.json`

编号说明：ADR 0020 §2.1 与 §6 已把 **0021** 预留给「退役 Qt gui」，本决策顺延为 0022。

## 0. 维护者拍板

> 「没有源码，是网上看到的，从零开始做，采集频率可自定义，
> 基于咱们已有的 rpa 能力来做采集能力支撑。」

分支形态另有一票：**新起 `xhs-workbench` 分支**，不重命名 `ppx-only`。

## 1. 背景

参考对象是一张「小红书 RPA 工作台」截图：本地 `127.0.0.1:8790` 的 Web 台，
盯的是小红书虚拟资料 / 知识付费商品（739 件、最高销量 221,000），
每天 8:00 起把 120 件商品错峰铺进 12 小时窗口，一件一个固定时刻，
一批 20 条、间隔 3~6 秒；另有告警 12、采集失败日志、全暂停/全恢复、8 个 tab。

关键判断：那不是「RPA 编辑器」，而是**用 RPA 跑垂直业务的数据运营台**。
设计重心不在采得多，而在长期不被风控地采。本仓 `ppx-only` 上是通用可视化
流程编辑器，两者产品形态不同，所以不走「改造 ppx 客户端」的路。

## 2. 决策

### 2.1 新分支 + 独占域

从 `ppx-only` 的 `cd74295` 起 `xhs-workbench`，独占 `apps/xhs-workbench/`。
`split_point` 仍是 `ff36ed21`，三条分支共同基线不变；契约与闸门从两分支
泛化为按 `branches` 键遍历，跨分支比对逐条进行、缺 ref 明确 `skipped`。

### 2.2 采集执行复用能力层，工作台不重写浏览器逻辑

工作台只经 `rpa-core run` 子进程调用，接口是：
`--inputs`（喂 `${inputs.keyword}`）、`--artifacts`、`elementRefs` +
`<flowDir>/elements/*.json`、`browser.queryAll` / `browser.executeScript`、
`result.json.outputs.<step>.outputs.*`、`events.jsonl`、
`pause` / `resume` / `runs list|show`。

**不 import `src/rpa_core` 内部模块。** 因此 README `scope.out` 里排除的
FastAPI / 数据库 / 调度器 / React UI 仍只针对运行时核心，本决策不改变
ADR 0020 的定案，也不需要改 `scope.out`。

### 2.3 频率可自定义，并发上限不可自定义

`config/schedule.json` 的 mode / startAt / windowHours / cron / intervalMinutes /
batchSize / perItemIntervalSeconds 全可改。截图那组数字只是默认预设。

`maxConcurrentRuns` 恒为 1：ADR 0015 明确「扩展侧必须串行化 `connectNative`，
否则并发调用会拉起多个 host 进程」。所以「频率」只改触发时刻与间隔，
不改为并发数；调度器产出有序时刻，执行层一次取一个。
该键写成别的值或被删掉都判配置非法，不静默纠正。

固定时刻用 `sha256(item_id) % window_seconds`，保证同一商品每天同一时刻采，
跨天快照可对齐；随机 jitter 只作用于批内间隔。

## 3. 后果

正面：三条分支职责清楚，`ppx-only` / `plugin-only` 的既有定案一行未动；
核心运行时零改动，工作台挂了不会污染能力层门禁；频率调整不碰代码。

代价与欠账（详见 `apps/xhs-workbench/README.md`）：真机 E2E 未验证（登录态、
选择器、`executeScript` 实际返回值）；跨 run 会话复用未实测，P0 仍每次
`browser.close`；快照库、告警规则、监控池状态机、HTTP 与 UI 都在 P1/P2；
应用层单测暂未接进 `check_all.py`，接线时要补哨兵证明真被跑到。

## 4. 合规边界

采集频率默认值是为降低平台风控风险而设的保守预设，不是对抗平台规则的手段。
实际运行的频率、范围与数据使用由维护者自行把关；把间隔调到 0 之前，
UI 应先显示 `estimate()` 给出的每日请求数与峰值 QPS。

---

## 修订 0022-R1（2026-11-11，维护者拍板）：编辑面松绑 import 边界

### 触发

P1 里工作台先自研了一份四区流程编辑器（指令栏/画布/属性/日志 + 执行控制），
真机验证通过。维护者随后判定：「重写重复、功能对齐麻烦」，要求复用 PPX 原型
的编辑器（`/.harness/demo/ppx_probe/app/`），并按工作台语境改造（保留新建流程、
面向小红书将来的多个工作流）。

### 松绑范围（只有一条，且只松编辑面）

- 工作台的**编辑面**允许 import `rpa_core.{catalog,compiler,model}`——通过 PPX 原型
  的 `app/api/api.py` 承载，复用其「定点改键 + model_validate 复校」的保存语义。
- **运行路径完全不变**：采集与编辑器内的「运行」仍是 `rpa-core run|resume` 子进程
  + `--inputs` / `--artifacts` / `elementRefs` / `events.jsonl` / `result.json` /
  `control.json` 文件契约。调度/快照/告警/监控/批量采集/数据层仍**不 import** 内核。
- 门禁不动：`check_architecture.py` 只管 `src/rpa_core` 内部方向；`check_branch_scope`
  管跨分支文件改动，不管 import——松绑不改任何闸门语义（实测全门禁仍绿）。

### 不做什么

- **不改 PPX 原型本体**：`.harness/demo/ppx_probe/` 仍归 `ppx-only`，ADR 0019 的
  冻结/隔离判据原样生效；工作台只「运行期消费」它，一行不写。
- **不装 ppx_py 进主 venv**（ADR 0019 §2.2）：桥层注入一个与 `ppx_py/bridge.py`
  语义一致的 ~40 行替身（`api_method` 打标 + `BridgeError(code, message)` +
  `Bridge.call` 的参数绑定与 `{ok,data,error,requestId}` 信封），原型转正前不背它的依赖。
- 原型 gui 的 `dist/` 构建产物按静态文件托管 + 一个 `window.pywebview.api.call` 的
  fetch 替身（ppx-js 的桥机制就是 pywebview，见 ppx-js/src/index.js）。

### 后果

工作台编辑器 = PPX 原型同款（含 if/forEach/try 结构编辑、元素资产面板、编译校验、
运行/停止），能力层升级自动对齐；自研四区编辑器退役（其「暂停/继续」由桥层对
`control.json` 的原子写补足——那是文件契约，不是 import）。风险：原型未转正
（ADR 0019 退出条件未全过），若原型大改，桥层要跟着修——这是本次松绑明确接受的代价。

## 修订 0022-R2（2026-11-12）：允许改原型的「共享编辑面」

### 触发

维护者指出编辑器里没有「如果 / 则」「尝试 / 捕获」。取证结论：能力层一直有
`IfNode` / `ForEachNode` / `TryNode` / `ReturnNode`，旧 Web 编辑器 M11 也有可拖入的
「控制流」分组；**迁移到 PPX 原型时只迁了「显示 + 编辑已存在节点」，没迁「新建入口」**
（唯一的 `rpa.insertNode` 硬编码 `type:"action"` 且只收 catalog 命令）。R1 后果段
「含 if/forEach/try 结构编辑」按当时实测应理解为**只含编辑已有节点**，不含新建。

### 决策

- **R1 的「不改 PPX 原型本体」作废，但只作废编辑面这一条**：原型
  `app/api/api.py` 与 `app/gui/src/main.js` 允许被工作台分支修改，用于补
  `rpa.insertControlNode`、虚拟组行的 `branch` 锚点与左栏「流程控制」分组。
  其余原型文件（窗口 / 启动器 / 打包 / 探针）仍归 `ppx-only`，ADR 0019 的隔离判据不变。
- **控制流仍是 AST 节点，不是 manifest 命令**：AGENTS.md 规则 1 与 `compiler.py` 的
  `Control flow cannot be a command` 不动，catalog 仍是 89 条，不新增任何命令。
- 治理配套：`.harness/branch-scope.json` 的 `shared_runtime` **精确列出这两个文件**
  （不是整个原型目录），`owner_of()` 先查 shared_runtime 再查 exclusive；
  该文件在 `must_match_across_branches` 里，故三分支各提交一次、blob 逐字节相同。
  理由与口径见 `docs/branch-ownership.md` 的「共享编辑面」一节。

### 后果

工作台与 PPX 原型共用一套编辑能力，一处改两端受益；代价是原型编辑面不再是
`ppx-only` 独占，跨分支改动要靠 `cherry-pick` 显式同步（契约只保证 `branch-scope.json`
与闸门三分支一致，不保证这两个文件本身同步）。`gui/dist` 仍按 `.gitignore` 不入库，
换机器后需 `npm run build` 重建。

## 修订 0022-R3（2026-11-12）：原型目录整体共享，撤回「逐文件放行」

### 触发

R2 之后紧接着补拖拽与底栏：拖拽样式要落 `style.css`、底栏结构要落 `index.html`，
两个文件都仍归 `ppx-only`。为了不越界，实现被迫把 CSS 与整块 DOM 搬进 `main.js`
运行时注入——**为了避免改一个独占文件，把版式藏进逻辑文件**，这是治理约束制造出来的
坏味道，不是技术取舍。维护者拍板放宽：当前分支也能编辑 PPX 分支文件。

### 决策

- `.harness/demo/ppx_probe/` **整棵目录从 `ppx-only.exclusive` 移出**，改列进
  `shared_runtime` 的目录前缀（以 `/` 结尾）。`owner_of()` 支持目录前缀，
  优先级为 **精确声明 > 目录前缀**：精确 `shared_runtime` 文件与各分支 `exclusive`
  先判，剩下才落到共享目录兜底。
- `ppx-only` 保留一条窄的独占域：`app/ppx.toml`、`app/package.json`、
  `app/pnpm-lock.yaml`、`app/pnpm-workspace.yaml`、`app/ppx.lock` —— 客户端打包配置，
  工作台没有理由改。ADR 0019 的「原型不 import Qt/gui UI 模块」隔离判据不变。
- `check_scope_sanity()` 新增目录前缀存在性检查；`branch-scope.json` 与
  `check_branch_scope.py` 都在 `must_match_across_branches` 里，三分支各提交一次、
  blob 逐字节相同。
- **R2 的「精确放行两个文件」作废**，R1 的「不改原型本体」仍只在编辑面范围内作废。

### 后果

编辑器的 DOM 回 `index.html`、版式回 `style.css`、逻辑留 `main.js`，三处各归其位；
工作台分支以后改原型任意文件都不必再动治理契约。代价是 `ppx-only` 不再拥有原型独占权，
原型编辑面事实上成为三分支共享资产，跨分支同步仍靠显式 `cherry-pick`。
`gui/dist` 依旧不入库，换机器要 `npm run build`。

## 修订 0022-R4（2026-11-18）：编辑面服务化边界

### 触发

元素编辑界面升级时暴露出边界失守：`app/api/api.py` 直接 import 了
`capture_kit.ElementEditDialog` 这个 **win32 UI 类**，还在应用层自己实现了名字校验、
定位校验、编辑模型构造与 css 重组。R1 放行的 import 白名单只有
`rpa_core.{catalog,compiler,model}`，现实早已越过却未成文；后果是「换一种编辑框形态
就得改内核」，且 HTML 与 win32 两个编辑面各行一套判据。

### 决策（吸收影刀做法：HTML 编辑器 + 语义化桥 + 后端服务）

- **内核侧**：`capture_kit.element_editing` 是元素编辑语义的唯一权威——名字校验、
  定位校验、编辑模型构造、层级勾选重组 css（转出 `selector_tree`）。该模块**不碰
  文件、会话、窗口**，是纯函数层。
- **工作台侧**：`api.py` 只负责会话与落盘，通过语义 RPC 暴露模型/校验/保存/重捕；
  HTML 编辑框与 win32 框都消费同一份模型与错误码，前端不感知 UIA / CDP / 对话框实现。
- **允许 import**：编辑面可 import 内核的公开无 UI 服务函数——`capture_kit.element_editing`
  导出的全部符号，以及 `runtime.element_refs` 的参数键口径（`element_capable_keys` /
  `element_kind_for_param_key` / `param_key_for_element_kind`），前端不硬编码 `selector`/`locator`。
  `ElementEditDialog` 仅允许作为**降级/详情入口**被构造，不得再从它读取模型或校验口径。
- **不做什么**：元素存储仍在 `<flowDir>/elements/*.json`（文件契约，不是 import），
  `elementRefs` 仍是唯一接线（ADR-002 ref-only）；不新增 manifest 命令，控制流仍是 AST。

### 后果

编辑面换形态不再动内核；两套编辑面的判据不会漂移。代价是 RPC 多一层模型/错误码
转换，且 `element_editing` 的每次判据变更都要两个编辑面一起回归。

## 修订 0022-R5（2026-11-18）：win32 原生框升为主编辑面

### 触发

M90 把桌面 `nodes` 模型接进了网页 `#elemDialog`，但实测用户点「捕获元素」和
「元素详情」拉起的都是 `capture_kit.ElementEditDialog`（win32 原生框）。R4 把它
定性为「降级/详情入口」与事实不符：在 Windows 上它其实是两条主路径的唯一编辑面，
网页框只在非 Windows / 桥不可用时出现。结果是功能做了，用户看不到。

### 决策

- `ElementEditDialog` 在 Windows 上是**主编辑面**，允许被捕获控制器与
  `rpa.editElement` 直接构造；网页 `#elemDialog` 降级为备用面。
- 两面仍然消费同一份内核语义：`element_editing.build_edit_model` 出模型、
  `desktop_editing.compose_selector` / `selector_tree.compose` 出定位。原生框只维护
  控件状态，不另写校验或重组口径。
- 判据必须钉住**用户实际走的路**：控制器传 `nodes`/`descriptor`、`rpa.editElement`
  传同一模型、原生框的预览与保存各调一次内核重组。缺一条就是「做了但没接线」。

### 后果

修改编辑模型时仍要两面一起回归；但用户不必知道有两套编辑框，桌面捕获与详情
看到的是同一个影刀式层级/属性页。R4 的「仅降级/详情入口」措辞作废，其余边界不变。

## 修订 0022-R6（2026-11-18）：元素库分组与来源元数据落地

### 触发

影刀元素库对标取证显示：库的核心是「按分组组织的资产树 + 来源/状态元数据」。
我方只有按 `kind` 的三桶硬分组，元素文件也没有 `group/framework/processName/
winTitle/screenshot` 权威。用户拍板先做「贴影刀单树」形态，但第一版只落 P1 数据模型。

### 决策

- 分组存 `<flowDir>/element_groups.json`，**不进 `elements/`**——`listElements` 用
  `elements/*.json` 枚举资产，放进去会造出一条没有选择器的假元素。
- 元素归属放 `ElementDescriptor` 的显式顶层 `group`（组 id）。顶层字段必须显式声明：
  未知键会被 pydantic `extra=ignore` 静默丢弃（历史事故：顶层 `url` 从未落盘）。
- `framework/processName/winTitle/screenshot/lastVerifiedAt/lastVerifyError` 同步显式化，
  供 P2 树形库/P3 状态列消费；旧资产缺字段返回 `None`，**不做数据迁移**。
- 文件名仍是元素与 `elementRefs` 的唯一接线（ADR-002 ref-only）；删除仍走引用护栏；
  不恢复手写选择器入口。

### 后果

P2 可以直接消费 RPC 画单树，不必先改盘上格式；代价是多一份分组元数据文件，
需要靠判据钉住它不混进 `elements/`，且每次新增顶层字段都必须同步模型与测试。

## R7（2026-11-18，M93 P2：树形库落地）

### 背景

P1 的分组/RPC 建好后，面板仍按 `kind` 三桶硬分，分组建了也看不见。P2 把数据接成树。

### 决策

- 树用「分组头行 + 缩进资产行」的**扁平 DOM**，资产行仍是 `elemBody` 的直接子节点：
  `verifyAllAssets` 按 `children` 找行写回命中数，嵌进容器会让它失效。
- 管理入口（新建/重命名/删组、移动、复制、查引用、多选批量）走**动态 DOM 菜单**
  （`#elemLibLayer`），不进 `index.html` 静态按钮区——静态按钮白名单是等值集合
  （捕获×2 + 全部校验），加按钮就红；资产行也保持恰好「详情/校验/删除」三动作。
- 分组折叠状态落盘（`setElementGroupCollapsed` 只改 `element_groups.json`），前端先
  本地翻、RPC 失败回滚；未分组是虚拟桶，不落盘。
- 右键删除复用行内 `deleteAsset` 的引用护栏链，不准另打 `deleteElement` 绕过。

### 后果

树/菜单/批量条都靠 `check_element_library.mjs` 的 DOM shim 真跑（含真右键事件），
只扫源码抓不到「菜单建了但右键不弹」这类假绿灯——负向验证已证明确实漏过一次。
`lastVerifiedAt/screenshot` 等状态字段 P2 只渲染已有值（processName/winTitle），
校验状态列与缩略图留在 P3；不提前造 UI。

### R8（M94：底栏高度）

底栏默认高随视口 `clamp(300px, 34vh, 440px)`，顶边 6px 手柄可拖拽，范围夹在
`140px ~ 视口-160px`，拉过的高度进 `localStorage`，折叠态隐藏手柄且不响应拖拽。
手柄是 div 不进静态按钮白名单；三个 tab 共用同一高度，不给元素库单独开分支——
视图切换不该让底栏跳高。拖拽只写 DOM 行内高度，不碰任何资产/流程数据。

### R9（M95：桌面元素校验的窗口链）

`visible_top_windows()` 的默认过滤是浏览器语义（空标题/桌面壳一律滤掉），桌面元素
校验不能原样复用：任务栏搜索按钮的宿主 `Shell_TrayWnd` 标题恒为空，旧实现永远
`count=0`。桌面 verifier 单独传 `include_desktop_shell=True`，只放行两种任务栏壳窗口
且保持 Z 序位置；浏览器链不传，判据钉死两边不漂移。`preferred_hwnd` 的语义不变。
若将来 automationId 漂移导致仍不命中，走候选回退，不放宽这条过滤。
