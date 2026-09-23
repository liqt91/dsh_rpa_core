# M38 指令测试 L1 契约矩阵（浏览器通道）

状态：`done`

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

## 1.2 交付（S1.2：工作台「指令测试」页签）

维护者要「一个页面做启动和展示」。ADR 0016 定 GUI 为唯一主力形态、devserver 冻结演进，
所以它落在**工作台**（`HomeWindow` 的第三个页签：流程库 / 运行历史 / 指令测试），而不是再起
一个本地 Web 页——那会引入第二个 UI 面，还要额外过架构门禁的 devserver 隔离断言。

1. **页签** `src/rpa_core/gui/command_matrix.py`（`CommandMatrixPanel`）：运行/停止 + 跑覆盖率
   校验 + 进度条 + 结果树（命令 → 变体两级）+ 输出日志。`HomeWindow.closeEvent` 连带
   `shutdown()`，不留一个还在跑矩阵的孤儿 Python。
2. **页面只做「点火 + 展示」，测试逻辑一行都不在这儿**：覆盖范围按**命名空间**读
   `tests/commands/cases/*.json`（唯一事实来源），所以 S2 桌面 / S3 数据的工作量**建表后自动
   出现**，不用改这个文件；未建表的命名空间也列出来，免得问「怎么只有浏览器」。
   页面**不读** `check_command_matrix.py` 的 `PENDING_NAMESPACES` 台账——同一件事读两处口径，
   迟早各说各话。
3. **结果来自被测方自己的结构化报告，不解析 pytest 输出**。三条候选的取舍（同样记在
   `tests/commands/conftest.py` §2 的 docstring 里，免得后人重走）：
   - ❌ 解析 `-v`：`pyproject.toml` 的 `addopts = "-q"` 会把 `-v` 抵消回默认 verbosity
     （实测 180 个用例只产出 16 行 stdout、**零** `PASSED` 行）——依赖调用方的 verbosity 算术。
   - ❌ 只读 `--junitxml`：格式标准，但**只在会话结束时落盘**，页面上没有「跑到哪了」。
   - ✅ JSONL 钩子（环境变量 `RPA_COMMAND_MATRIX_REPORT`）：被测方逐用例 append + flush，
     页面按**字节偏移增量读**，且**半行不消费**（否则会把写了一半的 JSON 当坏数据丢掉，
     那一条结果永久缺失）。
4. **跑测试是另起子进程**（`python -m pytest tests/commands`）：GUI 不承载 runtime（ADR 0011），
   也不该把 pytest 搬进 GUI 进程——pytest 会改全局状态（断言改写、warnings 过滤等）。
5. **缺省不写报告**：只有设了 `RPA_COMMAND_MATRIX_REPORT` 才启用；默认门禁与命令行手工跑
   都不产出任何文件。
6. **两道既有守卫复用而非重造**：矩阵用例层已钉死「真实终止进程 / 拉起浏览器」
   （`tests/commands/conftest.py` §1）与全局输入面（`tests/conftest.py`），所以这个按钮点下去
   不会动维护者的浏览器与键盘。

## 1.3 交付（S3：数据 / 工作流通道）

`data` 17 条 + `workflow` 1 条，共 **18 条命令 / 88 个变体**。这一片的插桩点与前两片**不同**，
理由写在 `tests/commands/test_data_matrix.py` 的 docstring 里：

1. **插桩点 = 真子进程，不是假桩**。浏览器通道的可测面在**通道协议**（下发了什么 op/args），
   所以 S1 打桩在最底层 `_exchange`；数据通道的命令本身就是纯 Python 的文件/字符串操作，
   `PythonWorkerExecutor` 真起 `python -m rpa_core.workers.python_worker`——**真子进程就是真机**，
   一次性能测到三件事：inputs 决定的行为、worker 回的 outputs 形状、**磁盘上留下了什么**。
   打桩反而会打掉最有价值的证据面。代价是每变体一次进程启动（实测 ~0.6s / 变体，全矩阵 64s）。
2. **公共驱动层** `tests/commands/matrix.py`：用例表加载、`expect` 解释、`{tmp}` 物化、
   落盘断言。两个驱动（浏览器 / 数据）共用一套 `expect` 语义——S1.2 的假绿灯正是「同一件事
   两处口径」的变体，不值得再赌一次。`matrix_tmp_dir` 迁到 `tests/commands/conftest.py`。
3. **`expect` 新增 5 个键**（数据通道逼出来的，全部记在 `matrix.py` 的表里）：
   `outputPaths`（按 `Path` 比较，跨平台分隔符无关）、`outputsMatch`（正则，给时间戳这类
   形状确定值不确定的输出）、`noEffects`（pure 命令**不产出** effect 记录）、
   `files`（磁盘断言：`equals` / `contains` / `missing` / `jsonContains` 四选一）。
4. **每个变体一个干净目录**（`{tmp}` 物化到 `matrix_tmp_dir/<命令>::<变体>`）：数据命令真的
   写文件、删文件，用例之间必须互不干扰，且要把「预置态 → 执行后磁盘状态」逐字节对上。
   变体可声明 `setup`（`files` / `dirs` / `table`）预置磁盘状态。
5. **安全阀** `tests/commands/guard.py`：用例表是**数据**，数据会写错，而这一通道真的会删
   （`data.deletePath` + `recursive` 就是 `shutil.rmtree`）。驱动在执行任何命令前把 `inputs`
   与 `setup` 里所有路径类值按**实现自己的解析规则**解析成绝对路径，越出本变体目录即判失败。
   它自己的契约测试在**默认门禁**里（`tests/contract/test_command_matrix_paths_guard.py`：
   正向不误杀 / 负向拦得住 / **接线**——用源码断言钉住「安全阀跑在执行器构造之前」）。
6. **子进程回收断言**：每个变体都断言 `active_process_count == 0`（几百个变体跑完不能留下
   一堆 python 孤儿）。

### 与 S1 一致的、被用例固化的「执行器层不做 schema 校验」

`data.limit` 的 `items` 传字符串会按字符拆、`data.log` 的 `level` 传 `INFO` 原样透传、
`data.writeText` 同时给 `text` 与 `lines` 取 `text`——三条都**不是缺陷**，而是
「schema 校验由 orchestrator 的 `Draft202012Validator` 统一负责」的直接证据（§1 的口径修正）。
三条都在用例表里带 `notes` 说明，免得后人当 bug 去「修」。

## 1.4 交付（S2.1：桌面 fixture 升级 + 「暂停/继续」真机证据）

**维护者定案（2026-09-22，两句）**：① S2 走**真桌面 fixture**，不走「打桩绑定层」那条更
便宜的路——这解掉了 §6 里悬置的口径；② 先补「**含桌面命令的流程 → 中途暂停 → 继续**」
的真机证据。第 ② 条此前只在**浏览器通道**验过（M21，macOS + Edge），桌面通道没有。

1. **靶子升级** `testapps/desktop/Program.cs`：新增**非幂等**计数器（`countButton` 点击累加、
   `countLabel` 只增不减）。这一条是「零重跑」从推断变成硬证据的关键——重跑一次读数就是 `2`。
2. **共享装配** `tests/e2e/desktop_fixture.py`（新）：编译 / 启动 / 抢前台 / 清理 + `csc.exe`
   可用性判据，由两个真机用例共用。复制一份的下场是「同一件事两处口径」——本仓刚在
   S1.2 的假绿灯上吃过这个亏，不赌第二次。
3. **真机用例** `tests/e2e/test_desktop_pause_resume.py`（新，`RPA_DESKTOP_E2E=1`）：
   `run`（子进程）→ 慢节点期间写暂停请求 → 收口为 `paused` → **`resume`（另一个子进程，
   与 GUI 的「继续」按钮同一条路）** → 三条断言（同一窗口 / 会话与元素都接回 / 零重跑，
   后者另附事件级证据：续跑段的 `stepCompleted` 集合恰好等于暂停点之后的节点）。
4. **跨进程续接实装（两后端）**：`base.desktop_sessions_from_scopes` 与
   `base.desktop_window_alive` 两个共用纯函数 + 两个后端各自的 `restore_from_scopes`。
   纯函数放 `base` 的理由与 S1.2 同：同一件事不该两处口径。

### 两处缺口都是实测出来的（「先探针后断言」的价值就在这里）

探针逐轮把缺口逼出来，**每一轮的错误码本身就是证据**：

| 实测顺序 | 续跑段的错误 | 说明 |
|---|---|---|
| 1 | `SESSION_NOT_FOUND`（`desktop.click`，`effects: []`） | 会话表没还原：窗口还在，句柄表是新的 |
| 2 | `ELEMENT_NOT_FOUND`（**同一个节点**） | 补上会话后才暴露的第二处：`elementId → 定位器` 映射也没还原 |
| 3 | `succeeded` | 两处补齐 |

第二处缺口的修法顺带定了一条口径：**定位器进 `effect.details.locator`，不进 `outputs`**——
快照里「资源绑定」的权威位置一向是 `resource + details`（M21 浏览器侧的 `tabId` 就在那里），
而 `outputs` 是面向用户与表达式的公开面（`x-outputs` 会进 GUI），把内部定位器塞进去等于把它
升格成契约。

### 与浏览器侧**刻意不同**的一处：HWND 会被回收复用

`browser.restore_from_scopes` 不校验标签页是否还在（理由：校验要一次扩展往返，而「跑起来才
发现页面被用户关了」与恢复期判定本来就是同一种错误）。桌面侧反过来——本地校验是微秒级，而
HWND 是 32 位整数句柄号、**会被系统复用**，不校验就可能把暂停点之后的命令指到一个刚好继承了
同一句柄号的**别人的窗口**上。所以还原前会 `IsWindow` + `GetWindowThreadProcessId` 复核属主，
失效则**不还原**，让后续命令照旧报 `SESSION_NOT_FOUND`（错误码与浏览器侧一致，风险面不同）。

### 真机边界（如实记录）

- **只跑 UIA 后端**。win32 后端的 `restore_from_scopes` 有契约测试覆盖，但**没有**对应的真机
  用例——**判因已纠正（2026-09-22，§1.6.2）**：本节原先写的理由是「win32 定位器认的是
  title/class/controlId，而 fixture 的控件是 UIA `AutomationId`，拿 win32 后端定位它们要另做
  一套靶子」——**后半句不成立**，win32 的 `title` 比的是控件**窗口文本**，`Submit` / `Count` /
  `note-ready` 都唯一命中（S2 补靶子时实测）。所以「win32 没有真机用例」是**当时没写**，
  不是「写不了」；缺的只是那一条针对 `restore_from_scopes` 的 win32 真机用例（靶子已够用）。
- 本片**不含** S2 的 36 条 L1 用例表；`PENDING_NAMESPACES` 里的 `desktop` 仍在。

### 顺带查清的一件事：既有的记事本切片在整目录运行时是抖的（**不是**本片引入）

S2.1 收口时发现 `RPA_DESKTOP_E2E=1 pytest tests/e2e`（整目录）红在**既有**的
`test_windows_desktop.py::test_windows_desktop_vertical_slice` 上。因为它落在**被本片改过的
win32 模块**，所以没有用「大概是环境问题」带过，而是做了对照实验：

1. 探针 `probe_win32_notepad_slice.py` 复用 pytest 留下的失败运行 `events.jsonl`（`tmp_path`
   保留最近三次），取出**三个不同失败签名**——`attachMain` 的 `ELEMENT_NOT_FOUND`
   （`title: 无标题 - 记事本`，`matchedCount: 0`）、`openDialog`/`inputPath` 的
   `SetForegroundWindow` 返回 0、`attachOpened` 的 `Handle <n> is not a vaild window handle`。
   **没有一个落在本片新增/修改的节点上**。
