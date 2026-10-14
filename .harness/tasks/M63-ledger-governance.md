# M63：台账治理（M62 审计决策落地）

Status: `wip`

> **2026-10-14 立项**。M62 只做了「修漂移 + 建门禁 + 建开机入口」，把结构性改造列为 §六 待拍板；
> 本任务执行维护者已拍板的四项 + 清一项已登记欠账。沿用 M62 先例**不占 active 位**（M52 未收口）；
> ④（新增 `wip` 状态）落地后，本任务即记 `wip`。
>
> 前置：M62（done）。本任务**不触产品代码**（`src/` / `commands/` / `tests/` 一行不改）。

## 一、决策来源（维护者拍板，2026-10-14）

| # | 决策 | 处置 |
|---|---|---|
| ① | `WORKLOG.md` 去留 | **归档并冻结** → `docs/archive/WORKLOG-2026H1.md` + 「已停止维护」banner |
| ② | 两个 `BACKLOG.md` 命名冲突 | **改名消歧**：`.harness/BACKLOG.md` → `.harness/commands-plan.md`（内容不动） |
| ③ | 台账字段长度闸门 | **全量落地**：`title ≤ 120` / `notes ≤ 2000` / `evidence ≤ 400` + 存量治理（见 §三 待定） |
| ④ | 「单一 `active`」约定 | **新增 `wip` 状态**：`active` 仍唯一=主线，旁支用 `wip` 表示在动 |
| ⑤ | 两个门禁的一层 `glob` → `rglob` | 清欠账（原 M62-S4） |

## 二、切片

| 片 | 内容 | 验收口径 | 状态 |
|---|---|---|---|
| **S1** | ⑤ `glob`→`rglob` **+ 第三条切片策略**（注册表分派）：`check_param_consumption` 按 handler 函数体逐命令切、`check_error_contract` 把切不出返回码的 6 条单列 `unsliced` 不计 `checked` | 两条门禁汇总行 `= 89`（`86+3` / `80+6+3`）；负向验证 **5/5**（含「跨命令掩蔽必须红」这个复发点） | ✅ |
| **S2** | ② 改名 `commands-plan.md` + 同步 5 处引用（`.harness/README.md` ×2 / `STATE.md` / `docs/yingdao-web-cmds-benchmark.md` / 根 `README.md`） | `check_docs` 绿；负向验证 **N1** | ✅ |
| **S3** | ① 归档 `WORKLOG.md` → `docs/archive/WORKLOG-2026H1.md` + 「已停止维护」banner（内容原样） | 根目录无 `WORKLOG.md`；无引用断裂 | ✅ |
| **S4** | ④ `check_tasks.py` 增 `wip` + README 合法值列同步 + M60/M62/M63 置 `wip` | 门禁绿；`active` 仍**恰好一个**；负向验证 **N2/N3** | ✅ |
| **S5** | ③ 新增 `.harness/scripts/check_ledger.py`（`title ≤ 120` / `notes ≤ 2000` / `PROGRESS` cutoff 之后 `evidence ≤ 400`）+ 存量治理（46 条超长 title、35443 字符 notes） | 门禁绿；负向验证 **4/4**（含「删掉 cutoff 标记必须红」的防空转自检） | ✅ |
| **S6** | 收口：四处台账同步 + `check_all.py` | 末行 `FULL GATE PASSED` | ✅ |

## 三、★ 动手前实测（两条与决策时假设不符）

1. **③ 的 `evidence ≤ 400` 上限与铁律冲突**：`PROGRESS.md` 是 **append-only 且历史条目一律不改写**，
   现有 **119 条 evidence >1000 字符**（max 6168）⇒ **不可能**在不违反「历史不改写」的前提下把存量压到 400。
   → 需维护者再定口子：**(a)** 上限**只对新条目**生效（设 cutoff 标记）；**(b)** 放弃 evidence 上限；
   **(c)** 上限落地、存量一次性豁免并冻结（豁免清单不增长）。
2. **③「34 条超长 `title` 搬迁到任务单」多数无落点**：实测 34 条中 **29 条没有对应任务单**
   （`desktop-coordinate-preview` / `gui-capture-startup` / `variable-scope-visibility` …）。
   → 改为：有任务单的迁任务单；无落点的把**原 `title` 全文存档**到 `.harness/feature-titles-archive.md`，
   新 `title` 取首句（≤120）。
