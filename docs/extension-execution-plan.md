# 自研扩展执行通道计划（M15 提案）

状态：`Phase 1 已落地`（2026-09-10）
日期：2026-09-10
前置：`yingdao-command-model.md` §〇（维护者决策 2026-09-08：自研扩展一等公民，playwright/bsk 二等）、`yingdao-web-cmds-benchmark.md`（44 条对标，✅22/🟡8/❌14）

> **2026-09-11 更新（现状覆盖）**：playwright 已**彻底移除**（执行 + 捕获 + 依赖），浏览器执行/捕获统一收敛到**自研扩展单通道**。旧的「扩展在线优先 / 离线静默回退 playwright / transport+channel 双轴」均不再成立：`browser.navigate` 已无 `transport`/`channel` 参数；扩展离线时命令**立即失败**（不白等、不回退）。本计划文档保留为历史提案记录，现行行为以 `ADR 0009` 为准。下文中凡把 playwright 作为可用通道讲述的段落均属历史描述，不再适用。

> **落地记录 2026-09-10（Phase 1）**：协议 v2 + 通道骨架完成。
> - 宿主侧 `rpa_core/extension_exec.py`：`ExtensionExecHub`（命令队列 + 长轮询 + 结果回收 + 权限钩子）与 `ExtensionExecClient`（执行器侧 HTTP 客户端）。
> - 路由：`GET /api/ext/command/next?wait=N`（长轮询 + 心跳）、`POST /api/ext/command/result`、`POST /api/ext/command/submit`、`GET /api/ext/status`、`GET|POST /api/ext/permissions`；无 token（devserver 仅绑定 127.0.0.1，无应用层鉴权）。
> - `rpa-core run` 子进程经 `RPA_EXT_HUB_URL` 回连宿主（RunManager 注入真实端口）。
> - 执行器 `extension` 通道：navigate(goto/back/forward/reload)、listPages、attach、executeScript、getText、click、hover、input、scroll、check、select、waitFor、close（解绑）。会话模型 = 用户浏览器里的 tabId 句柄；`browser.close` 只解绑不代关标签页。
> - 一等公民路由：`transport` 缺省时**扩展在线即优先走扩展**，离线静默回退 playwright（显式 `transport=playwright` 可强制）。
> - 权限：默认 `{"mode":"browser"}` 整个浏览器；`tabs` / `origins` 收窄为预留接口，宿主 `allows()` 与扩展 `assertAllowed()` 两处校验点同语义。
> - 扩展：manifest 升 0.2.0（+scripting/tabs/cookies/downloads/webNavigation + `<all_urls>`），background 增执行长轮询循环与 tabs/scripting/cookies 执行面，popup 增权限模式查看/切换。
> - 测试：`tests/contract/test_extension_exec_channel.py` 8 项（假扩展经真实 HTTP 扮演 background，覆盖协议/超时/权限/在线心跳/通道路由/边界）。
> - 待办：Phase 2 官方通道验收（screenshot/upload/download/drag/select 完整语义）、Phase 3 扩展独有能力（网络监听/对话框/批量抓取/元素句柄）、Phase 4 默认路由固化 + 编辑器侧通道状态展示。

## 〇、结论：先做自研插件的「执行通道」，对标剩余项作为它的验收用例

**两条路不是二选一，而是上下游关系。** 理由：

1. **指令对标的 manifest 层已基本完成**（24/44 直接覆盖，参数已对齐）。剩余 ❌14 里价值最高的一批——网络监听×3、对话框内容提取、批量数据抓取、元素句柄×3、元素库（elementRef）接入命令参数——**只能在扩展通道上实现**（chrome.debugger/webRequest/scripting 是唯一解）。先在 playwright 上堆这些等于给二等公民装修。
2. **产品差异化在用户真实登录态浏览器**：曾由三方件 bsk 验证这条需求线（已于 2026-09-11 移除，限于 CSS only / 仅主 frame）。投入已转移到自有可控的自研扩展上。
3. 剩余 ❌ 中纯 DOM 原语类（设置元素值/属性、获取位置、下拉选项、滚动条位置、停止加载）playwright 顺手可补，**不阻塞、不占用主航道**，按需穿插。

## 一、现状盘点