2. 从 `HEAD` 建干净 worktree（先确认导入的确是改动前的 `rpa_core`）跑同一探针：HEAD 14 次红 4、
   本仓库 14 次红 5，**同三款签名**；再做**交替 A/B 六轮**（控制环境随时间漂移）——HEAD 六次
   红 2、本仓库六次全绿。

→ 结论：**既有抖动，与本片无关**（此前「本仓库红得多」是时间聚集的假象）。该切片不走 resume
路径，本片新增的 `restore_from_scopes` 在此**根本不被调用**。已按 BACKLOG 先例登记（含三条
未验证的候选修法）——**登记而非顺手改**：②③ 两类不是用例侧能修的，且真要把桌面通道进 L1 矩阵
时，这片的稳定性才是前提。

## 1.5 交付（S2：桌面通道 36 条用例表）

**做法**：`cases/desktop.json`（UIA 17 条 / 78 个变体）+ `cases/desktop_win32.json`
（Win32 19 条 / 81 个变体；两数均为**建表时**，补靶子后是 80 / 92，见 §1.6），两个驱动
`tests/commands/test_desktop_matrix.py` /
`test_desktop_win32_matrix.py`，共享装配 `tests/commands/desktop_harness.py`。

1. **真靶子，不是打桩绑定层**（维护者 2026-09-22 定案）：驱动在真 fixture 上执行命令，
   断言结果契约与错误码。装配复用 S2.1 抽出的 `tests/e2e/desktop_fixture.py`
   （编译 / 启动 / 抢前台 / 清理），并补三件事：**每变体自建会话、跑完即弃**
   （避免 `countLabel` 被点过、窗口被最小化或隐藏、会话被关掉之后成片假红）、
   **每变体前重新抢前台**、**占位符物化**（`{session}` / `{element:名字}` /
   `{appTitle}` / `{pid}` / `{handle}`——这些值每次运行都不同，写不进表里）。
2. **驱动层两处小改**（`tests/commands/matrix.py`）：`materialize_inputs` 支持额外
   占位符表（其它通道不传，行为不变）；`_check_files` 只在需要文本断言时才读文件
   （`desktop.screenshot` 的 PNG 不是 UTF-8，无条件 `read_text` 会把「文件被写出来了」
   这条断言变成整个变体崩掉）。
3. **`PENDING_NAMESPACES` 清空**：86 条命令全部有表。
4. **expect 的证据来源**（不凭读代码编）：有真机或门禁内证据的按值断言
   （`matchedCount=1`、`resourceType=windowHandle`、`getWindowTitle.title == 靶子标题`、
   `ELEMENT_NOT_FOUND` + `details.matchedCount=0`、无过滤 → `INVALID_INPUT`）；
   没证据的只断言形状（`outputKeys` / `effect`）。**刻意不做的两类**：
   ① 按值断言控件文本（矩阵共用一个靶子进程，前面的变体会改它——那是把用例顺序写进期望）；
   ② 写「想象的正路径」（例如给一个 Edit 调 `select` 会怎样，未实测就不写）。

### 已知缺口（都在本片如实登记，没有静默）

**靶子侧的缺口已由 §1.6 补齐（2026-09-22）**，下表是补齐后的状态；下半张表是补靶子时
**新暴露出来的实现缺口**——它们此前连测都测不了（没有对应控件），现在有了可测面：

| 缺口 | 影响 | 状态 |
|---|---|---|
| 靶子无可拖 / 无下拉控件 | `desktop.select`（两后端）与 `desktop.drag` 只有负路径 | **已补**（§1.6）：`drag` 正路径打通；`select` 反而暴露实现缺口（见下表） |
| 靶子无菜单栏 | `win32.menuSelect` 的成功路径无从谈起（表里那条 `EXECUTOR_FAILED` 是**推断**） | **已补**：原生 `MainMenu`（HMENU）→ 正路径实测通过，推断也换成了实测 |
| win32 定位不到靶子控件 | win32 侧所有吃 `elementId` 的命令只有负路径 | **判因有误，已纠正**：win32 的 `title` 比的是控件**窗口文本**，实测 `title='Submit'` / `'Count'` / `'note-ready'` 都唯一命中——元素级正路径本来就有（§1.6） |
| `closeSession.forceKill=true` 会结束靶子进程 | 只覆盖缺省 `false` | 未解（需要独立的靶子实例） |
| `setWindowVisible=false` 会把窗口藏起来 | 只覆盖 `visible=true` | 未解（需要一个变体内部完成的显隐对，属流程层） |

补靶子时新暴露的**实现缺口**（都不是靶子的问题，靶子只是把它们变成了可实测的事实）：

| 缺口 | 实测证据 | 出路 |
|---|---|---|
| `desktop.select`（两后端）三个 `selectBy` 分支**静默假成功** | 三个分支都返回 success 而 `listStatus` 一步没动。ListBox 的 `iface_selection` 是 SelectionPattern（方法面只有 `GetCurrentSelection` / `CurrentCanSelectMultiple` / `CurrentIsSelectionRequired`，**没有 `Select`**），而 `label`/`value` 分支把 `GetCurrentSelection()`（= **当前已选中项**）当成「全部选项」遍历 | 按 SelectionItemPattern 对列表项调 `Select()`；`label`/`value` 改成枚举全部子项。由 `tests/e2e/test_desktop_target_effects.py` 的 xfail 钉住 |
| `desktop.win32.select` / `getSelectedText` 在 win32 后端**不可能生效** | win32 包装出的元素**没有 `iface_*` 属性族**（对窗口里每个子控件取 `iface_selection` / `iface_value` / `iface_invoke` 全部抛异常），两处都被 `except: pass` 吞掉 → `select` 返回 success，`getSelectedText` 恒返回空串 | 改走 win32 原生接口（如 `SendMessage(CB_SETCURSEL)`）或显式报 `EXECUTOR_FAILED`，别静默成功 |
| uia `desktop.getText` 对 Edit / ListBox 读到**相邻 Label 的文本** | `queryInput` → `'Name'`（旁边的 nameLabel）、`readOnlyNote` → `'Drag'`（旁边的 dragHandle）、`optionsList` → `'Ready'`（旁边的 resultText）。WinForms 的 Edit/ListBox 没有 AccessibleName，UIA 按 MSAA 的 labeled-by 规则回落 | 改走 ValuePattern / TextPattern。由 e2e 的 xfail 钉住。顺带纠正了 S2.1 里那句「读输入框要押注 getText 对 UIA Edit 也返回文本」——**那条假设实测是错的**，当时绕开是对的选择 |
| 项目依赖里**没有 Pillow**，而 `screenshot` 的回退路径要用它 | 两个后端的 `screenshot` 正路径都必然 `EXECUTOR_FAILED`（`'NoneType' object has no attribute 'save'`）：实现先试 UIA 图像属性（win32 侧没有 `iface_*` 必抛），回落到 `window.capture_as_image()` | 独立决策：加 Pillow 依赖，或改走 Win32 `BitBlt`（无第三方依赖） |
| `control_id` 过滤在 pywinauto 上**完全不生效** | `descendants(control_id=<任意值>)` 一律返回全部子控件（实测 14 / 14 / 14）。于是 `DesktopLocator` 的 `controlId` 字段：只给它会必然报 `ELEMENT_AMBIGUOUS`（把「过滤没生效」伪装成「元素不唯一」） | 换定位实现，或把 `controlId` 从 win32 locator 的可用字段里拿掉 |

> **S2.3 处置（2026-09-23，维护者定案）**：上表 5 行已清掉 4 个半——`screenshot` 加
> Pillow、uia `select`/`getText` 修复、win32 `select`/`getSelectedText` 显式失败、
> `controlId` 在 `_find` 里手工补滤，`getWindowList` 的会话声明也一并对齐——
> 过程、坑与验证详见 **§1.7**。剩余：`className` 动态名、win32 原生消息实现（均在 BACKLOG）。

### 验证（S2）

- **静态层**：`check_command_matrix.py` → `COMMAND MATRIX CHECK PASSED（已校验 86 条命令；
  未建表命名空间 0 个）`。建表过程中它先报出**两轮真缺口**（枚举缺省值没被显式写出、
  边界参数只有单侧取值；首轮还有 JSON 用 Python 式字符串拼接导致的解析失败），
  逐条修掉后才转绿——这正是它进默认门禁的价值。
- **负向验证 3 例**（都打在门禁实际检查的方向上，逐字节还原后复跑 PASSED）：
  ① 删掉 `desktop.drag` 整条用例 → 报「用例表里没有这条命令」；
  ② 把 `desktop` 写回 `PENDING_NAMESPACES` → 报「台账过期：cases/desktop.json 已存在」；
  ③ 把 `desktop.getWindowTitle` 砍到 2 个变体 → 报「变体数 2 < 阈值 3」。
- **驱动可收集**：`RPA_COMMAND_MATRIX=1 pytest tests/commands --collect-only` →
  browser 180 / data 88 / **desktop 78 / desktop_win32 81**（共 427 个用例；
  收集阶段不启真机）。补靶子后桌面两表长到 **desktop 80 / desktop_win32 92**（见 §1.6）。
- **执行层已跑（2026-09-22 补，原本是本片显式欠下的账）**：建表当天维护者的指令是
  「建表吧，建完不测」，所以 159 个变体的期望全是**读代码写的**；随后按「先补靶子」
  补完靶子并**首次上真机**，30 处期望被实测推翻并逐条校正，最终 172 个变体
  170 passed + 2 xfailed（exit 0）。完整过程、五类偏差与缺口登记见 **§1.6**；
  启用方式 `RPA_COMMAND_MATRIX=1 RPA_DESKTOP_E2E=1 pytest tests/commands`。

## 1.6 交付（S2 补靶子 + 桌面执行层首跑）

**维护者指令（2026-09-22）**：「先补靶子」——给靶子补上缺失的可测面，让只有负路径的命令
（`select` / `drag` / `menuSelect`）有正路径可走。

### 1.6.1 靶子新增（`testapps/desktop/Program.cs`）

| 新控件 | 给谁用 | 行为 |
|---|---|---|
| **原生菜单栏**（`MainMenu` → HMENU） | `win32.menuSelect` 的成功路径 | `Actions → Increment`、`Actions → Nested → Deep`，各写一个状态 Label |
| `optionsList`（ListBox，alpha/beta/gamma） | `select` / `getSelectedText` 的正路径面 | `SelectedIndexChanged` → `listStatus = "list:<idx>:<item>"` |
| `optionsCombo`（ComboBox，one/two/three） | 同上（下拉与列表是两种控件语义） | → `comboStatus` |
| `dragHandle`（Label，Text="Drag"） | `drag` 的正路径面 | 自实现 `MouseDown/Move/Up` → `dragStatus = moved:dx,dy` / `up:x,y` |
| `readOnlyNote`（只读 TextBox，Text="note-ready"） | 给两后端一个**文本恒定**的元素锚点 | `ReadOnly = true` |
| 4 个状态回显 Label | 让副作用可断言 | 初值都是 `none` |

窗口 `ClientSize` 440x260 → **620x360**（新控件要放得下）。

两处刻意选择：

1. **菜单必须用原生 `MainMenu`（HMENU），不能用 `MenuStrip`**：MenuStrip 是托管控件、
   画在客户区里，`GetMenu(hwnd)` 拿不到——而 `desktop.win32.menuSelect` 走的正是 pywinauto
   的**原生菜单**路径，MenuStrip 在它眼里等于「这个窗口没有菜单」。用错会以
   「窗口没有菜单」收场，然后人会掉进「实现是不是没接菜单」的坑里——靶子本身的假象。
2. **菜单项不碰 `countLabel`**：那是「暂停/继续零重跑」用例的判据（非幂等计数器），
   菜单项写它会把两个用例的判据缠在一起。菜单项只写自己的状态 Label。

### 1.6.2 靶子补齐后立刻被推翻的两条旧判因

