# 指令测试策略（初版）

状态：初版（2026-09-20，维护者已评审并定案关键取舍）；**L1 契约矩阵三条通道已全部建表**
（M38，2026-09-22——浏览器 32 / 数据+工作流 18 / 桌面 36，共 **86 条命令 / 427 个变体**，
见 `.harness/tasks/M38-command-matrix.md`）；其中**桌面那 36 条的执行层尚未在真机上跑过**
（见 §2「落地进展」与 M38 任务单 §1.5）
定位：**执行层按需启用、不随默认门禁跑**（见 §4）；
**覆盖率静态校验进默认门禁**（2026-09-22 定案）
关联：`AGENTS.md`（规则 6/7/11）、ADR 0013（浏览器执行收敛为扩展单通道）、M28（元素自愈与预检）、
`docs/command-inventory.html`（命令清单现状）

## 1. 问题与目标

**问题**：命令面已到 86 条（浏览器 32 / 桌面 UIA 17 / 桌面 Win32 19 / 数据 17 / 工作流 1
——2026-09-22 实测计数；此前这里写的 84 条是 M29–M37 一批命令增删之前的旧值），
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

**落地进展**：M38（2026-09-22）已补**浏览器 32 条**（唯一执行通道、参数最密）——
`tests/commands/cases/browser.json`（180 个变体）+ 公共假扩展 `tests/commands/harness.py`；
**数据/工作流 18 条**（`cases/data.json` 17 + `cases/workflow.json` 1，共 88 个变体）已交付——
注意这一通道与前一片的**插桩点不同**：它**零参数漂移**（M30 已确认），补的是**输出契约与错误分支**，
且数据命令是纯 Python 文件操作，`PythonWorkerExecutor` **真起子进程**（真子进程就是真机），
一次测到「`inputs` 决定的行为 / worker 回的 `outputs` 形状 / 磁盘上留下了什么」三面。
两片共用同一套 `expect` 语义与同一份驱动层（`tests/commands/matrix.py`），并新增**执行前安全阀**
（`tests/commands/guard.py`：这条通道真会 `deletePath + recursive`，用例表写错就会删到目录外；
其契约测试进默认门禁）。桌面 **36 条也已建表**（M38 S2，2026-09-22）：`cases/desktop.json`（UIA 17 条 / 78 变体）+
`cases/desktop_win32.json`（Win32 19 条 / 81 变体），驱动与装配是
`tests/commands/test_desktop_matrix.py` / `test_desktop_win32_matrix.py` / `desktop_harness.py`。
这一片的插桩点与前两片都不同——**它没有桩**：按维护者定案走真桌面 fixture（见 §3），
断言的是真窗口上产生的错误码与会话/元素契约。`PENDING_NAMESPACES` 至此清空，
**86 条命令全部有表**。但 S2 只完成了「建表」：**执行层尚未在真机上跑过**
（维护者 2026-09-22 指令「建表吧，建完不测」），未实测的期望与靶子缺口（可拖/下拉控件、
菜单栏、Win32 可定位控件等）逐条登记在 M38 任务单 §1.5，启用方式见 §4 那张表。
落地时 M38 顺手给出了本策略预判之外的两个收益：**实测出两个静态门禁抓不到的死参数**、
以及**把「打桩边界沿真实副作用面划」这条教训推广到进程面**（详见 M38 任务单 §3/§4）。

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
- **L1 的载体已定（M38 S2.1，2026-09-22 维护者裁决）**：走**真桌面 fixture**，不走「打桩
  pywinauto/Win32 绑定层」那条更便宜的路——绑定层桩掉之后只剩「桩被怎么调用」，而桌面侧要的
  证据本来就在真窗口上。fixture 已被验证能承载这件事（`RPA_DESKTOP_E2E=1`，一次 ~11s），并且
  已经用它补上了桌面通道的**「暂停/继续」真机证据**（跨进程 `resume` 续接同一窗口 / 会话与元素
  都接回 / 已完成节点零重跑）；装配（编译/启动/抢前台/清理）抽在 `tests/e2e/desktop_fixture.py`，
  S2 建表时直接复用。详见 M38 任务单 §1.4。