| 组件 | 现状 | 缺口 |
|---|---|---|
| `extension/`（MV3，190 行 JS） | 仅捕获：hover 高亮 + Ctrl+Click 回传描述符；权限只有 storage+alarms | **无执行协议**；无 scripting/tabs/cookies/downloads/webNavigation 权限 |
| devserver `/api/capture/extension/*` | 短轮询（1.2s/5s）+ POST 回传，无 token 配对（仅绑定 127.0.0.1），已稳定 | 需新增命令下发通道（轮询模型对低频捕获够用，对执行需长轮询降低延迟） |
| `executors/browser.py` | playwright 一等实现全部 24 条 | 已加 extension transport 分发 + 扩展会话模型 |
| `elementRef` | capture 产物是结构化描述符，但命令参数仍是裸 selector 字符串 | 元素库接入命令参数（横切差距 #4） |

## 二、分期计划

### Phase 1 — 协议 v2 + 骨架（通道立起来）

- **协议**：`GET /api/ext/command/next`（长轮询，hold ~25s，按 sessionId 出队）+ `POST /api/ext/command/result`；复用 capture 的 TOFU token 与心跳。命令携带 `id/type/payload/timeoutMs`，结果携带 `ok/value/error`。
- **扩展**：manifest 加 `scripting/tabs/cookies/downloads/webNavigation` 权限（`debugger` 按需另议）；background 增加命令轮询循环（alarms 保活已有）并路由：tabs/cookies/history/下载类 background 自处理，DOM 类经 `chrome.tabs.sendMessage` 转发 content script。
- **执行器**：`browser.py` 加 extension 分支；**扩展会话模型** = 用户浏览器单例 + `sessionId ↔ tabId` 映射（无"启动浏览器"概念，`navigate goto` = 新建 tab 或当前 tab 跳转，`attach/listPages` = `chrome.tabs.query`）。
- **transport 语义**：`transport=extension/playwright`（通道）。**channel（浏览器类型）两条通道都生效，但语义不同**：playwright = 启动/复用该内核的独立浏览器；extension = 校验扩展宿主浏览器（浏览器 = 插件装在哪，无法"启动"）。缺省通道下二者冲突时自动让位 playwright，详见 §四。

### Phase 2 — 核心命令集（~15 条，manifest 已就绪 = 对标验收用例）

navigate(goto/back/forward/reload)、click、input、hover、press、select、check、scroll、getText(infoType)、waitFor、executeJS、screenshot（`captureVisibleTab`）、cookie 四件套（`chrome.cookies`）、attach、listPages。
playwright 通道同名单测/合同已存在，扩展通道照 manifest 实现，实现一条亮一条。

### Phase 3 — 扩展独有能力（对标 ❌ 的主战场）

- 网络监听三件套（`chrome.debugger` Network domain 或 webRequest + 响应体捕获）
- 对话框 auto 策略 + alert/confirm/prompt 内容回读（content script 覆盖原生对话框）
- 批量数据抓取（相似元素循环 → 结构化表格）
- 元素句柄模型 + elementRef 元素库接入命令参数（横切差距 #4/#5）

### Phase 4 — 一等公民落位

扩展在线 → `transport` 默认 extension；playwright 显式回退（含设计期/headless 场景）。

## 三、风险与边界

- `chrome.debugger` 会顶「正在调试此浏览器」横幅且与企业策略可能冲突 → 监听/CDP 类能力做成可选权限，能不用则不用。
- MV3 service worker 随时休眠 → 命令轮询用 alarms 唤醒 + 长轮询保持，命令超时由 devserver 侧兜底。
- **在线判定窗口必须大于扩展侧长轮询 hold**（`ONLINE_WINDOW_SECONDS=30 > EXEC_HOLD_S=20`）：否则两次轮询之间会被误判离线，缺省通道偶发回退 playwright（曾踩）。
- 对话框覆盖脚本需 `document_start` 注入时机，现有 `document_idle` 需按页面分组处理。
- 企业环境禁止加载未签名扩展 → 保留 playwright 回退路径为产品级能力，不是临时方案。

## 四、使用：如何指定执行通道与浏览器（channel 语义）

浏览器指令（如 `browser.navigate`「打开网页」）有两条正交的选择轴：

| 参数 | 取值 | 作用 |
| --- | --- | --- |
| `transport` | 缺省 / `extension` / `playwright` | 走哪条执行通道 |
| `channel` | 缺省 / `chromium` / `chrome` / `msedge` / `firefox` / `webkit` | 用哪个浏览器 |

### 1. 指定「用自研插件」

