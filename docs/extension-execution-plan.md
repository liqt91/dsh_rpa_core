# 自研扩展执行通道计划（M15 提案）

状态：`Phase 1 已落地`（2026-09-10）
日期：2026-09-10
前置：`yingdao-command-model.md` §〇（维护者决策 2026-09-08：自研扩展一等公民，playwright/bsk 二等）、`yingdao-web-cmds-benchmark.md`（44 条对标，✅22/🟡8/❌14）

> **落地记录 2026-09-10（Phase 1）**：协议 v2 + 通道骨架完成。
> - 宿主侧 `rpa_core/extension_exec.py`：`ExtensionExecHub`（命令队列 + 长轮询 + 结果回收 + 权限钩子）与 `ExtensionExecClient`（执行器侧 HTTP 客户端）。
> - 路由：`GET /api/ext/command/next?wait=N`（长轮询 + 心跳）、`POST /api/ext/command/result`、`POST /api/ext/command/submit`、`GET /api/ext/status`、`GET|POST /api/ext/permissions`；token 复用捕获通道的 TOFU 配对。
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
2. **产品差异化在用户真实登录态浏览器**：bsk 已验证这条需求线，但它是三方件（CSS only / 仅主 frame / 未来可能放弃）。投入应转移到自有可控的扩展上。
3. 剩余 ❌ 中纯 DOM 原语类（设置元素值/属性、获取位置、下拉选项、滚动条位置、停止加载）playwright 顺手可补，**不阻塞、不占用主航道**，按需穿插。

## 一、现状盘点

| 组件 | 现状 | 缺口 |
|---|---|---|
| `extension/`（MV3，190 行 JS） | 仅捕获：hover 高亮 + Ctrl+Click 回传描述符；权限只有 storage+alarms | **无执行协议**；无 scripting/tabs/cookies/downloads/webNavigation 权限 |
| devserver `/api/capture/extension/*` | 短轮询（1.2s/5s）+ POST 回传 + TOFU token 配对，已稳定 | 需新增命令下发通道（轮询模型对低频捕获够用，对执行需长轮询降低延迟） |
| `executors/browser.py` | playwright 一等实现全部 24 条；bsk 部分 + 显式 COMMAND_NOT_FOUND | 需加 extension transport 分发 + 扩展会话模型 |
| `elementRef` | capture 产物是结构化描述符，但命令参数仍是裸 selector 字符串 | 元素库接入命令参数（横切差距 #4） |

## 二、分期计划

### Phase 1 — 协议 v2 + 骨架（通道立起来）

- **协议**：`GET /api/ext/command/next`（长轮询，hold ~25s，按 sessionId 出队）+ `POST /api/ext/command/result`；复用 capture 的 TOFU token 与心跳。命令携带 `id/type/payload/timeoutMs`，结果携带 `ok/value/error`。
- **扩展**：manifest 加 `scripting/tabs/cookies/downloads/webNavigation` 权限（`debugger` 按需另议）；background 增加命令轮询循环（alarms 保活已有）并路由：tabs/cookies/history/下载类 background 自处理，DOM 类经 `chrome.tabs.sendMessage` 转发 content script。
- **执行器**：`browser.py` 加 extension 分支；**扩展会话模型** = 用户浏览器单例 + `sessionId ↔ tabId` 映射（无"启动浏览器"概念，`navigate goto` = 新建 tab 或当前 tab 跳转，`attach/listPages` = `chrome.tabs.query`）。
- **transport 语义**：`transport=extension/playwright/bsk`（通道）。**channel（浏览器类型）两条通道都生效，但语义不同**：playwright = 启动/复用该内核的独立浏览器；extension = 校验扩展宿主浏览器（浏览器 = 插件装在哪，无法"启动"）。缺省通道下二者冲突时自动让位 playwright，详见 §四。

### Phase 2 — 核心命令集（~15 条，manifest 已就绪 = 对标验收用例）

navigate(goto/back/forward/reload)、click、input、hover、press、select、check、scroll、getText(infoType)、waitFor、executeJS、screenshot（`captureVisibleTab`）、cookie 四件套（`chrome.cookies`）、attach、listPages。
playwright 通道同名单测/合同已存在，扩展通道照 manifest 实现，实现一条亮一条。

### Phase 3 — 扩展独有能力（对标 ❌ 的主战场）

- 网络监听三件套（`chrome.debugger` Network domain 或 webRequest + 响应体捕获）
- 对话框 auto 策略 + alert/confirm/prompt 内容回读（content script 覆盖原生对话框）
- 批量数据抓取（相似元素循环 → 结构化表格）
- 元素句柄模型 + elementRef 元素库接入命令参数（横切差距 #4/#5）

### Phase 4 — 一等公民落位

扩展在线 → `transport` 默认 extension；playwright 显式回退（含设计期/headless 场景）；bsk 冻结不再投入。

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
| `transport` | 缺省 / `extension` / `playwright` / `bsk` | 走哪条执行通道 |
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
