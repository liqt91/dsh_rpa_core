# 浏览器元素能力的 MVP 边界

自研扩展是浏览器执行/捕获的唯一通道（ADR 0013/0015）。它给了我们三样别家没有的东西——
真实用户浏览器与登录态、无 CDP、无 `navigator.webdriver`——代价是**放弃了 CDP 才能做的事**：
真实输入注入、任意 frame 注入、文件选择、原生对话框拦截。

本文件把这条边界写清楚，目的是两点：① 撞上时能立刻判断「这是边界还是缺陷」；② 不再被
反复当成缺陷追问（M28 S4 的验收要求）。**下面每一条都标注了代码依据或「待真机确认」**，
不要凭印象增删。

判断口径：

| 类别 | 含义 | 处理 |
|---|---|---|
| **设计边界** | 通道形态决定的、明确定案不做 | 接线时绕开；要改先改 ADR |
| **未实现** | 已声明/曾计划，本期未覆盖 | 撞上会拿到显式错误码，不会静默错 |
| **参数漂移** | manifest 声明了但实现没消费 | **属缺陷等级**，见 §3 清单 |
| **待确认** | 结论未实测，别当事实用 | 真机验证后再写进本表 |

## 1. 边界矩阵（一眼表）

| # | 边界 | 现象 | 类别 | 绕法 |
|---|---|---|---|---|
| 1 | **iframe**：捕获与执行都只覆盖主 frame | iframe 内元素框不到、选择器命不中（`ELEMENT_NOT_FOUND`） | 设计边界 | 见 §2.1 |
| 2 | **shadow DOM**：选择器不穿透 shadow root | 自定义组件内部元素取不到 | 设计边界 | 用 host 选择器；或 `executeScript` 进 `shadowRoot` |
| 3 | **canvas / 无 DOM 控件**：只有元素级事件，没有坐标点击 | canvas 里画出的按钮无法定位 | 设计边界 | 找透明 DOM/无障碍层；或走桌面通道 |
| 4 | **不可信事件**：所有鼠标/键盘事件都是 `dispatchEvent` 合成（`isTrusted=false`） | 校验 `isTrusted` 的站点拒绝；浏览器原生行为（打开新窗口等）不触发 | 设计边界 | 桌面通道发真实输入 |
| 5 | **键盘操作**：无 `pressKey` 命令（只有 `input` 内的 Enter 与 click 的修饰键） | 无法按 Ctrl+A / Tab / Esc | 未实现 | `executeScript` 自行派发（仍是不可信事件） |
| 6 | **文件上传**：`browser.upload` 未实现 | `COMMAND_NOT_FOUND` | 未实现 | 见 §2.6 |
| 7 | **下载落盘**：`browser.download` 未实现 | `COMMAND_NOT_FOUND` | 未实现 | 浏览器默认目录 + `data.fileExists` 轮询 |
| 8 | **原生对话框**：`browser.handleDialog` 未实现 | `alert` 阻塞页面 JS，后续命令超时 | 未实现 | 见 §2.8 |
| 9 | **新标签页/弹窗不被自动接管** | 点开后后续命令仍作用在旧标签页 | 设计边界 | `browser.listPages` + `browser.attach` |
| 10 | **嵌套滚动**：`browser.scroll` 不给 selector 只滚 window | 内层 `overflow` 容器没滚动 | 设计边界 | 把容器选择器显式传给 `scroll` |
| 11 | **`waitFor` 的可见性口径**（`state=visible/hidden` 用的是 `count` 的轻量可见判定） | `opacity:0` 或落在视口外的元素：`waitFor(visible)` 判定「等到」，动作却报 `ELEMENT_NOT_VISIBLE` | 语义差异（见 §2.11） | 把 `waitFor` 当「已进入布局」信号；严格判定用 `getPosition` |
| 12 | **截图口径**：只截「窗口当前可见标签页」的可见区（M29 已删除 `fullPage`/`selector`） | 非整页、不能只截某元素；目标页在后台时**静默截到别的页** | 能力缺口（见 §2.12、BACKLOG「整页/元素截图」） | 让目标页在前台；整页需求带设计路径 |
| 13 | **后台标签页** | 部分操作不可靠；截图/焦点相关行为未保证 | 待确认（已定案不做焦点模拟） | 让目标页在前台 |
| 14 | **受保护页面**：`chrome://`、`edge://`、扩展商店页 | 注入被浏览器拒绝 | 设计边界 | 无（安全设计） |
| 15 | **元素命中不唯一** | `querySelector` 取第一个；自愈候选同理 | 设计边界 | 用更稳的选择器 / 缩小候选；证据里有 `matchedCount` |
| 16 | **自愈只认本流程元素资产** | 资产缺失或不匹配时按失败处理，不猜 | 设计边界 | 改版后重新捕获元素 |
| 17 | **CSS `:hover` 伪类**（待真机确认） | `hover` 派发了 mouseover/mousemove，但依赖 `:hover` 展开的菜单可能不出现 | 待确认 | 先真机验证再定论；必要时改用别的交互路径 |

