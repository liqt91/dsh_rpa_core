# M109：节点详情拆成非模态浮层——右栏退役、画布吃宽

Status: `done`

> 2026-11-25 立项 → 同日落地。维护者原话：「**感觉节点详情作为弹窗来填写会好一点**」。
> 三步取舍由维护者拍板：① 打开方式 = **非模态可拖浮层（不挡画布）**；
> ② 内容 = **参数区 + AST + 编译回显全搬进去**；③ 保存后 = **关窗**。
> 前置：M108（参数机制接线，参数面板此时刚铺满）、M102-S2/M112（底栏元素分栏，右栏拆分的先例）。

## 一、起因（不是审美问题，是宽度不够）

右栏固定 `340px`（窄窗 300 / 264），里面同时挂着六样东西：参数表单、元素引用面板、
输出变量列、`节点 AST`、编译回显、保存/撤销条。以 `browser.click` 为例，13 个可见字段
分两组，标签列按键的最宽标签定宽后，**控件实际只剩 ~180px** —— 长说明折成三行、
fx 变量菜单一开就贴边、`x-multiline` 的 textarea 只剩两行高。

三个让拆分变便宜的事实（改动前先量的）：

- 卡片行内**已经有 `summary`**，右栏不需要留着当摘要栏；
- `el.form` 是**唯一**参数渲染容器（13 处写入全在它下面），整块搬走即可；
- `selectCard` 是**唯一**选中入口，且已有 `isDirty()` + confirm 拦截可复用。

一个反方向的真风险：点卡片除了看参数，还承担「给下一条插入指定 branch」和「浏览流程时不
打断」。所以**没选**「点一下弹模态」—— 连看 5 个节点会变成开 5 次框关 5 次框。

## 二、落地清单

### 1. DOM（`apps/editor/gui/index.html`）

- 删 `aside#right`；中栏 `grid-template-columns` 从 `216px 1fr 340px` 变 `216px 1fr`，
  三条媒体查询同步（只收左栏）。
- 新增 `section#nodeFloat`（放在 `</main>` 之后，`position: fixed`），内含：
  `#nodeFloatHead`（标题 + dirty + unset + rpcMs + 关闭）、`#paramForm`、`.formBar`、
  `.astBox`（`#detail`）、`#compileOut`。
- **id 一个不改**：`paramForm / saveBtn / revertBtn / dirty / unset / rpcMs / detail /
  compileOut` 全部沿用 ⇒ main.js 里十几处写入路径零改动。

### 2. 样式（`src/style.css`）

- `.nodeFloat`：`width: min(460px, 100vw - 24px)`、`max-height: calc(100vh - 96px)`、
  `z-index: 30`（压在 modal 40 之下、画布之上，与 fx 菜单 60 / toast 60 不抢层）。
- `.nodeFloatHead` 拖动把手（`cursor: grab`），`.nodeFloatHead .count { margin-left: 0 }`
  —— `.count` 自带 `margin-left: auto` 会把 rpcMs 顶到关闭按钮旁边。
- 浮层内 `.astBox` / `.compileOut` **限高而非吃余高**：AST 那条必须写
  `.nodeFloat > .astBox[open]`，否则同 specificity 下被本文件更靠后的
  `.astBox[open] { flex: 1 1 auto }` 覆盖，参数区会被挤没。

### 3. 行为（`src/main.js`）

| 函数 | 口径 |
|---|---|
| `floatBounds(vw, vh, w, h)` | 纯函数：视口内四边夹取范围；窗口比浮层窄时 `max < min`，用 `Math.max` 让 min 赢 |
| `setFloatPos(left, top)` | 量真实 rect → clamp → 写 `style.left/top`，返回落点 |
| `openNodeFloat(card)` | **只在隐藏时落位**（贴右上，即原右栏的位置）；已显示则只换标题 ⇒ 连续核对不跳 |
| `closeNodeFloat()` | 只 `hidden = true`，不动 fields / exprModes；再点卡片重新走 RPC |
| `bindNodeFloatDrag()` | `mousedown` 在头部（关闭按钮上的按下不算），`document` 上跟 `mousemove/mouseup`，与 `bindDockResize` 同款 |

接线点四处：`selectCard` 顶部（覆盖 action / group / catch 三条路径）、`saveParams` 成功
（失败路径提前 return，窗口留着）、`removeCard` 删掉当前选中（否则下次保存是
`NODE_NOT_FOUND`）、`openFlow` 换流程（先关，落到第一张卡片时再开）。

`renderCompile` 补一句 `setStatus`：回显住在浮层里，**浮层关着时它谁也看不见**；编译是
顶栏按钮触发的，结果落在顶栏附近才对得上「我刚才点了什么」。

### 4. 一处结构性修正

`bindNodeFloatDrag()` **不能**放在 `bindBottomDock()` 里：后者有「tab/panel 数量对不上就
整体 return」的守卫，浮层拖动会被底栏 DOM 连坐。改成顶层与 `bindBottomDock()` 并列。

## 三、判据（`scripts/check_param_form_ui.mjs`，7 → 12 条）

| 用例 | 钉住什么 |
|---|---|
| 浮层：点卡片就打开（走 `selectCard`） | 真实入口接线 + 贴右上落位 + 标题跟随 |
| 浮层：切换节点只换标题，不把位置弹回去 | `openNodeFloat` 的 `hidden` 分支 |
| 浮层：拖到视口外会被夹回来 | `floatBounds` 四边界 + 窄窗口下 max<min |
| 浮层：保存成功关窗，保存失败必须留着 | 两条路径的真实分支 |
| 浮层：删掉正在看的节点会一并关窗 | 防 `NODE_NOT_FOUND` |

负向验证 **6 个注入全命中、各只红一条、md5 还原一致**：selectCard 不调用 / 每次都落位 /
去掉 clamp / 保存后不关窗 / 保存失败也关窗 / 删节点不关窗。

## 四、踩坑（两个都是「看起来红/绿，其实没打中」）

1. **第一版「点卡片打开」是假判据**：判据里直接调 `openNodeFloat(...)`，只能证明
   「函数本身会开」，证明不了「点卡片会调到它」—— 注入掉 `selectCard` 里那行调用，
   12 条判据全绿。负向验证当场打回，改成 `await run('selectCard({...})')`。
2. **注入写成「在调用上一行插 `// NEG`」不生效**：`//` 只注释自己那一行，下一行的调用
   照样执行 ⇒ 判据全绿，看着像判据不灵，其实是注入没打中。必须**改掉那句调用本身**。

## 五、遗留

- 浮层位置不持久化（刷新回右上）。没做是因为当前只有一块默认位置、且 `localStorage`
  里已有 `rpa.dockHeight` 一个键；加键要连带「窗口尺寸变化后落位越界」的迁移，先不做。
- 浮层与画布重叠时没有「吸附/靠边收起」；窄窗下 460px 宽会盖住大半个画布，此时依赖
  × 关闭。
- 本次改动**尚未在真实窗口里截图核验**（只过了 DOM shim 判据 + 全量门禁）。