| 旧结论（§1.5 表里的原文） | 实测 | 结论 |
|---|---|---|
| 「win32 定位不到靶子控件，所以 win32 侧只能全走负路径」 | win32 定位器的 `title` 比的是控件**窗口文本**：`title='Submit'` / `'Count'` / `'note-ready'` 都唯一命中（`matchedCount=1`） | **判因错**。真正不成立的是另外两条路：`className` 的哈希段不能硬编码（**2026-09-23 订正**：实测它不是「随编译产物变」，而是机器 + 运行时级常量——同机所有 .NET Framework 4.x 程序共享一段、跨重编译/跨源码/跨输出路径都不变，换机器才变；两个 Edit 还撞同一类名），`control_id` 的过滤**完全不生效**（`descendants(control_id=<任意值>)` 恒返回全部 14 个子控件；S2.3 已由 `_find` 手工补滤修活） |
| 「靶子无菜单栏 → `menuSelect` 的成功路径无从谈起」，表里那条 `EXECUTOR_FAILED` 是**推断** | 两条正路径真机通过：`["Actions","Increment"]` → `menu:increment`；`["Actions","Nested","Deep"]` → `menu:deep`；不存在的项 → pywinauto `MatchError` → `EXECUTOR_FAILED` | 推断换成了实测 |

顺带把「拖拽能不能真动控件」也钉了：两后端 `dragStatus` 都从 `none` → `up:225,150`。

**屏幕几何**：远程会话里 `CenterScreen` 把窗口放到**负 Y 区**（实测 window rect
`(647,-765)-(1273,-356)`，client 620x360），所以 `drag` 的绝对终点坐标必须在装配期现算
（`desktop_harness` 的 `{windowCenterX}/{windowCenterY}`，底层是
`desktop_fixture.client_center`：`GetClientRect` + `ClientToScreen`）。写死坐标要么拖不到
窗口内、要么把真实鼠标甩到桌面上无关的位置。

### 1.6.3 执行层首跑：30 处期望按实测校正

159 个「只建表没跑过」的变体第一次上真机，把「读代码猜的期望」全逼出来了。逐类如下
（改的都是**期望**，不是实现）：

| 类 | 条数 | 实测真相 |
|---|---|---|
| 空会话串 | 13 | `resolve_session_id` 把空串当「未提供」（`str(requested or '').strip()` 为空即回退）→ 落到**默认会话**，不是 `SESSION_NOT_FOUND`；元素级命令的失败点因此变成那个不存在的元素。两条变体名随之改成 `empty-session-string-falls-back-*` |
| 占位符没物化 | 4 | 两处驱动缺陷：① `materialize_inputs` 把**整串占位符**替成字符串，而 `processId` 在 schema 里是 integer、执行器不做转换 → 过滤恒不命中（`{pid}` 的 attach 报 `ELEMENT_NOT_FOUND`，pid 明明是对的）；② `expect` 从未被物化（`{appTitle}` 拿字面量去比真标题）。修法：整串占位符**保留原类型** + 新增 `substitute_extra`（只替 `extra`、不动 `{tmp}`，后者由 `check_files` 自己解析） |
| `getWindowList` 要会话 | 10 | 实现把会话检查放在命令分派**之前**，而 `_no_session_commands` 声明它不需要——**声明与实现不一致**（两后端同款）。表按实现建档（建会话）并登记 |
| `win32.attachWindow` 负路径 | 2 | 无效句柄报 `EXECUTOR_FAILED`（pywinauto 抛 `Handle ... is not a vaild window handle`），不是 `ELEMENT_NOT_FOUND`；details 只有 `{title, matchedCount}`（**没有** `className`） |

### 1.6.4 新增的两件记账 / 判据机制

**一、`knownGap`（变体级）**——把「期望是对的、产品是错的」这条正路径钉成**严格 xfail**。
与「期望写歪了就改期望」相反（S3 的 `data.writeText` 相对路径那条），分界是**期望本身对不对**：
期望错 → 改期望；期望对而产品没做到 → `knownGap`，期望原样留着。为什么不是「让这行红着」
或「把期望改成实测的错误行为」：前者会让「有没有新红」失效，后者会把 bug 固化成契约
（下一手读者会以为「正路径就该报 EXECUTOR_FAILED」）。严格模式则保证缺口一修就
**XPASS 转红**，逼着回来摘标记。

静态校验器配套三条（自我收紧）：`knownGap` 必须是非空理由、**标记过的变体不计入任何
覆盖率口径**（缺口不许凑覆盖率）、每条命令至少要有一个**未标记的** negative 变体
（缺口不许盖住整条命令）。两个后端的 `screenshot` 正路径用了它：
`'NoneType' object has no attribute 'save'`（Pillow 缺口，见 §1.5 下半张表）。

**二、`outputListContains`（`expect` 新键）**——给「结果随本机环境漂移、但被操作对象恒定」
的命令用。`getWindowList` 的正路径此前只断言形状（`outputKeys`），因为 `outputs` 是子集比较、
列表值只能整体等值，而整桌面枚举的结果随维护者开着的窗口漂移。问题不是「不够精确」而是
**收不了错**：`desktop.getWindowList` 走 pywinauto 的 `uia_element_info._get_elements`，那里
`except (COMError, ValueError): return []`——COM 拒绝调用时**静默返回空列表**，于是
「枚举失败」与「本机真没有匹配窗口」在断言上**完全同形**（都是 `windows: []`）。
锚点换成靶子窗口自己的标题后，两后端共 8 条正路径从「形状断言」变成真判据。

### 1.6.5 实测与噪声：`Windows fatal exception: code 0x8001010d` 已定性（**不是缺陷**）

首跑输出里 5 次出现 `Windows fatal exception: code 0x8001010d` 的线程栈，栈顶完全相同，
都落在 `desktop.getWindowList` 的 `PyDesktop(backend="uia").windows()`（`desktop.py:225`）。
定性结论：**噪声，不影响结论**，三条依据：

1. `0x8001010d` = `RPC_E_CANTCALLOUT_ININPUTSYNCCALL`：全桌面 UIA 枚举撞上某个窗口正在派发
   输入同步调用时，COM 拒绝本次调用。**pytest ≥5 默认启用 faulthandler**，它把这个 SEH 异常
   渲染成「fatal exception」；异常随后被 pywinauto 自己吞掉，进程继续。（上游同款记录：
   pytest issue #7059、StackOverflow 57523762——都是「COM + pytest ≥5」这一组合。）
2. 同一次运行里 **172 项：170 passed + 2 xfailed，exit 0**；5 条 `getWindowList` 变体全过，
   `-rfExX` 摘要里没有任何 FAILED / ERROR。
3. 单独复跑 `-k getWindowList`（两后端）也全过。另有 M7 已登记的同类 COM 健壮性缺口
   （`RPC_E_SERVERCALL_RETRYLATER` 被兜底成 `EXECUTOR_FAILED`），属同一类。

**刻意不采用 `-p no:faulthandler` 消音**：它会连真实的 C 层崩溃一起藏掉，而「测试进程崩了」
正是这个门禁最需要的信号（M38 §4.2 的教训就是「门禁看的是退出码」）。噪声留在日志里，
定性写在这里。

**但顺着这条路径查出一个真缺口**（见 1.6.4 第二条）：吞错返回 `[]` 是**静默**的。
专门验证「静默空列表能否复现」的探针（`.harness/spike/probe_desktop_window_list.py`）
连跑 8×2 次枚举，本机稳定 11（uia）/ 13（win32）项、每次都能命中靶子窗口，**没撞上**；
所以登记的是「路径存在、本轮未复现」，不是「已观察到故障」——别把没测到当没风险。

### 1.6.6 一个真 bug：session 级 fixture 被 setup 两次（已修）

`demo_app` 原先定义在 `desktop_harness.py`，两个驱动各自 import → pytest 按「fixture 定义
位置的模块」给**每个模块**建一份 FixtureDef → session 级 fixture 在两个驱动段各 setup 一次；
第二次现场编译时，第一次拉起的靶子进程正占着输出 exe，`csc /out:` 覆盖失败退出码 1 →
**第二个驱动整段 ERROR**。修法：fixture 收到 `tests/commands/conftest.py` 转出（全仓只剩
一份），并把 `kill_demo_apps()` 前移到编译**之前**（残留进程占住 exe 是同一个坑的另一半）。

**教训**：`--collect-only` **不解析 fixture**，装配层面的错误在收集阶段完全看不见
（当时两次 `--collect-only` 都干净）。**装配类改动必须以真实运行收口**——与 S1.2 那次
「只收集不执行看不见问题」同源。

### 1.6.7 验证（S2 补靶子 + 执行层）

- **靶子验收（真机 E2E）** `tests/e2e/test_desktop_target_effects.py`（新，`RPA_DESKTOP_E2E=1`）
  → **4 passed / 2 xfailed**：菜单两级选中、拖拽移动（两后端各一条）三条正路径真机通过；
  另两条 `xfail(strict=False)` 钉住 `select` 与 `getText` 的实现缺口（§1.5 下半张表）——
  用 xfail 而不是删掉，是为了让缺口一直是**可执行的**证据而不是一句注释。
- **静态层**：`check_command_matrix.py` → `COMMAND MATRIX CHECK PASSED（已校验 86 条命令；
  未建表命名空间 0 个；死参数台账 0 项；实现缺口 2 条）`——缺口逐条打印在 PASSED 行下面，
  每次门禁都可见。
- **桌面矩阵（执行层，两后端）**：172 个变体（UIA 80 / Win32 92）→ **170 passed + 2 xfailed，
  exit 0**；`-rfExX` 摘要无 FAILED / ERROR。
- **全量四驱动回归**（`RPA_COMMAND_MATRIX=1 RPA_DESKTOP_E2E=1 pytest tests/commands`）：
  见下（浏览器 180 + 数据 84 + 工作流 4 + 桌面 172）。
- **六向负向验证**（探针 `.harness/spike/probe_desktop_matrix_negative.py`，可复跑；
  判据是「红在**预期的那几条**上」）：

  | 注入 | 结果 |
  |---|---|
  | ① `knownGap` 写成空白串 | 静态校验器红，点名「knownGap 是空串」 |
  | ② `desktop.win32.screenshot` 全变体标 `knownGap` | 红在「没有任何未标 knownGap 的 negative 变体」——缺口盖不住整条命令 |
  | ③ 摘掉非缺口变体的 `savePath` | 红且点名 `desktop.screenshot.savePath`——缺口行确实**不计入**参数覆盖 |
  | ④ 删掉 `knownGap` 标记（对照） | 静态层**照样 PASSED**——证明自我收紧只能在执行层（严格 xfail），静态层够不着 |
  | ⑤ 真机两跑：带标记 / 摘标记 | 带标记 → 1 xfailed / exit 0；摘标记 → 那行**真红**且信息里是 `NoneType` |
  | ⑥ 真机两跑：`getWindowList` 锚点正常 / 换成不存在的标题 | 正常全过；换掉后**恰好那 4 条**正路径红，信息里有 `outputListContains.windows`——新判据真的被求值 |

  六向均按预期红/绿，用例表逐字节还原（探针自己断言 `read_bytes()` 相等）。
  第 ⑥ 向是「判据写了但没人执行」这道假绿灯的专用护栏（M38 §4.3 的同类）。
- **FULL GATE PASSED**（`check_all.py` 退出码 0）。

### 1.7 S2.3：产品侧缺口修复（2026-09-23，维护者定案「按推荐的来」）

§1.5 下半张表的 5 个实现缺口，本片清掉 4 个半（剩「className 动态名」与「win32 原生
消息实现」留在 BACKLOG）：

