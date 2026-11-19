# M102 — 元素库：捕获即截图、图像资产独立、界面重排

Status: `wip`

（S1/S2 已完成；S3 按维护者 2026-11-19 决定**搁置**；S4 待做）

## 背景

维护者三条诉求（2026-11-19）：

1. 捕获元素时，顺手把元素本身截一张图存进资产；
2. 元素库现在的界面不好看、不便操作，要优化；
3. 「按图捕获」不该挂在元素资产下，单独列一个「图像资产」tab。

第 2 条是主观判断，先把「为什么不顺手」量化，再谈重排，否则容易改成另一种不好用。

## 现状诊断

底栏（`#bottomDock`）横跨三列，高度默认 clamp 在 140px ~ 视口减 160px，元素多时
只有中间一条 40vh 上限的滚动区。当前元素面板自上而下是：

```
[搜索 ..............] [全部校验]
[捕获元素] [按图捕获]
── 待补元素 ──
  名称  第 3 步 · 桌面软件            [补一下]
── 已有资产 ──
▾ 分组名 12
  ☐ 名称  桌面 · Weixin.exe · 微信  3 处引用  [详情][校验][删除]
            Edit / 搜索 / mmui::XValidatorTextEdit
```

具体问题（都能在代码里定位）：

| # | 问题 | 位置 |
|---|---|---|
| 1 | 一行 5 个可点目标（勾选框、详情、校验、删除、右键区），删除与校验同权重并排 | `assetRow()` 2011 |
| 2 | 行高约 60px（名字一行 + 选择器独占一行），底栏 300px 时一屏只剩 4 行 | `style.css` `.elemRow.asset .elemMeta` |
| 3 | 同一动作两套入口：行内按钮 + 右键菜单，且菜单里还有行内没有的（复制/移动/查引用） | `openLibMenu()` 2176 |
| 4 | 待补（该做的事）与库存（已有的）混在同一滚动区，没有分区边界 | `renderElementsPanel()` 2126 |
| 5 | 分组缩进只有 18px、字号 13px 与资产名 14px 接近，层级靠肉眼分辨 | `.elemRow.asset[data-elem-name]` |
| 6 | 没有视觉锚点：所有行长得一样，靠读文件名找元素 | 无 `screenshot` 渲染 |
| 7 | 语义元素与图像元素混排：一边显示控件路径，一边显示「图像 32×32 @ (x, y)」 | `_selector_text()` 2781 |
| 8 | 顶栏两个捕获按钮（捕获元素 / 按图捕获）平级并排，用户每次都要先判断「我要用哪种」 | `index.html` `.elemTools` |

第 8 条正是诉求 3 的来源：**两种捕获产出的东西不是一类物品**，放在同一个按钮组里
逼用户先分类，而分类依据（控件树 vs 像素模板）是用户看不见的。

## 需求一：捕获即截图入库

### 数据形状

资产里 `screenshot` 字段已经存在，`_fill_library_fields()` 一直写 `None`
（`api.py:2301`），`listElements` 也已经把它透传给前端（`api.py:2547`）。所以不用
新增字段名，只需把它填上。

推荐 **内容寻址**：

```
elements/<name>.json
elements/shots/<sha256 前 16 位>.png
```

```json
"screenshot": "shots/9f2c4a1b7de80315.png"
```

**是路径字符串，不是对象**——`ElementDescriptor.screenshot` 的类型就是 `str | None`
（M92/P1 对齐影刀口径，`tests/unit/test_element_descriptor.py::test_library_fields_survive_roundtrip`
钉着它），而 `cli.py` 的 `element validate` 用**同一个模型**校验盘上的资产：
塞 dict 会让每个新捕获的元素被判成非法文档。宽高不存也拿得到——图里就有，
前端 `<img>` 加载完自然知道。

路径相对 `elements/`，用正斜杠（跨平台，别用 `os.sep` 拼进 json）。

为什么用内容哈希而不是 `<name>.png`：

- 改名、复制一份（`copyAssetFlow`）、覆盖重捕都不用动附属文件——否则
  「改名要连带改 png、复制要连带拷 png、删除要连带删 png」是三个必然漏掉一个的点；
- 同一元素重复截图内容相同 ⇒ 天然去重，不会堆垃圾；
- 名字里的 `/ \ : * ? " < > |` 在资产名里已被禁，但哈希命名连路径长度问题都没有。

