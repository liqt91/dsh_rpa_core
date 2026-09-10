# 影刀浏览器（网页自动化）指令整理与对标

状态：`对标基线`
日期：2026-09-10
用途：以影刀「网页自动化」44 条指令为基准，逐条映射 rpa_core 现有 `browser.*` 命令（14 条），明确已覆盖 / 部分覆盖 / 缺失，为命令面补齐提供清单。
数据来源：影刀官方文档实机抓取，原始正文 + 参数截图见 `docs/yingdao-cmds/<指令名>.md`（44 条，177 张参数截图）；指令模型抽象见 `docs/yingdao-command-model.md`。

> **落地记录 2026-09-10**：P0+P1 建议已采纳实现——新增 `browser.waitLoad / scroll / check / cookieSet / cookieGetAll / cookieGet / cookieRemove / attach / listPages / drag`（catalog 55→65），扩展 `browser.navigate`（action=goto/back/forward/reload）、`browser.getText`（infoType=text/html/outerHTML/value/href）、`browser.waitFor`（state=visible/hidden/detached/attached）。真机 E2E 24 项断言全部通过。P2 待元素句柄/监听协议设计后实施。

---

## 一、覆盖总览

| 状态 | 条数 | 占比 | 说明 |
|---|---|---|---|
| ✅ 已覆盖 | 22 | 50% | rpa_core 有直接对应命令，参数面基本对齐 |
| 🟡 部分覆盖 | 8 | 18% | 有相近命令但参数/语义有缺口 |
| ❌ 缺失 | 14 | 32% | rpa_core 无对应命令 |
| 合计 | **44** | 100% | rpa_core 现有 `browser.*` 共 24 条 |

> 结论（2026-09-10 P0+P1 落地后更新）：元素交互主链路、Cookie 管理、页面级导航/滚动/等待、元素信息读取、多标签管理、拖拽均已对齐（14 条新增 + 3 条扩展）；剩余缺口集中在 **元素对象句柄、批量数据抓取、网络监听、系统级对话框细节**（均依赖 P2 架构决策，见 §四）。

---

## 二、影刀指令全集 × rpa_core 逐条映射

图例：✅ 已覆盖 / 🟡 部分覆盖 / ❌ 缺失。「影刀参数」列只列核心参数，完整参数与截图见对应原始文档。

### 1. 网页操作（9 条）