| 缺口 | 处置 |
|---|---|
| `screenshot` 无 Pillow | **加依赖**（维护者定案）：`pillow>=10,<12; sys_platform=='win32'`。`capture_as_image()` 内部就是 PIL，加依赖即修复；两后端正路径真机落盘 PNG（文件头 `\x89PNG` 实测）。BitBlt 方案否决（为省一个通用库背一截 GDI+PNG 编码代码，不划算）。`knownGap` 标记摘除，静态层「实现缺口 2 条」清零 |
| uia `desktop.select` 静默假成功 | 改按**列表项** `SelectionItemPattern.Select()`；label/value 按列表项文本匹配（WinForms ListItem 的 Value 属性实测恒空，`30006`=`''`）；无匹配显式 `ELEMENT_NOT_FOUND`（details 带 enumerableItems），不再吞错 |
| uia `desktop.getText` 串位 | `_read_element_text`：ValuePattern 优先、`window_text()` 回落（Label 的 Name 就是文本，行为不变）。**坑**：comtypes 生成的 ValuePattern 只有 `CurrentValue` **属性**，`GetCurrentValue()` 方法不存在（实测 `AttributeError: GetCurrentValue`）——这正是修复初版仍回落串位的原因 |
| win32 `select`/`getSelectedText` 静默假成功/恒空串 | 维护者定案**先显式失败**：`EXECUTOR_FAILED` + details(operation)；原生消息实现登记 BACKLOG（LB_SETCURSEL 等，缓冲区要跨进程） |
| win32 `controlId` 过滤失效 | `_find` 拿到 descendants 后按 **ElementInfo** 的 control_id（GetDlgCtrlID）手工补滤。两个坑：pywinauto 的 `children` 只读 class_name/title/control_type（`control_id` 被静默忽略）；包装元素上的 `control_id` 在 0.6.9 是 deprecated **方法**而非属性（比出来恒 False），必须走 `element_info.control_id` |
| `getWindowList` 声明与实现不一致 | 从 `_no_session_commands` 移除（两后端），声明对齐实现：它需要会话 |

**读侧的一课（select）**：UIA 的 `Select()` 改的是 ListBox 选中项，但**不触发** WinForms
的 `SelectedIndexChanged`（探针 round5：选中=beta 而 listStatus 回显仍是 `'none'`）——
状态回显 Label 对这条命令不是有效读侧。处置：执行器把选中项读回写进
`effectDetails.selectedItem`（新增 expect 键 `effectDetails`，`effects[0].details` 的
子集断言），e2e 用例再叠加**独立于执行器**的 UIA SelectionPattern 读回防自证。

**controlId 的用例表口径**：WinForms 给控件分的 control id 实测**每次进程启动都漂**
（同一代码三次编译三次不同，同一 exe 连跑三次也三个不同值——**2026-09-23 订正**：
漂移粒度是「每次启动」而不是「每次编译」），没有按值正路径可写；用「错误 id 排除
title 命中」负路径钉住——旧实现下它是 `matchedCount=1` 的成功，新实现才是
`ELEMENT_NOT_FOUND`。

**验证**：真机探针五项全过（`.harness/spike/probe_desktop_s23_fixes.py`，中途经
round2–round5 四轮深挖定位 `GetCurrentValue` 方法缺失与 Select 不触发事件两处真相）；
e2e 靶子验收 `tests/e2e/test_desktop_target_effects.py` **6 passed**（两条 xfail 转正：
select 断言执行器读回 + 独立 UIA 读回，getText 断言 `note-ready`）；两后端桌面矩阵
180 项 exit 0（uia 85 / win32 95 变体）；静态层 PASSED（86 条命令，**实现缺口 0 条**）。
**负向验证 2 例**（均红在预期的那一条上、逐字节还原）：① index 行 `selectedItem` 期望
beta→gamma → 恰好该行红（`effectDetails` 接线生效）；② 停用 `_find` 的 control_id 补滤
→ 恰好「错误 id 排除」行红（证明过滤在扛判据）。
**已知波动**：整模块连跑中 `test_drag_moves_the_target_control[uia]` 出现过 1 次
dragStatus 未变（单跑与后续两轮整模块均过）——前台竞争类抖动，与 S2.3 改动无因果证据，
先记录不处置。

### 1.8 S4.1：L2 最小链路（2026-09-23，两案均按维护者推荐的来）

策略 §2 L2（真机冒烟）的第一步：不铺表，先把「起服务 + 拉起浏览器 + 真通道往返 +
会话建立」全链路打通。

- **插桩形状与 L1 同构**：唯一差别是后端——`ScriptedExtension` 换成**真的**
  `ExtensionExecClient`，执行器/会话/错误整形零改动（构造注入的回报）。
- **隔离两道**（M36 杀 21 个真进程教训的防线）：① `RPA_EXT_ENDPOINT_PREFIX=
  rpa_core_ext_l2_`——host 继承浏览器环境、pytest 客户端读自己的 env，两边只见
  测试端点，用例内顺带断言「可见端点全部带测试前缀」；② 独立
  `--user-data-dir` 临时 profile + `--load-extension` 注入（unpacked ID 由路径
  派生，host manifest 早已白名单，`extension_installer.native_host_origins` 的
  既有机制直接复用，**零机器状态变更**）。收尾按「命令行含 profile 路径」精确
  杀进程（CIM 查询，不碰同名浏览器其它实例）。
- **门禁**：`RPA_BROWSER_L2=1`（策略 §4 定案的浏览器侧同类开关）。只开它时
  conftest 收集层放行 L2 驱动、正向枚举 ignore 掉全部 L1 驱动；缺省 skip 并在
  尾部提示启用方式（与 L1/桌面 E2E 同款）。
- **首跑实测**：`1 passed in 6.05s`——navigate 真建会话 → getText 读真实 DOM
  的 `#t` == `'text'`、effect==read。收尾核验：0 个 l2-profile 的 msedge 残留
  （当时机器上另有 21 个真实浏览器进程与 1 个 09:55 拉起的 ext-host，均为
  维护者真实实例，未被触碰——隔离真实生效，不是纸面设计）。
- **新增**：`testapps/browser/basic.html`（靶页：#t 文本、#kw 输入、#sel 下拉、
  #chk 勾选、#btn 点击回写、#covered 遮挡对）、
  `tests/commands/test_browser_l2_matrix.py`（驱动 + session 装配 fixture）。
- **下一步（S4.2 起）**：给 `browser.json` 正路径变体逐个补 `l2` 块
  （page + 真机 expect；calls 类断言 L1 独有、不进 `l2`），驱动按 `l2` 块
  表驱动展开；静态校验器加 `l2` 块口径校验。逐类先探针实测再写期望
  （沿用 S2/S2.3 纪律）。

### 1.9 S4.2：L2 表驱动化 + 读族 6 命令落地（2026-09-23）

- **`l2` 块定形**（策略「同一份表」的落地形态）：变体级可选 `l2` =
  `{page, inputs?, expect}`；L1 的 `expect` 一字不动（那是假扩展口径的契约
  断言），真机期望独立成块。无 `l2` 的变体 L2 不收集——桩形状整形
  （null→"" 之类）、通道错误映射、纯 timeoutSeconds 下发断言天然 L1-only。
- **装配抽出共用**：`tests/commands/l2_harness.py`（`l2_browser_session()`
  上下文管理器）——pytest 驱动与 spike 探针共用同一份「起服务/拉浏览器/
  等上线/精确收尾」，避免「同一件事两处口径」。
- **先探针后写期望**：`.harness/spike/probe_browser_l2_read.py` 实测 15 条
  候选全过，并纠正一处推断——`getText` 的 `infoType=href` 读回的是**属性
  原值** `/next` 而非绝对 URL（少了一个动态端口断言的麻烦）。其余实测值
  直接进表（outerHTML 逐字节、getSelectOptions 的 options 全字段、queryAll
  的 `.q` 双匹配、executeScript 的 result 原样回传）。
- **范围**：读族 6 命令 15 变体（getText 6 / getSelectOptions 2 /
  getPosition 2 / queryAll 2 / executeScript 3）+ 端点隔离断言独立用例。
  `listPages` 本批**未收**：真实标签页列表的 URL 含动态端口，现有 expect 键
  没有「列表项正则匹配」能力——不为它单开键，留待后续切片连同 attach/
  navigate 的 URL 断言一起定口径。
- **驱动**：每变体新执行器 + 新会话（navigate 预置到 l2.page），变体间零
  状态泄漏；`sessionId` 一律替换为真建会话；`{page}` 占位符走
  `materialize_inputs` 的 extra 通道。
- **静态校验器第 8 条**（`check_command_matrix.py`）：l2 必须是对象、page
  必填且靶页存在、expect 非空且**禁含调用类键**（onlyCall/calls/noCalls）。
  PASSED 行新增 l2 块计数。
- **验证**：L2 真机 `16 passed in 8.56s`（15 变体 + 隔离用例）；L1 回归
  `180 passed`；静态层 PASSED。**负向验证 2 例**（均红在预期那条、逐字节
  还原）：① getText text 的 l2 期望改错 → 恰好该变体红（L2 断言链真在
  扛）；② l2.page 指向不存在靶页 + expect 混入 onlyCall → 恰好该变体两条
  报红、exit=1（第 8 条真在检查方向生效）。
- **靶页扩充**：`basic.html` 新增 `#link`（href 靶子）、`.q` ×2（queryAll
  多匹配）、`#kw` 预置值 `preset`（value 靶子）；`#t` 保持 textContent
  恰为 `text`（S4.1 冒烟依赖它）。

### 1.10 S4.3：交互族 10 命令 46 变体 + 一个真产品 bug（2026-09-23）

**L2 的第一笔回报就是一个真 bug**：`browser.check` 的 check/uncheck/toggle
**三个操作全部反转**——扩展侧设完 `el.checked` 又补发了一个合成 `click`，
而合成 click 在 checkbox 上触发**激活行为**（再切换一次 checked），把刚设的
值翻回去（`background.js` check case）。L1 桩断言天然看不见这类病
（stub 应答里 `checked` 永远是脚本写的值）；只有真 DOM 读侧能抓到。
修复：删掉那行 click 补发（input+change 是框架监听状态变更的标准事件面）。
**这是 L2 存在意义的实证**——策略 §2 预判的「真实浏览器里到底有没有生效」
类缺陷，第一个被抓的就是它。

**读侧机制落地（桌面 S2.3 双读侧教训的浏览器版）**：`l2` 块新增 `verify`
步——主命令之后、同执行器同会话、用**另一条命令**独立读回页面状态
（`#status`/`#mods` 读侧约定：动作回写 status、修饰键按 CWAS 回写 mods）。
驱动与静态校验器（第 8 条扩展：verify 步 command 必须在 catalog、expect
同样禁调用类键）同步支持。

**探针实测的其它语义**（都已写进表）：
- `input` 的 type 模式是**追加**（`preset`+`hi`→`presethi`），fill/clipboard
  是替换——真实语义差，L1 的参数下发断言看不出来；
- `click` 的 double 发的是 `click`+`dblclick`（不是 click×2）——靶页按真实
  语义收（左键 click 才计数、dblclick/右键 contextmenu/中键 mousedown 各自
  回写），不拿想象写期望；
- `keyIntervalMs`/`postDelayMs` 在真机上用 `elapsedAtLeastMs` 断言（实测
  216.9/318.8ms，下界取 200/50）；
- scroll 的 scrollY 精确值依赖视口——`l2_harness` 钉 `--window-size=1280,900`
  （L2 是本机可选冒烟，接受钉值）；`smooth` 变体**不收**（动画时序竞态，
  命令返回时位置未到位，无法同步断言）。

**注入方式**：46 个 l2 块用 `.harness/spike/add_l2_interact.py` 脚本注入
（browser.json 的 json.dumps 往返逐字节稳定已验证；每目标断言「恰好命中
一条且尚无 l2」）。手工补了一课：`selectBy-label` 的 `value` 也要覆盖
（L1 的 "Label" 是桩世界的值，真页面不存在——首跑恰好它红，改 `"Gamma"`）。

