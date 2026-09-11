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
- 待验证：~~开关跨重启持久性~~ **已确认（2026-09-02，Chrome 152 重启实测）**：开关持久（无需每次手动开启）；但浏览器 UUID 路径每次启动轮换（旧 WS URL 失效）——捕获服务端必须在每次会话启动时重新读取 `DevToolsActivePort`（M10 实装即为此方式，`devtools_active_port_url()` 每次读文件）。
- 定位：官方 UI 授权、零扩展、零第三方、标准 CDP——**登录态页面捕获的最优载体**，已证实可行且常驻可用。

### 2.3 Panerelay（第三方桥，MIT）——降为记录备查

- 机制：Web Store 现成扩展 + 本地 Node Bridge（Native Messaging）→ 对外暴露 CDP 兼容端点（`/cdp/playwright`），扩展经 `chrome.debugger` API 操作授权标签页——扩展权限的 CDP 不受 136 封锁影响。
- 优点：现成上架、授权边界完善（按 tab 授权、cookies 不外泄）、`playwright-cli attach` 官方支持其端点。
- 降级原因（2026-09-01 复评）：个人维护项目（31★）；需 Node 20+（违背仓库 Python/stdlib 取向）；能力被 M10c 自研扩展路线完全覆盖。**其按 tab 授权 UX 保留借鉴**。

### 2.4 Playwright MCP 官方扩展（机制已核实，作为 M10c 设计借鉴）

- 机制（2026-09-01 核实 `microsoft/playwright-mcp` README + `packages/extension`）：官方扩展（Chrome Web Store 上架）+ MCP server `--extension` 模式；扩展服务 worker **反向 WebSocket 连出**至 MCP server。
- **弹窗机制**：默认每次连接需在浏览器内批准；可把扩展 UI 展示的 profile 唯一 `PLAYWRIGHT_MCP_EXTENSION_TOKEN` 配置进客户端后自动重连、免弹窗——**token 配对是"每会话弹窗"的轻量正解，微软官方验证**。
- 多客户端隔离：每客户端一个标签组（颜色区分），只有组内标签页可达；拖拽进出改变授权范围；状态页可查看与逐个断开连接。
- 定位修订：**token 配对与标签组隔离 UX 并入 M10c 自研扩展设计**；MCP 工具调用形态本身不作为 rpa_core 传输链路（与进程内 API 定位不符、链路绕）。

### 2.5 自研 MV3 捕获扩展（M10c，已立项设计）

- 佐证：chrome-relay（MIT，2026-09 调研）与 Playwright MCP 官方扩展证明"扩展 attach 真实浏览器"是行业收敛答案；我们 M7 S0 结论（"live 复用登录态只有扩展路线"）与之一致。
- 两种子传输：
  - **主设计：扩展反连 dev server 的 WebSocket + token 配对**（Playwright MCP 机制）——dev server 已是本地 HTTP 服务，扩展 UI 展示 token、编辑器配置一次，机械量最小；
  - 备选：Native Messaging host（chrome-relay 机制）——零开放端口、Chrome 双向强制扩展 ID 白名单；host 协议仅为 stdin/stdout 长度前缀 JSON，**可用 Python 写**并消化进 `rpa_core`（dev server 子进程），但需注册 native-messaging manifest。
- 弹窗成本矩阵（定稿）：

  | 方案 | 连接批准成本 | 安装成本 |
  |---|---|---|
  | chrome-inspect-ws | 每次连接弹窗（不可绕过） | 零 |
  | 扩展默认（无 token） | 每次连接弹窗 | 装扩展 |
  | 扩展 + token 配对 | 一次配置，之后自动 | 装扩展 + 贴 token |
  | Native Messaging 配对 | 零弹窗 | 装扩展 + 注册 host |

- 定位：**M10c 立项设计、不在 M10 实装**——触发条件：M10b 的每会话弹窗在真实使用中构成疲劳，或需要零开放端口。

### 2.6 已排除路线

| 路线 | 排除原因 |
|---|---|
| CDP attach 默认 profile | S0 实测被 Chrome 152 封锁（136+ 上游策略） |
| profile 拷贝快照 | GB 级复制、app-bound 加密风险、快照过期（日常浏览器新登录不回流） |
| UIA 读 Chrome 无障碍树 | 只暴露 Name/ControlType，无法还原可靠 CSS selector；**2026-09-03 实测兜底进一步证伪**：hybrid 关闭时对没装扩展的 Chrome 网页正文 UIA 捕获命中渲染层 Pane（Intermediate D3D Window），verifyCount=29（29 个同型匹配，locator 不唯一不可用）——网页正文没有可用的 UIA 退路，网页捕获只有扩展/bsk 两条正路。UIA 的正确用途是桌面应用 + 浏览器 UI 骨架（TabStrip/Omnibox 等，已验证可达） |
| 代理抓包 | 抓流量不抓 DOM 元素 |
| 图像识别 | 仓库明确排除 |
| Firefox attach | Playwright 不支持挂用户 Firefox |