3. **⑤ 的「纯增益、零代价」不成立**（本任务动手时实测推翻，已回退）：
   - `glob`→`rglob` 后两个门禁计数 83 → **89**（与 `check_command_matrix` 对齐）。但**错误门禁**
     对 `data.table.*`（`python_worker._TABLE_HANDLERS` **注册表分派**，非 `if command ==` 字面量）
     切不出任何 `ErrorCode.X` ⇒ `returned = ∅` ⇒ 这 6 条被记成 `checked` 而**覆盖为零**。
     **实测**：`check_error_contract._analyse_module(python_worker.py)` 只切出 **13 条**
     `data.*` / `python.evalExpression`，**不含任何 `data.table.*`** —— 正是该文件 docstring
     第 42 行预警的「**checked 计数会 +1 而实际零覆盖**」。
   - **参数门禁**对这 6 条只有**模块级**覆盖：`grep` 能抓「声明了但全模块从未读取」的参数
     （实测：往 `appendRow.json` 注入 `__m63ProbeParam` ⇒ rc=1、精确点名），但**无逐命令归属**。
     **实测**：把只有 `getCell` 的 handler 才读的 `varName` 加进 `appendRow` 的 manifest ⇒
     门禁 **PASSED（假绿）**。
   - ⇒ 已 `git checkout` **回退** `glob→rglob`（基线回到 83）。真修法 = `check_param_consumption.py`
     docstring 第 39 行早已写明的「**第三条切片策略：按 handler 函数切片**」；且错误门禁还需把
     不可切片命令单列「已见不可切片」桶、**不计入 `checked`**。
     **教训**：登记欠账时写的「试算 89/89 全绿、零代价」是**推理**不是**实测**——若照抄登记就动手，
     等于亲手往门禁里塞 6 条假计数。

## 四、不在本任务范围内

- **不改写** `PROGRESS.md` / `memory/*.md` 的任何既有条目（`PROGRESS.md` append-only）。
- 不动产品代码（`src/` / `commands/` / `tests/`）。
- 不动工作区里既有的 M60 未提交改动与两个被删除的 `workflows/test/` 文件。

## 五、S2 / S3 / S4 落地记录（2026-10-14）

- **②** `git mv .harness/BACKLOG.md .harness/commands-plan.md`（git 记为 `R`，非删+增）；同步引用 5 处：
  `.harness/README.md` ×2、`.harness/STATE.md`、`docs/yingdao-web-cmds-benchmark.md`、根 `README.md`。
- **①** `git mv WORKLOG.md docs/archive/WORKLOG-2026H1.md` + 「已停止维护」banner；**正文原样保留**、
  仅停止追加。`.harness/README.md` 从未引用它（实测），无引用断裂。
- **④** `check_tasks.py` `VALID_STATUSES` 增 `wip`；`.harness/README.md` §二 `tasks/` 行的合法值列同步；
  `M60` / `M62` / `M63` 置 `wip`。现状：**1 `active`（M52）/ 3 `wip` / 2 `planned`（M51、M61）/ 17 `done`**。
- **门禁**：`check_tasks`（84 features, 1 active）/ `check_docs`（5 docs, 20 refs）/ `ruff` 逐条 **rc=0**。
- **负向验证**（`.harness/spike/probe_m63_ledger_negative.py`，**3 注入 3/3 HIT**、逐字节还原核 md5、复绿）：

| 注入 | 期望 | 结果 |
|---|---|---|
| N1 `STATE.md` 指回旧名 `.harness/BACKLOG.md` | `check_docs` 红（`dead reference`） | **HIT**（rc=1） |
| N2 `M61` 的 `Status:` 改成非法值 | `check_tasks` 红（`invalid task status`） | **HIT**（rc=1） |
| N3 `M61` 的 `Status:` 改成 `active`（第二个 `active`） | `check_tasks` 红（`active task mismatch`） | **HIT**（rc=1） |

⇒ ④ 的**放宽**（新增 `wip`）**没有连带破坏**两条既有判据：「非法值仍被拒」与「`active` 恰好一个」。
- **未跑全量 `check_all.py`**：等 S1 / S5 定案后随 S6 一并收口（**不预写 PASSED**）。

## 六、⑤ / ③ 决策落定（2026-10-14，维护者拍板）

**⑤ → 实现第三条切片策略（按 handler 函数切片）**：

- `check_param_consumption.py`：解析 `_TABLE_HANDLERS = {"<id>": <func>, …}`，对每个 handler **函数体**
  切 `inputs[...]` / `invocation.inputs[...]` 读取 ⇒ 得到**逐命令** `allowed`；与现有 `if command ==`
  分支体**取并集**（两种都是合法实现形态，缺一不可）。
- `check_error_contract.py`：handler 体里**没有 `ErrorCode.X`**（错误码由外层映射）⇒ 切不出返回码。
  故**不可切片的命令单列一桶**（`unsliced`），**不计入 `checked`**，在汇总行如实报出。