**验证**：L2 真机 `62 passed in 21.65s`（61 变体 + 隔离用例）；L1 回归
180 passed；静态层 PASSED（l2 块 61 个）。**负向验证 2 例**（均红在预期
那条、逐字节还原）：① click 的 verify 期望改错 → 恰好该变体红、报文带
`[verify[0] browser.getText]` 前缀（verify 链真在扛）；② verify 步注入
不存在命令 + onlyCall → 恰好该变体两条报红 exit=1（第 8 条 verify 口径
真在检查方向生效）。

### 1.11 S4.4：导航/attach/标签页族 24 变体 + 两个半真机 bug（2026-09-23）

探针（`probe_browser_l2_nav.py` + 深挖 `probe_browser_l2_history.py`）又抓到
**两个产品 bug 和一处对称性缺口**——全部是 L1 桩断言结构上不可见的：

1. **`navigate` 的 back/forward 恒失败**：`chrome.tabs.goBack/goForward` 对
   「历史栈明明有上一页」（history.length=2 实测）的标签页也必报
   "Cannot find a next page in history"——该 API 在 MV3 下不可靠。
   修复：改页内 `history.back()/forward()`（MAIN world），并补「先等导航
   开始（status/url 变化，3s 宽限）再 waitComplete」——否则 waitComplete
   会在导航起步前看到 complete 提前返回，把「真的 back 了」误判成无历史；
   真无历史时显式报错（保住旧 API 的失败语义）。
2. **`timeoutMs` 从不进 args**：`tabs_create/tabs_navigate/tabs_history` 只抬
   信封超时、不把 timeoutMs 发给扩展 → 加载等待恒 30s，`onTimeout` 策略
   永不触发（实测 onTimeout=error 的 3s 慢页照样成功）。修复：三个封装函数
   真转发；L1 表 7 条变体的 args 断言同步补上 timeoutMs（**契约变化被 L1
   当场抓住**——这正是矩阵该干的活，期望更新是刻意的）。
3. **已有会话路径吞掉 `timedOut`**：`onTimeout` 此前只在新标签页路径处置，
   换会话导航静默忽略。已补齐对称（stop→stopLoading / error→TIMEOUT）。

**机制增量**（驱动与静态校验器同步）：`l2.pre` 准备步（建历史栈、开第二页）、
`saveAs` 捕获 pre 步 tabId、`{page:NAME}` 命名页占位（先它后 `{page}`，
防子串腐蚀）、expect 侧的 `substitute_extra` 替换（listPages/attach 的
URL 动态端口断言就靠 `{page}`）、`l2.session: false` 显式不预置会话
（close 的空会话负路径）。`{tab}` 按 **int** 注入（navigate outputs 的
tabId 是字符串，closedTabIds 是 int——首跑假红过一次）。
新增靶页 `other.html`（标题靶子）与 `/slow.html` 动态路由（睡 3s，
onTimeout 两态的「必然超时」靶子）。

**取舍**：`closeTabs all=true` **不收**（会关掉整个测试窗口、端掉共享浏览器）；
`listPages no-open-tabs` 不可真机构造（关完最后一个标签页浏览器即退出）；
`goto-prepends-https`/`browserType-chrome`/`commandLineArgs` 留在 L1
（字符串逻辑/多浏览器/拉起路径，非 L2 价值点）。

**验证**：L2 真机 `86 passed in 31.07s`（85 变体 + 隔离用例）；L1 回归
180 passed；静态层 PASSED（l2 块 85 个）。**负向验证 2 例**（均红在预期
那条、逐字节还原）：① back 的期望改成 other → 恰好该变体红且报文显示
`{page:other}` 已替换成真 URL（pre 建栈 + 命名页替换 + 断言链全真）；
② 校验器注入 session 非布尔 + pre 坏命令 + `{page:nosuchpage}` → 恰好
三条报红 exit=1。

### 1.12 S4.5+S4.6：cookies/截图/等待族 + 未实现命令钉死，S4 收官（2026-09-23）

- **cookies 族真机语义**（host-only cookie，127.0.0.1）：set/get/remove 全链路
  互读（verify 用 cookieGet 独立读回）；实测语义入档：cookieGet 读不存在的名
  返回**空串**（不是 null）；cookieRemove outputs 只有 sessionId。
  **cookie 名按变体独立**（`l2cs`/`l2cg`/…）——profile 共享，防变体间污染读数。
- **waitFor 四态真机全过**（靶页补 `#hidden` = display:none）：visible=1 /
  hidden=0 / attached=1 / detached=0；不满足态 600ms 超时 → TIMEOUT +
  `elapsedAtLeastMs` 真计时断言（实测 627ms）。
- **screenshot 真 PNG 落盘**（16762 字节）；**waitLoad** 已加载页回当前 url；
  **stopLoading**：pre 步 executeScript 触发向 slow 的导航后立刻停——
  导航未提交前停掉，标签页留在 basic（实测口径）。
- **upload/download/handleDialog**（策略 §6 定案的本期未实现）四条变体在
  L2 钉 `COMMAND_NOT_FOUND` 显式失败——真后端同码。
- **`browser.closeBrowser` 全 6 变体不收 L2**（硬边界）：它按进程名杀该浏览器
  **全部**进程，L2 里跑会连维护者真实浏览器一起杀——这不是测试能隔离的东西，
  测试价值已在 L1 的进程面打桩里。至此浏览器 32 条命令全部有了 L2 处置：
  **109 个 l2 块**（28 条收真机正/负路径，closeBrowser 1 条显式不收，
  upload/download/handleDialog 3 条钉未实现）。
- **验证**：L2 真机 `110 passed in 38.22s`；L1 回归 180 passed；静态层
  PASSED（l2 块 109 个）。负向验证：waitFor 超时的 errorCode 改错 → 恰好
  该变体红（报文带真实错误消息），逐字节还原。

### 1.13 BACKLOG 桌面三条尾巴：第 1 条实现 + 两处判因订正（2026-09-23）

维护者问「backlog 的尾巴」，逐条取证。**结论先行：三条里第 1 条做完，第 2 条
的判因被实测推翻（出路待拍板），第 3 条证据不变。**

**尾巴 #1 —— win32 `select` / `getSelectedText` 原生消息实现（`done`）**

S2.3 时两条命令在 win32 侧是显式 `EXECUTOR_FAILED`（止血）。可行性探针
（`.harness/spike/probe_win32_native_select.py`、`probe_win32_native_select_e2e.py`）
先跑，四个问题的实测答案：

| 问题 | 实测 |
| --- | --- |
| `_find` 拿到的元素是什么 | **自动包装**成 `ListBoxWrapper` / `ComboBoxWrapper`（pywinauto 的 `windowclasses` 里就写着 `WindowsForms\d*\.LISTBOX\..*`），不必手工构造 |
| 原生 select 能否生效 | 能。三种选择都生效：ListBox `select("beta")`→`[1]`、`select(2)`→`[2]`；ComboBox `select("two")`→`1`、`select(2)`→`2` |
| 读回面 | ListBox `selected_indices()` + `item_texts()`；ComboBox `selected_index()` + `item_texts()` |
| 会不会触发 UI 事件 | **会**（见下，推翻了原判断） |

- **读侧陷阱（实现里专门防了）**：`ComboBoxWrapper.selected_text()` 内部是
  `item_texts()[selected_index()]`，而 `CB_GETCURSEL` / `LB_GETCURSEL` 在**无选中**时
  返回 -1，Python 负索引把它静默变成**最后一项**。实测量化：不加判断时 ComboBox 回
  `'three'`、ListBox 回 `'gamma'`；加了 `>= 0` 判断才是空串。落地成
  `_list_selection()` 助手（select 的读回与 getSelectedText 共用，保证两处口径一致）。
- **推翻原判断**：旧注释写「原生消息同样**不触发** `LBN_SELCHANGE`（与 UIA Select
  不触发 SelectedIndexChanged 同理）」。前半句对（原生消息不发通知），但**结论错**：
  pywinauto 的 `select()` 在设完 curs 之后会 `notify_parent(LBN_SELCHANGE / CBN_SELCHANGE)`
  ——即 `post_message(WM_COMMAND, MakeLong(msg, control_id))`。逐步隔离验证
  （`probe_win32_native_select_readside.py`）：`listStatus` 随每次 select 变
  `none → list:1:beta → list:2:gamma → list:0:alpha`，三次转变各不相同。
  **净效果：win32 原生路径会让 UI 真的反应，比 uia 侧强。**
- **契约无需改动**：两条 manifest 的 `errors` 已含 `ELEMENT_NOT_FOUND` /
  `EXECUTOR_FAILED`；非列表元素仍显式失败（判据从「后端不支持」收窄成「元素不支持」），
  原 `reports-unsupported-backend-explicitly` 变体因此改名为 `rejects-a-non-list-element`。
- **测试可行性（尾巴 #1 与 #2 的耦合点）**：ListBox / ComboBox **窗口文本恒空**，
  win32 侧只能按 `className` / `controlId` 定位；`controlId` 每次进程启动都漂。
  于 `desktop_harness.py` 加运行期占位符 `{listBoxClass}` / `{comboBoxClass}`
  （按需计算，只在变体真引用时枚举一次窗口）。
- **用例**：`select` 5 条按值正路径（ListBox value/label/index + ComboBox value/index，
  断言 `effects[0].details.selectedItem`）、3 条新负路径（无匹配项 / 索引越界 / 非数字
  索引，都收敛到 `ELEMENT_NOT_FOUND` + `enumerableItems`）、1 条非列表元素；
  `getSelectedText` 2 条形状正路径（选中态会被别的变体改动，按值断言等于把顺序写进期望，
  与 uia 侧口径一致）+ 1 条非列表元素。**win32 桌面矩阵 95 → 105 变体，exit 0。**
- **e2e 补两步按值**（`tests/e2e/test_desktop_target_effects.py::test_select_via_win32_native_messages`）：
  先选再读，断言 ① 执行器读回 `gamma` ② **独立读侧 `listStatus == 'list:2:gamma'`**
  （证明靶子自己反应了）③ `getSelectedText == 'gamma'`。该文件 6 → 7 例，7 passed / exit 0。
- **负向验证 3 例（两个方向都做了）**：① 期望侧——把 value-mode 的 `selectedItem`
  改成 `GAMMA-WRONG` → 恰好该变体红（`实得 'beta'`）；② 实现侧——注入
  `element.select(len(items) - 1)` → 5 条按值变体成片红（`实得 'gamma'` / `'three'`）；
  ③ 注入链——让 `_list_control_class_names` 返回空 → 装配层显式抛
  `AssertionError: 装配失败：desktop.win32.findElement(options) → ELEMENT_NOT_FOUND`
  （失败被正确归因到装配，不是被测命令）。三例均逐字节还原。

**尾巴 #2 —— `className` 的判因订正 + 出路落地（`done`，2026-09-23 选 ③，见 §1.16）**

原记录写哈希段「随编译产物变」。**实测推翻**（四个探针，五轴）：

| 轴 | 结果 |
| --- | --- |
| 同一 exe 连跑 3 次 | 类名恒定 |
| 同路径重编译 | 恒定 |
| 不同路径 + 不同文件名 | 恒定 |
| 纯注释改动 / 真实 IL 改动（改控件文本） | 恒定 |
| **换一个完全不同的 WinForms 程序** | **同一段 `34f5582_r8_ad1`** |

→ 哈希段是**机器 + 运行时级常量**（同机所有 .NET Framework 4.x 程序共享），
不是产物级也不是应用级；换机器或换 CLR/WinForms 版本才会变。
另订正：`controlId` 的漂移粒度是**每次进程启动**（同一 exe 连跑三次三个值），
不是「每次编译」。真正的问题变成**可区分性太粗**（两个 Edit 撞同名）+ 无窗口文本的
控件只能靠它定位。出路三选一（见 BACKLOG）：子串匹配 / 精确化文档口径 / 新增
`classNameRe`。**维护者拍板选 ③「新增 `classNameRe`」——已实现（§1.16）**；
`className` 本身语义不改，测试侧仍走运行期占位符。

