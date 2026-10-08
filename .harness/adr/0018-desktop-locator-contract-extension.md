# ADR 0018：桌面 locator 契约扩展（path 字段 / 控件级 matchMode / 锚点起步）

- 状态：**已接受（2026-10-08）**
- 日期：2026-10-08
- 关联：ADR 0003（Windows 桌面驱动）、ADR 0001（元素引用模型，`.harness/adr` 之外的 `docs/adr-001-element-reference-model.md`）、`docs/yingdao-gap-catchup.md` §4 M48、`M48-desktop-contract.md`
- 目的：为「桌面节点树 + 控件级匹配方式 + 锚点起步」三件事定契约，一次改完摊薄固定成本。

## 1. 背景与授权

M44 公开承诺「捕获即编辑**不动执行器契约**」（`M44-capture-edit-in-place.md`）。M48 的
D1/D2 恰恰要动桌面 locator 的执行器消费面，属**有意破戒**。

**破戒授权已获**（2026-09-29，记录于 `.workbuddy/memory/2026-09-29.md`）：
> M48 破戒已获授权（locator 可加 path 等字段）。

编号口径 2026-10-08 定案：**M48 保持「桌面契约一片」**，不重编号（授权锚点依赖此号）。

**现状实测**（2026-10-08，`desktop_agent.py` / `desktop.py` / `desktop_win32.py` /
`model/desktop.py` / `gui/element_editor.py`）：

| 项 | 现状 |
| --- | --- |
| locator 模型 | `DesktopLocator`（`extra="forbid"`）字段：`backend` `automationId` `controlType` `name` `title` `className` `classNameRe` `handle` `controlId` `menuPath` `foundIndex`；**无 path / 无 anchor** |
| agent 回传 | `_describe_info` 的 `selector.locator` 只带 `backend`/`controlType`/`automationId`/`name`；`metadata` 带 `controlType`/`automationId`/`name`/`className`/`windowHandle`/`windowTitle`。**祖先链在 `_window_scope_hit` 的 DFS 里天然存在但未回传** |
| uia `_find` | 只有 `control_type`/`title`（=locator.name）/`automation_id` 三个判据，**全部等值** |
| win32 `_find` | `title`/`class_name`/`class_name_re`（正则 search）/`control_id`/`found_index`，**除 classNameRe 外全部等值** |
| matchMode | 只作用于 `attachWindow` 的窗口 `title`（`desktop.py` L773 写死「className 恒等值」） |
| GUI 字段表 | `LOCATOR_FIELDS_BY_BACKEND`：uia = controlType/automationId/name；win32 = title/className/classNameRe/controlId/foundIndex |

## 2. 决策

### D1 —— locator 新增 `path` 字段（祖先链）

1. **字段形状**：`path: list[LocatorStep] | None`，`LocatorStep` 为有序的「**根窗口之下**
   到目标控件的容器级」层级描述（**不含目标本身、也不含根窗口自身**；目标仍由现有字段定位——
   path 是**收窄搜索域**的过滤条件，不是替代定位器）。每一步至少给一个可用于匹配的键。
   > **口径更正（2026-10-08 真机复验）**：初稿写的是「从根窗口到目标控件」。真机复验证明
   > **根窗口这一级必须不进 path**——UIA 里窗口不属于自己的后代，而执行器 `_narrow_by_path`
   > 规定「在候选的后代里找这一级」⇒ 留着根级会让**每条真实 path 在第一级收窄为空**、定位恒失败。
   > 定案修在**产侧**（`desktop_agent._path_steps_from` 剥根级；`path` 语义定为
   > 「根窗口**之下**的容器级」），执行器谓词保持「只看后代」不放宽。
2. **向后兼容**：`path` 可选。出现时校验每级形状（非空、每级至少一个键）；不出现时行为与今天
   **完全一致**（旧元素文档零迁移）。
3. **agent 侧**：`_describe_info` 把 `_window_scope_hit` 已算出的祖先链带出，落
   `selector.path`（与 M44 S1 的 `selector.path` 命名对齐，但**语义不同**：那里是 css 路径后缀，
   这里是控件祖先链——文档必须写明，避免混淆）。