代价：删除元素会留下孤儿 png。接受——总量可控（每张几 KB～几十 KB），
需要时再做一个「清理未被任何资产引用的 shots 文件」的按钮，不进本期。

### 几何来源（不新写换算）

| 腿 | 矩形 | 窗口 | 换算权威 |
|---|---|---|---|
| 桌面语义 | `metadata.rect`（屏幕坐标，M97 起必有） | `metadata.windowHandle` | `screen_shot.box_in_window()` |
| 浏览器 | `metadata.rect`（视口 CSS）+ `viewport` | 捕获时用 Z 序认的浏览器窗口 | `screen_shot.browser_crop_in_window()` |
| 图像 | 同上（模板自带 `sourceRect`） | 同上 | 同上 |

实测确认：`examples/api-usage/elements/微信搜索框.json` 的 `metadata` 里
`windowHandle=9046138`、`rect={155, -903, 179, 19}` —— 负坐标也能用，因为
`grab_window` 本来就是按窗口矩形截的。

新增一个 `screen_shot.element_shot_from_descriptor(descriptor, hwnd)`：
整窗截图 → 按上表换算得图内 crop → PIL 裁成 PNG → 返回 `{png_bytes, w, h, scale}`。
**换算全部调用现有两个函数**，本模块不出现第二套坐标算术（`screen_shot.py` 头注释
里记着 150% 缩放下二次换算会偏，这条红线不能碰）。

边界处理：

- crop 四周外扩 2px 让元素边缘可见，再夹回窗口内；
- 长边 > 512px 时等比缩到 512 存（整窗级元素省空间），`scale` 记实际缩放；
- 拿不到 rect、窗口不存在、截图失败 ⇒ `screenshot: null`，**不影响入库**
  （与 `make_image_proposal` 同一取向：截图是增强，不能把一次成功的捕获拖成失败）。

### 写入时机

只在一处落盘：`_do_commit()`（`api.py:3140`，web 弹窗与 win32 确认框共用的唯一权威）。
在 `_write_element_doc` 之前调截图并写 `elements/shots/<hash>.png`；写 png 失败就降级为
`screenshot: null`，不阻断 `elements/<name>.json` 落盘。

### 旧资产

`screenshot: None` 的旧资产**不迁移**（`invariants.md` #15：开发期硬切）。UI 显示占位块，
行内不强求有图。

### 隐私口径（已拍板：随仓库提交）

元素截图会拍到目标应用界面，流程目录是进 git 的，所以 `elements/shots/*.png` 会随
`elements/*.json` 一起被提交、被推到远端。这是选定的行为：别人拉到流程就能看到元素
长什么样。

要提醒的一条：截图是「捕获那一刻的屏幕像素」，元素指向聊天窗口时里面就有对话内容。
不想让它进仓库，只能挑不敏感的目标捕获，或者事后从 `shots/` 删掉那一张（资产里的
`screenshot` 会变成悬空引用，UI 要按「缺图」显示占位，不许报错）。

## 需求二：元素资产界面重排

### 布局：底栏内左右两栏

底栏很宽（跨三列），但很矮。**横向空间富余、纵向稀缺**，所以列表与详情应该左右排，
不该上下堆。

```
┌ 元素资产 · 图像资产 · 全局参数 · 运行日志 ─────────────────────────────┐
├──────────────────────────────┬──────────────────────────────────┤
│ [搜索………] [只看未使用] [▾] │  ┌──────────┐                     │
│ ┌ 待补 3 个（点开）────────┐ │  │          │  微信搜索框          │
│ │ · 登录按钮  第 2 步  补  │ │  │  截图     │  桌面 · Weixin.exe   │
│ └───────────────────────────┘ │  └──────────┘  搜到 1 个 · 10:12 校验过│
│ ▾ 登录流程 (4)                │                                  │
│  ▣[缩略] 用户名输入框  [3] ⋯ │  定位  Edit / 搜索 / XValidator…   │
│  ▣[缩略] 密码输入框    [2] ⋯ │  引用  第 3 步 · 用户名 → 用户名输入框│
│ ▾ 未分组 (2)                  │                                  │
│  ▣[缩略] 提交按钮          ⋯ │  [校验]  [详情]      [删除]        │
└──────────────────────────────┴──────────────────────────────────┘
```

