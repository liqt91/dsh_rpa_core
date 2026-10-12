# 影刀浏览器（网页自动化）指令整理与对标

状态：`对标基线`
日期：2026-09-10（2026-10-12 校正，见下「落地记录」）
用途：以影刀「网页自动化」44 条指令为基准，逐条映射 rpa_core 现有 `browser.*` 命令（32 条），明确已覆盖 / 部分覆盖 / 缺失，为命令面补齐提供清单。
数据来源：影刀官方文档实机抓取，原始正文 + 参数截图见 `docs/yingdao-cmds/<指令名>.md`（44 条，177 张参数截图）；指令模型抽象见 `docs/yingdao-command-model.md`。

> **落地记录 2026-09-10**：P0+P1 建议已采纳实现——新增 `browser.waitLoad / scroll / check / cookieSet / cookieGetAll / cookieGet / cookieRemove / attach / listPages / drag`（catalog 55→65），扩展 `browser.navigate`（action=goto/back/forward/reload）、`browser.getText`（infoType=text/html/outerHTML/value/href）、`browser.waitFor`（state=visible/hidden/detached/attached）。真机 E2E 24 项断言全部通过。P2 待元素句柄/监听协议设计后实施。

> **落地记录 2026-09-21（M29 参数漂移收口）**：本表的「✅ 对齐」此前是**参数面对齐**，
> 但其中一部分参数在扩展通道**从未被消费**（M28 S4 复查发现、M29 收口）。已处置：
> `browser.click` 的 `simulateHuman`/`clickPosition`/`modifiers`（Ctrl/Win 与事件字段对不上）
> **实装**；`browser.cookieGetAll` 的 `name`/`domain`/`path` 与四个 cookie 命令的 `tabId`
> **补上传导**；`browser.screenshot` 的 `fullPage`/`selector`（第 29 行）与 `browser.close` 的
> `forceKill`/`ignoreUnload`（第 4 行）在单通道下**无对应物，已从 manifest 删除**（不留假开关）。
> 口径与门禁见 `docs/element-mvp-boundaries.md` §3；`.harness/scripts/check_param_consumption.py`
> 已进全门禁，新增「声明了不生效」的参数会直接红。

> **落地记录 2026-10-12（M53：参数级缺口补全 + 本表校正）**：镜像改用影刀官方 noauth 接口
> 重抓（`scripts/scrape_yingdao_docs.py`，去 Playwright），网页侧 44 条正文与旧镜像**实质内容差异 0 条**，
> 借此逐行复核本表，发现**三类偏差**（均已在本轮修掉）：
> 1. **我方条数**：表里写 14 条 / §一 写 24 条，都不对——`commands/browser/` 实为 **32 条**（本表只映射到其中 24 条），已全部改正；
> 2. **两处凭空的「影刀参数」**：`设置元素值` 的「设置方式」与 `获取元素信息` 的「智能补全前缀」覆盖面。前者经三方核实（线上正文两处独立来源 + 参数面板截图）**影刀根本没有该参数**，我方 `setWay` 是**本命令扩展**，已改口径；后者只对「获取网页链接地址」生效，已按此收窄并实装；
> 3. **六条「有命令但跑不通」的假覆盖**：`browser.upload` / `browser.download` / `browser.handleDialog` 在扩展单通道下**明确返回 `COMMAND_NOT_FOUND`**（`docs/element-mvp-boundaries.md` §1 第 6~8 行、`docs/extension-channel-baseline.md`），本表却把六行标成 ✅/🟡 ⇒ 已一律改为 ❌（维护者 2026-10-12 拍板）。
>
> 同轮补的参数缺口（四契约同步，参数消费门禁不红）：`browser.getPosition` 补 **8 项输出**（右/底边距 + 中心点 x,y，此前只有 x/y/宽/高）；`browser.getScrollPosition` 补 `direction`（纵向/横向）与 `position`（当前位置/底部位置），**保留** `scrollX`/`scrollY` 不破坏既有流程；`browser.getText` 补 `infoType=attribute` + `attributeName`（任意属性）与 `completeUrlPrefix`（仅 href 生效）。扩展 build **0.9.1 → 0.9.2**（**维护者须重新加载扩展**）。

