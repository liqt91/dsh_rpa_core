# M62：harness / 文档层梳理（AI 协作视角）

Status: `done`

> **2026-10-14 立项**。本任务为**插入推进**：按现行 `check_tasks.py` 约定「同时只能有一个
> 任务文档是 `active`」，而 M52（selector-advisor）未收口 ⇒ 本任务以 `planned` 记录、**不占
> active 位**（沿 M60 先例），收口后转 `done`。
>
> ★ **这条「只能有一个 active」的约束本身就是本次审计认定的成本来源之一**（多线并行时台账
> 无法如实表达"当前在做什么"），已登记 §六 待拍板，不在本任务内擅自放宽。
>
> 前置：无。本任务**不触产品代码**（`src/` / `commands/` / `tests/` 一行不改），只动
> `.harness/` 文档与门禁脚本。

## 一、起因（维护者原话）

> 「从与 ai 协作的角度看，这个项目的 harness 层和文档层需要做哪些梳理和优化」
> → 「写任务单，然后开动」

## 二、取证（只读审计，全部为实测；本轮未改动任何文件）

### 2.1 三个瓶颈

| # | 瓶颈 | 实测 |
|---|---|---|
| 1 | **开机成本 ≈ 175 KB 文本，有效信息 < 2 KB** | 按 `.harness/README.md`「会话开始」的指示读一次 = README 5 KB + `feature_list.json` 75 KB + `PROGRESS.md` 尾 10 条 30 KB + `project_state.json` 65 KB |
| 2 | **一次事件写五处，且无仲裁规则** | `PROGRESS.md` 564 KB / `memory/*.md` 528 KB / `WORKLOG.md` 84 KB / `tasks/BACKLOG.md` 116 KB / `project_state.notes` 35 KB，记的是同一批事件的详略不同的副本；三处冲突时无任何文档规定听谁的 |
| 3 | **文字层零门禁，已在漂移** | `check_architecture.py` 完全不看 md；`check_tasks.py` 只校验 `Status:` 行 + active 一致 ⇒ 没有任何脚本校验文档里的计数 / 引用 / 长度 |

### 2.2 体量分布（`du -k` / `wc -c` 实测）

| 文件 | 体量 | 结构问题 |
|---|---|---|
| `.harness/PROGRESS.md` | **564 KB** / 365 行 / 309 条 | evidence 字段中位 **742** 字符、p90 2336、max **6168**；>1000 字符的 **119 条**；evidence 占全文件 56% |
| `.harness/feature_list.json` | **75 KB** / 83 条 | `title` 中位 144 字符、**max 9394**（`gui-ux-parity`）、**34 条 >200** —— `title` 被当成报告字段用 |
| `.harness/project_state.json` | **65 KB** | `notes` **单字段 35443 字符 = 整个 JSON 的 54%**，内容是 M30→M47 的历史叙述 |
| `.workbuddy/memory/*.md` | **528 KB** | 单日最大 98 KB（2026-10-13）；`MEMORY.md` **9965 字节**，贴 10000 字节静默截断线（丢尾部） |
| `WORKLOG.md`（仓库根） | 84 KB | 与 `PROGRESS.md` / memory 同质的第三份副本 |
| `.harness/tasks/BACKLOG.md` | 116 KB / 1194 行 | 单条里程碑的描述本身就是数千字小作文 |
| `.harness/spike/` | 299 文件 | 90 个 `probe_*.py` 证据件 + 214 个 `_*` scratch，平铺一个目录 |

单条任务单同样失控：`M38-command-matrix.md` 95 KB、`M52` 54 KB、`M41` 40 KB、`M39` 35 KB。

### 2.3 漂移实测（★ **上一轮 `866731e`「README 索引 + 修悬空 ADR 引用」之后仍存在**）

| 文档说 | 实际 |
|---|---|
| `.harness/README.md:16` feature「现 **82** 项」 | **83** |
| `.harness/README.md:43` `check_*.mjs`「**15** 个」 | **16** |
| `.harness/README.md:16`「用 `scripts/set_feature.py`」 | 真实路径 **`.harness/scripts/set_feature.py`** |