- **左栏（约 62%）**：搜索 + 「只看未使用」+ 视图切换（分组树 / 平铺）+ 待补带 + 资产树。
- **右栏（约 38%）**：选中项的卡片 —— 大图（截图）、名称、类型/来源、校验状态、定位全文
  （可复制）、引用清单、动作按钮。没有选中时显示一句「选一个元素看详情」，不空占空间。
- 窗口宽 < 1100px 时右栏折叠成行内展开（同一份 DOM 换 class，不做两套渲染）。

### 行：单行 + 悬停动作

```
[32×32 缩略图] 名称 [桌面] [3 处]                         ⋯
```

- 行高从 ~60px 降到 40px，一屏从 4 行变 8–10 行；
- 选择器不再独占第二行（改成 hover 全文 / 右栏全文），这是行高减半的主要来源；
- 常驻动作只剩「⋯」，hover 或键盘 focus 时浮出「详情 / 校验 / 删除」三个 32×32 图标位；
  删除永远排在最后且与其它两个隔 6px；
- **勾选框改为 hover/focus 才出现**（或按住 Ctrl 多选）——常态下把「5 个可点目标」降到 2 个；
- 键盘：↑↓ 移动、Enter 看详情、Delete 走删除护栏（有引用时弹说明框，行为不变）。

### 视觉层级

- **缩略图是主锚点**：32px 圆角方图 + 1px 边框；无图显示类型图标占位，不显示破图；
- **类型徽章**：网页 / 桌面 / 图像 三个语义色，新增 token：
  `--kind-web: var(--info)`、`--kind-desktop: #6f42c1` 一类的**语义变量**（写进 `:root`，
  不在组件里写十六进制）；
- **校验状态点**：绿=上次命中、红=上次失败、灰=没校验过，只在有 `lastVerifiedAt` 时出现，
  tooltip 带时间与命中数；
- 分组头：字号 12px + 左右贯通底色 + 计数徽章，与资产行的 40px 行高拉开对比；
- 数字统一用 `font-variant-numeric: tabular-nums`，避免「3 处引用」宽窄跳动。

### 待补带

待补元素从列表里提出来，做成左栏顶部一条可折叠带（默认展开，标题带数量徽章）。
它的语义是「这件事还没做」，与「库存里有什么」必须分开，否则用户永远在两种心态间切换。

### 动作去重

右键菜单保留（长期用户习惯），但**行内与菜单必须同源**：一份动作表
`ELEMENT_ACTIONS`，行内与菜单都从它渲染，避免现在「行内有详情/校验/删除、菜单里另有
复制/移动/查引用」的两套清单。

## 需求三：图像资产独立 tab

### 分流依据

磁盘分开之后，清单也分两个 RPC，别让元素面板再拿一遍图像资产：

- `rpa.listElements` → 只扫 `elements/*.json`（语义不变）
- `rpa.listImages` → 扫 `images/*.json`，返回结构与 `listElements` 一致
- `rpa.elementCandidates` → 两处合并（参数面板要能选图像资产）

两个清单都返回 `driver`（`api.py:2537` 现在没返回，缺字段按 `None` 处理），前端据此
加类型徽章。分组共用 `element_groups.json`，图像资产也能进分组。

### 存储：独立 `images/` 目录（已拍板）

```
<flow>/elements/<name>.json + elements/shots/<hash>.png   ← 语义元素 + 它的截图
<flow>/images/<name>.json                                 ← 图像资产（模板仍内联）
```

图像元素现在就是 `elements/<name>.json`，`driver == "image"`，模板 base64 内联在
locator 里（`image_proposal.py:151`），运行期由 `executors/image_target.py` 独立定位
（`DesktopLocator` 是 `extra="forbid"` 的严格模型，图像 locator 根本不走它）。
所以**换目录不影响执行层**：执行器只吃流程 JSON 里解析出来的 locator，不关心它在哪个文件。

搬目录的真正成本是「按名字找文件」这件事现在有四个假定 `elements/` 的地方。
一律收口，不许各处自己拼路径：

| # | 位置 | 现在 | 改成 |
|---|---|---|---|
| 1 | `runtime/element_refs.py:112` `make_element_reader` | `root = flow_dir/"elements"` | 先查 `elements/`，再查 `images/` |
| 2 | `executors/browser.py:396` 元素自愈 | `_flow_dir/"elements"` 扫 `*.json` | 走同一个 reader / 目录清单函数 |
| 3 | `cli.py:172/557` 元素写读 | `WorkflowStore(folder/"elements")` | 按资产类型选目录 |
| 4 | `api.py:2234` `_elements_dir` | 单目录 | 拆 `_kind_dir(path, kind)`，新增 `_asset_dirs(path)` |