4. **执行器侧**：uia / win32 各自 `_find` 增加「按 path 逐级收窄」的过滤——**这是本片最重的改动**，
   要沿「模型 → uia → win32 → manifest → 契约四条」全链各做一次负向验证。
   > **2026-10-08 真机复验补充**：收窄谓词**只找候选的后代**（不放宽成「含自身」），
   > 因为捕获侧已保证 `path` 不含根窗口级（见上面 D1 第 1 条的口径更正）。
   > 真机实测形状见任务单 §真机复验。
5. **GUI**：桌面确认框的节点树（D1 的界面面）是**可勾层级列表**，与 M44 的 Web 节点树同款交互，
   但数据源是 `selector.path`（控件祖先链）。

### D2 —— 控件级 `matchMode` 扩到 `automationId` / `name`

1. **扩面范围**：`matchMode`（`exact`/`contains`/`regex`）从「只作用于窗口 title」
   扩到**控件级**的 `automationId` 与 `name` 两个字段。
2. **作用域收敛**：`matchMode` **不作用于** `className`（保持恒等值，`desktop.py` L773 的既有口径
   不推翻）与 `controlId` / `foundIndex`（数值，无匹配语义）。`classNameRe` 已是独立字段，
   不并入 matchMode。
3. **默认值**：`exact`（与今天一致）。**未显式给 `matchMode` 时行为零变化**——这是兼容性的关键。
4. **manifest 同步**：`matchMode` 从 `attachWindow` 命令参数**升格/复制为 locator 字段**后，
   必须同步 `manifest.errors` 声明（`check_error_contract.py` 会校验）。
5. **歧义语义**：`contains`/`regex` 可能命中多个，沿用既有 `foundIndex` 选第几个；
   不给 `foundIndex` 且命中多个时的行为**必须与现有 `attachWindow` 口径一致**（不新发明）。

### D3 —— 锚点起步（anchor）

1. **范围**：**仅手动 + 运行期先到锚点**。自动推荐**只提示、不决策**（不做「自动选锚点」）。
2. **契约**：locator 新增 `anchor: AnchorSpec | None`，只描述「运行目标元素前，先定位并确认锚点元素
   存在」。锚点定位复用 `DesktopLocator` 本身（anchor 里嵌一个 locator），不发明第二套定位语言。
3. **运行期语义**：目标元素查找前，先解析 anchor；锚点找不到 → 报**明确的错误码**
   （不静默跳过、不回落成「直接找目标」——静默降级正是本项目最忌讳的一类错误）。
4. **不动的地方**：D3 **不引入**自动推荐引擎、不引入相似元素成组（那是后续里程碑 E 系的事）。

## 3. 后果

**正面**

- 桌面元素从「扁平字段等值匹配」升级为「有层级上下文 + 可选模糊匹配 + 锚点前置」，
  与影刀属性表/节点树的形态对齐。
- 三件事共用一次契约改动，`manifest` / `i18n` / 确认框字段表 / 两执行器一次改到位。

**负面 / 风险**

- **破戒代价**：`path` 与 `anchor` 是 locator 的**结构扩展**，会牵连四条契约
  （`test_protocols` 总数、`i18n.js`、`test_field_tables_match_what_executors_actually_read`、
  `test_extension_installer` 若涉版本）。**每条都要各做一次负向验证**。
  > **2026-10-08 实测更正**：本仓**没有 `test_protocols.py`**——该条是从别的仓沿用的措辞。
  > 本仓对应面是 `tests/contract/test_gui_element_editor.py` 的字段表契约；
  > `i18n.js` 全仓**只有一份**；`test_extension_installer` 未涉版本，不受影响。
  > 逐条实况见任务单 §任务 #8。
- **结构字段的界面承载**：`path` / `anchor` 的值不是标量（一个是层级列表、一个是嵌套 locator），
  塞不进「一个输入框一个值」的字段表。落地时新增 `STRUCTURED_LOCATOR_KEYS` 承载，
  并配 `test_structured_keys_are_actually_read` 防止它退化成「想加什么就加什么的豁免名单」。
  **`matchMode` 是两后端唯一合法共享的标量键**。
- **界面「能看见」不等于「能改」**：本片 GUI 只做**只读展示**（祖先链表格 + 锚点说明），
  改写入属后续切片——但**必须保证不丢**（见下条真 bug）。
