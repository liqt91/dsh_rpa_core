# BrowserSkill 调研与 S2 验证记录

状态：调研完成 + S2 部分实测通过（2026-09-02,bsk 0.1.11 / 扩展 0.2.0 / Edge 152)
关联:M14 前置调研；capture-transport.md §3 决策表；计划调整（2026-09-02,M14 由自研扩展改为 bsk 单扩展路线）

## 1. 项目概况

`Tencent/BrowserSkill`(MIT,Rust CLI/daemon + MV3 扩展 WXT/React)：让 AI agent 使用用户**真实已登录浏览器**而不打断其工作。商店上架（Chrome Web Store / Edge Add-ons),Windows x64 支持。

架构三段式：

```text
Agent → shell → bsk CLI ──JSON Lines(命名管道)──> bsk daemon ──WebSocket(127.0.0.1:52800)──> 扩展 ──chrome.debugger(CDP 1.3)──> 页面
```

关键机制：

- 扩展**主动出向**连 daemon，无需 Native Messaging 注册；握手校验 `Origin: chrome-extension://…`，无 token 配对（安全模型 = loopback + Origin 白名单）;
- daemon 自启（`~/.bsk/daemon.lock`)，任何 bsk 命令自动拉起；
- session = 4 字母 id + 独立 Agent Window（与用户窗口隔离，共享同一 profile 登录态）+ 用户标签页 borrow/return；每 session RPC 串行队列；5 分钟空闲超时兜底，`session stop` 为强制义务；
- 上传/下载带 `effect_state`(`none/committed/unknown`)，与我们 effect 契约同构。

## 2. 与 rpa_core 的适配性结论

### 2.1 执行层：生产级、确定性，selector 可直通

源码实证（`tools/interaction.ts`):click/fill/select/press/hover 均接受 `{ref?, selector?}`,selector 路径走 `DOM.querySelector`——**我们的 CSS locator 契约直通，不碰 @eN ref 体系**。动作质量：真实 `Input.dispatchMouseEvent`（可信事件，先 move 再 press/release)、scrollIntoView + 跨 frame 坐标投影、fill 用原生 setter + `Input.insertText`(IME 安全）+ 补发 input/change(React/Vue 兼容）、AbortSignal 全链路（符合规则 11)、JS 对话框自动 accept。

已知差距（按规则 9 以能力声明处理）:

- selector 路径**仅主 frame**,iframe 需 ref 体系（我们不走）;
- CSS only；但 XPath 可经 evaluate 的 `document.evaluate()` 自行补齐，成本低；
- chrome.debugger "正在调试" infobar 在 session 期间常驻（bsk 在 session stop 时 detach 回收）。

### 2.2 evaluate 通道：注入 picker 合法且够用

`tools/evaluate.ts` 实证：`Runtime.evaluate` 薄封装（`awaitPromise`/`returnByValue` 默认 true，异常带行列号带内返回），沙盒校验同写命令（限 Agent Window / 已借用标签页）——设计红线是"不许当用户标签页的 token 窃取窗口",Agent Window 内注入恰是该沙盒的用途。SKILL.md 的 "last resort" 措辞是面向 LLM agent 的纪律，不是技术限制。

### 2.3 record 不能当捕获用（范式实证）

`record.ts` + `lib/describe-target.ts` 实证：trace 步骤为语义操作（click/fill/press/select/scroll/navigate/switch_tab)，目标描述仅 `role/name/tag/name_attr/placeholder/nearby_label` + 几何提示，**刻意不含 CSS selector/id/class/XPath**("LLM textbook" 哲学：回放靠 LLM 重新观察定位，refs 是 record-local hints)。S2 实测 trace 印证了这一点（步骤仅含 role/name,`unmatched: true`)。确定性 RPA 回放不能建立在 record 之上。

### 2.4 反检测分层（针对小红书类强风控站点）

| 层面 | 可否检测 | 说明 |
|---|---|---|
| 事件信任性 | 不可检测 | CDP 派发 `isTrusted=true` 真事件，与 Playwright 同机制 |
| `navigator.webdriver` | 不可检测 | chrome.debugger 不设自动化标志；且是用户真实浏览器 |
| 行为遥测 | 有风险 | 点击零鼠标轨迹（仅一次 mouseMoved 直达）、fill 走 insertText 无逐键事件、时序规律性——强风控站点可识别 |

结论：设计期捕获（低频、用户在场）风险低；高频无人值守执行强风控站点需实测。缓解后手（不进 M14)：逐键 press、合成轨迹、节奏随机化。

## 3. S2 实测记录（2026-09-02,Edge 152，用户日常浏览器）

通过项：

1. 安装链路：install.ps1 → bsk 0.1.11,daemon 自启，Edge 商店扩展 0.2.0 连接，doctor 全绿；
2. session start → navigate → `click --selector` → `fill --selector` → press Enter → evaluate 读回，必应搜索全链路通过（结果页 10 条命中）;
3. evaluate 注入 M10 picker 脚本（IIFE 包裹）→ 合成点击 → 轮询 `window.__rpaCaptureResult` → 返回完整描述符（selector + verifyCount=1 + tag/id/classes/text/rect)→ evaluate `querySelectorAll` 回验命中数，全链路通过。

发现的问题与解法：

- **ControlOverlay 全屏遮罩**:Agent Window 常驻扩展自带遮罩（`<browser-skill-overlay>`,open shadow root),pointer-events 拦截使 e.target 恒为遮罩，原版 picker 的 hover 框住整页。主世界无法隐藏它（扩展同步循环 500ms 内重断言 display/pointer-events)。**解法：picker 改用 `elementsFromPoint` 取坐标下元素栈、沿 shadow host 链跳过遮罩节点**——不修改遮罩，坐标语义不受遮挡影响；
- record 模式可绕过遮罩（实测用户可直接点击页面翻页），但 record_start 长占用 session 队列，**无法并发 evaluate 读 picker 状态**，此路不通；
- 百度首页 `#kw` 不可聚焦（fill 报 "Element is not focusable")，与 S1 已知现象一致，URL 直达模式规避。

待补验证（S2 遗留）:

1. 新版 picker(elementsFromPoint 变体）的真实用户点选验证（本次用户取消未完成）;
2. 小红书实测：登录态断言（S1 遗留）+ 捕获 + 低风险执行，观察风控反应；
3. iframe 页面 selector 路径盲区边界确认；
4. chrome.debugger infobar 长期观感确认。

## 4. M14 方向结论

**bsk 单扩展路线**（替代原"自研捕获扩展"):

- M14a 执行传输：`browser.launch transport:"bsk"`,executor subprocess 调 CLI,session 映射 + 取消时强制 `session stop`;manifest 声明能力差异（css/xpath、主 frame);
- M14b 捕获：devserver 经 bsk evaluate 注入 picker(elementsFromPoint 变体）→ 轮询读回 → ElementDescriptor 落库 → evaluate 回验；
- 自研扩展（WS + token 配对，设计已备）降为 evaluate 通道不够用时的兜底；chrome-inspect-ws 维持"零安装备选"。

范式备忘（拟入 ADR 0009)：BrowserSkill 的语义 trace + LLM 重定位是 agentic 自动化范式，抗 DOM 漂移但牺牲确定性；rpa_core 坚持确定性 selector 回放（effect/幂等/审计契约的前提），可吸收其语义元数据作为**设计期自愈**辅助，运行时不引入 LLM。
