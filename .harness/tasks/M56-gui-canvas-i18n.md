# M56：GUI 画布中文化（i18n 中文名接入）

Status: `done`

> **2026-10-13 当日立项 → 当日落地**（画布卡片标题 + 参数摘要键名）。落实提交见文末。
> **ADR 0014 §9「后续切片边界」里列的「i18n 中文名接入」由本里程碑收口**——ADR 是历史
> 决策记录，本文不改写它，只在此登记该待办已落地，避免它继续以「未做」的形态挂着。
> 前置：M54（GUI 参数面板中文化，`i18n.js` 解析层已建）、ADR 0014、ADR 0016。

## 一、起因（维护者原话）

> 「画布中的指令，能显示成中文名吗」

**直答：能，而且管线早就预留了。** `flow_model.build_item(node, *, label=...)` 与
`build_model_from_workflow(workflow, *, label=...)` 都带 `label: 命令 id → 显示名` 参数，
docstring 写着「GUI 暂无 i18n 时直接用命令 id」；而 `app.py:2012` 调用时**没传**
⇒ 卡片标题取 `DisplayRole`，内容就是裸命令 id（`browser.input`）。

## 二、取证（动手前的现状）

| 项 | 现状 |
| --- | --- |
| 画布卡片标题 | `canvas.py:969` 画 `index.data(DisplayRole)` = `build_item` 的 `title`；因 `label` 缺省 ⇒ **裸命令 id** |
| 指令树 / Ctrl+P 命令条目 | **已是中文**（`app.py:391`、`:1333` 走 `load_command_display_names()`） |
| Web 画布与大纲 | **已是中文**（`app.js` 的 `commandName = id => I18N.commands[id] \|\| id`；`:1134` 标题、`:742`/`:1690` 树与大纲） |
| 参数摘要 | `summarize_args` 产出 `selector=#kw` —— **裸 schema 键名**，与 M54 修掉的参数面板漏点**同源** |
| 既有测试钉住 | 全仓**只有 1 处** `DisplayRole` 断言（`test_gui_canvas.py:156`，虚拟「结束 如果」行，不受影响）；其余一律断言 `ROLE_COMMAND_ID` ⇒ 零连带 |

### 2.1 三条产出路径（必须全覆盖）

| 路径 | 位置 | 场景 |
| --- | --- | --- |
| `build_model_from_workflow` | `app.py:2012` | 打开流程 / 撤销重做 / 重载 |
| `create_action_item` → `insert_command` | `flow_model.py:570/579/616` | 拖入 / 双击指令树 / Ctrl+P 插入 |
| `insert_subtree` | `flow_model.py:723` | 粘贴 |

只改一条 = 「有的卡片中文、有的还是英文」（M52 S3 的同款事故形态）。

### 2.2 `DisplayRole` 的四个消费者

画布标题 / 画布搜索 / Ctrl+P 节点跳转条目 / 运行日志与悬浮窗「正在执行：<标题>」。
其中**画布搜索文本已含 `ROLE_COMMAND_ID`**（`app.py:1232`）⇒ 标题改中文后
**英文 id 仍可搜**、并新增中文可搜（**不退化**，有判据钉住）。

## 三、做法（一处权威 ⇒ 三个产出路径自动一致）

- `flow_model.command_label(command_id)` = `gui/i18n.load_command_display_names().get(id, id)`。
  **不另建映射表**：在 Python 再抄一张就是第二套权威（加命令要改两处、漂移无症状——M54 的
  `CONDITION_OP_LABELS` 就是前车之鉴）。
- `build_item` 的 `label` **缺省改为 `command_label`**（而不是在三个调用点逐个传参）
  ⇒ 装载 / 新增 / 粘贴 / 撤销重做**自动一致**，新增产出路径也不会漏。
- `summarize_args(with_args, limit=2, *, command_id=None)`：键名走 `gui/i18n.field_label`
  （命令级覆盖 → 公共键 → 键名原文）；优先级排序仍按**原始键名**判，不动展示顺序。
