# rpa_core 扩展（捕获 + 执行通道）

MV3 零构建扩展（Chrome/Edge），两条通道：

1. **捕获通道**：捕获模式下在任意页面 hover 高亮、Ctrl+Click 捕获元素描述符回传 rpa_core devserver。无缝跨浏览器/跨页（无标签页借用、无逐次授权）。
2. **执行通道（M15，一等公民，唯一通道）**：长轮询 devserver 命令队列，在**用户真实已登录浏览器**里执行浏览器自动化（tabs / scripting / cookies / webNavigation）。rpa_core 的 `browser.*` 命令统一走本通道；`playwright` / `bsk` 已彻底移除，无回退。

## 安装（开发者模式）

1. Chrome：`chrome://extensions` → 开「开发者模式」→「加载已解压的扩展程序」→ 选本目录
2. Edge：`edge://extensions` → 同上
3. 两个浏览器都装 → 跨浏览器无缝捕获

> 也可用 `rpa-core install-extension`（默认输出开发者模式引导步骤并尝试打开目录）或
> 编辑器工具条「⇲ 插件」引导页自动打开本目录/复制路径。chrome:// 与 edge:// 扩展页
> 需手动在地址栏输入（外部无法直接导航）。详见 `docs/extension-install.md` §6.5.1。

## 配对（已移除，无需配对）

捕获/执行通道**无 token 配对**（devserver 仅绑定 `127.0.0.1`，loopback 本地工具不做应用层鉴权）。扩展首次加载并轮询 devserver 即可用，无需任何手动配对操作。

## 使用

- 编辑器元素库「＋捕获」（或 `rpa-core capture browser --transport extension`）发起捕获 → 扩展 content script 在所有页面激活 hover 高亮 → 鼠标移到目标 Ctrl+Click 捕获 → 描述符回传编辑器（可改名/改 selector 后入库）
- Esc 取消；普通点击不捕获（可正常导航）
- 网页 DOM 内容归本扩展；浏览器 UI 骨架（标签栏/工具栏）与桌面应用归桌面 hover 捕获（`rpa-core capture desktop --hover`）

## 协议（自测用）

捕获通道：

- `GET /api/capture/extension/pending` → `{pending, sessionId}`
- `POST /api/capture/extension/result` body `{sessionId, descriptor}`
- 短轮询：捕获激活 1.2s / 空闲 5s

执行通道：

- `GET /api/ext/command/next?wait=20&host=msedge&ver=...&platform=...&ua=...` → `{command: {id, op, args} | null}`（长轮询；空转即心跳，同时上报宿主浏览器身份）
- `POST /api/ext/command/result` body `{id, ok, value}` 或 `{id, ok:false, error:{code,message}}`
- `POST /api/ext/command/submit` body `{op, args, timeoutSeconds}` → 宿主侧（`rpa-core run` 子进程）提交命令并等结果
- `GET /api/ext/status` → `{online, lastPollSecondsAgo, queued, inflight, permissions, host}`
- `GET|POST /api/ext/permissions` → 权限查询/收窄

## 用哪个浏览器

`browser.*` 命令统一走自研扩展单通道：**扩展装在哪个浏览器，执行就在哪个浏览器**（没有"启动/切换浏览器"概念）。`transport` / `channel` 参数已随 playwright 一并移除，`browser.navigate` 不再暴露。

> 想操作浏览器（如 Edge）上的真实已登录会话：把本插件装到 Edge，其余留空即可。

编辑器顶部「扩展通道」徽标显示本插件的在线状态与宿主浏览器（5s 刷新）；插件弹窗里也能看到宿主浏览器和 devserver 识别状态。

## 执行权限（默认：整个浏览器）

- 默认 `{"mode": "browser"}`：全部窗口、全部标签页、全部 Cookie —— 与「扩展是一等公民」的定位一致，命令不做范围限制。
- 预留收窄模式（接口已立，两端同名同语义）：
  - `{"mode": "tabs", "tabIds": ["123"]}`：仅允许操作指定标签页
  - `{"mode": "origins", "allow": ["https://example.com"]}`：仅允许指定站点
- 校验点两处：宿主 `ExtensionExecHub.allows()`（下发前拦截，返回 `PERMISSION_DENIED`）+ 扩展 `assertAllowed()`（执行前拦截）。
- popup 可选切换模式；`tabIds` / `allow` 列表由宿主或人工写入 `chrome.storage.local.rpaExecPermission`。

> 注意：扩展不代管用户标签页生命周期——`browser.close` 对扩展会话只做解绑，不关闭用户的标签页。