## 2. 逐项说明

### 2.1 iframe（主 frame 限定）

- **依据**：`extension/manifest.json` 的 content script `all_frames: false`；`background.js`
  的 `pageCall` 用 `chrome.scripting.executeScript({target: {tabId}})`——**没有 `allFrames`、
  没有 `frameId`**（全仓 grep 无 `allFrames`/`frameId`）。捕获与执行因此**对称地**只覆盖顶层文档：
  不是「捕获得到、执行不到」的错配。
- **绕法**：① iframe 内容若有独立 URL（常见于内嵌表单/老系统），直接用 `browser.navigate` 开它；
  ② 同源 iframe 可用 `browser.executeScript` 里 `document.querySelector('iframe').contentDocument`
  自行操作（此时选择器由你在 JS 里写，不走元素资产）；③ 跨域 iframe 在 MVP 内无解。

### 2.2 shadow DOM

- **依据**：`domOp` 用 `document.querySelectorAll(sel)`（不穿透 shadow root）；`content.js`
  捕获同样只用 `document.querySelectorAll`，全仓无 `shadowRoot` / `composedPath` 用法。
- **绕法**：① 直接操作 shadow host（`<my-widget>` 元素本身可点、可读属性/文本）；
  ② `browser.executeScript` 里 `host.shadowRoot.querySelector(...)` + 自行派发事件——
  仅对 **open** shadow root 有效（closed 的连页面 JS 也进不去）。

### 2.3 canvas / 无 DOM 的图形控件

- **依据**：通道里只有 `page.call` 的元素级原语（click/hover/input/…），事件由
  `el.dispatchEvent(...)` 派发；没有坐标点击（无 CDP `Input.dispatchMouseEvent`）。
- **绕法**：① 多数 canvas 应用（地图、图表、在线设计器）带透明 DOM 覆盖层或无障碍层，
  这类可以命中选择器；② 纯 canvas 绘制且无 DOM 层的，走**桌面通道**
  （`desktop.uia` / `desktop_win32` 支持真实坐标点击，代价是窗口必须前台、且拿到的是屏幕坐标）。

### 2.4 事件可信度（`isTrusted`）

- **依据**：`clickElement` / `fire(el, "mousedown"|"click"|...)` / `pressEnter` 全部是
  `new MouseEvent(...)`、`new KeyboardEvent(...)` 派发。合成事件 `isTrusted === false`，
  且**不会触发浏览器的默认行为**（例如点 `<a target="_blank">` 不会真的开新窗口、
  表单必填/约束校验不参与）。
- **影响**：靠 `event.isTrusted` 做风控/防刷的站点会拒绝这类点击；「点了没反应」多为这一条。
- **绕法**：桌面通道发真实输入（UIA/Win32 的输入是系统级真实事件）。

