# M54：GUI 参数呈现补完（字段中文化 + 元素槽位口径）

Status: `planned`

> **进度：2026-10-12 拍板，G1~G4 当日全部落地**（结果与证据见 §六「实施结果」）。
> 状态仍取 `planned`——`check_tasks.py` 要求全仓唯一 `Status: active`，该位已被 M52 占用，
> 台账切换由维护者决定，本文不擅自占用。
>
> **2026-10-12 重评估**：原 M54（`element-slot-completion`）的落点被维护者一句
> 「**web 的不用改，重点做 GUI**」推翻，本文是重评估后的版本。三个结论：
> ① 原方案 2（补完 Web 前端引用模型）**作废**——它与 ADR 0016（Web 编辑器冻结、
> 不再作为功能对齐目标）本就冲突，是原方案的取向错误；
> ② 原方案 3（槽位键统一）**作废**——它必须同步改 Web 硬编码（`app.js` 三处）
> 与执行器接口，与「web 不改」直接冲突；
> ③ **真正的缺口在 GUI，而且是最基础的一层**：**GUI 参数面板的字段标签是裸英文键名
> （`selector` / `sessionId` / `timeoutMs`…），全仓 86/86 命令、88 个键、302 处**，
> 而 Web 侧有完整中文层（`i18n.js` 的 `fields` + `commandFields`）——GUI 一个都没用。
>
> 前置：`adr-001-element-reference-model.md`、`adr-002-element-ref-only.md`、
> `.harness/adr/0016-gui-primary-direction.md`、M23（GUI UX parity）、M12（Web 中文化）。

## 一、重评估结论（一句话）

维护者的观感「填写输入框还带着 `selector` 参数」**在 GUI 上就是字面事实**：
GUI 参数面板那一行的标签打出来就是 `selector`（裸键名，见 §2.1 实测）。
Web 侧反倒显示「选择器」——因为中文化层（M12）当年只落在 Web，GUI 从头到尾没有。
**修法 = 给 GUI 补同一份中文层（复用 `i18n.js`，不新增第二套权威）**，而不是改键名、
更不是改 Web。

## 二、取证

### 2.1 ★ GUI 参数面板的字段标签 100% 是裸键名

`gui/param_form.py::_attach` 的 label 构造只有一行：

```python
label_text = f"{name} *" if name in self._required else name
```

`name` 就是 JSON Schema 的键名。实测（offscreen 渲染全部 manifest）：

| 指标 | 值 |
| --- | --- |
| 有裸键名标签的命令 | **86 / 86** |
| 去重后的键名 | **88 个** |
| 裸键名标签总出现 | **302 处** |
| 出现最多的 | `sessionId`(62) / `timeoutMs`(32) / `selector`(16) / `elementId`(14) / `path`(8) / `value`(7) |

`browser.input` 的实际 QLabel 文本：

```
sessionId / selector * / 元素 / text * / timeoutMs / mode / append / pressEnter /
keyIntervalMs / clickBeforeInput / postDelayMs / 运行设置 / 超时（秒）
```

——只有 `运行设置` / `超时（秒）` / `元素` 三处是中文（硬编码），其余全是键名原文。

### 2.2 Web 侧有完整中文层，GUI 只消费了其中的「命令名」一块

`devserver/static/i18n.js` 的分层与消费面：

| 块 | 内容 | 谁在用 |
| --- | --- | --- |
| `commands`（86 条） | 命令 id → 中文显示名 | **Web + GUI**（`gui/command_palette.py` 正则解析，`app.py` 四处调用） |
| `fields`（82 键） | **字段键名 → 中文标签** | **只有 Web**（`app.js:1857 fieldLabel`） |
| `commandFields`（13 命令 / 27 键） | 同名参数按命令覆盖标签（如 `args`） | **只有 Web** |
| `ops`（8 键） | 条件操作符中文 | Web + GUI（GUI **硬编码抄了一份** `CONDITION_OP_LABELS`，见 §2.4） |
| `kinds` / `effects` / `flow` / `branchLabels` / `glossary` | 各类展示名 | Web（部分） |

⇒ **GUI 拿到的是「命令叫什么」，没拿到「参数叫什么」。** 这就是 `selector` 裸露的直接原因。

### 2.3 GUI 已有的中文化只有两块，且都很小

1. **枚举值**：`x-enum-labels` —— 全仓只有 **3 个** manifest 声明（`browser.navigate`、
   `data.log`、`data.setVar`），其余命令的枚举值在 GUI 里显示英文原值
   （`single`/`double`/`left`/`middle`/`visible`/`detached`…，同 `manifest-param-config.md` 的 P1-5）。
