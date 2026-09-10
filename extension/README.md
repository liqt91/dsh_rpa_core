# rpa_core 扩展（捕获 + 执行通道）

MV3 零构建扩展（Chrome/Edge），两条通道：

1. **捕获通道**：捕获模式下在任意页面 hover 高亮、Ctrl+Click 捕获元素描述符回传 rpa_core devserver。无缝跨浏览器/跨页（无标签页借用、无逐次授权）。
2. **执行通道（M15，一等公民）**：长轮询 devserver 命令队列，在**用户真实已登录浏览器**里执行浏览器自动化（tabs / scripting / cookies / webNavigation）。rpa_core 的 `browser.*` 命令在扩展在线时默认走本通道，`playwright` / `bsk` 为二等回退。

## 安装（开发者模式）

1. Chrome：`chrome://extensions` → 开「开发者模式」→「加载已解压的扩展程序」→ 选本目录
2. Edge：`edge://extensions` → 同上
3. 两个浏览器都装 → 跨浏览器无缝捕获

> 也可用 `rpa-core install-extension`（默认输出开发者模式引导步骤并尝试打开目录）或
> 编辑器工具条「⇲ 插件」引导页自动打开本目录/复制路径。chrome:// 与 edge:// 扩展页
> 需手动在地址栏输入（外部无法直接导航）。详见 `docs/extension-install.md` §6.5.1。

## 配对（自动，零操作）

扩展首次轮询 devserver 时自动生成 token 并携带——devserver **TOFU（首次接触自动采纳）**并持久化到 `workflows/.capture-extension-token`，之后只认这个 token。无需任何手动配对操作。popup 里可查看 token / 重新生成（重新生成后需删除 devserver 的 `.capture-extension-token` 文件再轮换）。

## 使用

- 编辑器元素库「＋捕获」（或 `rpa-core capture browser --transport extension`）发起捕获 → 扩展 content script 在所有页面激活 hover 高亮 → 鼠标移到目标 Ctrl+Click 捕获 → 描述符回传编辑器（可改名/改 selector 后入库）
- Esc 取消；普通点击不捕获（可正常导航）
- 网页 DOM 内容归本扩展；浏览器 UI 骨架（标签栏/工具栏）与桌面应用归桌面 hover 捕获（`rpa-core capture desktop --hover`）

## 协议（自测用）

捕获通道：

- `GET /api/capture/extension/pending`（头 `X-Capture-Token`）→ `{pending, sessionId}`
- `POST /api/capture/extension/result`（头 `X-Capture-Token`）body `{sessionId, descriptor}`
- 短轮询：捕获激活 1.2s / 空闲 5s

执行通道（`X-Capture-Token` 同一 token）：

- `GET /api/ext/command/next?wait=20&host=msedge&ver=...&platform=...&ua=...` → `{command: {id, op, args} | null}`（长轮询；空转即心跳，同时上报宿主浏览器身份）
- `POST /api/ext/command/result` body `{id, ok, value}` 或 `{id, ok:false, error:{code,message}}`
- `POST /api/ext/command/submit` body `{op, args, timeoutSeconds}` → 宿主侧（`rpa-core run` 子进程）提交命令并等结果
- `GET /api/ext/status` → `{online, lastPollSecondsAgo, queued, inflight, permissions, host}`
- `GET|POST /api/ext/permissions` → 权限查询/收窄

## 如何指定「走本插件」和「用哪个浏览器」

浏览器指令（如「打开网页」`browser.navigate`）两个参数：

- `transport`：**不填即缺省走本插件**（在线时）；`extension` = 强制（不可用时报错，不静默回退）；`playwright` = 强制独立自动化浏览器。
- `channel`：**本插件通道下是校验，不是启动**——本插件装在哪个浏览器，执行就在哪个浏览器。`channel=msedge` 表示"要求宿主是 Edge"，不匹配时缺省通道会自动让位给 playwright，显式 `transport=extension` 则直接报错。`chromium` = 任意 Chromium 内核发行版；`firefox`/`webkit` 不支持（插件只跑 Chromium 内核）。

> 想让流程操作用户真实已登录的 Edge：把本插件装到 Edge，其余留空即可。
> 想开一个干净的 Edge：`transport=playwright` + `channel=msedge`。

编辑器顶部「扩展通道」徽标显示本插件的在线状态与宿主浏览器（5s 刷新）；插件弹窗里也能看到宿主浏览器和 devserver 识别状态。

## 执行权限（默认：整个浏览器）

- 默认 `{"mode": "browser"}`：全部窗口、全部标签页、全部 Cookie —— 与「扩展是一等公民」的定位一致，命令不做范围限制。
- 预留收窄模式（接口已立，两端同名同语义）：
  - `{"mode": "tabs", "tabIds": ["123"]}`：仅允许操作指定标签页
  - `{"mode": "origins", "allow": ["https://example.com"]}`：仅允许指定站点
- 校验点两处：宿主 `ExtensionExecHub.allows()`（下发前拦截，返回 `PERMISSION_DENIED`）+ 扩展 `assertAllowed()`（执行前拦截）。
- popup 可选切换模式；`tabIds` / `allow` 列表由宿主或人工写入 `chrome.storage.local.rpaExecPermission`。

> 注意：扩展不代管用户标签页生命周期——`browser.close` 对扩展会话只做解绑，不关闭用户的标签页。