- **win32 后端要另做靶子**：它的定位器认 title/class/controlId，而现有 fixture 的控件是 UIA
  `AutomationId`——S2.1 只覆盖了 uia 后端的真机路径（win32 的还原目前只有契约测试）。

## 4. 与门禁/CI 的关系（定案：按需启用，不进默认 harness）

**这套测试是「按需启用的独立测试」，不并入默认 harness 门禁**（维护者定案 2026-09-20）。
理由：L1 的参数矩阵随命令面增长而线性膨胀（84 条 × 每参数多取值，规模是现有门禁的数倍），
把它压进每次提交都要跑的门禁会显著拖慢开发回环；而它要抓的是**周期性/发布前的系统性覆盖**，
不是每次改动的即时反馈。

| 场景 | 跑什么 | 怎么跑 |
|---|---|---|
| 日常提交 | 现有 `check_all.py`（含既有 740 项测试与 node 反漂移脚本） | 不变 |
| **按需：指令覆盖（执行层）** | L1 全量参数矩阵（跑变体） | `RPA_COMMAND_MATRIX=1 pytest tests/commands`（缺省不收集，见 `tests/commands/conftest.py`）。**桌面那两片要再开一个开关**：`RPA_COMMAND_MATRIX=1 RPA_DESKTOP_E2E=1 pytest tests/commands`——它们会真实开窗并抢前台 |
| **默认门禁：用例表对齐（静态层）** | 用例表与 catalog 是否对齐（不执行命令） | `.harness/scripts/check_command_matrix.py`（已进 `check_all.py`，毫秒级） |
| 按需：真机冒烟 | L2（浏览器需本机装扩展；桌面抢前台） | 显式开关（沿用 `--with-desktop-e2e` 约定，浏览器侧同类开关） |
| 发布前 / 改命令面后 | L1 + L2 一起 | 同上，人工触发 |

**保留在默认门禁里的**是两类**静态、毫秒级**的断言：

1. **反漂移断言**（沿用现有 node 脚本模式）：「manifest 声明了的参数必须被消费」——
   如 `scripts/check_input_helpers.mjs`（`keyIntervalMs` / `clipboard` 那次漂移的守卫）；
2. **用例表覆盖率校验**（M38，2026-09-22 定案新增）：`.harness/scripts/check_command_matrix.py`——
   只读用例表 JSON 与 manifest、**不执行任何命令**，却能在每次提交拦住「新增命令没补用例」
   「枚举加了值没人改表」「用例表随时间腐烂」这类问题。全量矩阵的**执行**仍按需。

## 4.1 定案的三项取舍（维护者 2026-09-20）

1. **L2 浏览器冒烟：要**（本地可选，需本机装扩展；无扩展时 skip 而非失败）。
2. **用例表声明形式：JSON**——一份数据同时喂 L1（假扩展）与 L2（真扩展），并可被清单页/文档消费，
   避免「文档与用例脱节」；表与 catalog 强绑定（新增命令不补用例即失败）。
3. **覆盖率机器校验：要**——按命令声明**最少参数变体数**，不达标即失败（防用例表随时间腐烂）。
   具体口径见 §5.2。

## 5. 落地顺序（定案）

0. **M28 S2（执行前预检与错误分类）先行**——它会新增错误码与行为分支（`ELEMENT_COVERED` /
   `ELEMENT_DISABLED` / `ELEMENT_NOT_VISIBLE`），用例表最好一次性把这些码纳进去，
   避免表刚建就改（维护者认可）。
1. **L1 浏览器命令用例表 + 输出契约校验**（最高优先：唯一执行通道、当前零覆盖）
2. **L1 桌面命令用例表**（UIA / Win32：参数整形 + 错误分类）——**已建表（M38 S2，2026-09-22）**：
   两表 + 两驱动 + 共享装配，`PENDING_NAMESPACES` 清空；**执行层尚未真机跑过**，见 §2 落地进展
3. **L2 浏览器冒烟**（本地 HTTP 测试页 + 真扩展 + **与 L1 共用同一份 JSON 用例表**）
4. **L2 桌面对齐**（复用 WinForms fixture / 记事本切片；显式开关内跑）
5. L3 / L4 维持按需与人工

### 5.1 用例表结构（JSON 草案）