### 2.5 键盘面缺失

- **现状**：命令目录里**没有** `pressKey` 类命令（`commands/browser/` 30 条已确认）。
  键盘相关只出现在：`input` 的 `clickBeforeInput`、输入完成后按 Enter（`pressEnter`）、
  `click` 的 `modifiers`（那是鼠标修饰键）。
- **绕法**：`browser.executeScript` 自行 `dispatchEvent`（仍不可信事件，见 §2.4）。
- **定位**：属未实现（不是设计禁项）。若要做，仍需接受「不可信事件」这一前提。

### 2.6 上传（`browser.upload`）

- **现状**：显式 `COMMAND_NOT_FOUND`：`该能力扩展暂未实现（自研扩展单通道下本期未覆盖）`。
- **原因**：给 `<input type=file>` 真正塞文件需要 CDP `DOM.setFileInputFiles`（或在页面里伪造
  `File` 对象），而我们是**刻意移除 CDP** 的通道（ADR 0013）。
- **绕法**：① 半自动：流程走到上传处交给人工选择（GUI 运行浮窗停在此步）；② `executeScript`
  用 `DataTransfer` + `new File([...])` 塞进 `input.files` 并派发 `change`——**部分**站点接受
  （受 `accept`、站点校验、后续提交逻辑影响），不是通用解。

### 2.7 下载（`browser.download`）

- **现状**：显式 `COMMAND_NOT_FOUND`。
- **可行的等效组合**（不需要新能力）：让浏览器按默认目录下载 → 用 `data.fileExists` /
  `data.readText` 轮询目标路径。缺的只是「指定保存目录 + 等下载完成」这类确定性保证。
- 注：manifest 已申请 `downloads` 权限（`chrome.downloads` 可查询/等待下载项），
  补起来成本可控——属**候选**而非边界。

### 2.8 原生对话框（`alert` / `confirm` / `prompt`）

- **现状**：`browser.handleDialog` 显式 `COMMAND_NOT_FOUND`；无 CDP 也就无法接受/关闭原生对话框。
- **现象**：页面里触发 `alert` 会**阻塞该页面的 JS 主线程**，此时 `page.call` 的注入脚本进不去，
  命令会一直等到通道超时（`TIMEOUT`）——这是最容易误判成「通道坏了」的一种。
- **绕法**：① 流程里避免触发；② 若确知会弹，可在触发前用 `executeScript` 覆写
  `window.alert/confirm/prompt`（对页面 JS 有效）；③ 站点自绘的确认框（DOM 实现）不受影响，
  正常用选择器点击即可。

### 2.9 新标签页 / 新窗口 / OAuth 弹窗

- **依据**：会话绑定的是具体 `tabId`（`_ext_sessions[sessionId] = tabId`），扩展不会把新页面
  自动并进来。
- **绕法**：`browser.listPages`（列全部标签页）→ `browser.attach`（`pattern` + `matchBy=url|title`
  + `useRegex`）建立新会话；`attach` 即激活，后续省略 `sessionId` 的命令默认作用在它上面。
- **为什么不做自动跟随**：跟随需要猜（同域新开页？SSO？用户自己开的？），猜错会静默操作错页面。

### 2.10 嵌套滚动容器

- **现状**：`browser.scroll` 无 `selector` 时滚的是 window（`_SCROLL_WINDOW_JS`）；要滚内层
  `overflow: auto` 容器必须把**容器选择器**传给 `scroll`。
- 动作类命令（click/input/…）在预检前会 `scrollIntoView`，**能**把元素从嵌套容器带进视口；
  带不进来（祖先 `overflow` 裁剪/滚动被拦）会显式报 `ELEMENT_NOT_VISIBLE`，不带着错误坐标硬点。

### 2.11 `waitFor` 的四态（M29 已澄清：**并不等价**）