2. **分组标题**：`x-param-groups` 的 `label` —— 全仓只有 **1 个** manifest 声明
   （`browser.navigate`：「常规」/「高级」）。

### 2.4 一处「两套权威」的存量隐患（顺带记录）

GUI 的 `CONDITION_OP_LABELS`（`param_form.py:802`）是**手抄** `i18n.js` 的 `ops` 块，
注释也承认「与 devserver/static/i18n.js 的 ops 表一致」。加一个操作符要改两处、
且没有门禁钉住两处相等 ⇒ 漂移无症状。属于本方案可顺手收敛的一项（G4）。

### 2.5 为什么「改键名」不是解法（原方案 3 作废的取证）

`selector` / `locator` 这两个字不只是「参数键」，它们同时是：

1. **执行器接口**：`executors/browser.py` 读 `inputs.get("selector")`；扩展线协议
   `page.call` 载荷也是 `{"tabId","selector",…}`（`_ext.page_call(tab_id, selector, method)`）；
2. **Web 前端硬编码**：`app.js:1798`（字段旁元素下拉判断 `key === "selector"`）、
   `app.js:2690`（`setWith(node, "selector", …)`）、`app.js:2692`（`"locator"`）；
3. **元素文档取值路径**：`runtime/element_refs.py::_VALUE_BY_KEY` 的元组首项。

改键名 ⇒ 上面三处都得跟着改，其中 2 就是「改 Web」。
**结论：在「web 不改」的前提下，差别只能在「显示层」做，不能在「键名」做。**
（这也顺带回答了「为什么不能叫 `elementId`」：浏览器腿没有句柄，见原方案 §1.3 的取证。）

## 三、原 M54 三项方案的处置

| 原方案 | 处置 | 理由 |
| --- | --- | --- |
| 2｜devserver 前端纳入引用模型 | **作废** | 与 ADR 0016「Web 编辑器冻结、不再为它补能力」冲突；维护者亦明确「web 不用改」。其取证保留在文末§附，降为**已知差异**登记。 |
| 3｜槽位键统一（`selector`/`locator` → 中立键） | **作废** | 必须同步改 Web 硬编码三处 + 执行器接口，与「web 不改」直接冲突（§2.5）。**差别改到「显示层」即可覆盖用户观感**。 |
| 4｜浏览器句柄层（`browser.findElement → elementId`） | **保留，另立里程碑** | 纯新增（对齐影刀 #20/#24/#25），不碰 Web；混在「命名/一致性」里做会重演 M51/M52 分界的顾虑。 |

## 四、GUI 侧方案清单

| 方案 | 内容 | 影响面 | 契约/存量 |
| --- | --- | --- | --- |
| **G1｜字段标签中文化**（推荐 · 核心） | GUI 参数面板 label 走 `i18n.js` 的 `commandFields[命令] → fields[键] → 键名` 三级回退（与 Web `fieldLabel` 逐字同口径） | `param_form.py::_attach` 一处 + 新增 i18n 解析（复用 `command_palette.py` 的正则模式） | 零契约改动（纯展示层）；`test_required_field_label_has_star` 需同步改（它断言 `startswith("browserType")`） |
| **G2｜元素槽位口径**（拍板：**跟影刀「操作目标」**） | 元素槽位在 GUI 里叫「**操作目标**」，不是 Web 的「选择器」/「定位器」；并按拍板「元素都是引用态」理顺两行——**引用态**行标签=`操作目标`、值=`元素名`（去掉了原先重复的「元素：」前缀），值行整体隐藏；**非引用态**元素行标「元素库」（那时值行才叫「操作目标」，两行同名会分不清哪行填什么） | `gui/i18n.py` 的 `GUI_LABEL_OVERRIDES` + `param_form` 元素行两处 | 零契约；**不改 `i18n.fields.selector`**（那是 Web 的键，web 不用改）——口径分歧登记在 GUI 层并加判据钉住，防无声漂移 |
| **G3｜枚举值中文化扩容** | 目前只有 3/86 个 manifest 有 `x-enum-labels`，其余在 GUI 里是英文原值 | manifest 若干（`clickType`/`button`/`state`/`selectBy`…） | `x-enum-labels` 是既有机制，扩展零风险 |
| **G4｜ops 标签去重** | GUI 删掉硬编码 `CONDITION_OP_LABELS`，改读 `i18n.js` 的 `ops` | `param_form.py` 一处 + 契约测试 | 消掉一处「两套权威」 |

### G1 的覆盖率（现算）

`fields`（82 键，含一处重复键 `text`——JS 后者覆盖前者，属既有小瑕疵）+ `commandFields`
（13 命令 / 27 键）合并后，对全仓 **300 处**「命令 × 字段」出现的覆盖是 **282 / 300**，
剩 **18 处** 无中文（真空白）：