- **两处摘要重算点必须喂 `command_id`**：`app.py:3187`（元素引用）与 `app.py:4403`
  （参数应用）。漏喂 ⇒「改完参数、摘要退回裸键名」——纯函数对、调用方没用上。

## 四、判据（`tests/contract/test_gui_canvas_i18n.py`，7 条）

1. 标题 = 中文名，且裸 id 不再出现在任何标题里；
2. 未知命令回退 id 原文（不假装有中文）；
3. 摘要键名 = 中文标签（含 M54 的口径「操作目标」）；
4. **命令级覆盖优先**：`data.setVar.varName` →「变量名」，公共键 fallback 是「接收变量名」；
5. ★ **三条产出路径逐字节相等**（标题 + 摘要）——含**字面独立证人**
   `"操作目标=#kw  文本内容=hello"`，因为只用 `summarize_args` 算期望值会与实现**同源**、
   纯函数整体失效时断言恒真（M54 探针抓到过同款假绿灯）；
6. **AST 闸**：`app.py` 里每一处 `summarize_args` 调用都必须带 `command_id`；
7. 画布搜索文本仍含英文 command id（防「改中文后老用户按 id 搜不到」）。

**连带同步**（既有判据断的是旧口径，非缺陷）：
`test_gui_canvas.py` 2 处（摘要键名 ⊆ 参数字典、`path=a.txt` 字面串）、
`test_gui_param_form.py` 4 处（`url=`/`selector=`/`browserType=` 字面串）。

## 五、负向验证（`.harness/spike/probe_m56_canvas_i18n_negative.py`）

8 注入，**全部精确命中**；逐字节还原核 md5 一致、还原后复绿 **200 passed**。

| # | 注入点 | 打中的判据 |
| --- | --- | --- |
| I1 | `command_label` 恒返回 id | 标题判据 2 条 + 搜索判据 1 条（中文名不可搜） |
| I2 | `build_item` 标题回 `command_id` | 同上（**纯函数对、界面没用上**） |
| I3 | 摘要键名回裸键 | **9 条**（跨 5 个判据文件） |
| I4 | 摘要丢 `command_id` | 命令级覆盖判据 1 条（**精准**） |
| I5 | `app.py` 参数应用路径丢 `command_id` | AST 闸 1 条 |
| I6 | `app.py` 元素引用路径丢 `command_id` | AST 闸 1 条 |
| I7 | `create_action_item` 走恒等 label | **三路一致**判据 1 条（单条产出路径漂移） |
| I8 | 搜索文本摘掉 `ROLE_COMMAND_ID` | **4 条**（含两条按 `"data."` 前缀搜的既有用例） |

**首轮 I1/I2/I8 报「多红」**，取证后确认**全是期望集推理不完整、非真缺陷**：
I1/I2 的连带来自搜索判据同时断言「中文名可搜」；I8 的两条既有用例正是
「按英文 id 搜索」的契约面（`_on_canvas_search_changed("data.")`），摘掉 command id 必然全红。
修正期望集后 8/8 通过。

## 六、验证与门禁

- 全量 pytest **1597 passed / 0 failed / 21 skipped / 2 xfailed**，rc=0
  （较 M54 的 1590 正好 +7 = 本次新增判据数）。
- `ruff check .` → `All checks passed!` rc=0。
- 5 组 Python 静态门禁**逐条单跑**全 rc=0：`check_architecture`（79 py / 86 manifests）、
  `check_tasks`（81 features, 1 active task）、`check_param_consumption`、`check_error_contract`、
  `check_command_matrix`（86 条命令，死参数 0 / 实现缺口 0）。
- **15 个 `check_*.mjs` 全 rc=0**（`check_all.py` 首条非零即退出，故一律逐条单独取证）。
- 全部改动文件 **CRLF=0（全 LF）**。

## 七、明确不动

- **Web 编辑器一行未碰**（ADR 0016 冻结）。`i18n.js` 本次**零改动**——
  `commands`/`fields` 两个块 M54 时已齐全，画布只需要读它。
