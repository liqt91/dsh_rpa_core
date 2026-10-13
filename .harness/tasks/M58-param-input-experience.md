# M58 — 参数输入体验：编辑态双缓冲 / 输入区域 / Py 两态

> 里程碑：M58（承接 M57 的参数表达式控件）
> 日期：2026-10-13
> 范围：`gui/param_form.py`（主）、`devserver/static/app.js`（一行级）、4 个 manifest 加声明
> Status: `done`

## 一、起因（维护者原话）

> 「py开关不是很明显，另外就是影刀切换py开关，填写内容也会切换，感觉是一个参数存了两套值，
> 一套是文本，一套是python表达式。还有就是影刀的参数输入，输入框也有输入区域，对可能会有
> 大量输入的参数会以输入区域形式展现」

三条诉求：① `Py` 按钮视觉不明显；② 切模式时内容应当各自记忆；③ 长文本参数要有输入区域。

**关键澄清（维护者当场补的，直接决定②的方案）**：

> 「我看了下，影刀只是**编辑态**的时候有两份值，一旦点击保存，就只存**模式**（python或非python）
> + **一份值**了」

⇒ 影刀**落盘的形状与我方现有模型完全一致**（`with[key]` + `_exprModes[key]`），差别只在**编辑态**。
这一句把「要不要加第二份持久化值」这个模型级改动直接消掉了。

## 二、取证（动手前的现状）

| 项 | 现状（实测） |
|---|---|
| 值存储 | **只有一份**：`orchestrator.py:755-762` 的 `python_fields` 与 `plain_inputs` **都从 `node.with_.items()` 分出** ⇒ 一份值两个出口 |
| 切模式 | 不换内容：切到 `python` 后同一段文本立刻被当代码求值（`你好` ⇒ NameError） |
| 输入控件 | **所有** string 都是单行 `QLineEdit`（`_build_text`）；manifest 里**零**多行声明 |
| Web 端 | `textField` 只造 `<input type=text>`；`jsonField` 用 textarea；CSS 早已有 `.field textarea { width:100%; font-family:Consolas; min-height:64px }` |
| Py 按钮 | 纯文字 `QToolButton("Py")` + `setCheckable(True)`，选中态只有 Qt 默认按下阴影；`fx` 是 `setAutoRaise(True)` 的裸文字 |
| ADR | `0016` 第 25 行：**不再为 Web 编辑器补能力**，但允许「**成本极低**」的例外（如一行前端） |
| 与既有取向的冲突 | M52 S6 拍板「两份真值 = 事故土壤」（元素引用的值快照）——但那是「快照与引用**同时可能生效**」，本次是「两份值**互斥使用**」⇒ 不同构，可做 |

影刀侧：仓内镜像 `docs/yingdao-cmds/` 里「文本输入模式 / Python 输入模式」的区分见
`上传文件.md:23`、`处理上传对话框.md:53`；「输入区域」是维护者实机观察（镜像正文未单独成条目）。

## 三、做法

### 3.1 编辑态双缓冲（`_expr_slots`）

```python
self._expr_slots: dict[str, dict[str, str]]   # 字段 → {"text": ..., "python": ...}
```

- `_swap_expr_slot(name, target)`：**存当前槽 → 取目标槽**；目标槽**首次使用继承**刚存下的这份
  （而不是填空）——否则点一次 `Py` 内容凭空消失，而用户要的正是「切过去改一改、切回来还是原样」。
- `_on_py_toggled` 里**先换槽、后改模式**：`_swap_expr_slot` 要按**旧模式**决定存进哪一格，模式先改就存错格。
- **落盘不变**：`values()` 与 `expr_modes()` 都不看 `_expr_slots` ⇒ 永远只有「当前模式那一份值 + 模式」。
  **模型 / 运行期 / 编译器 / Web 数据格式一行未改**（与影刀落盘口径一致）。
- 换槽用 `_replace_editor_text`（`blockSignals` 静默替换）：否则程序换内容会触发
  `_on_expr_text_changed` 的「标签删光即撤销 fx 态」判定，把模式顺手改掉。

### 3.2 输入区域（`x-multiline`）

- 新增**字段级声明 `x-multiline`**，本轮标注 4 个字段：`browser.executeScript.script`、
  `browser.input.text`、`data.setVar.value`、`browser.handleDialog.promptText`。
- 桌面 `_build_textarea` → `QPlainTextEdit`：**固定高度** 68px（不随内容长高、不撑散表单）、
  `setTabChangesFocus(False)`（Tab 留作缩进）。
- 与 `x-fx` / `x-python` **可叠加**（先多行输入区、再挂按钮）。
- Web `textField` 按 `opts.multiline` 造 `textarea`（复用既有 `.field textarea` 样式）；
  `schemaField` 把 `propSchema["x-multiline"]` 传进 opts。属 ADR 0016 的「成本极低」例外。
