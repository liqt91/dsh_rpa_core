# M29 浏览器命令参数漂移收口

状态：`done`

关联：M28（S3 修 `input` 两参数、S4 复查出 4 处残留漂移）、ADR 0013（浏览器执行收敛为扩展单通道）、
`docs/element-mvp-boundaries.md` §3（漂移清单与复查脚本）、BACKLOG「浏览器命令参数漂移收口」
前置：无。可与 `M26-flow-inputs-editor.md` 并行推进（本里程碑只动命令面与扩展通道）

## 背景

manifest 是命令契约的唯一事实来源：**声明了参数，用户就会在 GUI 里看到开关并按它的说明使用**。
M28 S4 用「参数名是否在实现里出现」的启发式复查 30 条 `commands/browser/*.json`，发现 4 处
「声明了不生效」。本里程碑做的第一件事是把这个启发式**换成按命令切片的静态审计**（见 S4），
结果又多挖出一处同类漂移（`cookieGetAll` 的过滤器）。

漂移的两种处理口径（BACKLOG 已定）：**实现它**，或**从 manifest 删掉**。不允许第三种状态
（默认值存在但无作用 = 用户以为是开关）。

## 任务（切片）

- [x] **S1 实装 `click.simulateHuman` / `clickPosition`**（2026-09-21 done）
  - 扩展侧新增纯函数区 `[click-helpers]`（`scripts/check_click_helpers.mjs` 切片求值，已进门禁）：
    `clickPoint`（center/random + 裁剪进「元素 ∩ 视口」，空交集返回 null）、`modifierFlags`
    （`Ctrl`/`Win` → `ctrlKey`/`metaKey` 归一化）、`simulateHumanEnabled`、`useProgrammaticClick`。
  - `clickPosition`：`center`（默认）/`random`（偏中心带 15%~85%）；**点击点与遮挡判定用同一个点**
    （此前预检看中心、事件坐标恒 0），事件现在带真实 `clientX/clientY`。
  - `simulateHuman`：`true`（默认）= 完整鼠标按键链；`false` = `el.click()`，且**只在普通左键单击时**
    走最短路径（右键/中键/双击/带辅助键仍走事件链——否则就是新的静默忽略）。
  - **顺带修掉同族第三处漂移**：`modifiers` 的 manifest 枚举是 `Ctrl`/`Win`，扩展里比的却是
    `"Control"`/`"Meta"` → 勾了等于没勾。
  - manifest 说明改写（删掉「（待元素被遮挡时可用）」——遮挡是显式 `ELEMENT_COVERED` 失败，
    该说明会把人引向错误解法；并写明两者都是合成事件、不能绕过风控）。
  - 证据：`scripts/check_click_helpers.mjs`（38 项，含交叉校验 Python 转发与 manifest 默认值）+
    `test_browser_input_params.py` 3 项（含端到端到 `page.call` 载荷）+ `check_precheck_helpers.mjs`
    新增 4 项（遮挡判定用传入点、坐标非法回退）。
- [x] **S2 cookie 族参数传导修复**（2026-09-21 done）
  - `cookieGetAll` 的 `name`/`domain`/`path` 真正转发（此前一个都没转发 → 浏览器级全量返回）；
    过滤器逐字面量取值（静态门禁按字面量判定，循环变量会让漂移藏回去）；证据里带生效的过滤器名。
  - 四个 cookie 命令把**会话绑定的 `tabId`** 传给扩展（此前从未传 → `tabUrl(undefined)` → `""`，
    `chrome.cookies.get/set/remove` 拿空 url 调 API）。
  - 纠正描述里的错误承诺：「支持子串匹配」→ Chrome 过滤器真实语义（name 精确 / domain 含子域 /
    path 精确）；`sessionId` 补上「同时决定 cookie 作用域 URL」的说明。
  - 证据：`test_browser_input_params.py` 3 项（过滤器 + tabId + 无过滤器仍按标签页限域）。
