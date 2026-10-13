# M59 — 参数面板行形态：标签一行 / 填写区一行

Status: `done`

> 里程碑：M59（承接 M58 的参数输入体验）
> 日期：2026-10-13
> 范围：`gui/param_form.py`（主）、`tests/contract/test_gui_param_form.py`、
> `tests/contract/test_gui_expr_controls.py`（文案判据改现算）

## 一、起因（维护者原话）

> 「参数设置侧边栏，感觉要根据侧边栏宽度来设计参数名标签和填写区域，感觉放两行好一点？
> 参数名标签、py按钮、fx按钮放一行，参数填写放第二行，这样按钮名称还能写详细一点？你评估下」

三条意图：① 行形态改**两行式**（标签 + Py + fx 一行，控件一行）；② 按钮文案因此**可以写详细**；
③ 「你评估下」——按项目纪律（改既有设计取向 ⇒ 先只读取证、给取舍、拍板后才动手），本轮先出取证。

**维护者拍板（AskUserQuestion）**：

| 问题 | 选择 |
|---|---|
| 按钮位置 | **A：按钮跟标签同行**（输入框独占第二行） |
| 改动范围 | **全部字段**（含下拉 / 数字 / 布尔 / 元素库行，不只表达式行） |
| 控制流表单 | **一起改**（`if` / `forEach` / `try` / `return`） |

## 二、取证（动手前，5 支只读探针 + 1 支诊断，全部实测）

### 2.1 双列布局的「宽度税」——这是本次真正的痛点

`QFormLayout.addRow(label, widget)` 的**标签列按整张表最宽的标签定宽**，并从**每一行**扣除。
300px 侧边栏里固定吃掉 **88px**，于是带 `Py` + `fx` 的字段输入框只剩 **76px**：

```
browser.input 的「文本内容」：[Py 36px][输入框 76px][fx 69px]
                              —— 两个按钮加起来（105px）比输入框还宽
```

改成单 widget 横跨两列（标签在上、控件独占一行）后，同一字段的控件宽 **76px → 284px**（内容宽满宽）。

### 2.2 两行式的代价（量化后才敢做）

| 项 | 实测 |
|---|---|
| 每字段行高 | **+18px**（3 字段 100 → 154px） |
| 全字段表单高 | 约 **+25%**（`browser.input` 418 → 522px）；侧边栏有 `QScrollArea`，竖向富裕 |
| 最小宽 | `browser.input` **347 → 260px**；86 条命令**中位 260 → 136**、**最坏 347 → 241** |
| 变宽的命令 | **0 条**（两行式只会更窄，不会更宽） |

### 2.3 方向只能是「桌面向 Web 收敛」

Web 端**本来就是标签在上**：`devserver/static/styles.css` 里 `.field label { display: block }`
+ `.field input { width: 100% }`；桌面端那个「标签在左 + QFormLayout 双列」是它自己发明的。
Web 已被 **ADR 0016** 冻结 ⇒ 不能反过来让 Web 去对齐桌面。

### 2.4 按钮文案的硬预算（★ 本次最容易踩的坑）

`QLabel` 与 `QToolButton` 的 **`minimumSizeHint() == sizeHint()`** —— 两者都**不能**缩到
`sizeHint` 以下 ⇒ 标签行最小宽 = 标签 + 按钮们的**直接相加**。于是：

```
侧边栏默认 300px ⇒ 内容宽 284px
带按钮的字段里最宽的标签 = 88px（browser.input 的「文本内容 *」）
⇒ 两个按钮共分 284 - 88 - 8(间距) = 188px
QToolButton 平台地板 = 69px（Py / fx / 变量 实测都是 69）
⇒ 只剩 50px 的「文字预算」
```

实测文案宽度：「Py」69 / **「Python」117** / 「Python 表达式」165；「fx」69 / **「变量」69** /
「插入变量」93。「Python」花掉 48px（117−69）⇒ 刚好花光预算；再配「插入变量」还要 24px
⇒ **306px 溢出**，300px 下会被裁字。