- **依据**：执行器 `_ext_wait_for` 用两个正交开关判命中——
  `want_visible = state in (visible, hidden)`、`want_present = state in (visible, attached)`，
  再交给扩展侧 `count` 的 `args.visible` 过滤：

  | `state` | 计数口径 | 命中条件 | 对标 Playwright |
  |---|---|---|---|
  | `visible` | 可见元素数 | 可见数 > 0 | `visible` |
  | `hidden` | 可见元素数 | 可见数 == 0（**存在但被隐藏也算**） | `hidden` |
  | `attached` | 全部元素数 | 总数 > 0 | `attached` |
  | `detached` | 全部元素数 | 总数 == 0（必须真的不在 DOM） | `detached` |

  `hidden` 会被「存在但不可见」满足，`detached` 不会——**两者不等价**。M28 S4 写的「`hidden` ≡
  `detached`」是**记录错误**，代码本身是对的，此处更正。
- **但两处「可见」口径确实不同（真实差异，未对齐）**：`count` 的 `isVisible` 只看
  client rects + `visibility`/`display`；执行前预检（M28 S2）还额外查
  `checkVisibility({checkOpacity})`、rect 尺寸与**是否在视口内**。于是 `opacity: 0` 或落在视口外的
  元素：`waitFor(state=visible)` 判定「等到了」，紧接着的点击却报 `ELEMENT_NOT_VISIBLE`。
- **绕法**：把 `waitFor(visible)` 当「元素已进入布局」的信号，可操作性交给动作命令的预检；
  需要严格可见性判断时用 `browser.getPosition` 拿 rect 自行判定（多一次往返，见
  `docs/extension-channel-baseline.md`）。

### 2.12 截图是「窗口可见区」

- **依据**：`background.js` 的 `screenshot` 分支用 `chrome.tabs.captureVisibleTab(tab.windowId)`。
- **两个后果**：
  1. **非整页**：需要滚动才能看到的内容不在图里；
  2. **可能截错页**：`captureVisibleTab` 截的是该窗口的**当前激活标签页**。若会话绑定的标签页
     在后台（例如用户切走了），拿到的图是**前台另一个页**——**静默**，不报错。
- **绕法**：确保目标标签页是它所在窗口的当前标签页（`browser.navigate` 默认 `active=true` 建页）。
  整页截图需分段截图 + 拼接，未做。

### 2.13 后台标签页

- 已定案**不做**焦点模拟（`docs/element-self-healing-plan.md` §3 P3：影刀亦无此设计，且我们无 CDP）。
- 已知受影响：截图（见 §2.12）；其余操作在后台标签页上的可靠性**未做保证**。
- **重估条件**：后台标签页可靠性成为真实阻塞场景时再评估。

### 2.14 CSS `:hover` 伪类（待真机确认，别当事实用）

- **现状**：`hover` 派发 `mouseover` / `mouseenter` / `mousemove`（事件构造里**没有带坐标**），
  且 `mousemove` 只派发到元素自身。
- **风险**：CSS `:hover` 是浏览器按**真实指针命中**维护的状态；若合成事件不更新它，则「鼠标悬停
  才展开的下拉/浮层」不会出现，后续 `click` 就会落在没展开的 DOM 上（表现为 `ELEMENT_NOT_FOUND`，
  或点到别处）。
- **怎么验证**（一条命令就够）：在带 `:hover` 展开菜单的页面上依次执行 `browser.hover` →
  `browser.getPosition`（菜单项选择器）。若菜单项仍不可见/不存在，即确认此边界成立。
- 在验证之前，**不要**把「hover 后点不到子菜单」当成缺陷报；也不要据此改 `hover` 的实现
  （改成带坐标的 mousemove 是另一个决定，需要真机对照）。

### 2.15 受保护页面

- `chrome://`、`edge://`、扩展商店页等禁止外部注入（浏览器安全设计），`executeScript` 会被拒。
  现象是一句通道/执行失败，无法绕过——这不是我们的缺陷（`docs/extension-install.md` §6.5
  记录过同一原因的导航限制）。

