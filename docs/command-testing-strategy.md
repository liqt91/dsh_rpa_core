# 指令测试策略（初版）

状态：初版（2026-09-20，待维护者评审后细化）
关联：`AGENTS.md`（规则 6/7/11）、ADR 0013（浏览器执行收敛为扩展单通道）、M28（元素自愈与预检）、
`docs/command-inventory.html`（命令清单现状）

## 1. 问题与目标

**问题**：命令面已到 84 条（浏览器 30 / 桌面 UIA 17 / 桌面 Win32 19 / 数据 14 / 工作流 4 等），
每条又有若干参数与枚举分支，组合空间远超人工测试能力——「全部人工测一遍」不可行；
而现有浏览器契约测试只覆盖「会话缺失 / 扩展离线」两类负路径（6 项），
**参数级行为、输出契约、枚举分支基本没有自动化覆盖**。

**目标**：用**分层自动化**把「每条命令 × 每个参数取值」的验证规模化，人工只保留无法自动化的部分。

**核心原则：测试的价值在「测全参数」**。只验证「命令能跑通」几乎无价值——真正的缺陷
（如 M28 S3 查出的 `keyIntervalMs` 被静默忽略、`clipboard` 形同虚设）都藏在**参数分支**里。
因此每层的验收口径都是「该命令的**每个参数 × 每个枚举值/边界**都有断言」，而不是「跑一次成功」。

## 2. 四层结构

| 层 | 依赖 | 覆盖对象 | 能否进默认门禁 |
|---|---|---|---|
| L1 契约（进程内） | 假扩展 / 假桌面 | 参数整形、输出契约、错误码、枚举分支 | ✅ 默认门禁 |
| L2 真机冒烟（本地） | 真实扩展/桌面 + 本地 HTTP 页 | 真实行为（DOM 读写、命中、遮挡） | ⚠️ 可选门禁（需环境） |
| L3 真机 E2E（场景） | 真实浏览器/桌面 + 真实站点 | 关键路径端到端 | ❌ 手动/按需 |
| L4 人工确认（不可替代） | 人 | 观感、手感、风控、登录态 | ❌ 手动 |

### L1 契约层（默认门禁，主力）

**两个断言维度**（缺一不可）：

1. **输入侧**：每个参数都真正改变行为——枚举值逐个跑并断言**产生不同的调用/副作用**；
   数值边界（0 / 上限 / 非法）跑并断言**钳制或显式失败**，绝不允许静默忽略。
2. **输出侧**：`outputs` 必须**符合该命令 manifest 的 `output_schema`**、`effect.kind` 与
   manifest 声明一致、`errors` 只出现 manifest 声明过的码。

**实现方式**：一个「命令用例表」驱动的参数化契约测试——对每条命令声明
`{command, 变体参数, 期望的扩展调用/结果}`，逐项执行并断言。表与 catalog **强绑定**
（新增命令不补用例即门禁失败）。

**现状缺口**：这张表和这套断言目前**不存在**（`test_browser_contract.py` 仅 6 项负路径）。
本策略落地时优先补浏览器 30 条（唯一执行通道、参数最密）。

### L2 真机冒烟层（可选门禁）

本地起 HTTP 服务（静态测试页，覆盖 input/select/checkbox/表格/滚动容器/遮挡层/iframe/
Shadow DOM/动态渲染），用**真实扩展**连通后逐条跑**无副作用或幂等**命令，断言真实 DOM 结果：

- 无副作用/幂等：`navigate`（goto/back/forward/reload）、`getText`（text/html/outerHTML/value/href）、
  `queryAll`、`getSelectOptions`、`getPosition`、`getScrollPosition`、`scroll`、`check`、
  `select`、`screenshot`、`executeScript`、`waitFor`（visible/hidden/detached/attached）、
  `waitLoad`、`stopLoading`、`cookie*`、`listPages`、`attach`
- 有副作用：`click` / `input`（三种模式 × append × pressEnter × keyIntervalMs）、`drag`、
  `setValue`、`setAttribute`、`hover`、`upload` / `download` / `handleDialog`
  → 用**专用测试页 + 复位**隔离，仍可自动化（断言页内状态变化，如 `window.__events`）
- 参数覆盖：与 L1 同表驱动（**同一份用例表**，只是执行后端从假扩展换成真扩展）

**关键价值**：M28 S3 那类「声明了不生效」的漂移，L1 能抓住；但「真实浏览器里到底有没有生效」
（如 `Input.insertText` 是否被框架接受、遮挡时是否真被拒）必须 L2。

### L3 真机 E2E（按需）

真实站点/真实登录态的关键路径（打开网页→读元素→写文件、百度示例等）。不进默认门禁
（依赖外网与登录态），按需跑并留证据。

### L4 人工确认（最小化）

只留三类无法自动化的：**观感与手感**（逐字输入节奏、报错可读性、GUI 交互）、
**风控/反检测**（是否被目标站点识别）、**登录态与真实副作用**（下单/提交类）。

## 3. 桌面侧（UIA / Win32）

同上分层，但 L2 依赖真实桌面：

- 现成测试应用（WinForms fixture，M2.2）+ 记事本垂直切片可作 L2 载体；
- 参数覆盖重点：`click`（clickType/button/modifiers/clickPosition/simulateHuman/postDelayMs）、
  `input`（mode/append/keyIntervalMs/clickBeforeInput/postDelayMs）、`attachWindow`（matchMode）、
  `findElement`（locator 各字段）、窗口操作族（state/visible/move/resize）；
- **注意**：桌面命令会抢前台焦点，L2 必须显式启用（沿用现有 `--with-desktop-e2e` 约定），
  且用例要自带 `_force_foreground` 兜底。

## 4. 与门禁的关系

- **默认门禁**：L1（全部）+ 现有 L2 子集（桌面 E2E 默认关闭，`--with-desktop-e2e` 开启）
- **可选门禁**：`--with-browser-e2e`（新增）：L2 浏览器层（需本机装扩展；无扩展则 skip）
- 门禁内的**反漂移断言**（沿用现有 node 脚本模式）：参数被声明就必须被消费——
  见 `scripts/check_input_helpers.mjs`（`keyIntervalMs`/`clipboard` 那次漂移的守卫）

## 5. 落地顺序（建议）

1. **补 L1 浏览器命令用例表 + output_schema 校验**（最高优先：唯一执行通道、当前零覆盖）
2. **补 L1 桌面命令用例表**（UIA/Win32 侧重参数整形与错误分类）
3. **搭 L2 浏览器冒烟**（本地测试页 + 真扩展 + 与 L1 共用用例表）
4. **L2 桌面对齐**（复用 WinForms fixture；`--with-desktop-e2e` 内扩展）
5. L3/L4 维持按需与人工

## 6. 已知边界（不要当缺陷反复追问）

据 M28 调研（与 `browser-use/jev-ultrafast` 的 MVP 边界一致）：**Shadow DOM、iframe、
canvas 元素、上传/下载对话框、弹窗标签页、嵌套滚动、任意键盘控件** 均不在当前实现范围；
`browser.upload/download/handleDialog` 在扩展通道明确返回「暂未实现」。
这些应作为**显式 skip 用例**登记（有记录、不静默），待后续立项再启用。

## 7. 待维护者决策

1. L2 浏览器冒烟是否需要（需本机装扩展；CI 上通常不可用，只作本地可选门禁）？
2. 用例表的**声明形式**：Python 表（与现有测试同仓）还是 JSON（可同时喂给 L1/L2 与文档）？
3. 覆盖率口径是否要**机器校验**（如「每条命令至少 N 个参数变体」不达标即门禁失败）？