**定档**：`_PY_BUTTON_TEXT = "Python"`、`_FX_BUTTON_TEXT = "变量"`；同时把 QSS `padding`
从 `1px 8px` 收到 `1px 6px`（两个按钮各省 4px）⇒ 88 + 117 + 69 + 8 = 282 ≤ 284，通过。
这段算式写进了常量注释，改文案前先算这笔账。

## 三、做法

### 3.1 字段束（`_field_bundle`，模块级函数）

```python
def _field_bundle(label: QLabel, control: QWidget) -> tuple[QWidget, QHBoxLayout]:
    bundle = QWidget()
    box = QVBoxLayout(bundle); box.setContentsMargins(0, 0, 0, 0); box.setSpacing(_BUNDLE_HEAD_GAP)
    head = QHBoxLayout(); head.setContentsMargins(0, 0, 0, 0); head.setSpacing(4)
    head.addWidget(label); head.addStretch(1)
    box.addLayout(head); box.addWidget(control)
    return bundle, head
```

- 调用方一律 `layout.addRow(bundle)`（**单个 widget、横跨两列**）取代 `addRow(label, widget)`。
- 返回 `(bundle, head)`——`head` 是标签行，供调用方往右端补挂按钮。
- `addStretch(1)` 把标签顶到左端、按钮贴右端（**I18 注入验证的正是这条**）。

### 3.2 表达式按钮搬到标签行（`_wrap_expr_row` → `_attach_expr_buttons`）

签名 `(self, name, editor, parent, head, *, allow_fx, allow_python)`；按钮建在 `head` 尾部。
顺序仍是 **Py 在前、变量在后**（与「先选模式、再插变量」的读法一致）。文案取两个新常量。
`parent` 传字段束本身（保证按钮不变成顶层窗口——旧判据「控件不得是顶层窗口」仍守着）。

### 3.3 全字段覆盖

- `_attach`：所有字段（文本 / 多行 / 下拉 / 数字 / 布尔 / 数组 / 对象）都走 `_field_bundle`。
- `_attach_element_row`：元素库行（combo + 标签 + 按钮）也走字段束，新增 `_element_row_bundles` 留档。
- `_build_output_aliases` / `_build_retry_timeout`：别名 / 超时 / 重试 3 处改字段束。
- `ControlNodeForm`：新增 `_add_field(label_text, widget)`，`if` / `forEach` / `try` / `return`
  四个分支共 9 处 `addRow(QLabel(...), w)` 全部改走它；并给它补了与 `ParamForm` 同构的
  外层 `QVBoxLayout` + `addStretch(1)`（即 M58 后续的「剩余高度落到表单底部」）。

### 3.4 门控标志 `expr_capable`（重构中唯一差点引入的真回归）

`_effective_type` 对 `{"type": "string", "enum": [...]}` **也返回 `"string"`** ⇒ 若在
`_attach_expr_buttons` 调用处写 `value_type == "string"` 判断，枚举字段会被挂上 Py 按钮，
而 `QComboBox` 没有 `textChanged` ⇒ 收集期报错。改为在**字符串分支内**置起 `expr_capable` 标志：

```python
expr_capable = False
...
elif value_type == "string":
    widget = ...; kind = _KIND_TEXT; expr_capable = True
...
if expr_capable and (allow_fx or allow_python):
    self._attach_expr_buttons(...)
```

★ 这是**重构引入的真回归**，被既有判据 `test_expr_controls_gated_by_manifest_declaration`
当场抓住（首跑红），不是事后自查发现的。

## 四、判据（`tests/contract/test_gui_param_form.py`，88 → **93**，+5）

