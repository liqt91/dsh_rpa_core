# M50 GUI 改写入口（祖先链增删改 + 锚点编辑）

Status: `done`

Plan: `.harness/tasks/M50-gui-locator-editing.md`（本文件）
由来: `.harness/tasks/M48-desktop-contract.md` §残留「GUI 改写入口（祖先链增删改、锚点编辑）」
前置: M48 桌面契约一片（`done`，D1/D2/D3 已落地，真机复验 16/16）

## 目标

M48 把 `path`（祖先链）与 `anchor`（锚点）**如实展示**出来了，但都是**只读**：
编辑器点一次确定虽不再丢数据（`compose_locator(structured=…)` 透传），用户却没法**改**。
M50 补上改写入口——把 M48 的两块只读控件升级为可编辑，让「捕获到的祖先链不对」时
用户能在编辑器里就地修，而不是重新捕获或手改 JSON。

## 边界（维护者 2026-10-08 定案，三选一全部取推荐项）

| 决策点 | 定案 |
| --- | --- |
| `path` 改写范围 | **增删级 + 改级内键**（每级的 controlType/automationId/name/className 都可编） |
| `anchor` 是否纳入 | **纳入**：内嵌**一层** locator 编辑器（模型已禁嵌套） |
| Web 侧是否同步 | **只改 GUI**（ADR 0016：GUI 唯一主力形态，Web 编辑器冻结演进） |

## M48 只读的两条理由，M50 如何回应

M48 在 `element_editor.py` 的 docstring 里写了两条「本片只读」的理由。M50 落地前先逐条回应，
否则就是「不理会前人的顾虑直接放开」：

   1. **`path` 的顾虑**：M48 说「『勾选后重拼』要决定保留哪几个键、以什么形状重拼，而那个语义
      本该由**捕获侧**决定」——担心编辑器立**第二套权威**。
      **M50 的回应**：重拼规则**不新发明**，而是**逐字复用捕获侧已有的那一条**——
      「每级至少一个键、全空级丢弃」（见 `desktop_agent._locator_step_for`：取不到任何键返回
      `None`，调用方跳过）。M50 把它抽成**同一份**纯函数给编辑器用
      （`model.desktop.prune_locator_steps`），不是抄一遍。这样编辑器与捕获侧的
      「一级长什么样」是**同一个判据**，不存在第二套权威。
      → 关键设计：**「每级至少一键」从捕获侧的隐式行为，升格为编辑器与捕获侧共享的显式约束**。
2. **`anchor` 的顾虑**：M48 说「让用户手编一段嵌套 locator 会引出『锚点自己也带锚点怎么办』」。
   **M50 的回应**：这个顾虑**模型层已经解决**——`DesktopLocator` 有校验「anchor 的 locator
   不能再带 anchor」（`AnchorSpec` 禁嵌套，M48 落地）。M50 的内嵌编辑器**只给一层**，
   天然不可能构造出嵌套；用户在锚点里也看不到「再加锚点」的入口。

## 切片

### S1 `path` 祖先链可编辑（增删级 + 改级内键）

- 现控件：只读 `QTableWidget`（4 列，`NoEditTriggers`）。
- 改为可编辑：4 列可改 + 「加一级 / 删选中级 / 上移 / 下移」按钮。
- 每级**至少一个键**（空行提醒，不落盘）；改完即时重判（走既有 `revalidate()`）。
- 产物回写 `self._structured["path"]`（空 ⇒ 移除该键，等价「不收窄」）。

### S2 `anchor` 内嵌一层 locator 编辑

- 现控件：只读 `QLabel` 说明。
- 改为：一个内嵌的**一层** locator 编辑器（backend + 字段勾选，复用标量字段表）。
  **不内嵌 `ElementEditorForm` 自身**——那会把「一层」变成任意深，与「模型禁嵌套」的口径打架；
  改为一个扁平的「一层 locator 表单」（即现有 `_build_desktop` 的字段部分抽成可复用块）。
- 空 ⇒ 产出 `None`（移除 anchor，回到「无锚点」）。

## S3 判据 + 负向验证 + 收口

**收口（2026-10-08）**：

- **门禁逐条单独取证**（`check_all.py` 首个失败即 `SystemExit`，一红其余没跑）：
  - `ruff check .` → `All checks passed!`（先修掉本轮新增的 3 处 E501/I001）
  - `check_architecture.py` → `75 python files, 86 manifests`
  - `check_tasks.py` → `69 features, 1 active task`
  - `check_param_consumption.py` / `check_error_contract.py` → `77 checked / 3 exempt / 0 skipped`
  - `check_command_matrix.py` → `86 条命令 … l2 块 109`
  - **13 个 `scripts/check_*.mjs`** 全 `EXIT=0`
  - `pytest` 全量 → `1418 passed / 21 skipped / 2 xfailed`，**2 failed** = 既有 QProcess 沙箱挂账
    （`test_gui_command_matrix.py` 两条，M48 已归因，**非本片引入**）。
