# M38 指令测试 L1 契约矩阵（浏览器通道）

状态：`active`

计划依据：`docs/command-testing-strategy.md`（2026-09-20 定案）；
维护者 2026-09-22 追加定案：**覆盖率静态校验进默认门禁**（见 §1）。

## 0. 为什么现在做

策略定案时留了一条显式的前置：**第 0 步「M28 S2 先行」**——S2 会新增
`ELEMENT_COVERED` / `ELEMENT_DISABLED` / `ELEMENT_NOT_VISIBLE` 三个错误码，用例表
最好一次性把它们纳进去，避免表刚建就改。M28 已收口，错误码已实装并由
`check_error_contract.py` 钉住声明，前置已消。

开工前的可开工性核查（2026-09-22）逐条实测，全部满足：

| 前提 | 实测结果 |
|---|---|
| 输出侧断言前提 | 86 条 manifest **全部**有 `output_schema` 或 `x-outputs`（零缺失） |
| 错误码断言前提 | 86 条 manifest **全部**有 `errors` 声明（零缺失） |
| 假扩展插桩点 | 设计好的依赖注入 `PlaywrightExecutor(ext_session=…)` + `ExtensionExecSession(client=…)`，不需要 monkeypatch |
| 按需启用的开关机制 | 可沿用 `tests/conftest.py` 的 `RPA_DESKTOP_E2E` 同款模式 |

## 1. 本切片交付（S1）

1. **公共假扩展** `tests/commands/harness.py`：只替换通道客户端最底层的 `_exchange`
   （单条收口），`submit` 的端点选择、失败重发、错误整形、往返度量**仍走真实实现**——
   用例断言的是「执行器真实下发了什么」，不是「桩被怎么调用」。
2. **用例表** `tests/commands/cases/browser.json`：浏览器通道 **32 条命令 / 180 个变体**，
   每条命令覆盖全部参数、每个枚举取值、`min/max` 参数的两个取值、至少一个负路径变体。
3. **驱动** `tests/commands/test_browser_matrix.py`：读表参数化执行，断言
   调用（op / args 子集或精确 / 通道超时）、输出契约、effect 种类、错误码与 details。
4. **覆盖率静态校验** `.harness/scripts/check_command_matrix.py`，**进 `check_all.py`**。
   口径 6 条：命令覆盖 / 参数覆盖 / 枚举覆盖 / 边界覆盖 / 最少变体数 / 负路径。
5. **收集隔离** `tests/commands/conftest.py`：缺省不收集（`collect_ignore_glob`），
   `RPA_COMMAND_MATRIX=1` 才跑；并在终端汇总里提示启用方式（与桌面 E2E 同款）。
6. **危险面守卫**（同文件）：autouse fixture 钉死「真实终止进程 / 拉起浏览器」两个面。

### 与策略原文的两处口径修正（都已写进代码注释与本文档）

1. **覆盖率校验从「按需」改为「进默认门禁」**（维护者 2026-09-22 拍板）：它只读
   用例表与 manifest、不执行命令、毫秒级，正是「防用例表腐烂」最该每次跑的位置；
   执行层（跑变体）仍按需。
2. **「required 缺省必须被拒」改为「每条命令至少一个负路径变体」**：输入 schema 的
   `required` 由 **orchestrator** 统一校验（`runtime/orchestrator.py` 用
   `Draft202012Validator(manifest.input_schema)`），执行器层只有手写校验的命令才报错。
   把「缺必填必失败」压到执行器层会写出与实现不符的断言。执行器层真正的责任是
   **失败路径要显式、错误码要可诊断**——第 6 条验的就是它。

## 2. 关键设计决定

- **桩只替换 `_exchange`**：同时拿到三样东西——真实下发的 `(op, args)`、信封里的
  `timeoutSeconds`、以及按 op 的脚本应答。`timeoutMs` 这类「只改变等待时长、不进 args」
  的参数，只有 `timeoutSeconds` 能证明它真被消费（此前只能断言「没报错」）。
- **`targetHost` 单独记录（`targets_seen`）**：它不进 payload、只用于选端点，
  所以多浏览器路由与参数转发是两件事，分开断言。
- **用例表里 `{tmp}` 占位符**：用例表是纯数据，不能写死本机路径；`browser.screenshot`
  这类真落盘的命令必须写到临时目录。
- **整个矩阵共用一个临时目录**（session 级 fixture），**不用** `tmp_path`：见 §4 的坑。

## 3. 实测发现（L1 的价值就在这里）

### 3.1 两个「声明了但实现没消费」的死参数（静态门禁抓不到）

| 命令.参数 | 真实情况 |
|---|---|
| `browser.closeTabs.browserType` | 声明了 `msedge|chrome`，但该命令分支**从不读它**——路由按会话绑定的 host 走 |
| `browser.waitLoad.state` | 声明了 `load|domcontentloaded|networkidle`，但 `tabs.waitLoad` 只收 `tabId`/`timeoutMs`，**state 从未下发** |

**为什么 `check_param_consumption.py` 没报**：它的判定是「参数落在『该命令分支的读取 ∪
通用读取』内」。这两个参数分别在 `_open_extension` 与 `_ext_wait_for` 里被读——而那两个
都是**不带命令字面量的辅助函数**，于是算进「通用读取」，**所有命令都被判为已消费**。
这正是该门禁 docstring 里写明的盲区，M38 首次给出实例。

两处已登记进 `check_command_matrix.py` 的 `KNOWN_DEAD_PARAMS`（带处置说明，且**自我收紧**：
参数一旦被用例覆盖、或从 manifest 删掉，门禁立刻报「台账过期」）。