- **编辑器适配层**（模块级函数）：`_editor_text` / `_insert_into_editor` / `_replace_editor_text` /
  `_style_editor`——上层逻辑（插变量 / 撤销 fx 态 / 换槽 / 收集值）只认统一接口，
  不然每加一处调用点都要 `isinstance` 分叉，漏一处就是静默不生效。

### 3.3 Py 两态视觉

- `_PY_BUTTON_QSS`：未点亮 = 灰字 + 浅描边 + 白底；点亮 = `ACCENT` 底 + 白字 + 加粗。加宽到 36px
  （纯文字太窄时底色块看不出状态）。
- `_FX_BUTTON_QSS`：同款静态描边；**去掉 `setAutoRaise(True)`**（autoRaise 在部分样式下会退化成裸文字，
  与 Py 一虚一实）。

### 3.4 顺带修掉的两处真问题（换槽引入 / 暴露的）

1. **切回普通模式后 `[变量]` 变成不解析的死文本**：切回时模式被 `pop`，而内容仍带标签 ⇒ 运行期走
   `resolve`（只认整串 `${var}`）⇒ 引用失效。新增 `_restore_fx_mode_for_content`：切回后内容若带标签
   就回到 `fx` 态。★ 与 `_on_expr_text_changed` **方向相反、不能合并**——那条管「敲字删光标签 ⇒ 撤销」，
   这条管「程序换内容 ⇒ 有标签就点亮」；合并会让手打 `a[title]` 也进 fx 态（M57 特意避开的坑）。
2. **换槽后光标被复位到内容开头**：`QPlainTextEdit.setPlainText` 把光标放到 0 ⇒ 切过去点 fx 插变量
   会插到最前面（实测 `web[web]`）。`_replace_editor_text` 统一把光标移到末尾。

## 四、判据（`tests/contract/test_gui_expr_controls.py`，13 → **32** 条）

新增 16 条：

| # | 判据 | 钉住什么 |
|---|---|---|
| 1 | `test_py_toggle_keeps_both_edits` | 核心：切过去改一版 → 切回原稿 → 再切过去那版还在 |
| 2 | `test_first_py_toggle_inherits_current_text` | 首次切换继承，不清空 |
| 3 | `test_slot_swap_moves_cursor_to_end` | 光标在末尾（**必须打在多行控件上**，见 §六） |
| 4 | `test_slots_never_leak_into_saved_values` | 缓冲不进 `values()`、落盘不出现 `_exprValues` |
| 5 | `test_switching_back_keeps_tags_alive` | 切回普通后 `[变量]` 仍解析（自动回 fx 态） |
| 6 | `test_switching_back_to_plain_text_drops_fx` | 反向兜底：取回纯文本不许停在 fx 态 |
| 7 | `test_multiline_declaration_switches_to_plain_text_edit` | 读声明才换多行；没声明仍单行 |
| 8 | `test_multiline_value_survives_collection` | 多行内容（含换行）完整收回 |
| 9 | `test_multiline_and_expr_declarations_compose` | `x-multiline` 与 `x-fx`/`x-python` 叠加 |
| 10 | `test_multiline_editor_expression_highlight_uses_its_own_selector` | 底色选择器跟真实控件类型一致 |
| 11 | `test_multiline_slot_swap_works_on_plain_text_edit` | 双缓冲对多行编辑器同样成立 |
| 12 | `test_py_button_carries_two_state_style` | 两态样式都在 |
| 13 | `test_py_button_is_widened_for_a_visible_state_block` | 加宽 |
| 14 | `test_fx_button_shares_the_same_frame` | fx 也带描边 |
| 15 | `test_both_editors_read_the_same_multiline_declaration` | ★ 声明键名唯一：桌面与 Web 各读一次同一份 |
| 16 | `test_multiline_declaration_is_only_used_on_string_fields` | 扫全仓：`x-multiline` 只挂在（有效类型为）string 的字段上 |

第 16 条的有效类型用 `param_form._effective_type` **现算**，不在判据里复写一遍「取首个非 null」
——否则判据与实现在同一个坑上各错一次（`data.setVar.value` 的 type 就是列表）。

## 五、负向验证（`.harness/spike/probe_expr_controls_negative.py`，6 → **14 注入**）

**14/14 精确命中**、期望集与实际集逐项相等、逐字节还原核 md5、复绿 32 passed。