| # | 判据 | 钉住什么 |
|---|---|---|
| 1 | `test_param_row_puts_label_above_control` | 标签在上、控件在下，且控件**满宽**（`editor.width() >= bundle.width() - 2`） |
| 2 | `test_expr_buttons_share_the_label_row` | 两个按钮与标签**同一 y**；`py.x < fx.x`；控件仍在下一行 |
| 3 | `test_control_form_rows_use_the_same_bundle_shape` | `ControlNodeForm("if")` 也是同一形状（束内标签在上、控件满宽） |
| 4 | `test_expr_button_text_fits_the_default_sidebar` | 扫**全部**带 fx/py 的命令，默认侧边栏宽下每个按钮 `width >= sizeHint`（**不裁字**）；末尾 `assert checked > 0` 防空转 |
| 5 | `test_real_commands_multiline_row_does_not_absorb_leftover_panel_height` | 扫真机 manifest 的 `x-multiline` 字段，束高 ≤ 自己的 sizeHint |

★ **第 5 条替换掉了首版的「元素库行不会被撑开」断言**：那条被负向验证 I18 判为**未命中**，
诊断脚本证明元素束（combo + 标签 + 按钮）**本来就不会**被撑开（`QPlainTextEdit` 的
`Expanding` 策略才是根因）⇒ 是**恒绿假判据**。换成扫真机 `x-multiline` 字段的版本后 I18 命中。
这正是 M58 特意避开的那类假判据又复现了一次（「覆盖『实现』≠覆盖『用户实际走的那条路』」）。

另一处连带修改：`test_gui_expr_controls.py::test_no_extra_var_button_remains` 原本硬编码
`{"Py", "fx"}`，改名后失效 ⇒ 改为从 `_PY_BUTTON_TEXT` / `_FX_BUTTON_TEXT` **现算**
（判据本意是「只有这两个按钮、没有多余的」，不是「文案是这两个字」）。

## 五、负向验证

### 5.1 新建 `.harness/spike/probe_m59_row_layout_negative.py` —— **4/4 精确命中**

| 注入 | 打的方向 | 期望红 | 实际 |
|---|---|---|---|
| **I15** | 字段束改**横排**（`QVBoxLayout(bundle)` → `QHBoxLayout(bundle)`） | 4（含「不裁字」连带） | 4 ✓ |
| **I16** | Py 按钮另挂**下一行**（`parent.layout().addWidget(py_button)`） | 1 | 1 ✓ |
| **I17** | 文案超预算（`"Python 表达式"`） | 1 | 1 ✓ |
| **I18** | 去掉弹性空隙（`addStretch(1)` → `addStretch(0)`） | 2 | 2 ✓ |

期望集**按推理补全**：I15 首版只写 3 条，实际 4 条——束改横排后标签 + 按钮 + 控件同排，
「按钮不裁字」判据也连带红，按推理补进期望集（不照抄实际）。

★ 探针自己抓出的两个问题（都靠既有防线拦下）：

1. **`_A_STRETCH` 裸锚点 `count == 2`**：`outer.addStretch(1)` 在 `ParamForm` 与
   `ControlNodeForm` 里**各有一处** ⇒ 必须带上后一行 `properties = schema.get("properties", {})`
   才唯一（且只该打 `ParamForm`，不能连控制流表单一起改）。锚点唯一性自检拦住。
2. **`_ATTACHED_WIDTH_MARGIN = 22` 太宽会漏注入**：I15 注入下横向布局里编辑器 101px、
   容器 195px（差 94）⇒ `>= 173` 不成立、判据不红 ⇒ 收紧到 `bundle.width() - 2`。

### 5.2 复跑 `.harness/spike/probe_expr_controls_negative.py`（M58 探针）—— **14/14 通过**

改造中门控从 `elif value_type == "string"` 里（12 空格）挪到**方法体层**（8 空格）
⇒ `_A_GATE` 锚点首跑 `count == 0`、探针 rc=1 中止。把锚点与注入文本都改成 8 空格后复跑
14/14 通过。**印证的纪律：重构会让别的探针失效，改了共享代码必须把相关探针全部复跑。**
（另：注入必须写成**合法语法的精准放宽**——`if True:` 会把整数/枚举/布尔控件一起塞进
`_attach_expr_buttons`，`QComboBox` 无 `textChanged` ⇒ 收集期就报错，判不出「打到哪条」。）

