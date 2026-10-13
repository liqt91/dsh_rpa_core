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

---

## 十一、追加：fx 菜单只列「用户命名的变量」（2026-10-13 同日）

### 11.1 维护者口径（原话）

> 「fx 下拉的变量，内置的不用展示出来，只需要展示流程输入的变量，和每一步明确定义了是输出变量的
> 变量（就是有变量名的，比如设置变量指令的变量名，或者打开网页指令输出参数的保存网页对象）」

追问确认两点（选项卡）：

1. `steps.<节点id>.outputs.<字段>` 这类**自动展开路径** → **一并去掉**（选「推荐」）。
2. 菜单 → **分两组加标题**（选「推荐」）。

再追问一点（实现时发现的边界）：循环/错误变量也算「你在节点里亲手取的名字」
⇒ 维护者选「**有名字就列**」。

### 11.2 取证（动手前，真实流程实测）

用维护者自己保存的 `workflows/test11/workflow.json` 跑 `_collect_reference_paths()`，**共 20 条**：

| 来源 | 条数 | 内容 |
| --- | --- | --- |
| `inputs.*` | 0（该流程未声明入参） | — |
| `output_aliases` 值 | 2 | `webpage1`、`text` |
| `data.setVar` 的 varName | 1 | `url` |
| `steps.<节点id>.outputs.<字段>` | **14** | `steps.n6.outputs.sessionId` … |
| 内置作用域 | 3 | `loop.item` / `error.code` / `error.message` |

⇒ **17 / 20 是噪声**；且 `steps.n6.outputs.sessionId` 与别名 `webpage1`、`steps.n2.outputs.value`
与别名 `text` **语义重复**。

**取证顺带查出一处真分歧**：`_collect_reference_paths` 硬编码 `raw["command"] == "data.setVar"`，
而变量面板（`_refresh_variables`）、`compiler`、`orchestrator` 一律读 manifest 的 **`x-var-write`**
声明。`commands/data/table/getCell.json` **同样声明了 `x-var-write`**，于是它的目标变量
**只在变量面板里有、fx 菜单里从来没有**。

### 11.3 做法

- **API 换形（开发期硬切，invariants #15）**：
  `ParamForm(variable_provider=…)` → `variable_groups_provider=…`，
  形状从 `list[str]` 变成 `list[tuple[组标题, list[str]]]`——分组在**供给方**决定，
  因为只有它知道一个名字是流程入参、还是某条命令的输出别名／赋值目标。
  `MainWindow._collect_reference_paths()` → `_reference_groups()`（旧名整条删掉，不留兼容层）。
- **三组**：`流程参数` / `输出与赋值变量` / `循环与错误变量`（空组整个不出现）。
- **只列用户命名的**：`inputs.<名>`、`output_aliases` 值、`x-var-write` 命中的字段值、
  显式命名的 `loop.<item_var>` 与 `<error_var>.code`。
- ★ **「显式命名」的判据要比「raw 里有这个键」严**：控制流表单把空值**预填成默认名**
  （`param_form._build_control`：`str(raw.get("item_var") or "item")`），用户点一次「应用」
  默认名就进 raw 了 ⇒ 按「键存在」判会把 `loop.item` 这个**内置名又列回来**。
  实际判据 = 与模型默认值不同，默认值从 **`ForEachNode.model_fields` / `TryNode.model_fields` 现算**，
  不手抄。
- **菜单渲染**：组标题是不可点的灰行（对齐 Web fx 浮层的 `.fx-var-popup-group`）、组间一条分隔条、
  空组不留标题也不留孤立分隔条、一条都没有时给「（无可引用变量）」占位灰行。
- **能力没有被删掉**：`${steps.x.outputs.y}` 仍可手打，运行期语法与编译期引用校验**一字未改**。

### 11.4 判据（新增 6 条 / 改写 5 条）

| 判据 | 位置 | 钉住什么 |
| --- | --- | --- |
| `test_reference_groups_list_inputs_in_their_own_group` | `test_gui_param_form.py` | 「流程参数」组 = 输入声明 |
| `test_reference_groups_exclude_auto_paths_and_builtins` | 同上 | **`steps.*` 与内置作用域不进菜单**（本轮核心） |
| `test_reference_groups_list_named_outputs_and_var_writes` | 同上 | 别名 + `x-var-write`；**用 `data.table.getCell` 钉住「认声明不认命令 id」** |
| `test_reference_groups_list_only_explicitly_named_loop_and_error_vars` | 同上 | 两条作用域分支；默认名不算命名 |
| `test_variable_menu_renders_group_headers_as_disabled_rows` | `test_gui_expr_controls.py` | 灰标题 + 一条分隔条 |
| `test_variable_menu_skips_empty_groups_and_their_separators` | 同上 | 空组不留标题／分隔条 |
| `test_variable_menu_shows_placeholder_when_nothing_is_named` | 同上 | 占位灰行 |
| 改写 `test_reference_groups_*` ×4（原 `test_reference_paths_*`） | `test_gui_flow_inputs_linkage.py` | 改用分组 API（`_input_paths` 摊平 helper） |

### 11.5 负向验证（`.harness/spike/probe_reference_groups_negative.py`，7 注入全中）

| 注入 | 打在哪 | 期望红 | 结果 |
| --- | --- | --- | --- |
| G1 自动路径 + 内置作用域**回流** | `_reference_groups` | 排除判据 + 该组精确内容判据 | 2/2 |
| G2 变量写入退回**硬编码命令 id** | 同上 | 目标名判据（getCell 漏掉） | 1/1 |
| G3 循环/错误判据**放宽成「键存在」** | 同上 | 命名判据 | 1/1 |
| G4 空组**不剔除**（provider 侧） | 同上 | 两条标签判据 | 2/2 |
| G5 组标题**变成可点** | `param_form` | 分组判据 | 1/1 |
| G6 空组**不剔除**（表单侧） | 同上 | 空组分隔条判据 | 1/1 |
| G7 无变量时**不给占位** | 同上 | 占位判据 | 1/1 |

逐字节还原核 md5、还原后复绿 122 passed。
★ G1 首跑「多红一条」，取证后确认是**推理应含**的连带（G1 把 `loop.item` 塞进 `scoped`，而
`test_reference_groups_list_only_...` 断言的是该组的**精确内容**）⇒ 按推理补进期望集，**非**「把模块打倒」。

### 11.6 验证与门禁

- 定向：`test_gui_expr_controls.py` + `test_gui_param_form.py` + `test_gui_flow_inputs_linkage.py`
  = **122 passed**。
- 全量 pytest **1614 passed / 0 failed / 21 skipped / 2 xfailed**（rc=0）。跑出过一次
  `test_gui_node_edit.py::test_delete_suppressed_while_param_editor_focused` 红——**已知环境级
  flaky**（offscreen Qt 焦点竞争，见 §四 flaky 清单），单跑即绿，**未动其代码**。
- `ruff check .` 全绿（自动修掉 2 处导入排序）。

### 11.7 明确不动 / 遗留

- **Web fx 浮层未同步**（仍列 `steps.*` 与「内置作用域」组）——Web 属 **ADR 0016 冻结面**，
  登记为**已知差异**（benchmark §四 P2.6 / gap-matrix §六）。
- 「`x-var-write` 的 `field` 值以 `${` 开头时跳过」这条**沿用**旧实现的判断，未改口径。
- 枚举 / 非字符串字段仍无变量入口（同上文 §九）。