新增一个极薄的 `element_store`（`rpa_core/runtime/` 或 `capture/` 下）：

- `asset_dirs(flow_dir) -> [elements_dir, images_dir]`
- `read_asset(flow_dir, name) -> dict | None`（先 elements 后 images）
- `write_asset(flow_dir, name, doc)`（按 `driver` 决定落哪个目录）
- `list_assets(flow_dir) -> [(name, doc, dir)]`

**同名禁止跨目录**：保存、改名、复制的重名检查要同时看两个目录，否则
`read_asset` 的先查顺序会把一份资产永久遮住（用户看到的是「改名失败/引用指错」）。
这条要有判据。

### 模板是否落文件：本期不做

搬目录时**模板继续 base64 内联**在 locator 里。原因：改成 `images/<hash>.png` 要让
`image_guard` 的形状校验、`image_target.locate_image` 的读取、校验链路、以及
「重捕后旧文件怎么办」一起动，属于模板格式变更，与「换个 tab」不是一件事。
代价是 `images/*.json` 每个几十 KB（模板 32×32 PNG ≈ 1–3 KB base64，实测可控）。

若以后要落文件，那时再单开一单，按「新格式硬切、旧资产重捕」处理（`invariants.md` #15）。

### 图像资产面板

- 顶部工具条：搜索 + 「按图捕获」（**从元素资产移过来**）+ 全部校验；
- 主体：网格卡片（96×96 缩略图 + 名称 + `179×19` 尺寸标注），每行按容器宽度自适应；
- 点卡片 → 右栏（或浮层）看大图 + 模板信息（尺寸、DPI、`sourceRect`、`minConfidence`、
  上次命中数）；
- 卡片右键菜单与元素资产同一份 `ELEMENT_ACTIONS`（复制/移动分组/查引用/删除都通用）；
- 空态：一句话 + 「按图捕获」按钮，不放装饰图。

元素资产面板的工具栏只留「捕获元素」，`#elemCaptureImage` 整块移进图像资产面板
（`startCapture(null, true)` 的调用不变）。

### tab 顺序与徽章

`元素资产 | 图像资产 | 全局参数 | 运行日志`，两个资产 tab 各带数量徽章。
`DOCK_TABS` 数组加一项即可（`main.js:93`），`activateDockTab` 不用改。

## 需求四：节点参数里「选元素」的交互

### 现状

`buildElemPick()`（`main.js:800`）+ `fillElemPick()`（`786`）就是一个原生 `<select>`：
`rpa.elementCandidates` 回来后每个资产一个 `<option>`，文本 `名字（网页/桌面）`。
六个问题：

1. 资产一多就是长下拉，**没有搜索**，只能滚；
2. 不知道是哪个应用/哪个网站的控件——`processName`/`winTitle` 清单里都有，但界面没用；
3. 图像资产混在同一列表，名称上分不出来（搬进 `images/` 后更必须分开标）；
4. 已引用态只有一行「已引用元素「X」」，除了「换一个」没有别的动作，也不能跳去元素库看它长什么样；
5. **没有「就地捕获并绑定」**：必须先切到元素库抓一个、再回来选，两次上下文切换；
6. 待补元素（流程里声明了但还没抓）根本不在候选里，用户打开看到的是空下拉。

### 设计：可搜索的组合框

```
参数：用户名
┌──────────────────────────────────────────┐
│ 搜索名称 / 应用 / 选择器                  │
│ ┌ 待补 1 ───────────────────────────────┐ │
│ │ !  登录按钮        第 2 步     [捕获] │ │
│ ├ 元素资产 8 ───────────────────────────┤ │
│ │ ▣  搜索框          桌面 · Weixin.exe  │ │
│ │ ▣  用户名输入框     网页 · xhs.com  3处│ │
│ ├ 图像资产 3 ───────────────────────────┤ │
│ │ ▣  图像1           32×32             │ │
│ └───────────────────────────────────────┘ │
│ + 捕获新元素（完成后自动绑定到这个参数）    │
└──────────────────────────────────────────┘
```

- 输入即过滤：名字 + 应用名 + 窗口标题 + 选择器，不区分大小写；
- ↑↓ 移动、Enter 选中、Esc 收起；输入法组字中（`isComposing`）不抢键——与
  `elemDialogName` 的处理一致；