## 六、验证与门禁

- **定向**：`test_gui_param_form.py` **93 passed**。
- **全量**：`./.venv/Scripts/python.exe .harness/scripts/check_all.py` ⇒ 末行 **`FULL GATE PASSED`**、
  **rc=0**（本轮亲跑，沙箱豁免前台执行）；其中 pytest **1636 passed / 0 failed / 21 skipped / 2 xfailed**
  ——较 M58 门禁的 1630 为 **+6 = M58 后续判据 1 条 + 本轮 5 条**。
- **逐条取证**：6 组 Python 项（`ruff check .` + 5 个 `check_*.py`）+ **15 个 `check_*.mjs`**，
  共 21 条逐条 `rc=0`（日志 `.harness/spike/_m59_gate_items.log`）。
- **提交后同树复跑**：pytest 仍 **1636 passed / 0 failed**（**两次 passed 数一致**），但收尾被环境级打断、
  `GATE_RC=127`（只有收尾失败，用例全过）。⇒ 按纪律取「有界重跑 + passed 数一致 + 其中一次 `rc=0`」为绿，
  两条证据留档（`_gate_all_m59.log` / `_gate_all_m59_postcommit.log`）。
- 说明：全量 pytest **在沙箱内**收尾会被 shim 的批量删除守卫打断（`_check_bulk_delete_guard` →
  `SystemExit: 1`，`rc=1`；pytest 临时目录清理触发），属**已知环境级现象**，与 `rc=127` / `rc=139` **同源**；
  **豁免沙箱后**取到 `rc=0`，故本轮按「先确认豁免、再判挂账」的纪律取绿。

## 七、明确不动

- **Web 端**：ADR 0016 冻结面（本轮方向是桌面向 Web 收敛，Web 无需改）。
- **模型 / 运行期 / 编译器**：一行未改（`values()` / `expr_modes()` 语义不变）。
- **`[name]` 字面文本 vs chip**：有意分歧，不修。
- **多行输入区固定 68px 高**：M58 遗留，未动。

## 八、遗留

1. 枚举参数仍无变量入口（动 Web 属冻结面）；数字 / 布尔 / 数组 / 对象字段按设计不开放。
2. Web 端编辑态双缓冲仍未同步（冻结面）。
3. Web fx 浮层仍列 `steps.*` 与内置作用域组（M57 已登记）。
4. 画布摘要的**枚举值**仍是原值（M56 遗留）。
5. 按钮文案已接近预算上限（282 / 284px）——再想写详细必须**同时**缩短 Py 或接受侧边栏顶宽到 ~320px。

## 九、交付

- 代码：`src/rpa_core/gui/param_form.py`（`_field_bundle` / `_attach` 返回束与头部 /
  `_attach_expr_buttons` / `_PY_BUTTON_TEXT` + `_FX_BUTTON_TEXT` / `_BUNDLE_HEAD_GAP` /
  QSS padding 6px / `expr_capable` 门控 / `ControlNodeForm._add_field` + 外层 stretch /
  `_element_row_bundles`）。
- 判据：`tests/contract/test_gui_param_form.py`（+5）、`tests/contract/test_gui_expr_controls.py`（文案改现算）。
- 探针：新建 `.harness/spike/probe_m59_row_layout_negative.py`（4 注入）；
  修复并复跑 `.harness/spike/probe_expr_controls_negative.py`（14 注入）；
  只读探针 6 支（`_probe_param_row_widths` / `_two_row_cost` / `_min_width` / `_shapes` /
  `_shapes_all` / `_m59_layout` / `_m59_render`）+ 诊断 1 支（`_diag_m59_stretch`）。
- 文档：benchmark §四 P2.6、gap-matrix §六、本文件。
- 台账：`PROGRESS.md` + `feature_list.json` + 当日 memory。