- `data.table.*` 六个命令的 `flowDir` / `table`（12）
- `browser.closeTabs` 的 `tabIds` / `all` / `ignoreBeforeUnload`（3）
- `browser.closeBrowser` 的 `force`（1）
- `desktop.attachWindow` / `desktop.win32.attachWindow` 的 `operationTimeoutMs`（2）

⇒ G1 落地时**顺手补这 18 个键**（写进 `i18n.js` 的 `fields` 或对应 `commandFields`），
否则那些字段会「回退成裸键名」，而 GUI 的可见性会让它比 Web 更扎眼。

## 五、实施切片（全部已落地，见 §六）

- **S0 ✅**：`test_gui_i18n.py::test_param_form_labels_come_from_i18n` 钉住面板渲染出的标签，
  并带一组**字面锚点**（`会话 ID` / `操作目标 *` / `超时（毫秒）` / `文本内容 *`）——
  这一组是必须的：负向验证 I1 实测到，若期望值与实际值**同源**（都经 `field_label`），
  中文化层整体失效时两边一起退化成裸键名 ⇒ 判据恒真（假绿灯）；字面锚点就是那条独立证人。
  `test_required_field_label_has_star` 的断言已从 `startswith("browserType")` 改成
  「`field_label` 取到的中文标签 + ` *`」（不硬编码中文）。
- **S1 ✅**：新增 `gui/i18n.py` 作 i18n.js 的**唯一解析入口**（commands/fields/commandFields/ops
  四块，括号计数法取块），`param_form._attach` 的 label 走三级回退，**英文键名降为 tooltip**；
  解析失败一律回退原文（不假装有中文）。`command_palette.load_command_display_names`
  改为同名 re-export，调用面零改动。
- **S2 ✅**：`selector`/`locator` → 「操作目标」（`GUI_LABEL_OVERRIDES`）；引用态元素行
  标签=`操作目标` + 值=元素名，非引用态标签=`元素库`；`元素：xxx` 的重复前缀去掉。
- **S3 ✅**：`test_every_manifest_field_has_chinese_label`——**每个 manifest 的每个字段**
  都要有中文标签（只比 `required` 是不够的，而 GUI 会把 properties 全渲染出来）；
  另加 `test_every_enum_field_has_value_labels`（enum ⇄ `x-enum-labels` **键集合相等**，
  多一个是死标签、少一个会露英文）。
- **G3 ✅**：24 个 manifest 共 **31 处** `x-enum-labels` 补齐（详情见 §六）。
- **G4 ✅**：`CONDITION_OP_LABELS` 改为读 `i18n.js` 的 `ops`，删掉手抄的那一份。

## 六、决策点（2026-10-12 拍板）与实施结果

| # | 决策点 | 拍板结果 |
| --- | --- | --- |
| 1 | G1 字段中文化做不做 | **做**（复用 `i18n.js` 同一份，不新建第二套权威） |
| 2 | 中文口径跟谁 | **跟影刀「操作目标」**（而不是 Web 的「选择器」/「定位器」） |
| 3 | G2 / G3 / G4 是否并做 | **全做** |
| 4 | 元素槽位呈现 | 维护者口径「**元素都是引用态**」⇒ 按引用态为准设计，**不为非引用态重排行结构** |
| 5 | 原方案 4（浏览器句柄层） | **未拍板**（本文建议另立里程碑；本轮未做） |

### 实施结果（2026-10-12，G1~G4 全部落地）

**新增**

- `src/rpa_core/gui/i18n.py`——`i18n.js` 的**唯一解析入口**（`commands`/`fields`/
  `commandFields`/`ops` 四块；括号计数法取块，容忍嵌套）。字段标签三级回退
  `commandFields[命令][键] → fields[键] → 键名原文`，最后套 `GUI_LABEL_OVERRIDES`。
  解析失败**回退英文原文**（与 Web `fieldLabel` 同口径，不假装有中文）。
- `tests/contract/test_gui_i18n.py`——9 条判据：解析层 2 / 三级回退 2 / 元素槽位口径 1 /
  ops 单一来源 1 / 全覆盖门禁 2（字段 + 枚举）/ 面板接线 1。
- `.harness/spike/probe_m54_gui_i18n_negative.py`——7 个注入的负向验证探针。

**改动**