### 2.16 / 2.17 命中的确定性与自愈来源

- 选择器命中多个时取 `querySelector` 的第一个（命中数进证据 `matchedCount`）。
- 自愈候选来自**本流程目录的元素资产**（`<flowDir>/elements/*.json`），按失败的主选择器反查；
  资产不匹配时按失败处理（`ELEMENT_NOT_FOUND` + `candidatesTried`），**不做语义猜测**。
  跨流程引用不生效。

## 3. 「声明了但没生效」的参数（M29 已收口，并已门禁化）

manifest 是命令契约的唯一事实来源：**声明了参数，GUI 就会渲染成开关、用户就会按说明去用**。
「默认值存在但无作用」= 缺陷（用户以为是开关），**不留第三种状态**：要么实现，要么删掉。
这些**不是边界**，是缺陷。

M28 的处理口径是「参数名是否在实现里出现过」的启发式；它看不见**同名字段在别处出现**
（`cookieGetAll.name` 就是这么漏掉的）。M29 换成**按命令字面量切片的静态门禁**——
`.harness/scripts/check_param_consumption.py`（已进 `check_all.py`），把口径固化下来。

### 3.1 本轮处置（M29，全部已落地）

| 命令 | 参数 | 处置 | 依据 |
|---|---|---|---|
| `browser.click` | `simulateHuman` | **实装** | 此前声明未转发。`true`（默认）= 完整鼠标按键链；`false` = 最短路径 `el.click()`（仅普通左键单击生效——右键/中键/双击/带辅助键时仍走事件链，`el.click()` 表达不了） |
| `browser.click` | `clickPosition` | **实装** | `center`/`random`（元素内偏中心带 15%~85%）。随机点裁剪进「元素 ∩ 视口」，并**用同一个点做遮挡预检**——此前预检看元素中心、事件连坐标都没有（`clientX/clientY` 恒 0），等于「判一个点、点另一个点」 |
| `browser.click` | `modifiers` 的 `Ctrl`/`Win` | **修复** | 同一族漂移：manifest 枚举是 `Ctrl`/`Win`，扩展里比的是 `"Control"`/`"Meta"` → 勾了等于没勾 |
| `browser.cookieGetAll` | `name`/`domain`/`path` | **实装** | 一个都没转发（浏览器级全量返回）。并纠正「支持子串匹配」的错误说明：Chrome 过滤器是 name 精确 / domain 含子域 / path 精确 |
| `browser.cookieGetAll` / `cookieGet` / `cookieSet` / `cookieRemove` | `sessionId` | **修复** | 只用于路由与资源名，从未把 `tabId` 传给扩展 → `tabUrl(undefined)` → `""`，`chrome.cookies.get/set/remove` 拿空 url 调 API、`getAll` 退化成浏览器级全量 |
| `browser.screenshot` | `fullPage`、`selector` | **删除** | `chrome.tabs.captureVisibleTab` 只能截「当前可见标签页的可见区」；元素裁剪与整页拼接都要先在扩展里解码图像（MV3 service worker 无 `Image`/`FileReader`）→ 走 offscreen document 或 CDP。缺口与代价见 BACKLOG「整页/元素截图」。连带的 4 个元素类错误码也从 `errors` 移除（无 selector 后不可能出现） |
| `browser.close` | `forceKill`、`ignoreUnload` | **删除** | 扩展通道的「关闭」= **本地解绑**：不代关用户标签页、不终止用户浏览器进程（也没有它的句柄）；`chrome.tabs.remove` 本身不弹 `beforeunload`。缺口见 BACKLOG「关闭标签页 / 终止我们拉起的浏览器进程」 |
| `browser.waitFor` | `state=hidden` 的细分语义 | **更正记录** | 代码本来是对的（四态由两个正交开关决定），M28 文档写成「与 `detached` 等价」是记录错误，见 §2.11 |

