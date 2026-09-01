# 浏览器元素捕获传输方案

状态：方案定稿（S1 验证待执行）
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

### 2.2 chrome://inspect 用户授权调试开关（S0 后新发现，实测进行中）

- 机制：Chrome 新增 `chrome://inspect/#remote-debugging` 页面，用户手动开启 "Allow remote debugging for this browser instance"。
- 本机实测（Chrome 152）：开启后 9222 端口开始监听，HTTP 服务响应存在，但**经典 `/json/*` 发现端点全部 404**——刻意的防扫描设计，连接需要显式 WebSocket URL（预期带会话 token，待从页面 UI 读取确认）。
- 若走通：官方 UI 授权、零扩展、零第三方、标准 CDP——**登录态页面捕获的最优载体**。
- 待验证（S1）：WebSocket URL 获取方式（UI 展示 / 本地枚举）、Playwright `connect_over_cdp` 兼容性、开关持久性（是否跨重启）、小红书登录态断言与 picker 注入回验。

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
| 次（M10b） | chrome://inspect 授权开关（S1 验证） | 登录态页面、零重登录 | S1 待验证 |
| 备选一 | Panerelay | 同上，若 2.2 失败 | S1 备选 |
| 备选二 | Playwright MCP 扩展 evaluate 链路 | 同上 | 远期 |
| 兜底 | 自研扩展 | 完全自主可控 | 远期 |

dev server 端点契约（M8 定义）：`POST /api/capture/browser/start {transport: "persistent" | "user-browser", ...}`，`user-browser` 的子类型由 S1 结论确定（chrome-inspect-ws / panerelay / mcp-extension）。

## 4. S1 验证协议（M10 前置）

1. 读取 chrome://inspect 页面展示的调试连接信息（WebSocket URL / token）
2. Playwright `connect_over_cdp(<url>)` → 枚举 contexts/pages → 定位 xiaohongshu 标签页
3. 断言登录态（头像等登录后元素）→ 注入 picker → hover 高亮 + 点击 → selector 生成并回验命中数
4. 重启 Chrome → 验证开关持久性（常驻传输 vs 捕获会话手动开启）
5. 结论写入 ADR 0007：传输子类型、运维方式（UI 提示流程）、降级链（2.2 → 2.3 → 2.4 → 2.5）

## 5. 顺带产出

- `browser.launch` 已具备 `userAgent` 参数与 automation 标志移除（M4.5 期间实现），持久 profile 模式的 manifest 扩展（`userDataDir`）在 M8 ADR 0007 中与捕获契约一并定型。