- 之后再把 `directory.glob("*.json")` 改回 `rglob`，使 `data.table.*` **真正**纳入（83 → 89 且非假计数）。
- 验收（负向验证）：① 未读参数注入 ⇒ 红；② **跨命令**参数（只有 `getCell` 的 handler 读的 `varName`
  加进 `appendRow`）⇒ **必须红** —— 这正是本轮推翻 `rglob` 时暴露的那个假绿的复发点；③ 逐字节还原 + 复绿。

**③ → `evidence ≤ 400` 上限「只对新条目生效」**：在 `PROGRESS.md` 设 **cutoff 标记行**，门禁只校验该
标记**之后**追加的条目；历史 **119 条 >1000 字符**保持原样（铁律：历史不可改写）。`title ≤ 120` /
`notes ≤ 2000` 仍**全量**生效；34 条超长 `title` 按 §三-2 处置（有任务单的迁任务单，无落点的存
`.harness/feature-titles-archive.md`）。

## 七、S1 / S5 落地记录（2026-10-14）

### S1 第三条切片策略（注册表分派）

- `check_param_consumption.py`：新增 `_registry_members()`（自动发现模块级 `_X_HANDLERS = {"<id>": <handler>}`
  与引用它的 dispatcher 函数，**不硬编码注册表名**）与 `_registry_reads()`；`_analyse_module` 返回
  5 元组（多出「注册表读取」与「致命错误」）。handler **函数体**的读取逐命令归属、dispatcher 体
  的读取算公共，**且这些读取不再计入「通用读取」**——不排除就是模块级覆盖（跨命令掩蔽）。
- `check_error_contract.py`：注册表命令按 handler 体切 `ErrorCode.X`；**切不出就单列 `unsliced`、
  不计 `checked`**（汇总行 `80 checked / 6 unsliced / 3 exempt = 89`）。
- 扫描口径 `directory.glob("*.json")` → `rglob("*.json")`（两条门禁）。
- **数字**：`86 checked / 3 exempt = 89`（参数）、`80 checked / 6 unsliced / 3 exempt = 89`（错误）
  —— 与 `check_command_matrix` 的 89 对齐，且**非假计数**。

**★ 两次「我自己写错、被当场抓出」**（都记在这里，因为它们是这条判据的**设计依据**）：

1. **error 门禁第一版把 dispatcher 的 `ErrorCode.*` 也归给了命令** ⇒ 门禁立刻红：5 条被要求声明
   实际不可达的 `COMMAND_NOT_FOUND`。这正是「框架级错误码没有按命令归属的语义」的实测证据 ⇒
   改成只切 handler 体 + `unsliced` 桶（负向验证 **N4** 把这一向固化下来）。
2. **注册表变量改名后 dispatcher 认不出来，它的读取静默退回「通用读取」** ⇒ 门禁照样绿（降级为
   模块级覆盖）。第一版的负向验证 **N3** 就是这么 MISS 的 ⇒ 改成**硬失败**（元组第三返回值
   `registry_errors`）。理由写在 `_registry_reads` 的 docstring 里：当前 dispatcher 的读取恰好都是
   共享参数，所以降级「看起来后果一样」——那是**巧合**，不是判据。

**负向验证**：`.harness/spike/probe_m63_registry_negative.py`，**5 注入 5/5 HIT**、逐字节还原核 md5、复绿。

| 注入 | 期望 | 结果 |
|---|---|---|
| N1 往 `appendRow.json` 注入无人读的参数 | 参数门禁红并点名 | **HIT** |
| N2 ★ **跨命令**：只有 `getCell` 读的 `varName` 加进 `appendRow` | 必须红（**M63 S1 之前是 PASSED 假绿**） | **HIT** |
| N3 注册表变量改名 | 硬失败（不许静默降级） | **HIT** |
| N4 ★ 把 dispatcher 码并回命令（改门禁源码） | 必须红（5 条假红） | **HIT** |
| N5 往 `_table_clear` 的 handler 体塞 `ErrorCode.X` | 自动由 `unsliced` 转受检并红 | **HIT** |

### S5 台账长度闸门 + 存量治理

新增 `.harness/scripts/check_ledger.py`（已登记进 `check_all.py` 与 `.harness/README.md`），三条判据：
`title ≤ 120`（全量）/ `notes ≤ 2000`（全量）/ `PROGRESS.md` **cutoff 标记之后**的条目 `evidence ≤ 400`
且必须**单行**；cutoff 标记必须**恰好出现 1 次**（防空转自检）。

| 对象 | 治理前 | 治理后 | 原文去向 |
|---|---|---|---|
| `feature_list.json` 的 `title` | 46 条 >120，最长 **9394** 字符 | 最长 **117** 字符 | `.harness/feature-titles-archive.md`（68031 字符） |
| `feature_list.json` 文件体积 | 75461 字节 | **13225** 字节 | — |
| `project_state.notes` | **35443** 字符（占 JSON 54%） | **582** 字符 | `.harness/notes-archive.md`（65177 字节） |
| `project_state.json` 体积 | 65726 字节 | **2225** 字节 | — |