⇒ **没有门禁的文字必然漂移**；上一轮整理加的是"索引"，加完后同一份文件立刻长出三处错。

### 2.4 内部引用完整性扫描

扫 `.harness/**/*.md` + `docs/*.md` + 根 `*.md`，共 672 个仓库相对路径引用 ⇒ **12 处指向不存在的文件**。
但**逐条查上下文后，只有 4 处是现行文档的真错误**（见 §三）——这条"先定性再动手"是本任务
最重要的前置，避免了在历史记录里"修死引用"（那正是本仓严禁的改写历史）。

## 三、死引用定性表（12 处）

| 引用 | 出现位置 | 定性 | 处置 |
|---|---|---|---|
| `scripts/set_feature.py` | `.harness/README.md:16` | **现役文档·路径错**（AI 会照着执行） | **S1 修** → `.harness/scripts/set_feature.py` |
| `docs/capture-browserskill.md` | ADR 0010 / M14 / PROGRESS | 历史（bsk 路线已废、文档已删） | 豁免 |
| `docs/command-inventory.md` | PROGRESS ×2 | 历史（当时产物，今为 `command-inventory.html`） | 豁免 |
| `docs/ocr-commands.md` | `tasks/BACKLOG.md` / M60 | 计划（"待写"的用户面文档，M60-S4 未启动） | 豁免 |
| `docs/run-history.md` | `M25-run-history.md` | 历史任务单里的当时二选一 | 豁免 |
| `scripts/check_manifest_param_meta.mjs` | `docs/manifest-param-config.md:240` | 建议（"新增一个…（或并入 pytest）"，是提议不是引用） | 豁免 |
| `tests/contract/test_extension_exec_channel.py` | PROGRESS（+ execution-plan） | 历史（Playwright 时代产物，已随 ADR 0013 删除） | 豁免 |
| `tests/e2e/test_editor.py` | M9 / M11 / M12 | 历史任务单（Playwright E2E 时代，已删） | 豁免 |
| `docs/macos-background-input-v1-plan.md` | `docs/cua-fit-assessment.md:62` | **误报**：引用的是**被评估的外部项目**的文档，不在本仓 | 豁免 |
| `docs/yingdao-web-commands-benchmark.md` | `tasks/BACKLOG.md:1067` | 历史（已完成里程碑 M23 条目；真名 `yingdao-web-cmds-benchmark.md`） | 豁免 |
| `src/rpa_core/executors/browser_bsk.py` | `.harness/BACKLOG.md:39` | 历史（已完成项 P1-S2 的计划描述；bsk 路线已随 ADR 0013 废） | 豁免 |
| `scripts/check_channel_preview.mjs` | `docs/extension-execution-plan.md:128` | 历史（该文档**自声明**"保留为历史提案，现行以 ADR 0013 为准"） | 豁免 |

> ★ **定性订正（动手后）**：立项时初判「S1 修 4 处」（README 路径、`yingdao-web-*`、
> `browser_bsk.py`、`check_channel_preview.mjs`），**逐条读上下文后收缩为 1 处**：后三处分别落在
> **已完成里程碑的历史条目**、**已完成项的历史计划描述**、**自声明为历史提案的文档正文**里——
> 按「历史条目永不改写」一律豁免。**边界（历史 vs 现役）是在动手时才显形的**，这也是把
> `check_docs.py` 的扫描面限定在**现役导航文档**的直接依据；若按初判去"修"，就等于篡改历史。

**结论**：`check_docs.py` 的检查面必须**小而准**——只覆盖「AI 会照着执行的现行文档」，
不扫历史台账（否则必然误报，而假判据比没判据更坏）。

## 四、切片

