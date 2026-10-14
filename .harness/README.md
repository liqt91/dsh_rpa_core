# .harness — 项目工作台

本目录是 rpa_core 的「**留痕 + 门禁 + 一手证据**」中枢：事实源在这里，门禁脚本在这里，
排查与对标的一手证据也在这里。

- **会话开始**：先读 **`STATE.md`**（当前状态单一入口，~1 KB）；只在需要细节时才去取 `PROGRESS.md`（append-only 时间线，**最大件**）或 `feature_list.json`（M63-S5 起已压到十几 KB，每条 `title` ≤ 120 字符）。
- **台账字段有长度闸门**（`check_ledger.py`）：`title ≤ 120` / `notes ≤ 2000` / `PROGRESS` cutoff 之后 `evidence ≤ 400`。它们都是「会话开始扫一眼」的字段，被当成报告字段用就会把开机预算烧在过去——超限即红。
- **每次实质改动后**：照文末「改动后同步」收口。

---

## 一、顶层台账（事实源）

| 文件 | 职责 | 写法约定 |
|---|---|---|
| `PROGRESS.md` | 时间线台账 | **append-only**：一行 `YYYY-MM-DD \| feature \| status \| evidence`。历史条目**永不改写**——多处任务文档按「`PROGRESS.md` 的 Mxx 条目」引用它。**`evidence` 只留一行索引**：`check_ledger.py` 的 cutoff 标记（文件内 `M63-S5 evidence cutoff`）**之下**追加的条目，`evidence` 超 400 字符即红；详情落当日 memory |
| `feature_list.json` | 特性台账（`id` / `title` / `passes`） | 改 `passes` 用 **`.harness/scripts/set_feature.py <id> true\|false`**；`title` 只写**一句话**（≤ 120 字符，门禁在 `check_ledger.py`）。**条数不在此写死**——要数就现算 |
| `project_state.json` | 排期 / 阶段事实源（`phase`、`active_milestone`、`active_plan`、`active_feature`、`next_milestone`、`task_index`、`scope`、`decision_gate`） | 动 `active_*` 排期是**决策**：先拍板再改。`notes` ≤ 2000 字符且**只记跨里程碑仍成立的决策**——不记进度叙述 |
| `feature-titles-archive.md` / `notes-archive.md` | M63-S5 缩短 `title` / `notes` 时的**原文存档** | 只回答「原文去哪了」，**仅存档、不再维护**；不参与门禁、不要求与现状同步 |
| `invariants.md` | **不变量**（工程铁律，编号列表） | 新增不变量要**同时在门禁脚本里落地**，否则只是文档 |
| `architecture.md` | 依赖方向与分层 | — |
| `commands-plan.md` | **指令优化规划**（对标影刀 72 条指令，四 Phase） | **原名 `BACKLOG.md`**（M63-② 改名消歧）；⚠️ 与 `tasks/BACKLOG.md` **不是一回事**，见下 |
| `yingdao-gap-matrix.md` | 影刀能力差距矩阵 | 对标复核用（改动时**双向**查漏） |

> ⚠️ **别再把这两个搞混**：`commands-plan.md`（本目录，**原名 `BACKLOG.md`**）= 指令参数/优化的分片规划；
> `tasks/BACKLOG.md` = **里程碑总表**（Mxx 清单 + 已完成段）。

## 二、子目录

| 目录 | 内容 | 保留 / 新增约定 |
|---|---|---|
| `adr/` | 架构决策记录，`NNNN-slug.md`（编号递增，**0009 缺号**不补；`0013` = 移除 playwright / 扩展单通道） | 决策变了**新增**一篇，不原地改写；被引用的编号**永不重编号** |
| `tasks/` | 里程碑细则 `Mxx-slug.md` + `BACKLOG.md`（总表） | 新任务文档**照抄 M56/M57 头部**；`Status:` **必须顶格独立一行**，值仅 `planned` / `active` / `wip` / `blocked` / `done`（`wip` = **在动但非主线**，`active` 仍唯一，M63-④）；**编号不重编号** |
| `spike/` | 只读探针 + 负向验证探针（按 M 编号命名） | **两类，务必分清**：<br>① **不带下划线**的 `probe_*.py` / `add_l2_*.py` = **证据件**——源码注释、用例、门禁按名引用它们，**不许删**；<br>② **下划线前缀** `_*` = 本机 scratch（探针产物 / 取证日志 / 诊断脚本），`.gitignore` 已整体收口（`/.harness/spike/_*`） |
| `demo/` | GUI 可行性 demo（PySide6 / fluent / QML 视觉对比） | `.py` / `.qml` 多被引用，属证据件；`*.png` 截图已忽略 |
| `scripts/` | 门禁脚本（见下） | 新增门禁要登记进 `check_all.py` |

## 三、门禁

总入口 **`.harness/scripts/check_all.py`**，依次跑：

