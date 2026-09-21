# M26 流程 inputs 声明编辑 UI

状态：`done`

S1–S4 全部完成（2026-09-21）。

关联：M21（运行参数对话框只读消费 `inputs`）、ADR 0016（GUI 为主力形态）、M25（运行历史用历史 inputs 再跑）
现状：流程级 `inputs`（`workflow.json` 顶层 `inputs` 字段）只能**手写 JSON**；GUI 的「运行参数」
对话框只按声明渲染输入框（只读消费），变量面板只列 `inputs.<名>` 路径——用户无法在界面里增删改声明。

## 真实形状（S1 开工时核实，取代原先的「待确认」）

**`Workflow.inputs: dict[str, Any]`，语义是 `{名称: 默认值}` 的扁平映射**——**没有** `type` /
`required` / `description` 三个字段。消费方三处，全部按这个形状读：

| 消费方 | 位置 | 怎么用 |
|---|---|---|
| 运行参数对话框 | `gui/app.py:RunParamsDialog` | `for name, default in inputs.items()` 渲染一行 `QLineEdit`；空 = 沿用默认；文本按 JSON 解析 |
| 运行期作用域 | `runtime/orchestrator.py` | `{"inputs": {**plan.workflow.inputs, **inputs}}` 合并进 `scopes.inputs` |
| 编译期引用校验 | `compiler/compiler.py:validate_refs` | `parts[0] not in workflow.inputs` → `Unknown workflow input reference` |

**所以任务单里那条备选成立了**：「若现形状无该字段，则本切片只做『名称 + 默认值』两列」——
S1–S3 按两列做，`type`/`required`/`description` 属后续增量（若要加，是**形状变更**，
`Workflow.inputs` 与 `${inputs.<名>}` 的文法都要跟着改，需要独立 ADR 级的决定）。

## 关键设计（先行定案）

## 目标

把流程 `inputs` 变成一等编辑对象（GUI）：

1. 增删改**输入项**：名称、类型、默认值、是否必填、描述；
2. 保存回写 `workflow.json` 顶层 `inputs`（形状与现状一致，向后兼容）；
3. 与既有消费方联动：运行参数对话框按新声明渲染默认值；`${inputs.x}` 引用补全与变量面板同步；
   编译器对未声明引用的既有校验不被破坏。

## 关键设计（先行定案）

- **JSON 形状不变**：继续用现有 `inputs` 映射（`{name: default}` 或含类型的声明对象，以现仓库
  实际形状为准），编辑 UI 只是它的可视化壳——**不引入第二份声明格式**。
- **校验在保存前**：名称必须匹配标识符规则（与 `output_aliases` 同口径）、不得与保留作用域
  （`steps`/`loop`/`error`/`variables`）冲突、类型与默认值必须自洽；错误就地提示、不落盘。
- **空默认值语义**：默认值为空 ≠ 必填——是否必填按声明显式表达（若现形状无该字段，则本切片
  只做「名称 + 默认值」两列，必填列为后续增量）。
- **不改 runtime**：inputs 只影响编译期校验与运行输入合并（`scopes.inputs`），不新增命令。
- **范围外**：输入分组/排序、敏感值（密码）掩码、跨流程共享输入模板。

## 任务（切片）