- 元素槽位**键名**（`selector`/`locator`/`elementId`）、扩展线协议（不 bump `EXT_BUILD`）、
  ADR-002 的 ref-only 口径。

## 八、遗留（**维护者 2026-10-13 明确「本轮不做」，非遗忘**）

1. **摘要的枚举值仍是原值**：如 `点击方式=single`。`x-enum-labels` 在**manifest** 里，
   而 `summarize_args` 是 `flow_model` 的纯函数、拿不到 manifest ⇒ 要中文化需把 manifest
   解析器接到卡片摘要（成本高于收益）。注意**参数面板的枚举下拉早已是中文**（M54 G3）。
   ⇒ 维护者 2026-10-13 拍板：**本轮不做**（不是漏了）。若将来要做，落点是在
   `summarize_args` 增加一个「枚举标签解析器」参数（由 `app.py` 用 catalog 提供），
   而不是让 `flow_model` 反向依赖 catalog。
2. **卡片无英文副标**：维护者选「只中文名（跟 Web）」。英文 id 靠画布搜索与
   Ctrl+P 条目的 `command_id · executor` tip 可达，卡片上不显示。

## 九、交付

| 提交 | 内容 |
| --- | --- |
| `3b9aa08` | M56 主体：`flow_model` / `app.py` 接线 + 判据 7 条 + 探针 + 台账 + 连带测试同步（10 files, +684/−21） |
| `849e96b` | 本文件 §八 补记维护者对遗留项的明确决定 |

推送：`push origin main` ⇒ `653c8c8..849e96b`，rc=0（`main...origin/main` 已同步）。

**注意**：`i18n.js` **本次零改动**——`commands` / `fields` 两块的中文名 M54 时已补齐，
画布只需要读它。因此本里程碑**不涉及** `test_editor_i18n.py` 的四契约同步面。

---

## 十、追加：卡片参数摘要的可读性（2026-10-13 同日，维护者反馈）

**维护者原话**：「画布中指令，灰色的参数小字有点看不清楚，看是不是调大字号。另外有的指令
有个 `+1`、`+2` 的后缀显示，是什么意思？」⇒ 两个问题，一个改、一个删。

### 10.1 取证（只读，动手前）