- 每项：缩略图 20×20 + 名称 + 类型徽章 + 来源（`processName` 或域名）+ 引用数；
- 分组头不可选；空组不显示；
- **已引用态**：字段下方一行摘要「搜索框 · 桌面 · Weixin.exe」+「换一个」「在元素库中查看」
  （后者切到对应 tab 并选中该资产）；
- **底部「+ 捕获新元素」**：起捕获时带上当前 `param_key`，保存成功后直接写
  `nodeRefs[key]` 并重绘表单——省掉「抓完再选一次」；
- 空态：只显示待补项与「+ 捕获新元素」，不显示空下拉。

### 后端

- `rpa.elementCandidates` 合并 `elements/` 与 `images/`，返回补 `driver`（清单已有
  `screenshot`/`processName`/`winTitle`/`refCount`，够 UI 用）；
- 「选中只写 `nodeRefs`、随保存全量提交」这条契约不动——前端仍然不写参数值；
- 就地捕获绑定复用 `rpa.commitElement`，不新增写盘路径。

### 判据

- 候选必须带 `driver` 与来源字段（缺了就渲染不出徽章，不能靠前端猜）；
- 过滤是纯前端：输入「微信」后只剩 2 项，对应分组头也要消失（断言名字集合）；
- 键盘：↓ 两次 + Enter 后 `nodeRefs` 有该键；
- 「+ 捕获新元素」起捕获时带 `param_key`，保存成功后 `nodeRefs` 自动有该键；
- 图像资产出现在候选里且带类型标注（跨目录合并的接线判据）。

## 判据影响（改 UI 必踩，先列清）

现有脚本把这些写死了，重排会红；**红得对**，要跟着改判据，不是改回去：

| 文件 | 位置 | 现在断言 | 要变成 |
|---|---|---|---|
| `scripts/check_element_pick_ui.mjs` | 132–135 | 捕获按钮恰好 `elemCapture`+`elemCaptureImage` 且都在工具区；白名单三项 | 按 tab 归属断言：元素面板 1 个、图像面板 1 个；白名单加新按钮 |
| `scripts/check_element_pick_ui.mjs` | 104 | `.elemCapture {` / `.elemFill, .elemAct {` 必须存在 | 类名保留或同步改（不要静默删样式） |
| `scripts/check_element_library.mjs` | 247–266 | 工具区在 `elemBody` 外、只有两个捕获按钮 | 改为「两个 tab 各自有工具栏」，按图按钮在图像面板 |
| `scripts/check_element_library.mjs` | 305 | 资产行 `详情/校验/删除` 三动作 | 三动作仍在 `ELEMENT_ACTIONS` 里，但常驻 DOM 只有 `⋯`；断言「动作表三项 + 菜单能渲染」 |
| `scripts/check_element_library.mjs` | 514–528 | 搜索/全部校验结构 | 搜索改为每 tab 一份（id 不能重复 ⇒ 派生 id） |

目录拆分另有两条必红的判据（不是 UI 判据，是数据判据）：

- **跨目录同名禁止**：`elements/` 已有「A」，往 `images/` 再存「A」必须 `NAME_TAKEN`；
  改名、复制同样查两个目录。否则 `read_asset` 的先查顺序会把其中一份永久遮住。
- **解析回退**：只有 `images/` 里有该资产时，运行期 `resolve_element_refs` 仍能解析出值
  （否则图像资产一旦被流程引用就 `ELEMENT_REF_MISSING`）。这条要直接钉
  `make_element_reader`，不能只钉 api 层。

新增判据（每条都要能红）：

1. **分流行数**：3 条资产（web / desktop / image），`listElements` 只回前两条、
   `listImages` 只回第三条；断言名字集合相等，不是「行数 > 0」。
2. **截图落盘**：替身 `grab_window` 返回固定图，commit 后 `elements/shots/<hash>.png` 存在、
   PNG 尺寸等于 crop 尺寸、`screenshot.hash` 与文件内容 sha256 前 16 位一致。
3. **截图失败不挡入库**：替身返回 `None`，commit 仍 `ok=true`、`screenshot=null`。
4. **几何复用**：断言桌面腿调用了 `box_in_window`、浏览器腿调用了 `browser_crop_in_window`
   （打桩计数），防止有人另写一套换算。