- **什么都不填 = 缺省走插件**：插件已装且在线 → extension；离线（未安装 / service worker 休眠 / devserver 未运行）→ 静默回退 playwright，流程不阻塞。
- 显式 `transport=extension` = 强制扩展通道，不可用时**报错**（不静默回退）——避免"以为走了插件，其实没有"。
- 显式 `transport=playwright` = 强制独立自动化浏览器（`headless/userAgent/userDataDir/args/waitUntil` 等参数只在这条通道生效）。

### 2. 指定「用 Edge」

- **要用户真实已登录的 Edge** → 把插件装到 Edge 里（**插件装在哪，扩展通道就在哪执行**），`channel` 可留空，或写 `msedge` 做双重确认。
- **要一个干净的 Edge 实例** → `transport=playwright` + `channel=msedge`（独立 profile，不带登录态）。
- **扩展通道下 `channel` 是校验、不是启动**：宿主不匹配时——
  - 缺省通道：自动让位 playwright（只有它能按 channel 启动目标浏览器）；
  - 显式 `transport=extension`：直接失败，错误信息给出实际宿主与处置建议（装到目标浏览器 / 改用 playwright）。
- `channel=chromium` 在扩展通道表示"任意 Chromium 内核发行版"（Chrome/Edge/Brave/Opera/Vivaldi 均可）；`firefox`/`webkit` 在扩展通道不支持（插件只跑在 Chromium 内核上）。
- 插件未上报宿主（旧版扩展）且显式指定了 `channel` → 明确失败并提示重载，不假装成功。

### 3. 怎么确认现在会走哪条通道

- 编辑器顶部「扩展通道」徽标：`在线（Edge）` / `离线`（5s 轮询 `/api/ext/status`）。
- 节点属性面板「执行通道」字段下方的实时解析预览：`当前将走自研插件：…` / `当前将走 playwright：…`，与执行器同一套判定逻辑。
- 插件弹窗（浏览器工具栏扩展图标）显示宿主浏览器 + devserver 识别状态。
- 命令行排查：`curl http://127.0.0.1:8765/api/ext/status` → `{"online":true,"host":{"browser":"msedge","version":"...","userAgent":"..."}}`。

### 4. 节点参数示例

```json
{ "type": "action", "command": "browser.navigate",
  "with": { "url": "https://example.com", "transport": "extension", "channel": "msedge" } }
```

### 5. 宿主身份上报（实现要点）

- 扩展 background 每次长轮询 `GET /api/ext/command/next` 时带 `host/ver/platform/ua` query，devserver 记入 hub（心跳即身份，不额外往返）；`ping` 也返回 `host`。
- 宿主名优先取扩展自报，缺省用 UA 推断（`extension_exec.browser_name_from_user_agent`，顺序敏感：Edge/Opera/Brave 的 UA 都含 `Chrome/`）。
- 判定规则集中在 `extension_exec.channel_matches_host`（后端）+ 扩展 `assertAllowed`（收窄模式），两处同语义。
- 前端 `app.js` 的 `channelMatchesHost` 是后端判定的等价副本（面板要即时预览，不能每次求后端）；改一处必须同步另一处，用 `node scripts/check_channel_preview.mjs` 跑判定矩阵做同步检查。

### 6. 实例级身份（instanceId）与多浏览器路由

一个流程可能同时操作 Edge、Chrome，甚至同浏览器的多个 profile（不同用户数据目录）。只用浏览器名路由会撞键：同浏览器多 profile 在 hub 里共享一个浏览器名，无法区分。为此引入**实例级唯一 id**：

- **实例 = 一个浏览器 profile**：扩展 `background.js` 用 `chrome.storage.local` 持久化 `rpaInstanceId`（`crypto.randomUUID()` 生成一次）。同一 profile 的多窗口/页签共享同一 storage + 同一 background SW → 同一 id；不同 profile 各自生成 → 不同 id。
- **上报**：长轮询 `GET /api/ext/command/next` 额外带 `iid=<instanceId>`；devserver `_host_report` 读 `iid` 存入 report 的 `instanceId`。
- **hub 归档/路由**：`record_host` 改按 `instanceId` 为 key 归档（旧版扩展无 id 时退化为浏览器名）；`next_command` 匹配 target 当且仅当 `target == 浏览器名 or target == instanceId`；命令结果由 hub 为 `tabs.create` 补带 `instanceId`（执行该命令的真实实例）。`status()` 新增 `instances`（在线实例详情），`hosts` 仍为浏览器名去重（供前端置灰）。
- **离线快失败**：`submit` 遇「明确 target 但从未在线」→ 立即返回 `TARGET_HOST_OFFLINE`，不白等超时。
- **executor 会话绑定**：`browser.navigate` 建会话后 `_ext_session_hosts[sessionId] = 回应真实 instanceId`（拿不到退浏览器名）；`navigate` 输出新增 `browserInstance`（实例唯一 id）+ `browserType` + `tabId`。