- **结构性坑（落地时实测）**：`compose_locator` 原先会**静默丢弃**结构字段——用户点一次
  「确定」，捕获回传的祖先链就没了且没有任何提示。已修为显式透传 `structured`，
  往返等价有专门判据（`test_editor_roundtrip_preserves_structured_locator_fields`）。
  **教训：「界面不提供改写入口」的口径不能靠「不传」来实现，否则就是静默数据丢失。**
- `path` 与 M44 的 `selector.path` **同名不同义**，是本 ADR 明确登记的**混淆风险点**，
  文档与判据都要写明。
- D2 扩面若改坏 `exact` 默认路径，会**静默影响所有既有桌面元素**——故「未给 matchMode 行为零变化」
  必须有专门判据钉住。

## 4. Schema 定稿（2026-10-08 维护者开工令）

- **`LocatorStep` 键集 = `controlType` / `automationId` / `name` / `className`**（即 agent 现在
  能拿到的四个；`className` 参与等值匹配，不参与 matchMode）。
  `LocatorStep` 每级**至少给一个键**（全空报 INVALID_INPUT），`extra="forbid"`。
- **D2 的 `name` 用 `contains`/`regex` 命中多个时**：**完全复用 `attachWindow` 既有口径**——
  配 `foundIndex` 选第几个；不给 `foundIndex` 且命中多个时报 `ELEMENT_AMBIGUOUS`
  （不新发明语义，与 uia 侧 contains/regex 走 EnumWindows 看全量候选的既有行为一致）。
  > 注：`attachWindow` 的 `exact` 路径有「FindWindowW 只回第一个句柄、静默附着」的**既有取舍**
  > （`desktop.py` L782-784 已登记）；本 ADR **不改变**该取舍，控件级只看 `_find` 的候选列表。

定稿依据：2026-10-08 维护者「开工」令，采纳建议值。


## 5. 变更记录

- 2026-10-08：初稿（M48 开工前置，schema 定稿）。
- 2026-10-08：**D1/D2/D3 全部落地**（模型 + 两执行器 + agent + manifest 声明面），
  12 处负向验证全绿。落地时增补两条决策：
  - **D3 锚点未命中不占用等待预算**——anchor 是**结构性凭证**（缺失说明当前上下文不对，
    等下去不会变好），故 `_find` 抛 `AnchorNotResolved`、调用点立即报 `ANCHOR_NOT_FOUND`，
    而不是让它退化成 `ELEMENT_NOT_FOUND`（那会把「锚点缺」和「目标没出现」混为一谈）。
  - **两后端共用同一个 `AnchorNotResolved` 与 `ANCHOR_NOT_FOUND`**——「锚点没找到」与后端无关，
    上层按错误码分支时不该被迫区分后端。
- 2026-10-08（任务 #8）：**GUI 展示 + 四契约同步落地，本片收口**。增补三条决策：
  - **标量 / 结构字段分层**：`LOCATOR_FIELDS_BY_BACKEND` 只放标量字段；
    `path` / `anchor` 走 `STRUCTURED_LOCATOR_KEYS`，由专门控件承载。附防豁免名单判据。
  - **`matchMode` 是两后端唯一合法共享键**；`ALL_FIELD_KEYS` 用 `dict.fromkeys` 去重。
  - **i18n 判据的事实源改为模型字段别名**（`test_i18n_covers_desktop_locator_alias_fields`）：
    原先只有「required 顶层键名」一条，而桌面命令 `required == ["locator"]`，
    `locator` **内部**字段一个都扫不到——实测「从 `fields` 里删掉 `anchor`」时门禁全绿。
    manifest 侧的 `locator.properties` 由人手抄（两后端各一份、都不全），不能作权威源。
  负向验证扩到 **19 处**（新增 GUI×4 + i18n×3）全绿；FULL GATE PASSED。
  - **探针事故**：负向验证探针的 stdout 曾接管道，BrokenPipe 在 `print` 处打断了
    `restore_all()`，注入留在源文件里、污染了后续改动。探针已加 `assert_no_sentinel()`
    启动自检 + `try/finally` 兜底还原（见 `M48-desktop-contract.md` §踩坑留档）。