**尾巴 #3 —— `getWindowList` 枚举被拒静默空列表（`等复现`，2026-09-23 拍板不改代码）**：
本轮未新增证据，状态与 S2.2 一致（路径存在、8×2 次枚举未复现；另有四驱动连跑时一次 15s
操作预算被全桌面枚举冷启动吃满的观测）。**维护者拍板「等复现」**——无可复现输入时不预先
改产品代码（详见 §1.16 末节）。

### 1.14 桌面变体数三处错账订正 + 页面口径说明（2026-09-23）

维护者问「这些测试都集成在指令测试页面了吗」，查代码时**顺手撞出三处过期数字**——
根因是尾巴 #1 那一轮给桌面两表加了变体（win32 95→105、uia 75→85），
**只改了任务单，BACKLOG / PROGRESS / 策略文档三处没跟上**。
与 M26 那次「纠正结论只改眼前一处」同病，只是这次漏的是**数字**而不是定性。

**本轮一手实测的真值**（不是转抄）：

| 口径 | 真值 | 怎么取的 |
| --- | --- | --- |
| 桌面 UIA `desktop.json` | 85 变体（17 条命令） | 逐命令数 + `--collect-only` 双向吻合 |
| 桌面 Win32 `desktop_win32.json` | 105 变体（19 条命令） | 同上 |
| **桌面合计** | **190** | `190 passed in 23.45s / exit 0`（`-p no:faulthandler -o console_output_style=count`） |
| 浏览器 L1 | 180 | 不变 |
| 数据 + 工作流 L1 | 84 + 4 = 88 | 不变 |
| 浏览器 L2 块 | 109 | 不变 |
| **全表变体总数** | **458**（`core 268 + desktop 190`） | 与 `load_case_table` 合并结果吻合 |

**一处我自己现算错的账（记下来）**：初次算总表时写成了 **567** = `core + desktop + l2`——
**错在把 `l2` 又加了一遍**。`l2` 是 `matrix_core` 的**子集**（那些变体在本页仍会被 L1 侧跑到），
不是并列项。正确口径是 `total = core + desktop = 458`。这个错**当场被新写的契约测试抓住**
（`assert 567 == 458`），正是「数字要先跑一遍再写」的又一个实例——本轮开头刚因为
没这么做而沿用了三处过期数字。

**订正的三处**：任务单 §1.6.7 与 §5 的两处 172（加指向本节的说明，不改写当时的历史记录）、
BACKLOG 当前任务段的 172、策略文档 §2/§4/§5 的三处 172 → 190。
`PROGRESS.md` 的 S2.2 条目里也有 172，**刻意保留**——那是当日的实测记录，
历史条目不该被回改（本轮的订正另立一条，见 PROGRESS 同日「桌面变体数订正」）。

**页面口径说明（本轮的实质交付）**：`scope_label` 原先只列「已覆盖：<5 个命名空间及其变体数>」，
读起来像「点下去这些都会跑」，而主按钮只点火 L1。新增 `ScopeBreakdown`（纯函数，
按**文件名 + 变体是否带 `l2` 块**分类，与 `tests/commands/` 的收集口径同源，
不写死数字）与三段文案：

- 「本页按钮实际跑 268 个变体（浏览器 / 数据 / 工作流 L1，假扩展、不碰本机浏览器）」；
- 「另有桌面 190 个变体：会被收集但因缺开关**全部跳过**（要 `RPA_DESKTOP_E2E=1`，
  它会真开窗并抢前台），本页不设」；
- 「浏览器真机 L2（109 个变体）由独立开关 `RPA_BROWSER_L2` 把关，本页连收集都不收集」。

契约测试补两条：① `test_scope_text_states_what_the_button_actually_runs`（三段文案都要在，
且**先断言三笔账非零**——否则空数据会把这条喂成假绿）；②
`test_scope_breakdown_matches_the_case_tables`（不钉死数字，只钉**划分规则**：
桌面两表进 `desktop`、带 `l2` 块的 `l2` 且 `l2 ⊆ core`、`total == core + desktop`）。
页签契约测试 16 → **18 项**，全绿。

**负向验证 3 例**（`tests/contract/test_gui_command_matrix.py`，判据是「红在**预期的那一条**上」，
注入文件 `md5 cb7d717…` 逐字节还原后复跑全绿）：

| 注入 | 结果 | 说明 |
|---|---|---|
| ① `l2` 计数点整段切掉（计数器恒 0） | **恰好 2 条红** | 一条落在 `assert (268 > 0 and 190 > 0 and 0 > 0)`（**「三笔账非零」这条假绿防线自己先炸了**，证明它不是摆设）；一条落在 `assert 0 == 109` |
| ② `DESKTOP_NAMESPACES` 漏掉 `desktop_win32` | **恰好 1 条红** | `assert 85 == 190`——105 个 win32 变体被误算进 `core`，点名精确 |
| ③ 文案写死旧数字 `172`（与数据脱钩） | **恰好 1 条红** | `'桌面 190 个变体' not in ...`，且失败报文**把成品文案整段打出来**，正好肉眼复核三段都在 |

②③ 是**两个方向**：②打「数据侧」（划归规则），③打「文案侧」（是否真从数据算）。
只做一个方向的话，另一侧脱钩不会被发现。

**未做的事（如实记）**：本轮**没有**把桌面与 L2 接进页面按钮——那是下一个切片
（三目标开关：L1 / L1+桌面 / L1+L2）。它需要先改 `tests/commands/conftest.py` 的收集口径
（现在 `RPA_BROWSER_L2` 与 `RPA_COMMAND_MATRIX` **不叠加**：只开 L2 时它会 ignore 掉全部 L1 驱动），
再给页面加开关。**这是设计好的欠账，不是遗漏**（已进 §6）。

### 1.15 页签集成三目标：L1 / L1+桌面 / L1+L2（2026-09-23，§6 那笔欠账的兑现）

§1.14 把「页签只点火 L1」如实写在了页面上，但页签仍**点不动**桌面与 L2。本节把它做实。

**前置：先让两个开关能叠加（`tests/commands/conftest.py`）**。改前 `collect_ignore` 是
`if/else` 二选一——`RPA_COMMAND_MATRIX` 一开就 ignore 掉 L2 驱动、`RPA_BROWSER_L2` 一开就
ignore 掉**全部** L1 驱动，所以「L1 + L2 一起跑」这条路**根本不存在**。改成把口径抽成纯函数
`collection_ignore(*, matrix_enabled, l2_enabled, existing=None)`，四格：

| matrix | l2 | 收集 | 实测（`--collect-only`） |
|---|---|---|---|
| 未开 | 未开 | 什么都不收集 | **0** |
| 未开 | 开 | 只有 L2 驱动（旧口径保持） | **110** |
| 开 | 未开 | 全部 L1 驱动（L2 驱动被 ignore） | **458** |
| 开 | 开 | 全部 L1 驱动 **+** L2 驱动（**新开的一格**） | **568** |

`458 + 110 = 568` 直接证明可叠加。抽成纯函数是为了让契约测试能注入固定清单验四格——
**不随目录里新增/删除驱动文件而漂**。

**页面侧：三个目标按钮**（`RUN_TARGETS` 三元组 + `find_target(key)`）：

| 按钮 | env 组合 | 副作用面 | 进度分母 |
|---|---|---|---|
| 运行 L1 契约矩阵 | `RPA_COMMAND_MATRIX=1` | 假扩展，不碰本机浏览器 | 268 |
| L1 + 桌面真机 | `+ RPA_DESKTOP_E2E=1` | 真开窗并抢前台（现场编译 WinForms 靶子），约半分钟 | 458 |
| L1 + 浏览器真机 | `+ RPA_BROWSER_L2=1` | 拉起一个真实浏览器窗口（独立 profile，不碰你已开的浏览器） | 377 |

做**三个**而不是一个「全跑」按钮，是因为三者副作用面完全不同（不碰浏览器 / 抢前台 / 拉起真浏览器），
混成一个会让「我点了运行然后浏览器被开了」出现。`_run_matrix` 从「硬编码注 `MATRIX_ENV=1`」
改成 `for name, value in self._target.env.items(): env.insert(...)`；`_progress_total()` 的分母随目标变。
保留 `self.run_button` 别名 = 第一个目标，兼容既有契约测试。

**契约测试 18 → 22 项**，新增四条：`test_targets_map_to_the_three_switches`、
`test_progress_total_follows_the_chosen_target`、`test_run_matrix_really_injects_the_target_env`、
`test_collection_matrix_is_additive`；扩展 `test_env_names_match_the_pytest_side` 到三个开关名
+ 三者互不相同。

**抓到一条自己写的假绿灯（本轮最重要的一条）**：第四条 `test_targets_map_to_the_three_switches`
只断言了 `RUN_TARGETS[i].env` 这个**数据定义**——而 §1.14 的教训已经写明「数据定义对 ≠ 注入真发生」。
把它跟 `test_progress_total_*` 一起跑仍 20 项全绿，直到我**摘掉 `_run_matrix` 的 env 注入循环**去做
负向验证，才发现没有任何一条能拦住「按钮二安静地跑成按钮一」。补了
`test_run_matrix_really_injects_the_target_env`（**中间态断言**：走真实入口 `_on_target_clicked`、
只打桩 `_start`，断言子进程**真实持有的**三个开关）——注入后它精确红在 `AssertionError: l1`。

**负向验证 2 例（两个方向，都做了）**：

| 注入 | 结果 | 说明 |
|---|---|---|
| ① 页面侧：`_run_matrix` 不注入目标 env（`pass` 掉那段循环） | **恰好 1 条红** | `test_run_matrix_really_injects_the_target_env` → `AssertionError: l1` |
| ② conftest 侧：`collection_ignore` 两开关不叠加（`return [L2_DRIVER_NAME]`） | **恰好 1 条红** | `test_collection_matrix_is_additive` → 红在第 372 行「两个都开」那一格 |

① 打「注入侧」、② 打「收集侧」——只做一个方向的话，另一侧脱钩不会被发现。两处注入文件
均逐字节还原（`grep INJECTED` 无残留）后复跑 22 项全绿。

**探针复核**：`.harness/spike/probe_gui_targets.py` 走真实入口，实测三个目标注入互不串味，
子进程真实持有的开关与上表一致、进度分母 268 / 458 / 377。

### 1.16 尾巴 #2 落地（`classNameRe`）+ 尾巴 #3 记账 + M38 收口（2026-09-23）
维护者拍板：「**尾巴2③，尾巴3等复现，然后收口**」。本节记落地与收口。

**尾巴 #2 → ③（新增 `classNameRe` 正则字段），`done`**

选 ③ 而非「子串匹配」或「只改文档」的理由：`className` 的可区分性问题（§1.13：两个
Edit 撞同名 `WindowsForms10.EDIT.app.0.<哈希>_r8_ad1`）本质是**类名里混了动态段**，
子串匹配治不了「用稳定前缀跨机器筛选」的需求，改文档只是描述现状。正则能表达跨机器的
稳定前缀，且与等值语义**可以共存**（两字段互斥，各司其职）。