| 片 | 内容 | 验收口径 | 状态 |
|---|---|---|---|
| **S1** | 修 §三 的现行文档真错误（核实后**只剩 1 处**）；`.harness/README.md` 的计数**去硬编码** | README 三处漂移改对（`82` → 不写死、`15` → 不写死、`set_feature.py` → `.harness/scripts/`）；`grep` 无残留写死计数 | ✅ |
| **S2** | 新增 `.harness/scripts/check_docs.py`：①**现役导航文档**的仓库内相对路径引用必须存在；②`STATE.md` 的 `active_plan`/`active_feature` 与 `project_state.json` 逐值一致；③`MIN_REFS` 防空转自检。登记进 `check_all.py` 与 README 清单 | 门禁 rc=0（5 docs / 20 refs）；**负向验证 5 注入全中** | ✅ |
| **S3** | 新增 `.harness/STATE.md`（**当前状态单一入口**，49 行）；`.harness/README.md` 与 `AGENTS.md` 的「会话开始」改指向它 | STATE.md 49 行；AGENTS.md 开机指示不再要求全文读 `PROGRESS.md` + `feature_list.json` | ✅ |
| **S4** | 清已登记欠账：`check_param_consumption.py` / `check_error_contract.py` 的 **一层 `glob` → `rglob`**（漏 `commands/data/table/*.json` 6 条） | **由 M63-S1 完成**，且实测**推翻了「零代价」的前提**（直接加宽会给错误门禁塞 6 条零覆盖假计数）⇒ 连**第三条切片策略**一起落地；现行两条门禁均 `= 89` | ✅ |
| **S5** | 收口：四处台账同步（`feature_list` 新增 feature / `PROGRESS.md` 一行 / `tasks/BACKLOG.md` M62 条目 / 当日 memory）+ 跑 `check_all.py` | 末行 `FULL GATE PASSED` | ✅ |

## 五、不在本任务范围内

- **不删不改** `WORKLOG.md`、`PROGRESS.md`、`memory/*.md` 的既有内容（`PROGRESS.md` 是 append-only 且被多处任务文档按「Mxx 条目」引用，历史条目的**格式与内容一律不动**，含那 6 处条目内续行）。
- 不动 `src/` / `commands/` / `tests/`（**零产品代码改动**）。
- 不动工作区里既有的 M60 未提交改动，也不动那两个被删除的 `workflows/test/` 文件（手测残留，见 §七）。
- 不新增 ADR（本任务不改架构决策；`invariants.md` 若需补条目另议）。

## 六、待拍板（本任务不擅自决定）

1. **`WORKLOG.md` 去留**：删 / 归档 / 降级为纯索引？（与 `PROGRESS.md`、memory 三重复）
2. **两个 `BACKLOG.md` 是否合并**：`.harness/BACKLOG.md`（指令优化规划）vs `.harness/tasks/BACKLOG.md`（里程碑总表）—— README 已警告"别混"，但同名文件本身就是认知负担。
3. **台账字段长度闸门**：`title ≤ 120` / `notes ≤ 2000` / `evidence ≤ 400` 字符上限是否落地？落地意味着现有 **34 条超长 `title`**（最长 9394）要一次性搬迁到任务单 —— 这是**大范围台账改写**，需明确授权。
4. **`notes` 拆分**：`project_state.notes`（35443 字符）是否拆到 `docs/` 或当日 memory？它现在是"排期事实源"里的事实上的历史书。
5. **「只能有一个 `active`」是否放宽**：多线并行（M52 未收口 + M60/M62 插入推进）时，现行门禁逼着新任务只能记 `planned`，台账与实际脱节 —— 这也是 M60 至今 `Status: planned` 却已落地 S1–S3 的原因。

## 七、基线状态（动手前记录，避免误归因）

本轮开工时工作区**是脏的**（非本任务产出，一律不碰）：

- 已修改未提交：`.harness/PROGRESS.md`、`.harness/feature_list.json`、`.harness/tasks/BACKLOG.md`、
  `.harness/tasks/M60-ocr-captcha.md`、`commands/ocr/recognize.json`、`docs/yingdao-cmds/填写输入框_web.md`、
  `src/rpa_core/devserver/static/i18n.js`、`src/rpa_core/workers/ocr_worker.py`、
  `tests/commands/cases/ocr.json`、`tests/contract/test_ocr_contract.py` —— 均为 M60 S2+S3 的落地产物。