- [x] **S1 声明模型与校验（能力层）**（已完成）
  - 在 `model/workflow.py`（或新增 `model/inputs.py`）把 `inputs` 声明解析/规范化成可编辑条目
    （名称/类型/默认值/描述），并提供 `validate_inputs_declaration(...)`：标识符规则、保留名冲突、
    类型与默认值一致性；保持既有 JSON 形状与向后兼容。
  - 验收：给定若干声明（合法/非法）校验结果稳定；既有流程文件解析不变（回归）。
  - **结论**：新建 `src/rpa_core/model/inputs.py`（不改 `Workflow.inputs` 的类型——形状兼容是硬约束）。
    落点两列（`name` + `json_text`），**不引入 type/required/description**（见上文「真实形状」）。
    提供 `InputEntry` / `entries_from_declaration` / `declaration_from_entries` /
    `validate_entries` / `validate_declaration` / `referenced_input_names`（后者供 S3 找残留引用）。
    **三条刻意的设计决定**：
    1. **读取宽松、保存严格**。`entries_from_declaration` 对历史非法声明不打回（否则用户会卡在
       「文件打不开也改不了」的死局），`declaration_from_entries` 才校验。测试专门钉住这个不对称。
    2. **名字规则比 `Workflow.id` 严：`[A-Za-z_]\w*`，不含点号**。因为 `${...}` 的文法
       （`compiler._REFERENCE`）把点号当路径分隔符，名字带点会导致「声明在册但永远引用不上」——
       这是静默失败，比报错更坏。保留名 `inputs`/`steps`/`loop` 与 `_RESERVED_ALIAS_ROOTS` 同口径；
       **测试直接 import 编译器常量断言两边一致**（而非复制字面量），编译器改了这里会红。
    3. **默认值文本规范化（`sort_keys=True`）**：否则对象默认值的键序抖动会让每次保存产生不同字节，
       污染 GUI 脏标记与 `git diff`。
    **接入编译路径**：`WorkflowCompiler.compile` 在引用校验之前先 `validate_declaration`，
    失败转 `WorkflowCompileError`——保证这个校验不是躺在库里的纯函数，而是真的拦得住。
    测试：`tests/contract/test_flow_inputs_model.py` **67 项**（往返保真恒等矩阵、名字合法性、
    默认值 JSON 语义含裸词/空文本、整份校验一次报全、与 `Workflow` 的形状互认、编译器正反例、
    引用提取）。**负向验证 2 例**：摘掉保留名检查 → 6 项红；把 `INPUT_NAME_PATTERN` 放宽到允许点号
    → 4 项红。均已还原复跑通过。
- [x] **S2 GUI 输入项编辑器**（已完成）
  - 菜单/工具栏入口「流程输入」→ 对话框或 dock：表格化增删改（名称/类型/默认值/描述），
    保存时校验通过才回写 `workflow.json`（走既有 `_build_document`/保存链路，保证撤销与脏标记一致）。
  - 验收：可增删改并保存；非法名称/冲突名被拒且不落盘；重开后声明一致。
  - **结论**：新建 `src/rpa_core/gui/inputs_dialog.py`（`FlowInputsDialog`）。**表格两列**
    （名称 + 默认值 JSON 文本）——与能力层形状一致，界面上不出现 `类型`/`必填`/`描述`。
    入口：工具栏「流程输入」+ 文件菜单（`_edit_flow_inputs`）。
    **三条刻意的设计**：
    1. **校验在「确定」之前，且不产出半成品**——不通过则**留在对话框**、`_result` 保持 `None`。
       最容易被写成「先 accept 再校验」，那样调用方会拿到非法声明。`accept()` 也做了防御性覆写：
       任何路径（含直接调用）都要先过校验。
    2. **只写 `_workflow_meta["inputs"]`，不碰文件**——落盘由既有
       `_build_document` → `model_to_workflow` → `Workflow.model_validate` → `save_workflow` 负责，
       因此脏标记/关闭确认与其它编辑完全一致。
    3. **模块级接缝 `app.flow_inputs_prompt`**（而非 `_edit_flow_inputs` 里的函数内 import）——
       给测试留可替换点，避免为验证「确定/取消/未变化」三条分支去驱动真实模态对话框。
       （这一点是测试驱动出来的：最初的函数内 import 让 monkeypatch 打不中，暴露了接缝缺失。）
    **非法声明仍可进入编辑**（`warn_and_edit`）：先弹警告说明哪里不合法，但不阻止打开——
    「读取宽松、保存严格」在 UI 层的对应做法，否则用户无法修正。
    测试：`tests/contract/test_gui_flow_inputs.py` **29 项**（两列形状、校验矩阵、一次报全、
    错误清除、`accept` 不可绕过、编辑操作、**往返恒等**、主窗口接线三态、声明到文档的端到端形状）。