1. `pytest`
2. `ruff check .`
3. `check_architecture.py` / `check_tasks.py` / `check_docs.py` / `check_ledger.py` / `check_param_consumption.py` / `check_error_contract.py` / `check_command_matrix.py`
4. 前端纯函数切片 `scripts/check_*.mjs`（在**仓库根 `scripts/`**，不是本目录；无 node 时跳过）
   —— **名目以 `check_all.py` 的 `PURE_FUNCTION_SCRIPTS` 为准**，个数不在此写死

`set_feature.py`：改 `feature_list.json` 的 `passes`。

**铁律**（踩过的坑，别重犯）：

- 判门禁只看**末行 `FULL GATE PASSED` + 退出码**；**报绿前必须做负向验证**（注入 → 门禁必须真红 → 逐字节 `md5` 还原）。
- `check_all.py` **首条 `pytest` 收尾非零会遮住其后全部** ⇒ 定位失败要**逐条单独跑**（`check_command_matrix` / `ruff` 最易被遮）。
- 门禁输出**重定向到文件再读**（接 `| head` 会 SIGPIPE 打断，且可能让探针的还原逻辑半途而废）。
- `check_param_consumption` / `check_error_contract` 靠 **`EXECUTOR_FILES` 前缀映射**切片源码：
  **新执行器必须登记**，否则该命令被判 `skipped` —— rc=0 且汇总行照报 PASS，**覆盖实为零（假绿）**。
- ★ **注册表分派现在也能查了**（M63 S1 加「第三条切片策略」）：`_X_HANDLERS = {"<id>": <handler>}`
  会被自动发现，按 handler **函数体**逐命令切参数、dispatcher 体的读取算公共。但**显式
  `if command_id == "<id>"` 链仍是首选形状**——零解释成本，而注册表分派要求门禁自带第三条策略，
  且**错误契约切不出来**（那 6 条 `data.table.*` 只能记 `unsliced`、不计 `checked`）。
  ★ 改这两条门禁的切片逻辑后，必须复跑 `.harness/spike/probe_m63_registry_negative.py`：
  它盯着「**跨命令掩蔽**」这个复发点（只有 A 命令读的参数加进 B 命令的 manifest ⇒ 必须红）。
- ★ **「命令压根没接线」在错误契约轴上也有判据了**（M63-S7 加**接线哨兵**）：每条非 `skipped`、
  非豁免命令必须在实现里**可定位到一个分派点**——`if command == "<id>"` 的字面量分支，或
  **模块级命令表**（`{"<id>": "<JS 方法名>"}` / `("<id>", …)`，且被命令载体以 `in` / 下标 /
  `.get()` 消费）里的 id。判不出即 `unwired` 报错。
  ★ **别把它退化成「`returned` 非空」**（这是登记时的候选，实测当场证否）：80 条 `checked` 里
  **53 条的 `returned` 本来就是空**的（错误码由不带命令字面量的辅助函数返回）——硬加会造出
  53 条假红，而假红最终被「加豁免」消掉，真漂移跟着漏过去。`per_command` 的**键**（分派点）
  与它的**值**（切出的码）是两件事，哨兵看键。
  口径与假红规模：`.harness/spike/probe_error_vacuity.py`；负向验证（**含元验证** —— 关掉哨兵
  后同一注入必须变绿，证明红色确由它产生）：`.harness/spike/probe_m63b_error_wiring_negative.py`。
- ★ **`unsliced` 不是自由桶**：错误门禁里切不出 `ErrorCode` 的注册表命令（当前 6 条
  `data.table.*`）必须与 `check_error_contract.UNSLICED_DECLARED` **双向相符** —— 桶里冒出
  未登记的 ⇒ 某条命令**悄悄**失去了错误契约检查；登记了却没进桶 ⇒ 台账过期。
  形态与 `check_param_consumption.KNOWN_GAPS` 同构：**只会变短**，不会长成长期豁免名单。
- 跑测试 / 门禁只用 `./.venv/Scripts/python.exe`（或 `uv run`）。
- ★ **改台账要显式 `newline="\n"`**：`Path.write_text` 在 Windows 上默认把 `\n` 翻成 `\r\n`，
  会把**整个**文件变成 CRLF —— 实锤：`set_feature.py`（改 `passes` 的官方入口）一次调用让
  `feature_list.json` 漂出 **426** 个 CRLF，一行 `passes` 的改动被埋进几千行噪声里。
  该脚本已修，`check_ledger.py` 现在盯着三个台账文件的换行。

## 四、改动后同步（收口清单）

1. `feature_list.json` —— 增/改特性；
2. `tasks/BACKLOG.md` + 对应 `tasks/Mxx-*.md` —— 里程碑状态与细则；
3. `PROGRESS.md` 追加一行（`project_state.json` 若动排期）；
4. 当日 `.workbuddy/memory/YYYY-MM-DD.md` —— 当日工作留痕；
5. 跑 `check_all.py`，末行 `FULL GATE PASSED`。

**全仓约定**：文件一律 **LF**（Windows 写文件用 `open(..., newline="\n")`，`Path.write_text` 会把整文件转 CRLF）；
`git commit` 走 `-F - < 消息文件`；**默认只本地提交，`push` 需授权**。