- 已删除未提交：`workflows/test/elements/el_textarea.json`、`workflows/test/workflow.json`（手测残留）。
- 未跟踪：`.harness/spike/probe_m60_*`、`probe_ocr_*`（M60 探针）。

⇒ 本任务提交时**只 `git add` 本任务产出的路径**，不使用 `git add -A`。

## 八、收尾记录（2026-10-14）

**门禁**（豁免沙箱前台跑，回显 `Sandbox bypassed (escalation-approved)`）：

- `check_all.py` 末行 **`FULL GATE PASSED`、rc=0**；
- pytest **1664 passed / 0 failed / 21 skipped / 2 xfailed**（274.57s）—— 本轮**无环境级假红**；
- 逐条取证（各 rc=0）：`ruff check .`（All checks passed!）/ `check_architecture`（81 py /
  89 manifests）/ `check_tasks`（84 features，1 active）/ `check_docs`（**本轮新增**，
  5 docs / 20 refs）/ `check_param_consumption`（80 checked / 3 exempt / 0 skipped）/
  `check_error_contract`（80/3/0）/ `check_command_matrix`（89 条命令）/ 全部 `check_*.mjs`。

**负向验证**（`.harness/spike/probe_m62_docs_gate_negative.py`，**5 注入 5/5 HIT**）：

| 注入 | 结果 |
|---|---|
| N1 README 的 `set_feature` 路径写错 | HIT（rc=1，点名该路径） |
| N2 AGENTS.md 引用不存在的文件 | HIT |
| N3 `_PREFIXES` 丢掉 `.harness/` ⇒ 引用数跌破 `MIN_REFS` | HIT（`refusing to report green`） |
| N4 STATE.md 的 `active_feature` 与 `project_state` 不一致 | HIT |
| N5 删掉 STATE.md 的 `active_plan` 行 | HIT |

逐字节还原核 md5、复绿（`DOC CHECK PASSED (5 docs, 20 refs)`）。

**★ 本轮最有价值的记录 —— 我自己写的判据出过一次假绿灯**：

`check_state_consistency` 首版用 `value in text` 判断 STATE.md 是否过期。N4 注入
（把 `active_feature` 值改成 `selector-adviser`）**MISS**：因为 `active_plan` 的值
`.harness/tasks/M52-selector-advisor.md` **恰好包含** `active_feature` 的名字，
两层互相兜底 ⇒ 改一行仍命中另一行、判据恒绿。改成**锚定表格单元格**的精确比较后才命中。
**教训**：跨字段一致性判据不能用子串包含 —— 这是本仓「互为兜底的两层必须一次删整对」的又一形态。

**另记一处「假红」**：`check_docs.py` 首版正则的扩展名 alternation 把 `js` 排在 `json` 前面，
`feature_list.json` 被截成 `feature_list.js` ⇒ 门禁误报。修法：加尾部断言 `(?![A-Za-z0-9_])`
并把长扩展名排前。**假红与假绿都只能靠「逐条看红在哪」来区分。**

**偏离与未做**：

- **S4 已由 M63-S1 清掉**（2026-10-14）：扫描口径加宽到 `rglob`，并**同时**落地第三条切片策略
  （注册表分派）。★ 实测**推翻了本任务单立项时写下的「纯增益、零代价」**：直接加宽会让错误门禁
  把那 6 条记成 `checked` 而**覆盖为零** ⇒ 详见 `M63-ledger-governance.md` §七。
  现行读数：`86 checked / 3 exempt = 89`（参数）、`80 checked / 6 unsliced / 3 exempt = 89`（错误）。
- **§六 五项待拍板**：① ② ③ ④ ⑤ 已由 **M63** 全部落地；⑥（提交策略）未决。
- 立项时初判「S1 修 4 处死引用」→ 核实后收缩为 **1 处**（见 §三 订正）。
- 工作区既有未提交改动（M60 S2+S3）与两个被删的 `workflows/test/` 文件 **未触碰**。