| 文件 | 内容 |
| --- | --- |
| `gui/param_form.py` | `_attach` 的 label 走 `field_label`（英文键名降 tooltip）；`_apply_element_state` 的元素行标签两态（`操作目标` / `元素库`）+ 去掉 `元素：` 前缀；`CONDITION_OP_LABELS` 改读 `load_op_labels()` |
| `gui/command_palette.py` | 命令名解析改为从 `gui.i18n` 同名 re-export（原自带正则删掉），调用面零改动 |
| `devserver/static/i18n.js` | `fields` 补最后 **7 个**此前无中文的公共键：`all` / `tabIds` / `ignoreBeforeUnload` / `force` / `operationTimeoutMs` / `flowDir` / `table`（**共享事实源，两端同时受益**；Web 此前回退英文原文） |
| `commands/**` 24 个 manifest | 补 **31 处** `x-enum-labels`（G3）：`clickType`/`button`/`clickPosition`/`matchMode`/`selectBy`/`state`/`mode`/`position`/`infoType`/`direction`/`setWay`/`operation`/`matchBy`/`browserType`/`action` |
| `tests/contract/test_gui_param_form.py` | `test_required_field_label_has_star` 改断言中文标签 |
| `tests/contract/test_gui_panels.py` | 引用态断言 `元素：searchBox` → `searchBox` + 行标签 `操作目标`／摘引用后 `元素库` |

**判据与负向验证**

- 7 个注入**全部精确命中**（期望红 = 实际红）：I1 `field_label` 恒返回键名 → 6 红；
  I2 元素槽位口径改回「选择器」→ 3 红；I3 ops 脱钩 → 1 红；I4 面板不接线 → 2 红；
  I5 `i18n.js` 少一个字段标签 → 1 红；I6 枚举多一个死标签 → 1 红；I7 枚举少一个键 → 1 红。
  逐字节还原核 md5 一致、还原后复跑 150 passed 绿。
- **探针抓到我自己写的判据缺陷**（最有价值的记录）：`test_param_form_labels_come_from_i18n`
  首版只有「期望值 == 实际值」的比对，而**期望值也来自 `field_label`** ⇒ I1 下两边同时
  退化成裸键名、判据恒真（「只比相等」的假绿灯形态）。补一组**字面锚点**（独立证人）后才精确命中。

**门禁**（2026-10-12）

- pytest 全量 **1590 passed / 0 failed / 21 skipped / 2 xfailed**；收尾崩溃 rc=127 与 139
  （两次 passed 数一致）⇒ 按本项目判据属**退出阶段环境级崩溃**，逐条单独取证补全：
  ruff `All checks passed!`；`check_architecture`（79 py / 86 manifests）、`check_tasks`
  （81 features, 1 active task）、`check_param_consumption`、`check_error_contract`、
  **`check_command_matrix`（86 条命令，死参数 0 / 实现缺口 0 / l2 块 109）** 全 rc=0；
  15 个 `check_*.mjs` 全 rc=0。**`check_command_matrix` 必须单跑取证**——它排在清单第 7 条，
  会被 pytest 那步的收尾崩溃遮住（`check_all.py` 首条非零即退出）。

**明确未做**

- 原方案 4（浏览器元素句柄层 `browser.findElement → elementId`）——另立里程碑候选，见 §三。
- `desktop` 命令的元素槽位参数是 **`elementId`**（现显示「元素 ID」），它**不是引用键**
  （`element_refs._VALUE_BY_KEY` 只有 `selector`/`locator`）⇒ 没有元素库行，本轮未动其文案。
- Web 编辑器一行未碰（`i18n.js` 只**增**键，未改任何既有条目）。

## 七、明确不动

- **Web 编辑器**（`devserver/static/*`）——冻结（ADR 0016）；本方案不碰它一行。
- **槽位键名**（`selector` / `locator` / `elementId`）——执行器接口 + 扩展线协议 + Web 硬编码，
  三者同名（§2.5）。
- **扩展线协议** ⇒ **不 bump `EXT_BUILD`**。
- **ADR-002 的 ref-only 口径**（元素引用即唯一真值源）——本方案只改「标签怎么显示」，
  不改「值从哪来」。
- `model/selector_ranking.py` 与桌面定位模型。

## 附：原方案的取证（保留存档）

原 M54 认定「ref-only 只落地在 PySide 一条腿上」，并取证 Web 前端三条写入路径
（`app.js:1828` 字段下拉 / `:2690,2692` 元素库插入 / `:2873` 捕获）全部写值快照、
`elementRefs` 在 `app.js` 出现 0 次、`insertElement`/`element-pick` 在测试里命中 0 次。
**这些取证仍然成立**，只是按 ADR 0016 + 维护者「web 不用改」的拍板，
**降为已知差异**（Web 编辑器与 GUI 在元素引用上语义不同；Web 端产出「快照 + 引用」并存
的数据会被 `devserver/app.py:234` 的 `model_validate` 拒绝——该行为本身是**正确的保护**，
只是 Web 端没有摘引用入口，用户需回 GUI 处理）。不在本方案范围内。