| # | 影刀指令 | 影刀核心参数（→输出） | rpa_core 对应 | 状态 | 差距备注 |
|---|---|---|---|---|---|
| 1 | [打开网页](yingdao-cmds/打开网页.md) | 浏览器类型(7值枚举)、网址、加载超时、命令行参数（→网页对象） | `browser.navigate` | ✅ | 参数面差异：影刀「浏览器类型」是用户语义枚举（cef/chrome/edge/ie/360se/firefox/QQBrowser）；我们拆成 `transport`(extension/playwright/bsk)+`channel`。**channel 两条通道都生效**：extension 通道下 = 校验插件宿主浏览器（插件装在哪就在哪执行），playwright 通道下 = 启动/复用该内核独立浏览器；缺省通道冲突时自动让位 playwright。**参数面板已对标影刀「常规/高级」分区**（navigate 2.5.0）：常规=跳转方式+网址，浏览器=transport+channel（中文选项标签），高级/启动选项/三方件折叠，当前通道不生效的字段自动归入底部「其他通道参数」折叠区，切换通道自动归位。输出同样为会话对象 |
| 2 | [选择浏览器用户](yingdao-cmds/选择浏览器用户.md) | 浏览器类型（→用户配置对象） | — | ❌ | 多账号/用户配置文件切换，依赖登录态的场景需要 |
| 3 | [获取已打开的网页对象](yingdao-cmds/获取已打开的网页对象.md) | 浏览器类型、标题/URL匹配（→网页对象） | `browser.attach` | ✅ | 同 context 按 title/url 子串或正则匹配，产出独立网页会话 |
| 4 | [关闭网页](yingdao-cmds/关闭网页.md) | 操作(关闭指定/关闭所有)、终止浏览器进程、忽略确认离开对话框 | `browser.close` | 🟡 | 有 `forceKill`/`ignoreUnload`；缺「关闭所有网页」（跨会话批量） |
| 5 | [跳转至新网址](yingdao-cmds/跳转至新网址.md) | 网页对象、跳转方式(新页面/**后退/前进/重新加载**)、加载超时 | `browser.navigate` | ✅ | `action=goto/back/forward/reload`（bsk 通道经 history JS 支持） |
| 6 | [等待网页加载完成](yingdao-cmds/等待网页加载完成.md) | 网页对象、超时时间(s) | `browser.waitLoad` | ✅ | state=load/domcontentloaded/networkidle |
| 7 | [停止网页加载](yingdao-cmds/停止网页加载.md) | 网页对象 | — | ❌ | 对应 `page.stopLoading` 类原语 |
| 8 | [鼠标滚动网页](yingdao-cmds/鼠标滚动网页.md) | 网页对象、在指定元素上滚动、位置(顶/底/指定/一屏)、平滑/瞬间 | `browser.scroll` | ✅ | position=top/bottom/point/page + 可选元素内滚动 + smooth |
| 9 | [自动处理弹框(web)](yingdao-cmds/自动处理弹框_web.md) | 网页对象、处理方式(接受/Dismiss) | `browser.handleDialog` | 🟡 | 影刀是「挂自动策略，弹了就按方式处理」；我们是「弹窗出现后主动调用一次」。语义不同，建议补 auto 模式 |

### 2. 元素操作（11 条）

| # | 影刀指令 | 影刀核心参数（→输出） | rpa_core 对应 | 状态 | 差距备注 |
|---|---|---|---|---|---|
| 10 | [点击元素(web)](yingdao-cmds/点击元素_web.md) | 网页对象、操作目标、模拟人工、点击方式、鼠标按钮、辅助按键、点击位置、执行后延迟、等待存在(s) | `browser.click` | ✅ | 参数面高度对齐（clickType/button/modifiers/clickPosition/simulateHuman/postDelayMs 齐全） |
| 11 | [鼠标悬停在元素上(web)](yingdao-cmds/鼠标悬停在元素上_web.md) | 网页对象、操作目标 | `browser.hover` | ✅ | 一致 |
| 12 | [填写输入框(web)](yingdao-cmds/填写输入框_web.md) | 网页对象、操作目标、内容、输入模式(set_value/模拟人工/逐字按键)、追加、Enter、执行后延迟、等待存在(s) | `browser.input` | ✅ | mode/append/pressEnter/keyIntervalMs/postDelayMs 对齐 |
| 13 | [填写密码框(web)](yingdao-cmds/填写密码框_web.md) | 同输入框，值为密码 | `browser.input` | 🟡 | input 可填，但无密码框专用语义（防泄露/掩码展示），编辑器体验差异 |
| 14 | [设置下拉框(web)](yingdao-cmds/设置下拉框_web.md) | 网页对象、操作目标、选择方式(按文字/索引/值)、等待存在(s) | `browser.select` | ✅ | `selectBy` 对齐 |
| 15 | [设置复选框(web)](yingdao-cmds/设置复选框_web.md) | 网页对象、操作目标、操作(勾选/取消/**反选**)、延迟、等待存在(s) | `browser.check` | ✅ | operation=check/uncheck/toggle，幂等到目标状态 |
| 16 | [设置元素值(web)](yingdao-cmds/设置元素值_web.md) | 网页对象、操作目标、设置方式(value/innerText/…) | — | ❌ | 直改 DOM 属性绕过事件，补 JS 事件触发可选 |
| 17 | [设置元素属性(web)](yingdao-cmds/设置元素属性_web.md) | 网页对象、操作目标、属性名、属性值 | — | ❌ | 可由 executeScript 变通，但缺结构化命令 |
| 18 | [拖拽元素(web)](yingdao-cmds/拖拽元素_web.md) | 网页对象、操作目标、目标位置、拖拽方式 | `browser.drag` | ✅ | selector → targetSelector（Playwright drag_to） |
| 19 | [等待元素(web)](yingdao-cmds/等待元素_web.md) | 网页对象、操作目标、条件(**出现/消失/可见/不可见**)、超时(s) | `browser.waitFor` | ✅ | state=visible/hidden/detached/attached |
| 20 | [获取元素对象(web)](yingdao-cmds/获取元素对象_web.md) | 网页对象、操作目标（→元素对象变量） | — | ❌ | 影刀有独立「元素对象」句柄可复用（配合关联元素/相似元素）；我们每条命令直接用 selector，无元素句柄概念 |

### 3. 数据提取（9 条）

| # | 影刀指令 | 影刀核心参数（→输出） | rpa_core 对应 | 状态 | 差距备注 |
|---|---|---|---|---|---|
| 21 | [获取元素位置(web)](yingdao-cmds/获取元素位置_web.md) | 网页对象、操作目标（→x,y,宽,高） | — | ❌ | 坐标级校验/人类操作模拟依赖 |
| 22 | [获取元素信息(web)](yingdao-cmds/获取元素信息_web.md) | 操作目标、信息类型(文本/源代码/值/链接地址)、智能补全前缀（→字符串） | `browser.getText` | ✅ | infoType=text/html/outerHTML/value/href（bsk 通道仅 text） |
| 23 | [获取下拉框选项(web)](yingdao-cmds/获取下拉框选项_web.md) | 网页对象、操作目标（→选项列表） | — | ❌ | 与 select 成对，动态选择场景常用 |
| 24 | [获取相似元素列表(web)](yingdao-cmds/获取相似元素列表_web.md) | 网页对象、操作目标（→相似元素列表） | `browser.queryAll` | 🟡 | queryAll 返回文本列表；影刀返回「元素对象列表」可逐个操作（依赖 #20 元素句柄） |
| 25 | [获取关联元素(web)](yingdao-cmds/获取关联元素_web.md) | 操作目标、关联方式(父/子/兄弟/前一个/后一个)（→关联元素对象） | — | ❌ | 依赖元素对象模型 |
| 26 | [获取网页信息](yingdao-cmds/获取网页信息.md) | 网页对象（→标题/URL/文本长度） | — | 🟡 | executeScript 可变通；建议给 navigate 输出补 title 或加独立命令 |
| 27 | [获取网页对象列表](yingdao-cmds/获取网页对象列表.md) | —（→网页对象列表） | `browser.listPages` | ✅ | 输出同 context 全部标签页 index/url/title |
| 28 | [获取滚动条位置](yingdao-cmds/获取滚动条位置.md) | 网页对象（→滚动位置） | — | ❌ | 与 #8 滚动成对 |
| 29 | [网页截图](yingdao-cmds/网页截图.md) | 截图区域(元素/可视区域/**整个网页**)、保存文件夹、随机文件名、剪切板输出（→文件路径） | `browser.screenshot` | ✅ | selector(元素)/fullPage(整页) 对齐；缺「保存到剪切板」输出方式 |

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
| 38 | [上传文件](yingdao-cmds/上传文件.md) | 网页对象、操作目标、文件路径 | `browser.upload` | ✅ | 一致 |
| 39 | [下载文件](yingdao-cmds/下载文件.md) | 网页对象、下载URL、保存路径 | `browser.download` | 🟡 | 影刀分「URL 直接下载」与「处理下载对话框」两条；我们 download 语义偏对话框捕获，URL 直下需确认 |
| 40 | [处理下载对话框](yingdao-cmds/处理下载对话框.md) | 保存文件夹、随机文件名（→文件路径） | `browser.download` | 🟡 | 缺「自动随机文件名」 |
| 41 | [处理上传对话框](yingdao-cmds/处理上传对话框.md) | 操作目标、文件路径 | `browser.upload` | 🟡 | 同 #38，系统级文件对话框路径待确认（扩展通道下是否覆盖） |
| 42 | [处理网页对话框](yingdao-cmds/处理网页对话框.md) | 网页对象、处理方式(确定/取消) | `browser.handleDialog` | ✅ | `action` + `promptText` 对齐 |
| 43 | [提取网页对话框内容](yingdao-cmds/提取网页对话框内容.md) | 网页对象（→对话框内容） | — | ❌ | alert 文本回读 |
| 44 | [执行JS脚本](yingdao-cmds/执行JS脚本.md) | 网页对象、参数、JS代码（→脚本结果） | `browser.executeScript` | ✅ | args/timeoutMs/result 对齐 |

---

## 三、结构性差距（跨指令切面）

影刀的指令面之下有 6 个横切模式，rpa_core 需要对应机制才能补齐上面的 ❌/🟡：

1. **会话对象变量**：影刀 39/44 条指令第一参数是「网页对象」（用户命名变量，fx 引用）。rpa_core 用 `sessionId` + `${steps.x.outputs.sessionId}`，机制等价、编辑器已包装为对象下拉 —— 维持现状（调研文档决策）。
2. **浏览器类型枚举**：影刀 7 值用户语义枚举（cef/chrome/edge/ie/360se/firefox/QQBrowser）vs 我们 `transport`+`channel`。一等公民扩展通道落地时需重定义（见 `yingdao-command-model.md` §五-1）。
3. **等待元素存在(s) / 执行后延迟(s)**：影刀所有元素操作指令的标准尾参。我们部分命令有 `timeoutMs`/`postDelayMs`，但**不统一**——补命令时应作为规范默认带上。
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

**实现边界说明**：新命令均为 playwright 通道一等实现；bsk 通道能力受限（CSS only），waitFor 仅 visible、getText 仅 text、navigate 的 back/forward/reload 经 history JS 支持，其余新命令在 bsk 会话下显式报 COMMAND_NOT_FOUND（与 upload/download/handleDialog 现状一致）。