5. **改名/复制后截图仍可读**：改名后 `listElements` 里的 `screenshot.file` 仍存在。
6. **删除降权**：默认 DOM 不含删除按钮（按 `aria-label` 找不到），hover 后出现——
   这是「误删防护」的行为判据，不是样式判据。
7. **图像 tab 空态**：没有图像资产时给专门文案 + 捕获按钮，不是「没有元素」。
8. **候选跨目录合并**：`elementCandidates` 同时返回 `elements/` 与 `images/` 的资产，
   且每项带 `driver`。
9. **就地捕获绑定**：带 `param_key` 起捕获并保存后，`nodeRefs` 自动含该键。

契约测试：新增 `tests/contract/test_element_shot.py`（截图形状、失败降级、内容哈希）
与 `tests/contract/test_element_store.py`（两目录读取、同名禁止、解析回退）。

## 分期

| 期 | 内容 | 独立价值 | 风险 |
|---|---|---|---|
| S1 | 截图入库（后端）+ 契约 | 资产有图可看，后三期的基础 | 低；失败全部降级为 null |
| S2 | 元素资产面板重排（左右栏 / 行卡片 / 悬停动作 / 待补带） | 直接解决「不便操作」 | 中；判据要成组改，白名单别漏 |
| S3 | `images/` 目录 + 图像资产 tab（store 收口 / 网格 / 按图捕获迁移） | 解决诉求 3；数据与展示同时分开 | 中高；四处 `elements/` 假定必须全改，漏一处就是「图像资产运行期解析不到」 |
| S4 | 参数面板选元素（组合框 / 分组 / 就地捕获绑定） | 解决「指令里选元素」 | 中；候选合并依赖 S3 |

顺序 S1 → S2 → S3 → S4。S3 与 S4 之间有硬依赖（候选要能列出 `images/`）。

## 不做的事

- 不做模板迁移、不改造旧资产（`screenshot: None` 保持 None）；
- 不把图像模板从 base64 改成文件引用（见需求三「模板是否落文件」）；
- 不做「校验时自动重拍截图」——本期只截捕获那一刻；
- 不做截图清理器（孤儿 png 先留着）；
- 不改桌面 Qt 编辑框（`capture_kit/edit_dialog.py`）——那套是 win32 原生控件，与这份 web 面板无关。

## 决议（2026-11-19）

1. **元素截图跟着仓库提交**：不做 `.gitignore`。截图是流程的一部分，别人拉下来能直接看到
   元素长什么样。代价是仓库体积与截图内容公开——捕获时拍到什么就提交什么。
2. **图像资产独立 `images/` 目录**：文件层分开，`elements/` 只放语义元素与它们的截图。
   按名字找资产这件事收口成 `element_store`，四处假定 `elements/` 的地方全部改走它。
3. **元素面板左右两栏**：列表 + 选中项详情；窗口窄于 1100px 回落单列。
4. **新增第四项**：节点参数里选元素改成可搜索组合框，并把「就地捕获并绑定」做进去。

## 实施记录（2026-11-19）

维护者当日的追加判断：**图像库（图像指令用的模板）与元素缩略图是两个概念**，前者
目前还没有指令、先搁置。所以 S3 不做，S1/S2 先落地。

### S1 捕获即截图（已完成）

| 位置 | 改了什么 |
|---|---|
| `screen_shot.element_shot_from_descriptor()` | 整窗截图 → 按既有换算裁到元素 → PNG（长边 >512 缩到 512） |
| `screen_shot.store_element_shot()` | 写 `<elements>/shots/<sha256 前16>.png`，返回**相对路径字符串** |
| `screen_shot.read_element_shot()` | 读回字节，**限定在 `shots/` 内**（挡路径穿越） |
| `screen_shot.element_shot_payload()` | 转 data URL（按需） |
| `api._hold_captured()` | 捕获那一刻就拍（此时桌面确认框还没盖上来），存进 `entry["shot"]` |
| `api._do_commit()` | 入库时才写盘；编辑/改名路径没新图 ⇒ 保留旧图 |
| `api.list_elements()` | 多回一个 `driver` 字段（S3/S4 分流用，本期先透出） |
| `api.element_shot()`（新 RPC） | 按名字取 data URL，**不进 listElements 批量响应** |

两处与原方案不同，都是写代码时撞出来的：

