# .harness — 项目工作台

本目录是 rpa_core 的「**留痕 + 门禁 + 一手证据**」中枢：事实源在这里，门禁脚本在这里，
排查与对标的一手证据也在这里。

- **会话开始**：读本文件的「顶层台账」两张表 + `PROGRESS.md` 尾部 + `feature_list.json`。
- **每次实质改动后**：照文末「改动后同步」收口。

---

## 一、顶层台账（事实源）

| 文件 | 职责 | 写法约定 |
|---|---|---|
| `PROGRESS.md` | 时间线台账 | **append-only**：一行 `YYYY-MM-DD \| feature \| status \| evidence`。历史条目**永不改写**——多处任务文档按「`PROGRESS.md` 的 Mxx 条目」引用它 |
| `feature_list.json` | 特性台账（现 **82** 项；`id` / `title` / `passes`） | 改 `passes` 用 `scripts/set_feature.py <id> true\|false`；`title` 可追加一句说明 |
| `project_state.json` | 排期 / 阶段事实源（`phase`、`active_milestone`、`active_plan`、`active_feature`、`next_milestone`、`task_index`、`scope`、`decision_gate`） | 动 `active_*` 排期是**决策**：先拍板再改 |
| `invariants.md` | **15 条不变量**（工程铁律） | 新增不变量要**同时在门禁脚本里落地**，否则只是文档 |
| `architecture.md` | 依赖方向与分层 | — |
| `BACKLOG.md` | **指令优化规划**（对标影刀 72 条指令，四 Phase） | ⚠️ 与 `tasks/BACKLOG.md` **不是一回事**，见下 |
| `yingdao-gap-matrix.md` | 影刀能力差距矩阵 | 对标复核用（改动时**双向**查漏） |

> ⚠️ **两个 BACKLOG**：`BACKLOG.md`（本目录）= 指令参数/优化的分片规划；
> `tasks/BACKLOG.md` = **里程碑总表**（Mxx 清单 + 已完成段）。别混。

## 二、子目录

| 目录 | 内容 | 保留 / 新增约定 |
|---|---|---|
| `adr/` | 架构决策记录，`NNNN-slug.md`（现 **17** 篇：0001–0008、0010–0018，**0009 缺号**；`0013` = 移除 playwright / 扩展单通道） | 决策变了**新增**一篇，不原地改写；被引用的编号**永不重编号** |
| `tasks/` | 里程碑细则 `Mxx-slug.md`（**71** 篇）+ `BACKLOG.md`（总表） | 新任务文档**照抄 M56/M57 头部**；`Status:` **必须顶格独立一行**，值仅 `planned` / `active` / `blocked` / `done`；**编号不重编号** |
| `spike/` | 只读探针 + 负向验证探针（按 M 编号命名） | **两类，务必分清**：<br>① **不带下划线**的 `probe_*.py` / `add_l2_*.py` = **证据件**——源码注释、用例、门禁按名引用它们，**不许删**；<br>② **下划线前缀** `_*` = 本机 scratch（探针产物 / 取证日志 / 诊断脚本），`.gitignore` 已整体收口（`/.harness/spike/_*`） |
| `demo/` | GUI 可行性 demo（PySide6 / fluent / QML 视觉对比） | `.py` / `.qml` 多被引用，属证据件；`*.png` 截图已忽略 |
| `scripts/` | 门禁脚本（见下） | 新增门禁要登记进 `check_all.py` |

## 三、门禁

总入口 **`.harness/scripts/check_all.py`**，依次跑：

1. `pytest`
2. `ruff check .`
3. `check_architecture.py` / `check_tasks.py` / `check_param_consumption.py` / `check_error_contract.py` / `check_command_matrix.py`
4. 前端纯函数切片 `scripts/check_*.mjs`（**15** 个，在**仓库根 `scripts/`**，不是本目录；无 node 时跳过）

`set_feature.py`：改 `feature_list.json` 的 `passes`。

**铁律**（踩过的坑，别重犯）：

- 判门禁只看**末行 `FULL GATE PASSED` + 退出码**；**报绿前必须做负向验证**（注入 → 门禁必须真红 → 逐字节 `md5` 还原）。
- `check_all.py` **首条 `pytest` 收尾非零会遮住其后全部** ⇒ 定位失败要**逐条单独跑**（`check_command_matrix` / `ruff` 最易被遮）。
- 门禁输出**重定向到文件再读**（接 `| head` 会 SIGPIPE 打断，且可能让探针的还原逻辑半途而废）。
- `check_param_consumption` / `check_error_contract` 靠 **`EXECUTOR_FILES` 前缀映射**切片源码：
  **新执行器必须登记**，否则该命令被判 `skipped` —— rc=0 且汇总行照报 PASS，**覆盖实为零（假绿）**。
- 实现侧的分派写**显式 `if command_id == "<id>"` 链**，别用 handler 注册表：两条门禁只认字面量分支体，
  注册表会让命令「看着被检查、实则零覆盖」。
- 跑测试 / 门禁只用 `./.venv/Scripts/python.exe`（或 `uv run`）。

## 四、改动后同步（收口清单）

1. `feature_list.json` —— 增/改特性；
2. `tasks/BACKLOG.md` + 对应 `tasks/Mxx-*.md` —— 里程碑状态与细则；
3. `PROGRESS.md` 追加一行（`project_state.json` 若动排期）；
4. 当日 `.workbuddy/memory/YYYY-MM-DD.md` —— 当日工作留痕；
5. 跑 `check_all.py`，末行 `FULL GATE PASSED`。

**全仓约定**：文件一律 **LF**（Windows 写文件用 `open(..., newline="\n")`，`Path.write_text` 会把整文件转 CRLF）；
`git commit` 走 `-F - < 消息文件`；**默认只本地提交，`push` 需授权**。