| 落点 | 内容 |
| --- | --- |
| `model/desktop.py` | `class_name_re` 字段（alias `classNameRe`，`min_length=1`）+ `require_identity` 互斥校验 |
| `commands/desktop_win32/{attachWindow,findElement}.json` | 声明 `classNameRe`；`className` description 补「与 classNameRe 互斥」 |
| `commands/desktop/attachWindow.json` | 同上（uia 侧） |
| `executors/desktop_win32.py` | 模块级 `import re` + `ValidationError`；`attachWindow` 正则过滤分支、`findElement` 映射 `INVALID_INPUT`、`_find` 正则过滤 |
| `executors/desktop.py`（uia） | `ValidationError` 映射 `INVALID_INPUT`；`_find_windows_by_title` 加 `class_name_re` 参数、`_class_matches` 支持正则、exact 分支仅在 `not class_name_re` 时走 `FindWindowW` |
| `devserver/static/i18n.js` | `classNameRe: "类名（正则）"` |
| `tests/contract/test_desktop_attach_window.py` | 桩签名 + 两条新用例（正则可单独用 / 互斥）+ `shared` 断言加 `classNameRe` |
| `tests/commands/cases/{desktop_win32,desktop}.json` | 各 +3 变体（正则正向 / 正则负向 / 互斥） |

**三条真实踩坑（都靠实测定性，不是读代码推断）**：

1. **`cannot access local variable 're'`（我自己引入的真 bug）**——`_execute_sync` 里
   `getWindowList` 分支原有 `import re`，使 `re` 在整个函数作用域被当成**局部名**；
   模块级加了 `import re` 后，同一函数内**早于**那个局部 `import` 使用 `re` 就炸
   `UnboundLocalError`。修法：删掉函数内多余的局部 `import re`，统一用模块级。
   这是新正则分支首跑就炸出来的，读代码时完全没看出来。
2. **`findElement` 互斥实测回 `EXECUTOR_FAILED` 而非 `INVALID_INPUT`**——
   `DesktopLocator.model_validate` 抛的 `ValidationError` 被外层 catch-all 吞成
   `EXECUTOR_FAILED`（像内部崩了）。修法：执行器显式捕获 `ValidationError` 改报
   `INVALID_INPUT`（用户输入问题），**两后端都加**。用例期望同步按实测校正
   （初写 `INVALID_INPUT`，首跑得到 `EXECUTOR_FAILED`，改代码后才回 `INVALID_INPUT`，
   notes 记了这次校正）。
3. **uia `attachWindow` 的 exact 分支漏了 title 等值支**——`classNameRe` 存在时 exact 不走
   `FindWindowW`、改走 EnumWindows，但那段 `_on_window` 的 title 匹配只有 `contains` /
   `regex` 两支，**没有 exact 支**，`matched` 于是恒 `False` → 任何「exact + classNameRe」
   都匹配不到（实测 `ELEMENT_NOT_FOUND`：`Desktop window did not match`）。
   修法：补 `else: matched = not title or title == win_title`（空 title 等价于不按标题过滤，
   与 `FindWindowW(None, None)` 语义对齐）。

**正则判据必须用实测类名写（重要）**：两条 `attachWindow` 正向的原写
`"WindowsForms10\\.Window\\.app"` **跑不过**——探针实测主窗口类名是
`WindowsForms10.Window.8.app.0.34f5582_r8_ad1`，**比子控件多一段窗体序号 `.8`**，
正则得写成 `"WindowsForms10\\.Window\\.[0-9]+\\.app"`。子控件（LISTBOX 等）无该段，
`"WindowsForms10\\.LISTBOX"` 能直接命中。**判据来源是
`.harness/spike/probe_win32_target_classes.py`**——又一次印证「用例期望先跑探针实测再写」。

**负向验证（exact 分支修复，两个方向都做了）**：

| 注入 | 结果 |
|---|---|
| 删掉 `_on_window` 里新补的 `else` 支（回到无兜底） | **恰好 1 条红**：`desktop.attachWindow::class-name-regex-binds-the-window-by-its-dynamic-class` → `ELEMENT_NOT_FOUND` |
| 逐字节还原（备份回写 + `md5sum` 复核） | 哈希 `b00ef897e06e753fd7914ab126afe9aa` 与注入前一致 |

**桌面矩阵实测（本轮真值）**：

| 驱动 | 变体 | 结果 |
| --- | --- | --- |
| `test_desktop_win32_matrix.py` | **111** | `111 passed`（首跑 110 passed / 1 failed → 校正正则后全绿） |
| `test_desktop_matrix.py`（uia） | **88** | `88 passed`（首跑 87 passed / 1 failed → 修 exact 分支后全绿） |
| 桌面合计 | **199** | 均 `RPA_COMMAND_MATRIX=1 RPA_DESKTOP_E2E=1`，`-p no:faulthandler`，exit 0 |

（win32 105→111 = +6、uia 85→88 = +3。win32 的 +6 里有 +3 是新变体、另 +3 是 §1.13
尾巴 #1 那轮已加但未计入本表的历史值——**以本轮实测 111 为准**。）

**尾巴 #3 → 等复现（不改代码）**：维护者拍板「等复现」。`getWindowList` 枚举被拒静默空列表
路径存在、8×2 次枚举未复现（与 S2.2 一致）；`operationTimeoutMs` 的 15s 冷启动吃满也只观测到
一次。**不预先改产品代码**——没有可复现的输入，任何「区分枚举失败与真没有窗口」的实现都
只能靠猜（改成抛错反而可能把「真没窗口」误判成失败）。记入 BACKLOG 的「等复现」栏，
保留观测证据，复现后按现场再决策。

**收口门禁（本轮一手实测）**：

- **FULL GATE PASSED / exit 0**：`1113 passed / 21 skipped / 2 xfailed`（`check_all.py` 默认门禁，
  不含桌面 E2E 与指令矩阵——按需开关）。静态层全过：`ARCHITECTURE CHECK PASSED`（63 py / 86 manifest）、
  `TASK CHECK PASSED`（58 features / 1 active task）、`PARAM CONSUMPTION CHECK PASSED`
  （77 checked / 3 exempt / 台账 0）、`ERROR CONTRACT CHECK PASSED`（77 checked / 3 exempt）、
  `COMMAND MATRIX CHECK PASSED`（86 条命令 / 未建表 0 / 死参数 0 / 实现缺口 0 / l2 块 109）、
  ruff `All checks passed!`、五个 `.mjs` 切片检查全过。
- **门禁抓到一处真缺口（如实记）**：首跑 `ERROR CONTRACT CHECK FAILED` 点名
  `desktop.findElement` / `desktop.win32.findElement` 的 `INVALID_INPUT` 未声明——正是本轮
  新增的 `ValidationError → INVALID_INPUT` 映射点。补进两处 manifest 的 `errors` 后转 PASSED。
  **这印证了「新增返回点必须同步 manifest.errors」不是形式要求**：单向门禁在这里真的拦下了
  「实现能返回、契约没声明」的不一致。
- 顺带修掉两处 ruff：`desktop_win32.py` 的 import 排序（`pydantic` 应在 `pywinauto` 前）
  与 `desktop.py` 一处 E501（docstring 折行）。

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

### 4.3 负向验证的假绿灯：注入点没落在被测分支上，且收尾兜底掩盖了增量机制（2026-09-22）

S1.2 页签有一条白盒集成用例（`test_panel_streams_jsonl_into_rows`：子进程边写 JSONL、页签边
出结果行）。按惯例做负向验证——**摘掉 `_run_matrix` 里的 `self._timer.start()`（轮询点火）**，
期望用例转红。实测 **exit=0，照样绿**。

原因两层叠加，**第二层才是真问题**：

1. **注入点没打在被测分支上**：用例当时走的是 `_start(...)` + 自己调 `panel._timer.start()`，
   而注入点在 `_run_matrix`——那条路径根本没被执行。与 M30 S4「拿『删掉已声明的返回点』去
   验证单向门禁」同病：方向不对，删了也不红。
2. **即便改走真实入口，「结束后有行」也证明不了轮询**：`_on_finished` 收尾时**还会再
   `_poll_report()` 一次**，所以无论如何行总会出来。轮询真正买的是**实时**（跑到哪了），
   **只有中间态能证明它**——这属于一类新的假绿灯形式：不是「用例没覆盖到」，而是
   「用例覆盖了别的路径 + 收尾兜底把缺陷补上了」。

**改法**：用例改走**真实入口** `_on_run_clicked()` → `_run_matrix()`，只打桩 argv 组装那一半
（替换 `build_pytest_command`），其余全走真实实现；子进程写完第一行后**等测试放行**才继续，
于是「进程还在跑时第一行已经进表」成为可断言的中间态。三向负向验证全部精确命中：

| 注入 | 结果 |
|---|---|
| `_run_matrix` 摘掉 `_timer.start()` | 红，落在「轮询没有点火」断言 |
| `__init__` 摘掉 `_timer.timeout.connect(self._poll_report)` | 红，落在「实时性」断言 |
| `_run_matrix` 摘掉 `env.insert(REPORT_ENV, …)` | 红，落在「实时性」断言 |

第二条最能说明问题：**它在旧用例下是绿的**（定时器在响、只是没人听，收尾那次 poll 照样把行
补上）。负向验证没红时先怀疑验证本身没打到——M32（`all` 宽松判定）、M30 S4（打错方向）之后
第三次出现，但形式不同，前两次是「用例没覆盖到」，这次是「覆盖了别的路径 + 收尾逻辑掩盖」，
比前两次隐蔽。**推论**：凡「收尾时还会兜底做一次」的设计，其增量机制（轮询 / 流式 / 增量读）
都必须靠**中间态**断言，靠终态永远测不出来。

### 4.4 S3 的两条小账（都不是事故，但都值一句话）

1. **把假设当契约写进了表**：`data.writeText` 那条「相对路径 `../up.txt` 归一化后仍在
   workspace 内 → 允许」是**读代码推断**出来的，没进探针——实测直接红：
   `_within_workspace` 的判据是「拼上 workspace 后的绝对路径仍在 workspace 内」，
   `..` 一越出 workspace 就拒（哪怕还在临时目录里）。改成两条用例后契约才准：
   `sub/../ok.txt`（归一化后在 workspace 内 → 允许）+ `../escape.txt`（越出 → 拒绝）。
   **教训**：探针要覆盖**判据的两侧**，只测「反面」很容易把正面写成想象。
   这次是矩阵自己红出来的，代价只有一次运行——但它是「用例期望先实测再写」这条纪律的又一次实证。
2. **自检用例混进变体流会污染页签口径**：驱动里那两条安全阀自检用例没有 `<命令>::<变体>`
   形状，报告钩子照样记成 case，于是页签的进度分子（测试数 270）与分母（用例表变体数 268）
   对不上。处置：把安全阀自检挪到 `tests/contract/`（**顺带让它在默认门禁里跑**——它是安全
   性质的，本就不该只在按需矩阵里被验证），矩阵报告恢复「一条 case 一个变体」的干净不变量。
   与 §1.2 的「同一件事两处口径」是同一类毛病，这次的两处口径是**测试数 vs 变体数**。

## 5. 验证

- **执行层**：`RPA_COMMAND_MATRIX=1 uv run pytest tests/commands` → **180 passed / exit 0**
  （浏览器通道单驱动；**四驱动全量的当前真值见 §1.13 的桌面订正**——2026-09-23）。
- **静态层**：`check_command_matrix.py` → `PASSED（已校验 32 条命令；未建表命名空间 3 个
  共 54 条命令；死参数台账 2 项）`。
- **实现级负向验证**（证明 L1 真能抓漂移）：临时删掉 `browser.click` 里 M29 修好的
  `simulateHuman` 转发 → 精确 **2 个用例红**（正是断言该参数的那两个），恢复后全绿。
- **校验器三向负向验证**（证明口径不是摆设）：① catalog 新增命令未补用例 → 红；
  ② manifest 新增枚举值未覆盖 → 红；③ 台账里的死参数被真的覆盖 → 报「台账过期」。
  三向均 exit=1 且命中预期关键词，恢复后 PASSED。
