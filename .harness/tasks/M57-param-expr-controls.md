# M57：参数表达式控件（对标影刀的「左 Py 右 fx」）

Status: `done`

> **2026-10-13 当日立项 → 当日落地**。落实提交见文末。
> 前置：M54（`gui/i18n.py` + 面板中文化）、M56（画布中文化）、ADR 0016（Web 编辑器冻结）、
> `docs/yingdao-web-cmds-benchmark.md` §三-8 / §四 P2.6、`.harness/yingdao-gap-matrix.md` §六。
> 本里程碑**不占 active 位**（`project_state.active_plan` 仍是 M52）。

## 一、起因（维护者原话）

> 「桌面读 x-python、x-fx；fx 不是开关概念，是点击后，弹出变量选择，直接选变量，不需要 +变量这个按钮多一步；
> 零迁移，目前还是开发阶段，不用前向兼容（**这条写入规范**）」

三句话对应三件事：**门控改读声明**、**fx 变按钮**、**不做前向兼容并写进规范**。

## 二、取证（动手前的现状）

- `param_form._build_field` 此前只对「纯 `string` 且非 `enum`」建 `_wrap_fx_row`，**不读字段级
  `x-fx` / `x-python`**（全 `src/` 里只有 `model/command.py` 那对**顶层死字段**带这两个名字，
  `list[str]` 类型、86 个 manifest 零使用）⇒ 桌面比 Web 宽松，两端长期分化。
- 旧 `_wrap_fx_row` 是**一个开关 + 一个条件按钮**：`fx_button` 可选中（`toggle` 置/摘 `_exprModes`），
  `var_button`（「＋变量」）**只在开关打开后可见**（`var_button.setVisible(fx_button.isChecked())`）
  ⇒ **普通文本模式下没有变量入口**，且要多点一次按钮。
- Web 端 `app.js:1898-1978` 早已是「`fx-btn` + `py-btn`」两个按钮（互斥），门控来自字段级
  `x-fx` / `x-python`（`:1716-1717`）——**桌面要对齐的正是这套**。
- 运行期不需要改：`orchestrator._execute_action` 按 `node.expr_modes[key]` 分流
  （`python` → 子进程求值；其余 → `resolve` / `resolve_tags`），`_exprModes` 就是唯一真值。
- 字段级声明实测：`x-fx` **12** 个、`x-python` **4** 个（`browser.input.text` / `data.setVar.value` /
  `data.table.appendRow.row` / `data.table.setCell.value`）。

## 三、做法

### 3.1 门控改读声明（与 Web 同源）

`_build_field` 新增

```python
allow_fx = bool(field_schema.get("x-fx"))
allow_python = bool(field_schema.get("x-python"))
```

只有「纯 `string` 且非 `enum`」且 `allow_fx or allow_python` 时才挂表达式行 ⇒ 与 Web 的规则一字不差。
**没声明的字段就没有入口**（要加就在 manifest 加 `x-fx`/`x-python`，不在 GUI 兜底）。

### 3.2 拓扑：左 `Py`、右 `fx`

`_wrap_expr_row(name, editor, *, allow_fx, allow_python)` 生成

```
[Py（可选中，仅 x-python）] [ 输入框 ] [fx（普通按钮，仅 x-fx）]
```

- **`Py`**：`setCheckable(True)`，选中 ⇒ `_expr_modes[name] = "python"`，取消 ⇒ 摘掉（仅当当前是 python）。
- **`fx`**：**`setCheckable(False)`** —— 它不是开关。`clicked` ⇒ `_show_variable_menu(name)`
  弹出变量列表；选中某一项 ⇒ `_insert_variable` **直接插到光标处**。
- **「＋变量」中间按钮已删除**（少一步）。

### 3.3 插入语义与 fx 态的生命周期

- `_insert_variable`：当前是 `python` 模式 ⇒ 插入**变量名原文**（融进表达式）；否则插入 **`[变量]` 标签**
  并把字段置为 `fx` 态。
- `_on_expr_text_changed`：**标签被删光 ⇒ 自动撤销 `fx` 态**。判据复用 `runtime.resolver.has_tags`
  （**唯一事实源**，别各写一份正则）。不撤销的后果是字段停在「文本里没有任何标签、运行期却按标签解析」，
  用户随后手打的**字面 CSS 选择器**（`a[title]`，恰好命中 `resolver._TAG`）会报 `INVALID_REFERENCE`。
- `_sync_expr_state`：按 `_expr_modes` 刷新「Py 选中态 + 编辑器底色」。
- 注册表 `_expr_rows[name] = (Py按钮|None, fx按钮|None, 编辑器)`（原 `_fx_buttons` 已废）。

### 3.4 `expr_modes()` 剪枝

只回传**当前界面真的有表达式控件**的字段——未声明的字段即使 raw 里残留旧模式也不写回
（开发期硬切，见 §五）。

## 四、判据（`tests/contract/test_gui_expr_controls.py`，13 条）

