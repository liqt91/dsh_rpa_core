# 浏览器元素捕获传输方案

状态：方案定稿（S1 已验证通过，2026-09-01，Edge 152）
关联：M8（ADR 0007 依据）、M10（实装）、S0 实测结论（M7 任务单）

## 1. 问题定义

浏览器元素捕获（hover/点击 → CSS selector + 元素元数据 → 填入编辑器属性面板）价值最高的场景是**带登录态的页面**（企业内网、小红书等后台）。约束条件：

- Chrome 136+ 出于反 infostealer 目的，封锁默认用户目录上的远程调试（`--remote-debugging-port` / `--remote-debugging-pipe` 均无效）。
- S0 实测（本机 Chrome 152）：启动参数方式无效；显式 `--user-data-dir` 指向默认目录同样无效；无企业策略可绕过。
- 结论：**"直接 attach 用户日常浏览器" 的经典路线已死**，需要重新选择传输通道。

## 2. 候选路线全景（调研结论）

### 2.1 持久 profile（launch_persistent_context）

- 机制：专用 `user-data-dir` 的托管 Chromium，登录一次 cookies 落盘，长期复用。
- 佐证：**Microsoft Playwright MCP 的默认方案与此完全一致**（`mcp-{channel}-{workspace-hash}` 专用持久 profile，"All the logged in information will be stored in the persistent profile"）——Chrome 136 封锁后行业收敛到同一答案。
- 代价：与日常浏览器是两份登录态（每站点登录一次）；profile 同一时刻只能被一个浏览器实例使用。
- 定位：**主路线**，零外部依赖，M10a。

### 2.2 chrome://inspect 用户授权调试开关（S1 已验证通过）

- 机制：Chromium 新增 `chrome://inspect/#remote-debugging`（Edge 为 `edge://inspect/#remote-debugging`）页面，用户手动开启 "Allow remote debugging for this browser instance"。
- S1 实测（2026-09-01，Edge 152.0.4191.53，用户日常浏览器 + 手动开启开关）：
  - 开关开启后 `DevToolsActivePort` 文件出现在用户数据目录（`%LOCALAPPDATA%\Microsoft\Edge\User Data\DevToolsActivePort`），首行端口 9222、次行浏览器 UUID 路径；**WebSocket URL = `ws://127.0.0.1:<port><path>`，从该文件读取即可，无需页面 UI 交互**。
  - `/json/version` 等 HTTP 发现端点 404（防扫描），与 S0 结论一致；显式 WS URL 直连成功。
  - Playwright `connect_over_cdp` 全链路可用：枚举 contexts/pages（用户真实标签页可见）、新开标签页导航/输入/读取、picker 注入（CSS selector 生成 + querySelectorAll 命中数回验 + 高亮渲染）、原生 CDP session（`Runtime.evaluate`）。
  - 连接断开不关闭浏览器；测试标签页可单独关闭，用户标签页不受影响。
  - 运维备注：百度首页搜索框被验证页隐藏（`#kw` not visible），搜索 URL 直达（`/s?wd=`）可靠——M10 捕获脚本优先用 URL 直达模式。
- 待验证：开关跨重启持久性（用户重启 Edge 后重读 `DevToolsActivePort` 即可确认）。
- 定位：官方 UI 授权、零扩展、零第三方、标准 CDP——**登录态页面捕获的最优载体**，已证实可行。

### 2.3 Panerelay（第三方桥，MIT）

- 机制：Web Store 现成扩展 + 本地 Node Bridge（Native Messaging）→ 对外暴露 CDP 兼容端点（`/cdp/playwright`），扩展经 `chrome.debugger` API 操作授权标签页——扩展权限的 CDP 不受 136 封锁影响。
- 优点：现成上架、授权边界完善（按 tab 授权、cookies 不外泄）、`playwright-cli attach` 官方支持其端点。
- 风险：个人维护项目（31★）；需 Node 20+；Python `connect_over_cdp` 兼容性需 S1 实测。
- 定位：2.2 不可用时的**次选**。

### 2.4 Playwright MCP 官方扩展

- 机制：微软官方扩展（`microsoft/playwright` 仓库 `packages/extension`）连接运行中浏览器；传输形态是 **MCP 工具调用**（`browser_evaluate` 等），非 CDP 端点。
- 定位：2.2 与 2.3 均不可用时的替代载体（捕获脚本经 MCP `browser_evaluate` 注入）；链路较绕，远期备选。

### 2.5 自研 MV3 扩展（旧项目路线）

- 机制：content script 捕获 + service worker 回传 dev server（旧项目 95KB runner 的精简版，全新写约 300 行）。
- 定位：**远期备选**——仅当 2.2/2.3/2.4 全部不可用时启动；好处是完全自主可控。

### 2.6 已排除路线

| 路线 | 排除原因 |
|---|---|
| CDP attach 默认 profile | S0 实测被 Chrome 152 封锁（136+ 上游策略） |
| profile 拷贝快照 | GB 级复制、app-bound 加密风险、快照过期（日常浏览器新登录不回流） |
| UIA 读 Chrome 无障碍树 | 只暴露 Name/ControlType，无法还原可靠 CSS selector |
| 代理抓包 | 抓流量不抓 DOM 元素 |
| 图像识别 | 仓库明确排除 |
| Firefox attach | Playwright 不支持挂用户 Firefox |

## 3. 决策

| 优先级 | 传输 | 场景 | 状态 |
|---|---|---|---|
| 主（M10a） | 持久 profile | 公共页 + 可接受登录一次的站点 | 已定 |
| 次（M10b） | chrome://inspect 授权开关（DevToolsActivePort WS URL） | 登录态页面、零重登录 | S1 已验证通过 |
| 备选一 | Panerelay | 同上，若 2.2 失效 | S1 备选（未启用） |
| 备选二 | Playwright MCP 扩展 evaluate 链路 | 同上 | 远期 |
| 兜底 | 自研扩展 | 完全自主可控 | 远期 |

dev server 端点契约（M8 定义）：`POST /api/capture/browser/start {transport: "persistent" | "user-browser", ...}`；`user-browser` 子类型定案为 **`chrome-inspect-ws`**（`userBrowserType` 参数），Panerelay/MCP 仅作降级备选记录。

## 4. S1 验证协议（已执行，2026-09-01）

1. ~~读取 chrome://inspect 页面展示的调试连接信息~~ → **实际更优**：开关开启后 `DevToolsActivePort` 文件落盘于 User Data 目录，直接读取端口 + 浏览器 UUID 构造 WS URL，零 UI 交互
2. ~~Playwright `connect_over_cdp(<url>)` 枚举~~ → 通过（Edge 152，1 context / 12 真实标签页，新标签页全可控）
3. ~~登录态断言 + picker 注入~~ → 登录态断言改期（用户未登录小红书）；picker 链路在 example.com 完整验证（selector 生成 → 命中回验 → 高亮）；百度搜索经 URL 直达操作并读回结果
4. 开关持久性：待用户重启 Edge 后重读 `DevToolsActivePort` 确认（唯一遗留项）
5. 结论已写入 ADR 0007 §4：`user-browser` 子类型定案 `chrome-inspect-ws`，运维方式 = 用户在 `edge://inspect` 开启开关 + 服务端读 `DevToolsActivePort`；降级链 2.3 → 2.4 → 2.5 保持记录但暂不启用

## 5. 顺带产出

- `browser.launch` 已具备 `userAgent` 参数与 automation 标志移除（M4.5 期间实现），持久 profile 模式的 manifest 扩展（`userDataDir`）在 M8 ADR 0007 中与捕获契约一并定型。