- **偏离 §三-2 的处置**：实测 46 条超长 `title` 里绝大多数**没有仓库内落点**（任务单只覆盖 5 条），
  且「有落点」那 5 条的内容本就已在任务单里（判据是「任务单文本含该 feature id」）⇒ 逐条迁入
  等于把内容**再复制一遍**。故统一存档到 `feature-titles-archive.md`，task 单不动（符合「一事件一处」）。
- 迁移脚本自带两道断言：① 先证明「重序列化能**逐字节还原**原文件」，② 改完断言**除目标字段外
  零处差异**（逐 feature / 逐顶层键比对）。两个脚本都留在 `spike/_m63_*_migrate.py`（`_` scratch）。
- **负向验证**：`.harness/spike/probe_m63_ledger_gates_negative.py`，**4 注入 4/4 HIT**、逐字节还原、复绿：
  `title` 超限 / `notes` 超限 / cutoff 之后 `evidence` 超限 / **删掉 cutoff 标记（防空转）** 各必须红。

### S6 收口（2026-10-14）

- 台账同步：`feature_list.json` 的 `harness-docs-hygiene` 扩写 title（覆盖 M63）并置 `passes`；
  `PROGRESS.md` 追加 cutoff 标记 + M63 一行；`tasks/BACKLOG.md` 的 M62 记 `done`、新增 M63 条目；
  M62 任务单的 S4 行与「偏离与未做」同步；当日 memory 追加落地记录。
- **门禁真值**：`check_all.py` 末行 **`FULL GATE PASSED` / rc=0**（前台 + 沙箱豁免）；
  pytest **1664 passed / 0 failed / 21 skipped / 2 xfailed**；静态门禁逐条 rc=0 ——
  architecture（81 py / 89 manifests）、tasks（84 features / 1 active）、docs（5 docs / 24 refs）、
  ledger、param（`86 checked / 3 exempt = 89`）、error（`80 checked / 6 unsliced / 3 exempt = 89`）、
  command_matrix（89 条）、ruff（`All checks passed!`）。
- **`STATE.md` 内的体积数字同步更新**：原文写「`PROGRESS.md` 564 KB / `feature_list.json` 75 KB /
  合计约 175 KB / notes 35 KB 别读」——经本轮治理后除 `PROGRESS.md` 外全部作废，已改。
- **/!\ 并发写**：收口期间发现**另一个会话**在动（`git log` 的 HEAD 由 `72efdb9` 变为 `d173024`；
  `spike/_fix_glob_claim.py` / `_trim_memory*.py` 于 14:30 新建；`M tasks/M60-ocr-captcha.md`）
  ⇒ 提交时只 `git add` 本任务的路径，**不碰** `tasks/M60-ocr-captcha.md`、`docs/yingdao-cmds/*`、
  `workflows/*` 等他人改动。
  （注：`tasks/M60-ocr-captcha.md` 的 `planned → wip` 是**本任务 S4** 改的，随本笔带入；
  该文件的其余 hunk 若来自他人则不入。）

### ★ 收口时又撞出一个真实的既存缺陷：`set_feature.py` 把整个台账写成 CRLF

`set_feature.py` 是 `.harness/README.md` 指定的「改 `passes` 的**官方入口**」，但它用
`Path.write_text` 且没给 `newline="\n"` ⇒ Windows 上把 `\n` 全翻成 `\r\n`：
**一次调用就让 `feature_list.json` 漂出 426 个 CRLF**（`git add` 时的 warning 暴露），
一行 `passes` 的改动被埋进几千行噪声里。

- 已修 `.harness/scripts/set_feature.py`（加 `newline="\n"` + 注释说明为什么不能省），
  并用它重写一次验证：`CRLF = 0`。
- 给 `check_ledger.py` 加**第 4 条判据**（三个台账文件 `CRLF` 必须为 0）+ 负向验证 **N5**。
- **同类排查**：`src/` 下另有 6 处 `write_text` 未给 `newline`（`cli.py` / `control_channel.py` /
  `extension_installer.py` ×2 / `gui/debug_log.py` / `runtime/checkpoint.py` / `workers/python_worker.py`），
  写的是**运行期产物**（结果 JSON / checkpoint / 扩展 manifest），不是仓库台账、也不受「全仓 LF」
  约束 ⇒ **不在本任务范围**（本任务不触产品代码），仅记录在案。
  `scripts/scrape_yingdao_docs.py` 早已有包装函数处理此坑（可作正面先例）。