| 判据 | 钉住什么 |
|---|---|
| `test_expr_controls_gated_by_manifest_declaration` | 四种门控组合的控件形状（fx_only / py_only / both + 非 string 不受影响） |
| `test_undeclared_string_field_gets_no_fx` | 回归：没声明的 string 字段**不再**白拿 fx |
| `test_fx_inserts_tag_at_cursor_and_enters_fx_mode` | 点击 fx → 菜单 → 插入 `[name]` + 置 fx 态 |
| `test_fx_button_is_not_checkable` | **fx 不是开关**（维护者口径） |
| `test_no_extra_var_button_remains` | 「＋变量」已删（表单里只剩 `Py` / `fx`） |
| `test_py_toggle_is_exclusive_with_fx` | 两模式互斥；python 模式下 fx 插**变量名原文** |
| `test_python_mode_initial_state_from_expr_modes` | 初始态来自 `_exprModes` |
| `test_fx_mode_dropped_when_last_tag_removed` | 标签删光自动撤销 fx 态 |
| `test_manual_css_selector_does_not_enter_fx_mode` | 手打 `a[title]` 不会被拖进 fx 态 |
| `test_expr_modes_pruned_to_hosted_fields` | `expr_modes()` 只回传有控件的字段 |
| `test_expr_buttons_have_no_pre_attached_menu` | 回归：不预挂 QMenu（弹层残影） |
| `test_expr_row_widgets_are_never_windows` | 回归：控件创建即带父级（顶层窗口闪现） |
| `test_tag_detection_reuses_runtime_resolver` | **AST 闸**：标签判据必须复用 `resolver.has_tags` |

连带同步：`test_gui_param_form.py` 删掉 5 条旧 fx 用例（拓扑已变）与 `catalog_schema_for_fx` 夹具，
把端到端那条 `test_apply_persists_expr_modes_to_raw` 从 `read`/`selector`（**已不声明 x-fx**）改到
`open`/`url`（声明了 `x-fx`），并改为走真实入口 `_insert_variable`。

## 五、规范：开发期不做前向兼容

维护者要求**写入规范** ⇒ `.harness/invariants.md` 新增第 15 条：

> Pre-release project: changes are hard cut-overs. Do not add backward-compatibility shims,
> schema-version migrations, or legacy-value adapters — drop an old shape outright rather than
> supporting both. Persisted documents (workflows, element descriptors, node `_exprModes`, settings)
> are not migrated.

并写入 `.workbuddy/memory/MEMORY.md` §5 约定（含「改既有设计取向时别再提零迁移兼容方案，直接给新语义」）。

## 六、负向验证（`.harness/spike/probe_expr_controls_negative.py`）

**6 注入全命中、期望集与实际集逐项相等、逐字节还原核 md5、复绿 13 passed**：

| 注入 | 打的方向 | 期望红 |
|---|---|---|
| I1 | 门控失效（所有 string 都给表达式行） | 3（含连带） |
| I2 | fx 插入不置 fx 态 | 1 |
| I3 | Py 选中不置 python 态 | 1 |
| I4 | 标签删光不撤销 fx 态 | 1 |
| I5 | fx 按钮变回可选中 | 1 |
| I6 | `expr_modes()` 不剪枝 | 1 |

I4 的 `pop` 行在文件里出现两次（缩进不同）⇒ 锚点带完整上下文；探针起手有哨兵自检与锚点唯一性自检。

## 七、验证与门禁

- 全量 pytest：**1606 passed / 0 failed / 21 skipped / 2 xfailed**。其中
  `test_panel_streams_jsonl_into_rows` 与 `test_shutdown_kills_running_child` 是**已知环境级假红**
  （QProcess 子进程没起来；**单独重跑 rc=0 全绿**）。
- `check_all.py` 的 pytest 一步非零 ⇒ 按纪律**逐条单独取证**：`ruff check .`、5 组静态
  （architecture / tasks / param_consumption / error_contract / command_matrix）、
  **15 个 `check_*.mjs`** —— **全部 rc=0**（`ls scripts/*.mjs` 实有 15 个，与 `PURE_FUNCTION_SCRIPTS` 一致）。

## 八、明确不动

- **Web 编辑器**：一行未碰（`app.js` / `selectField` 属 ADR 0016 冻结面）。
- **运行期 / 模型**：`_exprModes` 的语义与取值不变 ⇒ 不需要改 `orchestrator` / `model`。
- **枚举字段**：仍无变量入口（影刀有）——见 §九。
- **数字 / 布尔 / 数组 / 对象字段**：按项目设计不开放表达式入口。

## 九、遗留

- **枚举参数仍无变量入口**（影刀「浏览器类型」有；镜像 10 处「支持 fx 变量」取证）。
  要做得同时动 `_build_enum` 与 Web 的 `selectField` ⇒ **Web 属 ADR 0016 冻结面，需维护者单独放行**。
  已登记在 benchmark §四 P2.6 与 gap-matrix §六。
- **「插入即进 fx 态 + `[name]` 字面文本」vs 影刀「普通模式即可插 chip」**：这是**有意分歧**——
  用「显式 fx 态」换掉与 CSS 属性选择器语法的冲突（默认 `resolve` 只认整串 `${var}`，`[name]` 归
  `resolve_tags`）。不修，已登记为取舍。

## 十、交付

- 代码：`src/rpa_core/gui/param_form.py`（`_wrap_expr_row` / `_on_py_toggled` / `_show_variable_menu` /
  `_build_variable_menu` / `_insert_variable` / `_on_expr_text_changed` / `_sync_expr_state` / `expr_modes` 剪枝）、
  `src/rpa_core/runtime/resolver.py`（新增 `has_tags`）。
- 判据：新建 `tests/contract/test_gui_expr_controls.py`（13 条）；`tests/contract/test_gui_param_form.py` 同步。
- 探针：`.harness/spike/probe_expr_controls_negative.py`。
- 规范：`.harness/invariants.md` #15。
- 文档：`docs/yingdao-web-cmds-benchmark.md`（§三-8 / §四 P2.6 / 取证记录）、
  `.harness/yingdao-gap-matrix.md` §六、`docs/manifest-param-config.md` banner ④。
- 台账：`PROGRESS.md` + `feature_list.json` + `.workbuddy/memory/MEMORY.md` + 当日 memory。