- [x] **S3 与运行/引用联动**（已完成）
  - 运行参数对话框按新声明渲染（默认值/类型）；变量面板与 `${` 补全列出 `inputs.<名>`；
    删除声明后残留引用在编译校验里如实报错（不静默）。
  - 验收：新增输入后运行对话框立即出现；删除后引用报错可见。
  - **结论**：三个消费方**本来就都读 `_workflow_meta["inputs"]`**（`RunInputsDialog` 逐行渲染、
    `_collect_reference_paths` 产出 `inputs.<名>`、`compiler.validate_refs` 校验引用），
    所以 S2 的落点选对之后，联动是**自动成立**的——S3 的实质工作是**验证它真的成立**，
    而不是新增接线代码。这正是本行的价值：**「接线自动」与「接线正确」是两件事，后者要证明。**
    测试：`tests/contract/test_gui_flow_inputs_linkage.py` **19 项**，四组：
    ① 运行对话框随声明增删（含默认值进 placeholder、JSON 解析、非法 JSON 抛错、空声明不弹窗）；
    ② 补全路径随声明增删、缺 `inputs` 键不抛、改完声明立即刷新变量面板；
    ③ **跨层一致性**（关键）：补全产出的 `inputs.<名>` 必须被 `compiler._REFERENCE` 完整匹配，
    且「声明 → 引用 → 编译通过」端到端跑通——**两处各自实现「什么算合法引用」是这类功能的经典漂移点**；
    ④ 删除声明后残留引用报错：编译期 `WorkflowCompileError` + GUI「校验」入口
    （`collect_validation_issues`）都可见，且声明还在时不误报。
    另按 M25 任务单 §风险 补：**历史输入不被当前声明裁剪**（给 `read_run` 打桩返回含已删除声明的
    历史 inputs，断言 `_start_run` 收到的是完整历史输入）——声明**不是**运行输入的过滤器。
    **负向验证 2 例**：切断 `_collect_reference_paths` 的 inputs 接线 → 2 项红；
    让历史再跑丢弃 inputs → 1 项红。均已还原（`git diff` 确认 app.py 仅 53 行新增、0 删除）。
- [x] **S4 测试与文档**（已完成）
  - 单测：规范化/校验矩阵；契约：GUI 编辑器往返（保存→重开）、运行对话框联动、编译校验回归。
  - 文档：`docs/` 相应章节 + PROGRESS；验收：full gate 通过。
  - **结论**：新建 **`docs/flow-inputs.md`**（8 节：唯一形状 / 三个消费方 / 名称规则 /
    编辑入口 / 读取宽松保存严格 / 声明不是运行输入的过滤器 / 测试与门禁 / 范围外）。
    刻意记下两件容易被后人重新踩的事：① **为什么不做带类型的声明对象**（形状变更，需独立 ADR）；
    ② **名称为什么不许点号**（`${...}` 把点号当路径分隔符 → 声明在册但永远引用不上，
    静默失败比报错更坏）。
    测试三份共 **115 项**（能力层 67 + 对话框 29 + 联动 19），负向验证 4 处全部按预期报红。

## 验收

- 声明可在 GUI 内增删改并正确回写；既有流程文件零迁移；
- 运行参数对话框/引用补全/编译校验三方与声明保持一致；
- 每切片：GUI 合同测试（offscreen）+ `check_all.py` 全门禁通过；PROGRESS 追加一行。

## 风险 / 注意

- **形状兼容是硬约束**：先读清现仓库 `inputs` 的真实形状（可能是 `{name: default}` 简写或对象），
  编辑 UI 不得改变已存在文件的语义。
- 保留名冲突（`steps`/`loop`/`error`/`variables`）必须拦截，否则运行期解析会歧义。
- 与 M25「用历史输入再跑」的交互：历史 inputs 可能含已删除的声明——再跑时如实按历史输入执行，
  不因当前声明缺失而静默丢弃。