**处置（2026-09-22 维护者定案：删除，S1.1 已完成）**：两项都从 manifest 删除，
`KNOWN_DEAD_PARAMS` 随之清空（台账机制保留，供 S2/S3 的通道复用）。

- **删的是「声明」不是「能力」**：两者都没被消费过。`closeTabs` 的目标浏览器由**会话绑定**
  决定（`_ext_session_hosts`，`navigate` 时记下真实 `instanceId`），本来就没有「用参数覆盖路由」
  的入口；扩展的 `tabs.waitLoad` 只收 `tabId`/`timeoutMs`，`state` 从来只在纸面上存在。
- **对标差距没有扩大**：`docs/yingdao-web-cmds-benchmark.md` 里影刀「等待网页加载完成」的核心参数
  也只有「网页对象 + 超时(s)」——`state` 是我们自己多声明的，不是影刀要求的能力。该文档的差距
  备注已同步更正（顺带更正第 4 行「缺关闭所有网页」的过期结论：M32 的 `closeTabs` 与 M35 的
  `closeBrowser` 已补上）。
- **同步清理面**（漏一处就是「另一份文档里的旧结论」）：manifest ×2、`devserver/static/i18n.js`
  的参数标签、用例表两条 `notes`、对标文档、BACKLOG、本任务单。
- **负向验证**：把 `state` 临时加回 `commands/browser/waitLoad.json` → 门禁立刻红
  （`没有任何变体显式设置该参数`），证明「新声明但没实现也没用例」这件事仍然拦得住。

### 3.2 被正确断言的既有语义（写期望时以实测为准，不凭读代码猜）

- `browser.navigate` 的 goto 走 `tabs.create` 时，**通道超时被抬到 45s**
  （`max(timeout_s, _EXT_SW_WAKE_SECONDS)`，覆盖 MV3 SW 唤醒窗口），不是 `timeoutMs` 原值；
- 通道错误码 `TIMEOUT` 有显式分支，映射为 **`TIMEOUT`** 而非 `EXECUTOR_FAILED`
  （其余通道码才是 `EXECUTOR_FAILED`）；
- `_ensure_scheme` 只补协议、**不加尾斜杠**（`example.test` → `https://example.test`）；
- 任何一个 `timeoutMs` 落到通道信封时都被钳到 `max(0.1s, …)`——`timeoutMs=1` 实测得到 `0.1`。

## 4. 事故与教训（两起，都已变成永久防线）

### 4.1 建表探针真的杀掉了本机 21 个浏览器进程（2026-09-22）

`.harness/spike/probe_browser_commands.py` 第一版对每条命令跑「最小必填输入」以采集
真实 op/args——但 `browser.closeBrowser` 的语义就是「按名杀掉该浏览器的全部进程」
（M35 定案），探针没打桩，**实测终止了 21 个真实进程**（输出里带着真实 pid 列表）。

与 M36（剪贴板用例往维护者前台粘贴 "hi"）**同源**：打桩边界要沿**真实副作用面**划，
不沿「参数传递面」划。这次是进程面，比剪贴板更重。

两处修复：探针内打桩进程面；`tests/commands/conftest.py` 加 autouse 守卫
（`_terminate_process` / `_wait_processes_exit` / `launch_browser` 一律拦下，
`_list_browser_processes` 钉成空表——枚举无副作用但**返回值随本机环境漂移**）。
要验破坏性路径的用例必须自己显式打桩（LIFO 覆盖守卫）。

### 4.2 `tmp_path` 让「全绿的测试」把门禁判失败

矩阵最初给每个用例都挂了 pytest 的 `tmp_path`：150+ 个用例各建一个编号目录，
会话结束时 pytest 在 **atexit** 里批量删除，在受限执行环境被批量删除守卫拦下，
以 `SystemExit(1)` 收场——**159 个用例全绿，pytest 退出码却是 1**。
改为整个矩阵共用一个临时目录后，pytest 连 basetemp 都不必创建，退出码恢复 0。

**教训**：门禁看到的是**退出码**，不是那行「159 passed」。测试全绿与门禁通过是两件事。

## 5. 验证

- **执行层**：`RPA_COMMAND_MATRIX=1 uv run pytest tests/commands` → **180 passed / exit 0**。
- **静态层**：`check_command_matrix.py` → `PASSED（已校验 32 条命令；未建表命名空间 3 个
  共 54 条命令；死参数台账 2 项）`。
- **实现级负向验证**（证明 L1 真能抓漂移）：临时删掉 `browser.click` 里 M29 修好的
  `simulateHuman` 转发 → 精确 **2 个用例红**（正是断言该参数的那两个），恢复后全绿。
- **校验器三向负向验证**（证明口径不是摆设）：① catalog 新增命令未补用例 → 红；
  ② manifest 新增枚举值未覆盖 → 红；③ 台账里的死参数被真的覆盖 → 报「台账过期」。
  三向均 exit=1 且命中预期关键词，恢复后 PASSED。

## 6. 剩余（后续切片）

- **S2 桌面通道**（UIA 17 + Win32 19，共 36 条）：需真实桌面 fixture 或既有 WinForms
  测试应用；注意会抢前台，用例自带兜底。已在 `PENDING_NAMESPACES` 登记。
- **S3 数据 / 工作流通道**（`python.worker` 18 条）：M30 已确认零参数漂移，但**输出契约
  与错误分支未覆盖**。已在 `PENDING_NAMESPACES` 登记。
- **S4 L2 真机冒烟**：与 L1 **共用同一份用例表**，把执行后端从假扩展换成真扩展
  （策略 §2 L2，显式开关启用）。
- **两个死参数的处置**：**已完成（S1.1，2026-09-22）**——两项均从 manifest 删除，
  删掉的是「声明」而非「能力」，对标差距未扩大。详见 §3.1。