- [x] **S3 清掉单通道下无法兑现的参数**（2026-09-21 done）
  - `screenshot.fullPage` / `screenshot.selector` 删除（`captureVisibleTab` 只能截可见区；
    裁剪/拼接要解码图像，MV3 SW 无 `Image`/`FileReader`）；连带移除 4 个不可能出现的元素类错误码。
  - `close.forceKill` / `close.ignoreUnload` 删除（close=本地解绑；不代关用户标签页、不杀用户
    浏览器进程；`tabs.remove` 也不弹 `beforeunload`）。
  - 代码留注释说明「为什么这里没有这些参数」，缺口与设计路径进 BACKLOG（两条独立任务）。
  - 证据：`check_param_consumption.py` 对 `screenshot`/`close` 的声明集校验通过。
- [x] **S4 防回归门禁 + 文档纠错**（2026-09-21 done）
  - `.harness/scripts/check_param_consumption.py` 进全门禁：AST 按 `command == "<id>"` 字面量切片，
    声明的参数必须被该命令分支读取或属通用读取；`COMMAND_NOT_FOUND` 的未实现命令整体豁免
    （实现后自动纳入）；**负向验证**：临时插一个假开关 → 门禁立刻红。
  - `docs/element-mvp-boundaries.md` §3 从「漂移清单 + 启发式复查脚本」改写为「已收口 + 门禁口径
    + 迁移说明 + 覆盖范围外风险」；**纠正 `waitFor` 的记录错误**（§2.11：四态由两个正交开关决定，
    `hidden` 与 `detached` 并不等价——代码是对的，文档写错了），并补记两套「可见」口径的真实差异。
  - 同步 `docs/yingdao-web-cmds-benchmark.md`（第 4/10/29 行 + 落地记录）与
    `docs/command-optimization-plan.md`（`browser.close` 定案不做；click 参数由纸面对齐改为已实装）。
  - 新发现并入 BACKLOG：「桌面/数据通道参数漂移复核」（含 `timeoutMs` 会让 GUI 隐藏引擎超时 →
    那些节点实际没有任何超时控制）、「整页/元素截图」、「关闭标签页/终止进程」、「两套可见口径对齐」。

## 验收

- `commands/browser/*.json` 里不再存在「声明了、但执行器与扩展都不会读到」的参数（门禁化）；
- 每条删除/实现都有可追溯理由（代码注释 + 文档 + BACKLOG）；
- 每切片：契约测试 + `check_all.py` 全门禁通过；PROGRESS 追加一行。

## 收口补充（2026-09-21）

- 本轮共处置 **7 处**「声明了不生效」：实装 3（`click.simulateHuman`/`clickPosition`/`modifiers`）、
  补传导 2（`cookieGetAll` 过滤器、四个 cookie 命令的 `tabId`）、删除 2 组
  （`screenshot.fullPage`+`selector`、`close.forceKill`+`ignoreUnload`）；另有 1 处**文档记录错误**
  更正（`waitFor` 四态，代码本来就是对的）。
- 门禁已覆盖扩展通道 **27 条命令**（3 条未实现命令整表豁免）；覆盖范围外的 48 条
  （桌面/数据通道）**如实打印跳过条数**并进 BACKLOG 待复核——不假装已全清。
- S4 审计顺带发现 `cookieGetAll.name` 是被 M28 的启发式**漏掉**的（同名字段在别处出现即算已消费）：
  这正是把口径从「参数名出现过吗」换成「按命令分支切片」的理由。

## 风险 / 注意

1. **删参数是破坏性的**：`additionalProperties: false` 会让旧流程在**校验期**失败（这是有意的：
   静默忽略更坏）。因此只在「实现代价 > 收益」且「参数从未生效」时才删，并逐条在文档写明迁移动作。
2. **`clickPosition=random` 会改变点击坐标**：此前事件完全没有坐标（恒 0,0）。现在 `center` 也带上
   真实中心坐标；若页面按坐标做区域判断（图表/画布），行为比过去更正确，但确有变化。
3. **门禁的盲区**：静态读取判定看不到「参数名在别处被读」的情况（`cookieGetAll.name` 就是这么
   漏掉的）。门禁按命令字面量切片已大幅收窄，但仍非形式化证明——新参数仍要在代码评审里确认真进了
   扩展载荷。
4. **不改既有 effect/output 契约**：`click` 仍是 `unsafe-write`、`cookieGetAll` 仍是 `read`；
   S1/S2 只补参数传导，不动 effect 语义与输出字段。