| 注入 | 打的方向 | 期望红 |
|---|---|---|
| I1 | 门控失效（所有 string 都给表达式行） | 3（含连带） |
| I2 | fx 插入不置 fx 态 | 2 |
| I3 | Py 选中不置 python 态 | 6（依赖模式切换的新判据连带） |
| I4 | 标签删光不撤销 fx 态 | 1 |
| I5 | fx 变回可选中 | 1 |
| I6 | `expr_modes()` 不剪枝 | 1 |
| **I7** | 换槽后**不填回**目标槽 | 4 |
| **I8** | 首次切换**不继承**（填空） | 4 |
| **I9** | 换槽后**不复位光标**（多行分支） | 1 |
| **I10** | 切回普通**不恢复 fx 态** | 1 |
| **I11** | `x-multiline` **不读声明** | 6（含「声明键被读」闸） |
| **I12** | 多行样式选择器写死 `QLineEdit` | 1 |
| **I13** | Py 两态样式丢失 | 1 |
| **I14** | Py 不加宽 | 1 |

★ 探针自己抓出的问题（都靠既有防线拦下，写下来防重犯）：

1. **锚点唯一性自检又立一功**：I2 的 8 空格锚点
   `        self._expr_modes[name] = "fx"` 是 M58 新增的 12 空格
   `_restore_fx_mode_for_content` 那一行的**子串** ⇒ `count == 2`，被自检拦下、补上下文后唯一。
2. **注入写成非法语法 ⇒ 判不出「打到哪条」**：I11 首版写成 `if False:`，而锚点在**条件表达式**中间
   （`X if ... else Y`），带冒号直接是语法错误 ⇒ junitxml 里红名单只有**模块名**、`passed=0`。
   改成 `if False`（不带冒号）后精确命中 6 条。**教训：注入必须是「合法语法的精准放宽」**。
3. **判据打错了对象 ⇒ 注入打不中**：I9 首版锚点打在 `QLineEdit` 分支的 `editor.end(False)` 上，
   而 **`QLineEdit.setText` 本来就把光标放到行尾**（`QPlainTextEdit.setPlainText` 才复位到 0）
   ⇒ 删掉那行不会红。判据与注入都改到多行分支后才命中。

## 六、验证与门禁

- **定向**：`test_gui_expr_controls.py` 32 passed；三组定向（含 `test_gui_param_form.py` /
  `test_gui_flow_inputs_linkage.py`）**138 passed**。
- **全量**：pytest **1630 passed / 0 failed / 21 skipped / 2 xfailed**（rc=0）
  —— 较 M57 的 1614 正好 **+16 = 本轮新增判据数**。
- **静态**：`ruff check .` 全绿；5 组 Python 静态门禁（tasks / architecture / param_consumption /
  error_contract / command_matrix）逐条 rc=0；**15 个 `check_*.mjs`** 逐条 rc=0。
- **JS**：`node --check src/rpa_core/devserver/static/app.js` 通过。

## 七、明确不动

- **模型 / 运行期 / 编译器**：`with` 一份值 + `_exprModes` 的形状未变（这正是维护者澄清的影刀落盘口径）。
- **Web 的 `py-btn` / `fx-btn` 双缓冲**：Web 仍是「一份值 + 模式」，属 ADR 0016 冻结面，登记为已知差异。
- **枚举 / 非字符串字段的变量入口**：同上（M57 遗留，仍需单独放行）。
- **`x-multiline` 未加到更多字段**：只按维护者口径的「代码/脚本 + 长文本」四类。

## 八、遗留

1. Web 端编辑态双缓冲未同步（冻结面）。
2. Web fx 浮层仍列 `steps.*` 与内置作用域组（M57 已登记，本次未动）。
3. 枚举字段仍无变量入口（需动 Web，冻结面）。
4. 多行输入区的**高度不可调**（固定 68px）；影刀是否可拖拽放大未取证。
5. `x-multiline` 的判定仍要**人工在 manifest 里声明**——没有「按描述自动推断」，
   这是**有意**的（与 `x-fx` / `x-python` 同一取向：门控读声明、不在 GUI 猜）。

## 九、交付

- 代码：`gui/param_form.py`（`_expr_slots` / `_swap_expr_slot` / `_restore_fx_mode_for_content` /
  `_build_textarea` + 4 个编辑器适配函数 + 两套按钮 QSS）、`devserver/static/app.js`（`textField` multiline）。
- 声明：`commands/browser/{executeScript,input,handleDialog}.json`、`commands/data/setVar.json`。
- 判据：`tests/contract/test_gui_expr_controls.py`（+16）。
- 探针：`.harness/spike/probe_expr_controls_negative.py`（扩到 14 注入）、
  `.harness/spike/probe_m58_expr_slots.py`（只读探针，打印中间态）。
- 文档：benchmark §四 P2.6（+3 项打勾）、gap-matrix §六（+3 行）、`manifest-param-config.md` banner ⑥。
- 台账：`PROGRESS.md` + `feature_list.json` + `.workbuddy/memory/MEMORY.md` + 当日 memory。