位置建议 `tests/commands/cases/<namespace>.json`（如 `browser.json` / `desktop.json` / `data.json`），
每条命令一个条目，**参数变体逐项声明 + 期望**：

```json
{
  "browser.input": {
    "layer": ["L1", "L2"],
    "variants": [
      {"name": "default-mode-is-fill",
       "inputs": {"selector": "#kw", "text": "hi"},
       "expect": {"channelArgs": {"mode": "fill"}, "outputsSchema": true}},
      {"name": "type-mode-with-interval",
       "inputs": {"selector": "#kw", "text": "hi", "mode": "type", "keyIntervalMs": 200},
       "expect": {"channelArgs": {"mode": "type", "keyIntervalMs": 200}}},
      {"name": "clipboard-rejected-is-explicit-error",
       "inputs": {"selector": "#rich", "text": "hi", "mode": "clipboard"},
       "expect": {"errorCode": "EXECUTOR_FAILED",
                  "errorDetails": {"reason": "input_mode_not_accepted"}}},
      {"name": "unknown-mode-falls-back-to-fill",
       "inputs": {"selector": "#kw", "text": "hi", "mode": "paste"},
       "expect": {"channelArgs": {"mode": "fill"}}}
    ]
  }
}
```

- **L1** 断言 `channelArgs`（下发给扩展/桌面的参数）与 `errorCode`/`errorDetails`；
- **L2** 断言真实结果（`outputs` 值、DOM 状态、页内事件）；
- 两边**共用同一份表**，`expect` 里各自关心的键互不干扰。

### 5.2 覆盖率机器校验（定案：要）

一个独立校验器读取用例表 + catalog，逐条检查并**失败即报**：

1. **命令覆盖**：catalog 每条命令都在表里出现（可标注 `skip` 并写明原因与依据）；
2. **参数覆盖**：命令 `input_schema.properties` 的**每个参数**至少被一个变体显式设置过；
   枚举参数**每个枚举值**至少出现一次；
3. **边界覆盖**：声明了 `minimum`/`maximum`/`minLength` 的参数至少有「合法值 + 边界/非法值」两个变体；
4. **最少变体数**：每条命令的变体数不低于声明阈值（缺省 3；有副作用的指针/键盘类可更高）；
5. **必填负路径**：`required` 字段缺省时至少一个变体断言失败（错误码与声明一致）。

不达标即 **按需测试的校验器**报错（不阻塞日常门禁，但发布前必须清账）。

## 6. 已知边界（不要当缺陷反复追问）

据 M28 调研（与 `browser-use/jev-ultrafast` 的 MVP 边界一致）：**Shadow DOM、iframe、
canvas 元素、上传/下载对话框、弹窗标签页、嵌套滚动、任意键盘控件** 均不在当前实现范围；
`browser.upload/download/handleDialog` 在扩展通道明确返回「暂未实现」。
这些应作为**显式 skip 用例**登记（有记录、不静默），待后续立项再启用。

## 7. 已定案 / 仍开放

**已定案（2026-09-20）**：L2 要（可选启用）；用例表用 JSON；覆盖率要机器校验；
本测试按需启用、**不进默认 harness**；实现顺序上 **M28 S2 先行**。

**已定案（2026-09-22，M38 开工时）**：

1. **覆盖率校验拆两层**——静态层（用例表与 catalog 对齐）**进默认门禁**；执行层（跑变体）按需。
2. **「required 缺省必须被拒」口径修正为「每条命令 ≥1 个负路径变体」**：输入 schema 的 `required`
   由 orchestrator 统一校验（`Draft202012Validator`），执行器层不是该责任点。

**仍开放**（实现时再定，不影响开局）：

1. L1 的 `channelArgs` 断言方式：在假扩展桩里记录「收到的参数」直接断言（轻）；
   还是断言「参数产生的不同副作用」（重但更接近真实，L2 已覆盖）——倾向 L1 轻断言 + L2 重断言。
2. 用例表是否需要**自动生成初稿**（从 manifest 的 schema 机械展开枚举与必填，再由人补期望）
   ——能大幅降低建表成本，但生成的 `expect` 需要人工填写，否则只是形状覆盖。
3. 覆盖率阈值（每条命令最少变体数）按命名空间给出并写进校验器配置。