1. **`screenshot` 是字符串不是对象**。`ElementDescriptor.screenshot: str | None`
   （M92/P1 对齐影刀），而 `cli.py` 的 `element validate` 用同一个模型校验盘上文件——
   塞 dict 会让每个新捕获的元素被判非法文档。宽高不存，图里就有。
2. **必须重跑 `app/gui` 的 `npm run build`**。工作台托管的 `gui/dist` 才是真正跑的那份，
   `apps/xhs-workbench/tests/test_editor_globals.py::test_dist_bundle_is_rebuilt_from_source`
   会把「源码有、产物没有」的 RPC 名逐个揪出来（`rpa.elementShot` 当场被抓）。

### S2 面板重排（已完成）

- 左列表 + 右详情（`#elemSplit` / `#elemDetail`），<1100px 隐藏右栏回落单列；
- 行高 60 → 40px：缩略图 28px + 名字 + 元信息**单行截断** + 引用徽标 + 悬停动作；
- 行内三个动作默认 `opacity: 0`，hover / 聚焦 / 选中时浮现（删除仍是最后一个）；
- 右栏：大图、名字与类型、所属窗口、引用数、捕获时间、**完整定位原文**（`<pre>` 不截断）。

★ 那条「选择器独占一行、允许折行」是 M93 为「看不全」加的补丁，本轮**故意撤回**：
完整定位改为常驻右栏，行里只需要「能认出是哪个」。这不是没看到旧反馈。

### 判据（都做过负向验证）

- 新增 `tests/contract/test_element_shot.py` 18 条：几何（复用两个换算，打桩计数）、
  内容哈希命名与去重、读回、路径穿越、四类降级；
- `scripts/check_element_library.mjs` 28 → 33 条：结构（详情栏位置/无静态按钮）、
  样式（窄屏回落、动作隐身、单行截断）、行为（点行填右栏原文、只对带图资产拉图并真塞 `<img>`、删后收敛）；
- `probe_m102_negative.py` 9 个注入：失败集逐项相等 + md5 逐字节还原。

### 过程中抓到的两个假判据

1. **路径穿越用例恒绿**：`secret.png` 被写在元素目录**里面**，于是 `../secret.png`
   压根不存在，去掉护栏照样返回 None。改成把文件放到目录外 + 补一条「合法路径读得回来」的正例。
2. **`node.isConnected` 写成取反**：最小 DOM shim 里没有这个属性（undefined），
   `if (!node.isConnected) continue` 会让缩略图永远不出现；改成 `=== false`。

### 还没做

- S3（`images/` 目录 + 图像资产 tab）：**搁置**——图像库对应的是图像指令，目前没有；
- S4（参数面板选元素改组合框 + 就地捕获绑定）：候选合并原本依赖 S3，S3 搁置后
  可以只在 `elements/` 内做，需要单独拍板；
- 旧资产（`screenshot: null`）不迁移，界面显示首字母占位。

## S2b 网页元素为什么没缩略图（2026-11-19，S1 上线后真机反馈）

维护者测完 S1：「桌面元素有截图了，网页元素没有呢？」。真机 trace 一读，是三处各错
一点叠出来的，每一处单独看都「像是对的」：

1. **`viewport` 在描述符顶层**（`content.js::buildDescriptor` 与 `selector` 平级）。
   ppx 的 `_hold_captured` 先把它归一化成 `ElementDescriptor`，顶层未知键被
   `extra="ignore"` 静默丢掉；`element_shot_from_descriptor` 又只读
   `selector.viewport` ⇒ 视口恒 `None` ⇒ `browser_box_in_window` 返回 `None` ⇒
   `element_shot no_box`。而 S1 的契约用例和接线探针**都把 viewport 写在
   `selector.viewport`**——判据覆盖的是错的形状，所以一路绿。
2. **窗口几何在 Edge 开侧栏时崩**：`browser_viewport_in_window` 用
   `border=(outerWidth-width)/2` 当**垂直**边框。真机 `viewport 1628 / outerWidth 1920`
   ⇒ border=146 ⇒ `origin_y=(1040-996-146)×1.008=-102.85`，元素被推成负 y，判成
   「滚出窗口」。`capture_shot` 那边同一份数也给出 `has_crop=false`。
3. **扩展从来没有要过视口图**：host 只能用整窗截图，而整窗截图天生要猜「视口在窗口里
   偏多少」。

### 改法（拍板：走视口图，含兜底）