- **页签（S1.2）**：`tests/contract/test_gui_command_matrix.py` **16 项**——纯函数 9（仓库根定位 /
  命名空间描述含坏文件容错 / argv 形状且不许自造选项 / JSONL 增量读含半行与坏行 / nodeid 切分 /
  状态累计 / **面板与 pytest 侧的环境变量名一致**——一份约定两处副本的反漂移钉）+ 数据合同 2（真跑一个变体：`RPA_COMMAND_MATRIX_REPORT` 一设就产出可增量读的
  JSONL；不设则零产出）+ 页签装配 5（三个页签与控件齐 / 覆盖范围文案含未建表命名空间 /
  找不到用例表时只提示不起子进程 / **真实入口的实时流式管道** / `shutdown` 收掉子进程）。
  **§1.15 扩到 22 项**（+ `test_targets_map_to_the_three_switches` /
  `test_progress_total_follows_the_chosen_target` / `test_run_matrix_really_injects_the_target_env` /
  `test_collection_matrix_is_additive`；`test_env_names_match_the_pytest_side` 扩到三个开关名），
  22 passed / exit 0。
- **§1.15 收集口径四格（实测）**：`--collect-only` 数得 0 / 110 / 458 / 568（矩阵关+L2 关 / 只 L2 /
  只矩阵 / 两者都开），`458 + 110 = 568` 证明可叠加。
- **S1.2 三向负向验证**（见 §4.3；探针保留为 `.harness/spike/probe_gui_matrix_panel.py`，
  与 `probe_browser_commands.py` 同性质，可复跑）：摘点火 / 摘 `timeout.connect` / 摘 `REPORT_ENV`
  注入，三向均 exit=1 且分别落在预期断言，文件逐字节还原。
- **S1.2 正向回归**：带报告钩子的全量矩阵 180 passed / exit 0，报告 180 条 case + 1 条 summary，
  `(command, variant)` 零缺失——钩子没有影响矩阵本身。
- **S3 执行层**：`RPA_COMMAND_MATRIX=1 pytest tests/commands` → **268 passed / exit 0**
  （浏览器 180 + 数据 84 + 工作流 4），带报告钩子重跑：报告 268 条 case + 1 条 summary、
  `(command, variant)` 唯一组合 268、**无 `command` 缺失记录**、按命名空间
  `{browser: 180, data: 84, workflow: 4}`、非 passed 为空。
- **S3 静态层**：`check_command_matrix.py` → `PASSED（已校验 50 条命令；未建表命名空间 1 个
  共 36 条命令，死参数台账 0 项）`——`PENDING_NAMESPACES` 只剩 `desktop`。
- **S3 安全阀契约**（在默认门禁里）：`tests/contract/test_command_matrix_paths_guard.py`
  **3 项**——正向不误杀 / 负向拦得住（越界 `workspace` 与 `../` 越界的 `setup` 都被点名）/
  接线（源码断言 `assert_confined` 在 `PythonWorkerExecutor(` 之前，即**执行前**）。
- **S3 五向负向验证**（探针 `.harness/spike/probe_data_matrix_negative.py`，可复跑；判据是
  「红在**预期的那几条**上」，不是「有没有红」）：

  | 注入 | 结果 |
  |---|---|
  | ① 表侧：用例表删掉 `data.deletePath.recursive` 全部出现 | 静态校验器红并点名该参数 |
  | ② 实现侧：`writeText` 的 `lines` 只写第一行 | 只红 `lines-branch-joins-with-newline` |
  | ③ 实现侧：摘掉 `writeText` 的 `_within_workspace` 防线 | 只红两条逃逸负路径 |
  | ④ 实现侧：`data.log` 默认 `level` 改成 `debug` | 只红 `defaults-to-info-level` |
  | ⑤ 安全阀：用例 workspace 越出变体目录 | 只红该变体，且**没跑到命令层**（信息里是安全阀文案） |

  五向均 exit=1、失败集合与预期逐个相等、注入文件逐字节还原。
- **S2.1 真机 E2E**（`RPA_DESKTOP_E2E=1 uv run pytest tests/e2e/test_desktop_pause_resume.py`）
  → **2 passed / exit 0**：① 跨进程「暂停 → 继续」用例（`resume.wait() == 0`、
  `status == succeeded`、`return_value == "1"` 即零重跑、`readResult` 读回暂停前敲进去的
  那句话即同一窗口、续跑段 `stepCompleted` 集合**恰好等于**暂停点之后的节点集）；② 流程形状
  用例（钉命令序列 + 钉「暂停点之后的 `click` 必须引用暂停**之前**的 `sessionId`/`elementId`」
  ——这两条引用就是会话/元素还原的触发点，改动流程的人会在这里被拦下）。
- **S2.1 正向回归**：`tests/e2e/test_uia_desktop.py` **3 passed / exit 0**——fixture 加了计数器
  控件、装配抽到共享模块，既有的 UIA 全链路（`hello rpa` / `dialog:world`）不受影响。
- **S2.1 契约测试** `tests/unit/test_desktop_session_restore.py` **14 项**（进默认门禁）：
  解析器矩阵（attach/findElement/closeSession/坏数据/**两后端前缀隔离**/`last_sid` 归一）、
  **写侧驱动**的反漂移（驱动真实的 `attachWindow`/`findElement` 产出 effect，再交给解析器读回）、
  还原后旧 `elementId` 真能被后续命令解析（对照组：只还原会话 → `ELEMENT_NOT_FOUND`，
  正是实测里那个中间态）、窗口已失效不还原、恢复是叠加不是清空。
- **S2.1 负向验证 4 例**（都打在门禁/用例实际检查的方向上）：
  ① **只改写侧**前缀字面量（读侧常量不动）→ 契约测试 2 红（字面量断言 + 写→读往返 KeyError）；
  ② **只改读侧**常量（写侧不动）→ 同样的 2 红（两个方向都闭合）；
  ③ 摘掉 uia `findElement` 的 `details.locator` → 契约测试 1 红（`KeyError: 'locator'`）；
  ④ 临时停用 uia 执行器的 `restore_from_scopes` → 真机 E2E 红在「续跑段」，失败详情精确落在
  `nodeId: clickCount` / `commandId: desktop.click` / `effects: []`（**修复前的状态被复现**）。
  四例均逐字节还原，还原后 E2E 与契约测试复跑全绿。
- **FULL GATE PASSED**（S2 补靶子后：静态层 + 契约测试 + ruff + .mjs 切片检查全过；
  桌面 E2E 与指令矩阵仍按需，见 §1.6.7）。
- **S2 执行层（2026-09-22 补跑）**：`RPA_COMMAND_MATRIX=1 RPA_DESKTOP_E2E=1 pytest tests/commands`
  → 四驱动全绿（桌面当时的 172）——**这个 172 已过期：§1.13 订正为 190，§1.16 尾巴 #2
  落地后当前真值是 199（win32 111 + uia 88），见 §1.16**；
  六向负向验证见 §1.6.7。

## 6. 剩余（后续切片）

- **页签集成桌面与 L2**（2026-09-23 新增，**已完成**）：见 §1.15。页签现有三个明确目标
  （L1 / L1+桌面 / L1+L2，副作用面各不相同），前置的 conftest 收集口径已改成可叠加
  （四格实测 0 / 110 / 458 / 568），契约测试 18 → 22 项，双向负向验证各 1 例。
- **S2 桌面通道**（UIA 17 + Win32 19，共 36 条）：**已完成（2026-09-22）**——
  两表 + 两驱动 + 共享装配见 §1.5；`PENDING_NAMESPACES` 已清空（86 条命令全部有表）；
  靶子缺口已补、执行层已首跑并校正（§1.6）。**剩下的都是「产品侧的决策/实现」**，
  不是用例表的事：
  - ~~`desktop.select`（两后端）与 `desktop.getText`（uia）的实现缺口~~ ——**全部已实现**
    （uia/win32 的 select 与 getText/getSelectedText，2026-09-23 S2.3 + §1.13），
    e2e 两条 xfail 已转正；**只剩 `desktop.win32.input` 无稳定锚点**（可写 Edit 的
    窗口文本就是它的内容），见 `desktop_win32.json` 该命令的 notes；
  - ~~`desktop.screenshot` 的依赖决策~~ ——**已定案加 Pillow**（S2.3），两条正路径转正；
  - ~~`control_id` 过滤失效~~ ——**已修活**（S2.3，`_find` 手工补滤）；
    `getWindowList` 的「声明不需要会话、实现需要」**已对齐**（S2.3）。
    **剩余**：~~`className` 的匹配语义（§1.13 尾巴 #2，待拍板）~~ ——**已落地选 ③
    新增 `classNameRe`（§1.16）**；`getWindowList` 的「区分枚举失败与真没有窗口」
    + `operationTimeoutMs`（尾巴 #3）**记为「等复现」，不改代码**（§1.16）；
  - 靶子侧仍未覆盖的两条：`closeSession.forceKill=true` 会结束靶子进程（要独立的靶子实例）、
    `setWindowVisible=false` 需要「一个变体内部完成的显隐对」（属流程层）。
  - **S2 建表时的复用提示**（按「真桌面 fixture」这一定案重写）：桌面通道的可测面是
    「执行器真实下发了什么 + 真窗口上发生了什么」，而 `matrix.py` 的 `expect` 已经能覆盖
    「调用面 / 结果面 / 磁盘面」。**不要再加「打桩绑定层的调用记录」**——那正是被裁决掉
    的那条路：桌面侧的绑定层是 pywinauto/Win32，桩它等于把「真机证据」换成「桩被怎么
    调用」，而 S2.1 已经证明真 fixture 跑得动（一次 ~11s，可接受）。S2 落地时又验了一次
    这条：装配上唯一需要补的是**会话与元素的预置**（`desktop_harness.py` 的 `setup`），
    不是任何桩。
  - **波动风险（S2 未被它影响）**：既有记事本切片在 `tests/e2e` 整目录运行时抖（§1.4 末节，已按
    BACKLOG 先例登记，含三条未验证的候选修法）。S2 的用例表走 `tests/commands` 那套矩阵驱动
    （`RPA_COMMAND_MATRIX=1`），**与 `tests/e2e` 无关**，故不被它阻塞；但若日后要把桌面 E2E
    纳入常规回归，那片的稳定性是前提。
- **S4 L2 真机冒烟**：与 L1 **共用同一份用例表**，把执行后端从假扩展换成真扩展
  （策略 §2 L2，显式开关启用）。数据通道这一半已经算「真机」（真子进程 + 真文件），
  所以 S4 的增量只落在浏览器通道。**已完成（2026-09-23，§1.8–§1.12）**：
  浏览器 32 条命令全部有了 L2 处置——109 个 l2 块真机全绿
  （`RPA_BROWSER_L2=1 pytest tests/commands/test_browser_l2_matrix.py`，
  110 passed 含隔离用例）；过程中修复 3 个真机才可见的产品 bug
  （check 三操作反转、back/forward 的 goBack API 不可靠、timeoutMs 不进 args）
  与 1 处对称性缺口（已有会话路径吞 timedOut）。
- **两个死参数的处置**：**已完成（S1.1，2026-09-22）**——两项均从 manifest 删除，
  删掉的是「声明」而非「能力」，对标差距未扩大。详见 §3.1。
- **S1.2 页签的显式取舍（不是缺口）**：① 页面**不解析 pytest 输出**，所以「子进程在收集/导入
  阶段就失败」时没有逐用例行可展示，只能看日志面板——`_on_finished` 对 `summary is None` 给了
  专门文案（含「常见原因：pytest 未安装」）。②「停止」用 `kill()` 而非优雅终止：pytest 没有
  可用的优雅退出信号，且报告逐行 flush，已跑完的用例不会丢。③ 页签在 `HomeWindow` 里是**第三
  个页签**，`test_workbench_has_command_matrix_tab` 用 `titles == [...]` 精确钉住顺序——后续再
  加页签要显式改这条断言（有意：工作台的信息架构不该被悄悄改动）。