---

## 一、覆盖总览

| 状态 | 条数 | 占比 | 说明 |
|---|---|---|---|
| ✅ 已覆盖 | 26 | 59% | rpa_core 有直接对应命令，参数面基本对齐 |
| 🟡 部分覆盖 | 4 | 9% | 有相近命令但参数/语义有缺口 |
| ❌ 缺失 | 14 | 32% | rpa_core 无对应命令，或命令存在但本期未实现（跑起来是 `COMMAND_NOT_FOUND`） |
| 合计 | **44** | 100% | rpa_core 现有 `browser.*` 共 32 条（本表映射到其中 24 条） |

> 结论（2026-10-12 校正后）：元素交互主链路、Cookie 管理、页面级导航/滚动/等待、元素信息与位置读取、
> 多标签管理、拖拽均已对齐；剩余缺口集中在 **元素对象句柄、批量数据抓取、网络监听**，
> 以及**文件上传/下载与原生对话框**（命令已登记但扩展通道本期未实现）——均依赖 P2 架构决策（见 §四）。

---

## 二、影刀指令全集 × rpa_core 逐条映射

图例：✅ 已覆盖 / 🟡 部分覆盖 / ❌ 缺失。「影刀参数」列只列核心参数，完整参数与截图见对应原始文档。

### 1. 网页操作（9 条）