- **一次全量套件里的红（已闭环为 flaky）**：`test_gui_node_edit.py::test_delete_suppressed_while_param_editor_focused`
  在**前两次全量**里都红（`assert not True` ⇒ `delete_action.isEnabled()` 仍为真、`editor.setFocus()` 未生效），
  但**第三次全量（同代码、同命令、deselect 两条 QProcess 挂账）完整通过**：
  `EXIT=0`、**零 `FAILED` 零 `ERROR`**（尾部 `SystemExit: 1` 是已知 atexit 删除守卫噪声，不改退出码）。
  更强的对照：该用例**单跑绿**、与前后邻居（`test_gui_home*`/`test_gui_motion`）同跑绿、
  连跑三次 `element_editor + node_edit` 组合全绿。该用例来自 2026-09-15（早于 M48/M50）、用
  `window.show()+setFocus()`，是典型**焦点竞争**形状（与 M48 记的 `0x8001010d` 同族）。
  ⇒ **判定：flaky（既有的焦点竞争，非 M50 引入）**——依据是「同代码可完整跑绿」这条正向证据；
  仍**不写「M50 绝对无关」**（未做纯净全量对照：M48 新测试文件 untracked、stash 不带 ⇒ 纯树全量收集失败）。
- **负向验证 25 处全命中**（`probe_m48_negative.py`，含本轮新增 I22–I25）：对照绿 → 注入精确红 →
  逐字节还原核 md5 → 复绿，全程零残留哨兵。
- **台账**：本任务单置 `done`；`project_state.active_milestone/active_plan` 推进；`PROGRESS.md` 加一行。

## 判据要求（每条各做一次负向验证）

- `path` 增删级 / 改键 / 上移下移 / 空级丢弃 / 往返等价（编辑不改动 = 原样）。
- `anchor` 一层编辑 / 清空即移除 / **模型拒绝嵌套**（构造一条带嵌套的，走模型校验必报）。
- **两端同规则**：编辑器重拼用的 `prune_locator_steps` 与捕获侧 `_locator_step_for` 的口径
  必须有一条**成对判据**钉住（防止两处各写一份、日后漂移）。
  （实现名落为 `model.desktop.prune_locator_steps`：捕获侧 `_locator_step_for` 与编辑器
  `_commit_path_steps` **共用同一份**；成对判据见 `test_editor_path_pruning_is_shared_with_capture`。）
- 「编辑不改动 ⇒ 逐字节原样」（往返等价）是硬要求（M48 修过一次静默丢数据，不能退回去）。

## 牵连的四契约（项目铁律，实测口径见 M48 §任务 #8）

- `tests/contract/test_gui_element_editor.py`（字段表契约）
- `tests/contract/test_editor_i18n.py` + `devserver/static/i18n.js`（**只读新增文案如要 i18n**）
- `test_field_tables_match_what_executors_actually_read`
- `test_extension_installer`（未涉版本，**不受影响**）

## 状态记录

- 2026-10-08：任务单建立，三处口径定案（见 §边界）；**未动 `src/`**。
- 2026-10-08：**S1 落地**——`model/desktop.prune_locator_steps` 抽取为共享纯函数，
  捕获侧 `_locator_step_for` 改用它；`element_editor._build_locator_path_tree` 重写为
  可编辑表格（4 列可改 + 加一级/删选中级/上移/下移）+ `path_hint`；空 path 由「不建表」
  改为**常驻建表**（用户要能新建祖先链），旧判据据此重写（不变量从「控件存在」挪到「结果文档」）。
- 2026-10-08：**S2 落地**——`element_editor._build_anchor_view` 重写为「启用锚点」勾选框
  + 内嵌**一层** locator 表单（backend 下拉 + 字段行，复用 `LOCATOR_FIELDS_BY_BACKEND`）；
  `_commit_anchor` 收成 `{"locator": {...}}`，未勾/空 ⇒ pop。
- 2026-10-08：**M50 判据 12 条落地**（`test_gui_element_editor.py`，59 → 66 项）：
  改键 / 增删级 / 上移下移 / 空级丢弃 + 提示 / 末级清空 ⇒ 移除字段 / **往返等价** /
  **两端同规则**（共享纯函数成对）/ 锚点启用编辑 / 取消即移除 / 无嵌套入口 / 模型拒嵌套。
  全组 `EXIT=0`。
- 2026-10-08：**负向验证扩至 I22–I25**（挂进 `probe_m48_negative.py`）：共享纯函数不再丢空级、
  编辑器绕开共享纯函数、祖先链表退回只读、取消勾选不移除 anchor——逐处注入 → 精确红 →
  逐字节还原核 md5 → 复绿。
