# 元素自愈与执行前预检（M28 计划依据）

本文件记录两项外部调研结论与由此确定的计划：①`browser-use/jev-ultrafast`（MIT）的可借鉴点与
不可借鉴点；②我们自身**已声明但未实现**的参数漂移。结论已由维护者确认（2026-09-20）。

## 1. 参考对象：jev-ultrafast 是什么

LLM 浏览器 Agent：给一句自然语言目标，TypeSafe 的 Jev 策略模型每次从「原子快照生成的**有序元素表**」
里挑**操作 + 元素**；只有 `TYPE_TEXT` 才再调一次小模型生成文本。动作空间仅
`CLICK / TYPE_TEXT / SELECT / SCROLL_UP / SCROLL_DOWN / WAIT / DONE / BLOCKED`。

与我们的定位不同（我们是确定性 RPA：用户编排流程、可复现、无 LLM 决策、规则 6 禁动态执行），
**其决策层无参考价值，观测层与执行层价值高**。

## 2. 可借鉴（已确认纳入 M28）

| 机制 | 它的实现（`snapshot.js` / `browser.py`） | 我们的现状 |
|---|---|---|
| 元素语义快照 | `role` 归一化 + `accessibleName` 优先级链 + 容器文本 | ✅ M10 已借鉴（`extension/content.js`） |
| **运行期消费候选（元素自愈）** | 动作时按 `identity` 重新解析目标，**校验后才执行** | ❌ **只存不用**：`selector.candidates` 仅在 `model/capture.py` 校验，运行期 `page.call` 只传单个 `selector` |
| **执行前预检** | `isConnected` / `disabled` / `aria-disabled` / `inert` / `checkVisibility({checkOpacity,checkVisibilityCSS})` / rect 非零且在视口内 / **`e.contains(document.elementFromPoint(x,y))`（遮挡拒绝）** | ❌ 无：点在被覆盖元素上会**静默点到遮罩层** |
| **失败不盲重试** | 校验失败抛 `StalePage("Target changed or is covered. Observe again.")`，显式报错 | ⚠️ 只有 `ELEMENT_NOT_FOUND`，缺「被遮挡 / 不可见 / 禁用」分类 |
| 等待有用状态 | 输入后等 `[role=option]` 可见（≤200ms）；其它交互 ≤2 帧或 50ms | ⚠️ 无「等联想列表」语义（列为可选增强，暂不做） |

## 3. 不借鉴（含理由）

- **LLM 决策层 / 投机多头**：与我们「用户编排 + 可复现」的定位冲突，且违反规则 6 的精神。
- **Browser Harness daemon + CDP**：我们已刻意移除 playwright/debugger，收敛到「自研扩展 +
  Native Messaging」单通道（ADR 0013/0015）。
- **自有后台标签页 + `setDeviceMetricsOverride` 固定视口**：我们操作的是**用户已登录的真实浏览器**，
  不占标签页、不改用户窗口尺寸。
- **快照序号 id（`e1/e2`）**：它自己也只在一次决策周期内使用；与 M10 结论一致（可持久化的只有
  页面自身属性）。
- **P2b：新增 `mode: "insert"`（select-all + 单次插入）**——**维护者定案不做**（2026-09-20）。
  理由见下节：它的输入方式是速度/框架兼容技巧，**不是反检测手段**，而我们已有 `fill/type/clipboard` 三档。
- **P3：后台标签页焦点模拟（`Emulation.setFocusEmulationEnabled`）**——**不做**（2026-09-20）。
  证据：影刀体系**没有**类似设计，反而要求「目标窗口在前台有焦点」（社区实践额外用 Python
  `WindowFocusGuard` 抢焦点 / 多 Windows 桌面隔离），并提供**无头模式**作为配置项；加上我们
  **没有 CDP**，JS 层伪造 `hasFocus` 覆盖面有限、收益不确定。留作「若后台标签页可靠性成为
  实际问题再评估」。

## 4. 反检测口径（澄清一个常见误解）

jev 的输入路径是：`Input.dispatchMouseEvent`（浏览器级可信鼠标）→ select-all（平台感知修饰键）→
`Input.insertText` **一次性插入**。这是**速度 + 框架兼容**做法（一次插入 vs 逐字 N 次往返），
不是反检测技术：

- 它**完全不产生 keydown/keyup**——依赖按键事件驱动 UI 的控件（按键联想、掩码输入）会失效，
  按键盘节奏做风控的站点也会把「一个按键事件都没有」视为异常；
- 反检测的行业做法（影刀社区「风控专题」）恰恰相反：**逐字符模拟输入 + 字符间隔 0.1–0.3s +
  真人鼠标轨迹 + 随机延时 + 独立用户数据目录 + 屏蔽 `navigator.webdriver`**；
- 我们在这条轴上的既有优势：真实 profile/UA、无 `navigator.webdriver`、**无 CDP**（jev 用 CDP，
  而 CDP 本身可被检测）、扩展通道（与影刀同构）。

**缺口不在「输入方式」，而在「逐字间隔没生效」**——见下节。

## 5. 自查发现：两处「声明了但没实现」的参数漂移（缺陷）

`commands/browser/input.json` 声明与 `extension/background.js` 的 `input` 分支不一致：

1. **`keyIntervalMs` 被忽略**：manifest 写「逐字输入时的按键间隔（毫秒）」，扩展里是**同步逐字循环**
   （`for (const ch of ...)`，无任何延时）→ 用户设了间隔其实没生效；而逐字间隔正是风控场景最需要的参数。
2. **`clipboard` 模式未实现**：扩展只有 `mode === "set"` 分支，其余一律落进逐字分支 → 粘贴语义不存在。

这两项列为 M28 的 **P2a（提前于其它增强）**：属缺陷修复，不是新特性。

## 6. 未来：AI 按自然语言生成流程，这个项目有价值吗

**Jev 模型本身价值低**（托管付费 API；动作空间是浏览器通用 click/type/select/scroll；没有流程
AST/循环/条件/变量概念；不可自托管）。**它的架构模式价值高**，且与未来 AI 生成天然契合：

1. **受限动作空间**（「只提供受支持的操作与目标」）→ 模型只输出**我们 84 条命令目录 + 元素资产名 +
   参数**，由 compiler 静态校验；**模型输出永不成为选择器/JS/坐标**（与规则 6 同构）。
2. **观测表作为 grounding** → 我们 M10 已有，M28 的 P0 让它「可执行」；未来生成/修复步骤时，
   这张表就是「可选目标清单」，避免模型编造选择器。
3. **执行前校验 + 失败显式报错** → M28 的 P1。

**形态建议**：AI 生成放**设计期**（GUI/devserver）：自然语言 → 生成**流程草稿 AST**（catalog 命令 +
元素资产引用 + 变量别名）→ compiler 静态校验 + 预检 → 用户在 GUI 审阅/编辑后保存；**运行时永不
生成代码**。M25（运行历史）+ M27（工作台）正好是「生成 → 审阅 → 运行 → 复盘」的载体。