- `content.js`：点击那一刻**先收掉覆盖层**（否则红框/hint 被拍进缩略图）→ 向
  background 要一张视口图（800ms 超时兜底，background 可能收到不回）→ 附在
  `rpa-capture-result` 上。
- `background.js`：新增 `rpa-shot-request` → `chrome.tabs.captureVisibleTab`，转
  JPEG q=0.9（1920×1040 的 PNG base64 1~3MB，native messaging 扛不住；坐标不变）。
  **`rpa-capture-result` 分支必须显式透传 `viewportShot`**——只挑
  `descriptor/contentBuild` 两个字段的话图在这里就没了。
- `extension.py`：`_take_viewport_shot` 挂成
  `captureShot={dataUrl, space:"viewport"}`；没有图就退回窗口截图（老扩展/无权限）。
  标记 `space` 是必需的：同一字段两种几何口径，消费方得能分辨。
- `screen_shot.py`：新增 `viewport_box_in_image`（rect 本来就是视口坐标 ⇒ 只乘
  「图宽/视口宽」，**没有第二个原点**）；抽 `_clamp_box` 给两条腿共用钳位；
  浏览器腿算不出框时**兜底整图缩略图**（桌面腿不兜底：rect 来自 UIA，缺了就是元素没了）。
- `descriptor_viewport`：读顶层，回退 `selector.viewport`（既有 `capture_shot` 一直认它）。
- 扩展版本 0.9.4（manifest / background / content 三方一致）。

### 判据（都做过负向验证）

- `tests/contract/test_element_shot.py`：+视口图用例（用真机那组数：视口 800 / 窗口 1920）、
  +整图兜底用例、+桌面腿不兜底用例；
- `tests/contract/test_extension_viewport_shot.py` 3 条：挂图带 `space`、无图退回窗口截图、
  非图载荷不当图；
- `scripts/check_capture_shot.mjs` 18 条（新，已进门禁）：content 取图函数四种回包 + 超时、
  capture 体的顺序与字段、background 透传与请求接线、两端 type 常量对账；
- `check_capture_lifecycle.mjs` 适配 async：`gesture()` 现在 await 一个 tick
  （捕获要等截图，同步断言会读到「还没发生」）；
- `probe_m102_s2b_negative.py` 7 个注入，逐项断言失败集相等 + md5 还原：
  `selector-only` / `space-ignored` / `no-fallback` / `host-drops-shot` /
  `content-no-shot` / `host-forgets` / `overlay-first`。其中
  `host-drops-shot`、`content-no-shot`、`host-forgets` 三条**纯函数层全绿**，只有接线探针
  或 JS 通道判据会红——「纯函数层绿 ≠ 接线层绿」的现成样本。

### 仍需真机确认

改完要**重载扩展**（Load unpacked 是快照，不自动重载）并重启 ppx，然后抓一个网页元素看
右栏大图。判断依据：trace 里 `capture_shot mark=viewport` 出现，且 `element_shot mark=ok`
的 `crop` 落在视口坐标范围内；若出现 `fallback_full` 说明几何仍没算出来（此时有整图，但
不是特写）。

## S2c（现场复验后）：视口图没有 imageSize，消费侧静默放弃

维护者重载扩展 0.9.4 后再抓网页元素，「还是没有截图」。trace 铁证：
`capture_shot mark=viewport` 有、`element_shot` 一行没有（连 `no_box` 都没有）。

根因不在几何，而在**字段契约**：`_take_viewport_shot` 只挂 `{"dataUrl", "space"}`，
`element_shot_from_descriptor` 却硬性要求 `imageSize`，缺失就 `return None` 且不打任何
trace。S2b 的探针和契约都自己塞了 `imageSize` —— 判据形状 ≠ 生产接线形状，又是假绿。

改法：消费侧新增 `data_url_image_size`，从 dataUrl 只读图像头量尺寸；仍量不出来时打
`element_shot no_image_size` 再放弃。上游不必多传一个自己也未必知道的字段。

判据：
- 两个探针改成真机形状（captureShot 只有 dataUrl + space），**先证红再证绿**；
- 新契约 `test_element_shot_viewport_shot_without_image_size`；
- `probe_m102_s2c_negative.py` 1 注入：期望 pytest 1 / node 0 / 两探针红；
- 既有 7 注入的期望集补上新用例（同一因连带，非误红）后复跑；
- 全量 1076 passed。本次只改 Python，**需重启 ppx**，扩展不必再动。
