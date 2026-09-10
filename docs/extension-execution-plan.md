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
- **transport 语义**：采纳 command-model §五-1 选项 A——`transport=extension/playwright/bsk`（通道），浏览器类型仅 playwright 通道保留。

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
- 对话框覆盖脚本需 `document_start` 注入时机，现有 `document_idle` 需按页面分组处理。
- 企业环境禁止加载未签名扩展 → 保留 playwright 回退路径为产品级能力，不是临时方案。