## 五、排障：插件装了、也重载了，为什么执行还是没打开浏览器

### 1. 三种「离线」形态，处置完全不同

| 顶部徽标 | `/api/ext/status` | 含义 | 处置 |
|---|---|---|---|
| `扩展通道：离线` | `online:false` | 没有扩展在轮询：没装 / 没启用 / 宿主浏览器没开 | 按「⇲ 插件」四步加载；确认目标浏览器正在运行 |
| `扩展通道：在线（Edge）` | `online:true, host.browser` | 通道可用 | — |

### 1.5 属性面板的参数分区（对标影刀「常规/高级」）

「打开网页」参数较多（12 个），且很多只属于某一条通道。属性面板（navigate 2.5.0）按 manifest `input_schema.x-param-groups` 分区渲染：

- **常规**：跳转方式、网址；**浏览器**：执行通道 + 浏览器类型（"指定 Edge" 就在这两个下拉里，选项已中文化）；
- **高级 / 启动选项（playwright）**：可折叠，组内任一字段填过值时自动展开；
- **其他通道参数（N）**：x-depends 不满足的字段自动归入底部折叠区，不再平铺占地方；切换通道后重渲染自动归位；
- 未声明分组的指令仍按原平铺渲染，`x-param-groups` 是纯增量能力（放在 input_schema 内，随 catalog 下发，无需后端改动）。

### 2. 无配对机制

扩展通道**已移除 token 配对**（devserver 仅绑定 `127.0.0.1`，loopback 本地工具不做应用层鉴权）。不再存在「token 不匹配 / 配对失败」形态：扩展装了且在轮询就能用，也不再有「重置配对」按钮和 `.capture-extension-token` 文件。离线只可能是 `online:false`（没装 / 没启用 / 宿主浏览器没开）。

### 3. 排障顺序（都可从界面看到，不必翻日志）

1. **顶部徽标**：`在线` / `离线`；
2. **运行面板**：失败时显示 `失败：<code> · 节点 <nodeId>` + 原因 + 通道；
3. **立即失败 vs 等 30 秒**：扩展通道命令是「入队等扩展来领」，扩展不在线时若不预检要躺满 `timeoutMs`（默认 30s）才以 TIMEOUT 收场——用户只会看到"没打开浏览器"。现在执行前先探通道：离线 **<1s** 失败并给出「① 浏览器已打开 ② 扩展已加载并启用 ③ devserver 在运行」的清单；
4. **TIMEOUT 只在真超时时出现**：扩展在线但没在时限内回应（浏览器挂起 / MV3 service worker 休眠），错误信息指向这一点；连接不上 hub（`RPA_EXT_HUB_URL` 端口不对）另给「重启 devserver」的说明。

### 4. 「运行后什么都没发生」：先看运行面板的启动失败说明

子进程在产出任何事件前退出（编译/校验失败）时，界面过去只显示 `完成：exit 1`，原因全丢在 stderr 里（而且 stderr 管道根本没被读取）。现在：

- `RunManager` 持续读取子进程 stderr（顺带消除管道写满导致的阻塞风险），当**没有结果且非零退出**时，`GET /api/runs/{id}` 返回 `startupError: {message, tail}`；
- CLI 编译/校验失败改为结构化错误（`{"error": "COMPILE_FAILED", "message": "..."}` + 退出码 2），不再甩 traceback；
- 前端运行面板显示「运行未能启动（退出码 N）」+ 原因，并在**运行前先编译**（`POST /api/compile`，能力集与 CLI 一致），编译不过就直接摊开错误、不白启动子进程。

实战触发场景：**不可重放指令配了重试次数**。`browser.navigate` 的 `effect.replay = unsafe`，编译期与运行时都会拒绝重试（重复执行会产生重复副作用，如重复开标签页），报 `Unsafe replay command cannot be retried: browser.navigate`。属性面板现在对该类指令**禁用「重试次数」**，历史非法值会标红并给出「清除重试次数」一键修复。

若确实希望某类跳转可重试，属于 manifest 的 `effect.replay` 策略问题（例如把 `back/forward/reload` 视为幂等），需单独评估，不要绕过编译期校验。