## 3. 决策

| 优先级 | 传输 | 场景 | 状态 |
|---|---|---|---|
| 主（M10a） | 持久 profile | 公共页 + 可接受登录一次的站点 | 已定 |
| **无缝捕获** | **自研 content-script 扩展**（`<all_urls>` content script + token 配对 + HTTP 回传） | **跨浏览器/跨页无缝 hover 捕获**（鼠标在 Chrome/Edge/各页面间移动框选不申请权限） | 已实装（2026-09-03）；实机验收待人工 |
| 次（M10b） | chrome-inspect-ws（DevToolsActivePort WS URL） | 登录态页面、零重登录 | S1 已验证通过；**每连接弹窗确认，高频场景降级为备选** |
| 记录备查 | Panerelay | —— | 降级（Node 依赖） |

> **2026-09-11 更新**：BrowserSkill(bsk) 捕获/执行链路已整体移除。网页捕获与执行统一收敛到**自研扩展 + playwright** 双通道，不再维护 bsk 三方件能力。

**2026-09-03 三次修订（无缝捕获场景 + 重估）**：维护者场景——Chrome/Edge 同时开、各若干页面，捕获模式下鼠标在浏览器/页面间移动无缝框选、零逐次授权。重估结论：**bsk 的 Agent Window + 借用模型结构性不满足**（borrow 搬动标签页 + 每次借用内嵌确认），无缝捕获只有自研 content-script 扩展（`<all_urls>` content script 天然全页面注入）原生支持。**通道分工定稿（后于 2026-09-11 移除 bsk）**：自研扩展 = 设计期捕获主路线 + 运行期执行；桌面 hover = 非浏览器区域。自研扩展不走 WS（stdlib http.server 无 WS 服务端），改 HTTP POST 回传 + pending 短轮询。重估时顺带核实 Playwright MCP 官方 README 已劝 coding agent 走 CLI+SKILLS（佐证 ADR 0006 §6 CLI 优先），且其 ref 体系（@eN 会话局部）与我们确定性 selector 回放契约冲突，维持不作传输。

**2026-09-02 二次修订（S2 后）**:M14 由"自研捕获扩展"调整为接入 Tencent/BrowserSkill（单扩展、商店分发、免弹窗、顺带获得登录态执行能力）。**该路线已于 2026-09-11 移除**，网页捕获/执行回归自研扩展 + playwright。自研扩展方案一度保留为兜底，现为捕获主路线。

**2026-09-02 修订**：chrome-inspect-ws 虽开关跨重启持久，但**每次 WebSocket 连接都弹窗确认**，高频捕获下疲劳成本不可接受；M10c（自研扩展 token 配对）提前为 M14 实装，`extension-ws` 成为登录态捕获的优先传输，chrome-inspect-ws 降为"扩展未安装时的无安装成本备选"。

dev server 端点契约（M8 定义）：`POST /api/capture/browser/start {transport: "persistent" | "user-browser" | "extension", ...}`；`user-browser` 子类型现有 **`chrome-inspect-ws`**（弹窗式）；自研扩展 `extension` 为捕获主传输。

## 4. S1 验证协议（已执行，2026-09-01）

1. ~~读取 chrome://inspect 页面展示的调试连接信息~~ → **实际更优**：开关开启后 `DevToolsActivePort` 文件落盘于 User Data 目录，直接读取端口 + 浏览器 UUID 构造 WS URL，零 UI 交互
2. ~~Playwright `connect_over_cdp(<url>)` 枚举~~ → 通过（Edge 152，1 context / 12 真实标签页，新标签页全可控）
3. ~~登录态断言 + picker 注入~~ → 登录态断言改期（用户未登录小红书）；picker 链路在 example.com 完整验证（selector 生成 → 命中回验 → 高亮）；百度搜索经 URL 直达操作并读回结果
4. 开关持久性：待用户重启 Edge 后重读 `DevToolsActivePort` 确认（唯一遗留项）
5. 结论已写入 ADR 0007 §4：`user-browser` 子类型定案 `chrome-inspect-ws`，运维方式 = 用户在 `edge://inspect` 开启开关 + 服务端读 `DevToolsActivePort`；降级链 2.3 → 2.4 → 2.5 保持记录但暂不启用

## 5. 顺带产出

- `browser.launch` 已具备 `userAgent` 参数与 automation 标志移除（M4.5 期间实现），持久 profile 模式的 manifest 扩展（`userDataDir`）在 M8 ADR 0007 中与捕获契约一并定型。