### 3.2 迁移说明（被删除的 4 个参数）

`input_schema` 是 `additionalProperties: false`，所以旧流程若引用过
`fullPage`/`selector`/`forceKill`/`ignoreUnload`，**会在校验期显式失败**
（`Additional properties are not allowed`）。这四个参数从未生效，迁移动作就是**删掉该字段**。
宁可失败也不要静默跑错——「勾了完整页面、实际截了可见区」比报错更坏。

### 3.3 门禁口径（`.harness/scripts/check_param_consumption.py`）

1. 对每条命令取其 executor 的实现文件；
2. AST 解析，把 `inputs.get("x")` / `inputs["x"]` 归到**所在分支比较的字面量命令**上
   （`if command == "browser.click":`、`if command in (...)`）；不在任何命令分支里的读取算
   「通用读取」（`_post_delay` 的 `postDelayMs`、`_match_ext_tab` 的 `pattern`/`matchBy` 等）；
3. 声明的参数必须落在「该命令分支的读取 ∪ 通用读取」内；
4. 执行器里显式返回 `COMMAND_NOT_FOUND` 的命令（`upload`/`download`/`handleDialog`）**整体豁免**：
   整张参数表都是待实现清单，实现后自动纳入检查。

**盲区（如实记，不当形式化证明）**：① 只认字面量键——实现里按变量取（`inputs[key]`）会看不见，
所以实现侧按字面量取值（见 `cookieGetAll` 的过滤器写法）；② 读取写在**不带命令字面量的辅助
函数**里时会算进「通用读取」，让所有命令都通过（如 `waitFor` 的 `state` 读在 `_ext_wait_for` 中）。
负向验证：往 `click.json` 里临时加一个 `deadSwitch`，门禁立刻红（M29 已实测）。

### 3.4 覆盖范围之外的同类风险（M29 只收口扩展通道）

- **桌面 / 数据通道**（`desktop.*`、`python.worker`）的分派不是 `command == "<id>"` 字面量形状
  （走注册表与 helper），静态切片不适用，门禁**如实打印跳过条数**。已知待核对项已登记 BACKLOG
  「桌面/数据通道参数漂移复核」，其中一条值得单独点名：**多条桌面命令声明了 `timeoutMs` 但执行器
  不读**，而 GUI 见到命令自带 `timeoutMs` 就会**隐藏引擎级超时字段**（`gui/param_form.py`）——
  这些节点等于**没有任何可用的超时控制**。
- **两套「可见」口径**未对齐（`count` 的轻量判定 vs 执行前预检的严格判定），见 §2.11。

## 4. 明确不在 MVP 范围（已定案，含理由）

| 项 | 定案 | 理由 |
|---|---|---|
| CDP / `chrome.debugger` 真实输入注入 | 不做 | 与「无 CDP、不可检测性更好」的通道定位冲突（ADR 0013/0015） |
| `input` 的 `mode: "insert"`（select-all + 单次插入） | 不做 | 是速度/框架兼容技巧，不产生按键事件，不是反检测手段；已有 fill/type/clipboard 三档（`element-self-healing-plan.md` §3） |
| 后台标签页焦点模拟 | 不做 | 影刀亦无此设计，且我们无 CDP（同上） |
| 整页/元素级截图 | 未做 | 需分段拼接或 CDP；当前只有「窗口可见区」（§2.12） |
| 多 frame 注入 | 未做 | 需 `allFrames` + frame 内选择器命名空间，属通道协议扩展 |

## 5. 相关文档

- `docs/extension-channel-baseline.md` —— 通道往返数与耗时基线（性能侧边界）
- `docs/element-self-healing-plan.md` —— 自愈与预检的调研依据、已定案不做项
- `docs/capture-transport.md` —— 捕获传输（含「网页正文无 UIA 退路」等实测结论）
- `docs/extension-execution-plan.md` —— 扩展执行通道与命令覆盖