| # | 影刀指令 | 影刀核心参数（→输出） | rpa_core 对应 | 状态 | 差距备注 |
|---|---|---|---|---|---|
| 1 | [打开网页](yingdao-cmds/打开网页.md) | 浏览器类型(7值枚举)、网址、加载超时、命令行参数（→网页对象） | `browser.navigate` | ✅ | 参数面差异：影刀「浏览器类型」是用户语义枚举（cef/chrome/edge/ie/360se/firefox/QQBrowser），我们收敛为**自研扩展单通道**、仅支持 Edge/Chrome 插件，故 `browserType` 只取 `msedge`/`chrome`（必选一，默认 msedge，无 auto）；playwright / bsk 已彻底移除。多浏览器各自装了插件时，命令按 `browserType` 路由到对应扩展实例，未装插件的浏览器在编辑器下拉置灰、运行期快速失败（TARGET_HOST_OFFLINE）。参数：`browserType/url/action/timeoutMs`（分组：浏览器 + 常规 + 高级）。输出为会话（网页）对象 |
| 2 | [选择浏览器用户](yingdao-cmds/选择浏览器用户.md) | 浏览器类型（→用户配置对象） | — | ❌ | 多账号/用户配置文件切换，依赖登录态的场景需要 |
| 3 | [获取已打开的网页对象](yingdao-cmds/获取已打开的网页对象.md) | 浏览器类型、标题/URL匹配（→网页对象） | `browser.attach` | ✅ | 同 context 按 title/url 子串或正则匹配，产出独立网页会话 |
| 4 | [关闭网页](yingdao-cmds/关闭网页.md) | 操作(关闭指定/关闭所有)、终止浏览器进程、忽略确认离开对话框 | `browser.close` / `browser.closeTabs` / `browser.closeBrowser` | ✅ | 三条命令分工（M32 引入后两者，M35 收敛 closeBrowser 语义）：`close`=**本地解绑**（把标签页留给用户）；`closeTabs`=关标签页，`tabIds`（自 `browser.listPages`）与 `all`（当前窗口全部）二选一，可选 `ignoreBeforeUnload`（M37，默认 true）；`closeBrowser`=**按类型终止该浏览器的全部进程**（含用户自开窗口，执行即显式授权）。`closeTabs` **没有** `browserType`：路由按会话绑定的那台浏览器走（M38 S1.1 删除该声明——它从未被本命令分支读取） |
| 5 | [跳转至新网址](yingdao-cmds/跳转至新网址.md) | 网页对象、跳转方式(新页面/**后退/前进/重新加载**)、加载超时 | `browser.navigate` | ✅ | `action=goto/back/forward/reload` |
| 6 | [等待网页加载完成](yingdao-cmds/等待网页加载完成.md) | 网页对象、超时时间(s) | `browser.waitLoad` | ✅ | 影刀本身也只传「网页对象 + 超时」；我们**只等 `load`**——manifest 曾声明 `state=load/domcontentloaded/networkidle` 但从未转发给扩展（`tabs.waitLoad` 只收 `tabId`/`timeoutMs`），M38 S1.1 已删除该声明，属删除「声明」而非「能力」 |
| 7 | [停止网页加载](yingdao-cmds/停止网页加载.md) | 网页对象 | `browser.stopLoading` | ✅ | 扩展 `tabs.stopLoading`，输出回带当前网址 |
| 8 | [鼠标滚动网页](yingdao-cmds/鼠标滚动网页.md) | 网页对象、在指定元素上滚动、位置(顶/底/指定/一屏)、平滑/瞬间 | `browser.scroll` | ✅ | position=top/bottom/point/page + 可选元素内滚动 + smooth |
| 9 | [自动处理弹框(web)](yingdao-cmds/自动处理弹框_web.md) | 网页对象、处理方式(接受/Dismiss) | `browser.handleDialog` | ❌ | 命令已登记但**本期未实现**：扩展单通道下直接返回 `COMMAND_NOT_FOUND`（`element-mvp-boundaries.md` §2.8）。语义也与影刀不同（影刀是「挂自动策略，弹了就按方式处理」，我们是「弹了就主动调一次」），两者都要补 |

### 2. 元素操作（11 条）

| # | 影刀指令 | 影刀核心参数（→输出） | rpa_core 对应 | 状态 | 差距备注 |
|---|---|---|---|---|---|
| 10 | [点击元素(web)](yingdao-cmds/点击元素_web.md) | 网页对象、操作目标、模拟人工、点击方式、鼠标按钮、辅助按键、点击位置、执行后延迟、等待存在(s) | `browser.click` | ✅ | 参数面齐全**且已实装**（M29 前 `clickPosition`/`simulateHuman`/`modifiers` 只进 manifest 未消费，属纸面对齐） |
| 11 | [鼠标悬停在元素上(web)](yingdao-cmds/鼠标悬停在元素上_web.md) | 网页对象、操作目标 | `browser.hover` | ✅ | 一致 |
| 12 | [填写输入框(web)](yingdao-cmds/填写输入框_web.md) | 网页对象、操作目标、内容、输入模式(set_value/模拟人工/逐字按键)、追加、Enter、执行后延迟、等待存在(s) | `browser.input` | ✅ | mode/append/pressEnter/keyIntervalMs/postDelayMs 对齐 |
| 13 | [填写密码框(web)](yingdao-cmds/填写密码框_web.md) | 同输入框，值为密码 | `browser.input` | 🟡 | input 可填，但无密码框专用语义（防泄露/掩码展示），编辑器体验差异 |
| 14 | [设置下拉框(web)](yingdao-cmds/设置下拉框_web.md) | 网页对象、操作目标、选择方式(按文字/索引/值)、等待存在(s) | `browser.select` | ✅ | `selectBy` 对齐 |
| 15 | [设置复选框(web)](yingdao-cmds/设置复选框_web.md) | 网页对象、操作目标、操作(勾选/取消/**反选**)、延迟、等待存在(s) | `browser.check` | ✅ | operation=check/uncheck/toggle，幂等到目标状态 |
| 16 | [设置元素值(web)](yingdao-cmds/设置元素值_web.md) | 网页对象、操作目标、元素值 | `browser.setValue` | ✅ | **校正**：影刀**没有**「设置方式」参数（原表凭记忆写成「设置方式(value/innerText/…)」）。我方 `setWay`=value/innerText/innerHTML 是**本命令扩展**，manifest 描述已改口径；`value` 与影刀「元素值」对齐，默认 `setWay=value` 即影刀的语义 |
| 17 | [设置元素属性(web)](yingdao-cmds/设置元素属性_web.md) | 网页对象、操作目标、属性名、属性值 | `browser.setAttribute` | ✅ | 参数名差异：影刀「属性名」→ 我方 `name`。直改属性、不触发事件（与影刀一致） |
| 18 | [拖拽元素(web)](yingdao-cmds/拖拽元素_web.md) | 网页对象、操作目标、目标位置、拖拽方式 | `browser.drag` | ✅ | selector → targetSelector（Playwright drag_to） |
| 19 | [等待元素(web)](yingdao-cmds/等待元素_web.md) | 网页对象、操作目标、条件(**出现/消失/可见/不可见**)、超时(s) | `browser.waitFor` | ✅ | state=visible/hidden/detached/attached |
| 20 | [获取元素对象(web)](yingdao-cmds/获取元素对象_web.md) | 网页对象、操作目标（→元素对象变量） | — | ❌ | 影刀有独立「元素对象」句柄可复用（配合关联元素/相似元素）；我们每条命令直接用 selector，无元素句柄概念 |

### 3. 数据提取（9 条）

| # | 影刀指令 | 影刀核心参数（→输出） | rpa_core 对应 | 状态 | 差距备注 |
|---|---|---|---|---|---|
| 21 | [获取元素位置(web)](yingdao-cmds/获取元素位置_web.md) | 网页对象、操作目标、相对于(屏幕/页面)、转为 dpi96（→左边距、右边距、顶部边距、底部边距、中心点横坐标、中心点纵坐标、宽度、高度） | `browser.getPosition` | ✅ | **本轮补 8 项输出**（此前只有 x/y/宽/高；`right`/`bottom`/`center` 由执行器从 boundingRect 派生）。**基准只有一种**：相对**浏览器页面左上角**（客户区域）；影刀的「相对于屏幕左上角」与「转为 dpi96」**未支持**（已知欠账，屏幕坐标需 `window.screenX/Y` + 窗口 chrome 补偿） |
| 22 | [获取元素信息(web)](yingdao-cmds/获取元素信息_web.md) | 操作目标、操作(文本/源代码/值/链接地址/**属性**/位置)、属性名、智能补全地址前缀、等待元素存在(s)（→字符串） | `browser.getText` | ✅ | `infoType`=text/html/outerHTML/value/href + 本轮新增 **`attribute`**（配 `attributeName` 取任意属性，如 href/src/class/data-*）与 **`completeUrlPrefix`**（智能补全地址前缀，**仅 href 生效**）。「操作」里的**获取元素位置**一项由 `browser.getPosition` 独立承担（影刀把它也列在这个下拉里） |
| 23 | [获取下拉框选项(web)](yingdao-cmds/获取下拉框选项_web.md) | 网页对象、操作目标（→选项列表） | `browser.getSelectOptions` | ✅ | 输出 `options`（`{index, value, label, selected}` 列表）+ `count`；比影刀多回带 `selected` |
| 24 | [获取相似元素列表(web)](yingdao-cmds/获取相似元素列表_web.md) | 网页对象、操作目标（→相似元素列表） | `browser.queryAll` | 🟡 | queryAll 返回文本列表；影刀返回「元素对象列表」可逐个操作（依赖 #20 元素句柄） |
| 25 | [获取关联元素(web)](yingdao-cmds/获取关联元素_web.md) | 操作目标、关联方式(父/子/兄弟/前一个/后一个)（→关联元素对象） | — | ❌ | 依赖元素对象模型 |
| 26 | [获取网页信息](yingdao-cmds/获取网页信息.md) | 网页对象（→标题/URL/文本长度） | — | 🟡 | executeScript 可变通；建议给 navigate 输出补 title 或加独立命令 |
| 27 | [获取网页对象列表](yingdao-cmds/获取网页对象列表.md) | —（→网页对象列表） | `browser.listPages` | ✅ | 输出同 context 全部标签页 index/url/title |
| 28 | [获取滚动条位置](yingdao-cmds/获取滚动条位置.md) | 网页对象、元素滚动条、滚动条(纵向/横向)、位置(当前位置/底部位置)（→滚动位置） | `browser.getScrollPosition` | ✅ | **本轮补 `direction`（vertical/horizontal）+ `position`（current/bottom）**，输出新增 `scrollPosition`；**`scrollX`/`scrollY` 恒返回且语义不变**（不破坏既有流程）。`bottom` 口径＝**可滚动内容总长**（`scrollHeight`/`scrollWidth`，影刀原文「即滚动条的长度」），**不是**最大滚动距离。影刀的「自动向上查找滚动条元素」未支持 |
| 29 | [网页截图](yingdao-cmds/网页截图.md) | 截图区域(元素/可视区域/**整个网页**)、保存文件夹、随机文件名、剪切板输出（→文件路径） | `browser.screenshot` | 🟡 | 只截「窗口当前可见标签页的可见区」；M29 已删 `selector`/`fullPage`（`captureVisibleTab` 能力边界，整页/元素截图见 BACKLOG）；缺「保存到剪切板」 |

### 4. 批量抓取（1 条）

| # | 影刀指令 | 影刀核心参数（→输出） | rpa_core 对应 | 状态 | 差距备注 |
|---|---|---|---|---|---|
| 30 | [批量数据抓取](yingdao-cmds/批量数据抓取.md) | 网页对象、抓取方式、相似元素循环（→数据表格） | — | ❌ | 影刀的招牌能力（结构化表格抽取 + 分页循环）；可用 queryAll+loop 变通但体验差距大 |

### 5. Cookie 管理（4 条）

| # | 影刀指令 | 影刀核心参数（→输出） | rpa_core 对应 | 状态 | 差距备注 |
|---|---|---|---|---|---|
| 31 | [设置Cookie](yingdao-cmds/设置Cookie.md) | 网页对象、浏览器类型、cookie数据 | `browser.cookieSet` | ✅ | Cookie 对象数组（name/value + url 或 domain+path） |
| 32 | [获取筛选所有Cookie](yingdao-cmds/获取筛选所有Cookie.md) | 筛选(domain/path/name)（→Cookie列表） | `browser.cookieGetAll` | ✅ | name 精确 + domain/path 子串筛选 |
| 33 | [获取指定Cookie信息](yingdao-cmds/获取指定Cookie信息.md) | cookie名称（→cookie值） | `browser.cookieGet` | ✅ | |
| 34 | [移除指定Cookie](yingdao-cmds/移除指定Cookie.md) | cookie名称 | `browser.cookieRemove` | ✅ | 缺省 name = 清空全部 |

### 6. 网络请求监听（3 条）

| # | 影刀指令 | 影刀核心参数（→输出） | rpa_core 对应 | 状态 | 差距备注 |
|---|---|---|---|---|---|
| 35 | [开始监听网页请求](yingdao-cmds/开始监听网页请求.md) | 网页对象、监听条件(URL/资源类型)（→监听对象） | — | ❌ | 接口级取数（比 DOM 抓取稳定），配合扩展执行通道是差异化机会 |
| 36 | [停止监听网页请求](yingdao-cmds/停止监听网页请求.md) | 监听对象 | — | ❌ | |
| 37 | [获取网页监听结果](yingdao-cmds/获取网页监听结果.md) | 监听对象（→请求/响应数据列表） | — | ❌ | |

### 7. 文件与对话框（6 条）

| # | 影刀指令 | 影刀核心参数（→输出） | rpa_core 对应 | 状态 | 差距备注 |
|---|---|---|---|---|---|
| 38 | [上传文件](yingdao-cmds/上传文件.md) | 网页对象、操作目标、文件路径 | `browser.upload` | ❌ | 命令已登记但**本期未实现**：扩展单通道下直接返回 `COMMAND_NOT_FOUND`（`element-mvp-boundaries.md` §2.6）。原表标 ✅「一致」是错的 |
| 39 | [下载文件](yingdao-cmds/下载文件.md) | 网页对象、下载URL、保存路径 | `browser.download` | ❌ | 同 #38（§2.7）。影刀分「URL 直接下载」与「处理下载对话框」两条，两条我们都没实现 |
| 40 | [处理下载对话框](yingdao-cmds/处理下载对话框.md) | 保存文件夹、随机文件名（→文件路径） | `browser.download` | ❌ | 同 #38（§2.7） |
| 41 | [处理上传对话框](yingdao-cmds/处理上传对话框.md) | 操作目标、文件路径 | `browser.upload` | ❌ | 同 #38（§2.6） |
| 42 | [处理网页对话框](yingdao-cmds/处理网页对话框.md) | 网页对象、处理方式(确定/取消) | `browser.handleDialog` | ❌ | 命令已登记但**本期未实现**（`element-mvp-boundaries.md` §2.8；`alert` 会阻塞页面 JS）。原表标 ✅ 是错的 |
| 43 | [提取网页对话框内容](yingdao-cmds/提取网页对话框内容.md) | 网页对象（→对话框内容） | — | ❌ | alert 文本回读 |
| 44 | [执行JS脚本](yingdao-cmds/执行JS脚本.md) | 网页对象、参数、JS代码（→脚本结果） | `browser.executeScript` | ✅ | args/timeoutMs/result 对齐 |

---

## 三、结构性差距（跨指令切面）

影刀的指令面之下有 6 个横切模式，rpa_core 需要对应机制才能补齐上面的 ❌/🟡：

1. **会话对象变量**：影刀 39/44 条指令第一参数是「网页对象」（用户命名变量，fx 引用）。rpa_core 用 `sessionId` + `${steps.x.outputs.sessionId}`，机制等价、编辑器已包装为对象下拉 —— 维持现状（调研文档决策）。
2. **浏览器类型枚举**：影刀 7 值用户语义枚举（cef/chrome/edge/ie/360se/firefox/QQBrowser）vs 我们 `transport`+`channel`。一等公民扩展通道落地时需重定义（见 `yingdao-command-model.md` §五-1）。
3. **等待元素存在(s) / 执行后延迟(s)**：影刀所有元素操作指令的标准尾参。我们部分命令有 `timeoutMs`/`postDelayMs`，但**不统一**——补命令时应作为规范默认带上。
   > **M30 更新（2026-09-21）**：`timeoutMs` 的「声明了不生效」已四类通道全部收口
   > （`.harness/scripts/check_param_consumption.py` 覆盖 78 条命令、跳过 0 条），
   > 桌面侧 `timeoutMs` 已变成**真等待**（此前是一次性查找）。但**「统一带上」这件事仍未做**——
   > 各通道的 `timeoutMs` 语义还有一处刻意差异：桌面侧会连带抬高节点超时与执行器操作超时
   > （浏览器侧只交给等待选择器）。`postDelayMs` 的统一化同样仍待办。
   > 详见 `docs/element-mvp-boundaries.md` §3.4 与 `docs/desktop_backends.md`「超时层级与元素等待」。
4. **元素库（捕获的元素描述符）**：影刀「操作目标」一律来自元素库（捕获 + 云端持久化）。我们用裸 `selector` 字符串；自研扩展已具备捕获能力，需要把捕获产物接入命令参数（elementRef 结构化）。
5. **元素对象句柄**：影刀支持把元素保存为对象变量再操作（获取元素对象/关联元素/相似元素列表）。我们是纯 selector 模型，缺这层（影响 #20/#24/#25）。
6. **错误处理三件套**：影刀所有指令统一带「错误处理方式(继续/停止/重试)+重试次数」。我们 `errorPolicy` 已覆盖，需确认新命令默认接入。

---

## 四、补齐建议与落地状态

**P0 — 高频主链路缺口（✅ 已落地 2026-09-10）**
- ✅ 页面导航组：navigate 加 `action=goto/back/forward/reload`；`browser.waitLoad`；`browser.scroll`
- ✅ 元素信息组：`getText` 加 `infoType`（text/html/outerHTML/value/href）；`waitFor` 加 `state`（visible/hidden/detached/attached）
- ✅ `browser.check`（勾选/取消/反选幂等语义）

**P1 — 登录态与多标签（✅ 已落地 2026-09-10）**
- ✅ Cookie 四件套：`browser.cookieSet / cookieGetAll / cookieGet / cookieRemove`
- ✅ `browser.attach`（按标题/URL 匹配已开页面，产出独立会话）、`browser.listPages`
- ✅ `browser.drag`

**P2 — 进阶能力（待设计，依赖元素对象模型/监听协议）**
- [ ] 元素句柄模型（获取元素对象/关联元素/相似元素对象列表）
- [ ] 批量数据抓取（结构化表格）
- [ ] 网络监听三件套（配合自研扩展 background 通道）
- [ ] 对话框 auto 策略模式 + alert 内容回读
- [ ] **文件上传/下载与原生对话框**（#38~#42）：`browser.upload` / `browser.download` /
      `browser.handleDialog` 三条命令**已登记但本期未实现**（`COMMAND_NOT_FOUND`）。
      扩展通道缺 CDP，`<input type=file>` 的赋值与原生对话框都无法从 service worker 完成
      ——落地路径与代价见 `docs/element-mvp-boundaries.md` §2.6/§2.7/§2.8 与 `.harness/BACKLOG.md` P4 三片。

**实现边界说明**：新命令统一为自研扩展单通道实现。

---

## 五、本轮参数级对照（`getPosition` / `getScrollPosition` / `getText`）

只列**本轮动过**的三条，逐项可查证。影刀侧参数取自镜像正文
（`docs/yingdao-cmds/获取元素位置_web.md` 第 17~33 行、`获取滚动条位置.md` 第 15~35 行、
`获取元素信息_web.md` 第 23~47 行）；我方侧取自 `commands/browser/<id>.json`（唯一事实源）。

### 5.1 `browser.getPosition` ← 影刀「获取元素位置(web)」

| 影刀 | 我方 | 结论 |
|---|---|---|
| 网页对象 | `sessionId` | 等价（编辑器已包装成对象下拉） |
| 操作目标（元素库） | `selector` | 机制等价（我们无元素句柄，见 §三-4/5） |
| 相对于（屏幕左上角 / 浏览器页面左上角） | — | **只支持后者**（`getBoundingClientRect` 即视口坐标）。屏幕坐标**未支持**，已记欠账 |
| 转为 dpi96 | — | **未支持**；扩展通道拿不到可信的设备缩放因子来源 |
| 等待元素存在(s) | `timeoutMs` | 等价 |
| 输出：左边距/右边距/顶部边距/底部边距/中心点横坐标/中心点纵坐标/宽度/高度（**8 项**） | `x` / `right` / `y` / `bottom` / `centerX` / `centerY` / `width` / `height`（**8 项**） | **本轮补齐**：`right`=x+width、`bottom`=y+height、`centerX`=x+width/2、`centerY`=y+height/2，由执行器从 boundingRect **派生**（扩展只回四个原始量，避免两套算法漂移） |

> 命名口径：我方沿用 `x`/`y`（=左边距/顶部边距）而非 `left`/`top`，是历史命名，
> 语义与影刀一致（`x` 就是左边距）；`x-outputs` 的中文标签已写成「X 坐标 / Y 坐标 / 右边距 / …」。

### 5.2 `browser.getScrollPosition` ← 影刀「获取滚动条位置」

| 影刀 | 我方 | 结论 |
|---|---|---|
| 网页对象 | `sessionId` | 等价 |
| 元素滚动条（可选；**无滚动条时自动向上查找**） | `selector`（可选） | 可选元素对齐；**「自动向上查找」未支持** |
| 滚动条（纵向/横向） | `direction`（`vertical` 默认 / `horizontal`） | **本轮新增** |
| 位置（当前位置/底部位置） | `position`（`current` 默认 / `bottom`） | **本轮新增** |
| 等待元素存在(s) | `timeoutMs` | 等价 |
| 输出：滚动位置（**单个数值**） | `scrollPosition`（新）+ `scrollX`/`scrollY`（**保留**） | `scrollPosition` 按 `direction`×`position` 取轴取值；旧输出**恒返回、语义不变**（维护者定案：不破坏既有流程） |

> **`bottom` 口径**：影刀原文写「底部位置：滚动条的底部位置，**即滚动条的长度**」⇒ 取
> **可滚动内容总长**（`scrollHeight`/`scrollWidth`），**不是**最大滚动距离
> （`scrollHeight - clientHeight`）。这是**刻意选定的口径**，改它等于改契约语义
> （`browser.py::_scroll_position_value` 的 docstring 已钉住）。
> 整页时内容总长取 `documentElement` 与 `body` 的**较大者**（quirks 模式下前者会偏小）。

### 5.3 `browser.getText` ← 影刀「获取元素信息(web)」

| 影刀 | 我方 | 结论 |
|---|---|---|
| 操作目标（元素库） | `selector` | 机制等价 |
| 操作：获取元素文本内容 | `infoType=text`（默认） | 等价（可见文本；输入框回退 textContent/title/placeholder，见 `content.js`） |
| 操作：获取元素源代码 | `infoType=html` | 等价（元素内部 HTML）；另有多出的 `outerHTML`（含元素自身的完整 HTML） |
| 操作：获取元素值 | `infoType=value` | 等价 |
| 操作：获取网页链接地址 | `infoType=href` | 等价 |
| 操作：获取元素属性（href/src/class…） | `infoType=attribute` + `attributeName` | **本轮新增** |
| 操作：获取元素位置 | — | 由 `browser.getPosition` 独立承担（见 5.1） |
| 智能识别并补充地址前缀(http:// 或 https://) | `completeUrlPrefix`（默认 `false`） | **本轮新增**，**仅 `infoType=href` 生效**（维护者定案，严格对标该选项在影刀里挂在「获取网页链接地址」之下） |
| 等待元素存在(s) | `timeoutMs` | 等价 |
| 保存元素信息至 | `value` | 等价（单字符串输出；`x-outputs.value.primary=true`） |

> **补前缀的取值规则（五种形态，逐条有判据）**：① 已有协议头 → 原样；
> ② 空值/纯空白 → 空串（**不做**「这串文本看着像网址」的猜测）；③ 以 `//` 开头 →
> 补「当前页面协议 + `:`」；④ 以 `/` `.` `#` `?` 开头（相对引用）→ 用**浏览器解析后的
> 绝对地址**（`el.href`），拿不到才退回原值（否则 `/next` 会被拼成 `https:///next`）；
> ⑤ 其余（裸域名/裸文本地址）→ 补「当前页面协议 + `//`」。补哪个协议按**当前页面**协议
> （页面是 `http:` 就补 `http:`，其余一律 `https:`）。协议头判据与 host 侧 `_SCHEME_RE`
> **同口径**（跨端正则由 `scripts/check_url_prefix_helpers.mjs` 钉住）。