- 卡片由 `canvas.py::CardDelegate.paint` 自绘：标题 `BASE_PX`(12px) 粗体，
  摘要走 `fonts.mono_font(option.font)` —— 而 `mono_font` **写死 `MONO_PX`(10px)**，
  即摘要比标题小 2px 且是全应用最小字号；颜色 `TEXT_SECONDARY`(#64707d) 对白底
  对比度 **5.05:1**（刚过 WCAG AA 的 4.5）。
- **`SUB_PX` 是「死 token」**：它的注释写着「次级说明（**画布参数摘要**、命令面板副标题等）」，
  但全仓**零处使用** —— 摘要实际用了语义不符的 `MONO_PX`（那个是给命令 id / 快捷键一览用的）。
  所以「摘要太小」不只是取值问题，是**用错了 token**。
- `+N` 来自 `summarize_args` 末尾的 `extra = len(with_args) - limit`：摘要最多展示 2 对，
  其余折成 `+N`（如 5 参数的 `browser.input` → `操作目标=#kw  文本内容=hello  +3`）。
- **Web 端本来就没有这个后缀**：`devserver/static/app.js::summarize` 直接
  `JSON.stringify(with)` 全部参数 —— 删掉它反而**收敛**了两端口径。
- 行高预算充足：`_ROW_HEIGHT = 46`，11px 摘要不会挤（原 10px 也只是留白多）。

### 10.2 维护者拍板（选项卡）

1. 字号：**只调大到 11px**（不改颜色）。
2. `+N`：**不再展示** ——「解释成本太高」。

### 10.3 改动

| 文件 | 改动 |
| --- | --- |
| `gui/fonts.py` | `SUB_PX` 10 → **11**（并补上「为何是 11」的注释）；`mono_font` 新增 **`size` 关键字参数**（缺省仍是 `MONO_PX`，快捷键一览等既有消费面不变） |
| `gui/canvas.py` | 摘要改走 `fonts.mono_font(option.font, size=fonts.SUB_PX)` —— 让死 token `SUB_PX` **名实相符**，而不是去动 `MONO_PX`（那会顺带放大快捷键一览） |
| `gui/flow_model.py` | 删掉 `extra` / `+N` 追加段；docstring 记明**为何删**（维护者拍板 + Web 本来就无此后缀） |

**为什么字阶走 `SUB_PX` 而不是 `MONO_PX`**：两者当前值都会是 11，差别在**语义与联动** ——
`MONO_PX` 属于「命令 id / 快捷键」这一档，摘要把它借来用正是当初「用错 token」的根因；
`SUB_PX` 才是次级说明这一档。同时 `mono_font` 缺省行为不变 ⇒ 快捷键一览维持 10px，
**改动面严格限定在维护者要求的那一处**。

### 10.4 判据（+3，全部落在 `tests/contract/test_gui_fonts.py`）

1. `test_mono_font_accepts_explicit_size` —— **纯函数层**：`size=` 能落到指定字阶、
   缺省仍是 `MONO_PX`（防「显式字号改了族名」与「缺省被顺手改掉」）。
2. `test_summary_font_is_at_least_11px` —— **需求锚**：`SUB_PX >= 11` 且 `< BASE_PX`
   （次级说明仍要比基准小一号）。将来谁再调小，必须是有意识的行为。
3. `test_canvas_summary_uses_sub_px_not_mono_px` —— **接线层**：`canvas.py` 必须出现
   `size=fonts.SUB_PX` 且不得再有 `fonts.mono_font(option.font)`。
   ★ 纯函数层绿 ≠ 画布真的用了它 —— 正是「两层各判一次」才拦得住静默退回 10px。

连带同步既有判据 3 处（`test_gui_canvas.py` / `test_gui_param_form.py` 里原本断言
`+1` / `+2` 出现的地方，改为断言「**没有** `+`」与两对的确切文本）。

### 10.5 负向验证（`.harness/spike/probe_summary_readability_negative.py`）

4 注入**全命中、期望集与实际集逐项相等**、逐字节还原核 md5、复绿 132 passed：

| # | 注入 | 期望红 |
| --- | --- | --- |
| I1 | `canvas.py` 摘要退回 `mono_font(option.font)`（丢 `size`） | `test_canvas_summary_uses_sub_px_not_mono_px` |
| I2 | `mono_font` 忽略 `size`（恒 `MONO_PX`） | `test_mono_font_accepts_explicit_size` |
| I3 | `flow_model` 把 `+N` 加回去 | `test_summarize_args_limits_pairs_and_truncates` + `test_summarize_args_limit_respected` |
| I4 | `SUB_PX` 调回 10 | `test_summary_font_is_at_least_11px` |

探针带三样自保：哨兵自检（带注入即拒绝运行）、锚点**唯一性**自检（`count == 1`，
防注入落到别的函数上）、`try/finally` 逐字节还原。

### 10.6 门禁

`FULL GATE PASSED`（rc=0）：pytest **1600 passed** / 0 failed / 21 skipped / 2 xfailed
（较 M56 主体的 1597 正好 **+3 = 本次新增判据数**）+ ruff + 5 组静态 + 15 个 `check_*.mjs`。

### 10.7 顺带改正的过期记录

- `.harness/tasks/M31-gui-crossplatform-audit.md` 表 B 的 **B4** 行原写着
  「`+N` 是否出现得比 Win 早」——该项已随行为移除，标注作废。
- `.harness/adr/0014-editor-desktop-shell.md` §10 里「前 2 个参数 + `+N` 计数」是
  **当时的视觉契约记录**，按本仓惯例 ADR 不改写，在此登记**已被本节取代**。
- `.harness/PROGRESS.md` 的 `2026-09-15 | desktop-gui-slice2` 行是历史记录（append-only），
  保留原样。

