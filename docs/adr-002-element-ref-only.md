# ADR-002：元素引用去快照（ref-only）——元素库成唯一真值源

- 状态：Accepted（2026-10-12，维护者拍板）
- 关联：ADR-001（元素引用模型，本 ADR 取代其「本期实现」中的双写取舍）；
  M52 S6；`.harness/tasks/M52-selector-advisor.md`

## 背景与问题

ADR-001 定的是**引用模型**（「指令引用元素名，**值只在元素库存一份**，运行期解析」），
但它的「本期实现」为两个理由额外保留了 `with` 里的**值快照**：
① 旧文件零迁移；② 元素删除时可回落。于是每个元素参数都**双写**
（值进 `with` + 引用进 `elementRefs`）。

两份真值带来持续的同步成本，并且**已经产出真事故**：

- 2026-10-12 真机：`browser.input` 在下拉里选了元素，但走工具栏「运行」时
  `_action_form_dirty` 没把「选了元素」算作未应用的编辑 ⇒ `_commit_pending_edits`
  整段跳过 ⇒ 引用被**静默丢掉**，落盘节点既无 `selector` 也无 `elementRefs`，
  运行报 `INVALID_INPUT · 'selector' is a required property`。

维护者拍板：「`selector` 这个字段多余了，既然存了元素字段，那就直接去元素库找就行了，
没必要再额外存储一个 selector，增加同步难度。」——即**走完 ADR-001 定的终态**。

## 决策

**去快照（ref-only，硬切）**：

1. 被元素引用的参数**不再在 `with` 里存值**；`elementRefs` 是**唯一真值源**。
2. `ActionNode` **拒绝**「同一参数既有快照又有引用」——早于本切点的双写数据
   打开即报错，**不做静默迁移**（维护者选「硬切」）。
3. 运行期解析不到元素（被删 / 改名 / 文件坏）⇒ **硬失败** `ELEMENT_REF_MISSING`
   （含 `details.nodeId`），该指令**不执行**；旧的 `elementRefFallback` 回落事件
   **移除**。口径与 `ANCHOR_NOT_FOUND`（ADR 0018 §2 D3）一致：**宁可停，
   不静默降级**——本项目一贯不允许「静默用旧值跑」。
4. GUI 参数面板：引用生效的参数**只显元素名**（`元素：xxx`），值控件**整行隐藏**；
   ✕ 摘引用回到手填。写入侧两条路径（元素库「插入参数」、面板「从元素库选择」）
   都**只写引用**，节点不留第二份真值。
5. 编译期必填校验（GUI `collect_validation_issues`、画布必填红标）把 `elementRefs`
   视作**满足** manifest `required`——否则 ref-only 节点会恒红。

## 代价（已知并接受）

- **流程文件不再自包含**：flow 目录离开 `elements/` 就跑不了（元素缺失即硬失败）。
  换来的是「一份真值、零同步」——而引用模型的卖点本来就在元素库那一份上。
- **存量硬切**：早于本切点的双写流程文件需重新插入元素（维护者确认接受）。
- 判据随之重写：`test_element_refs.py` 里「回落保留快照」「回落事件」语义整体替换为
  「硬失败 + 不执行」。

## 影响面

`model/workflow.py`（校验）、`model/errors.py`（新错误码）、`runtime/element_refs.py`
（缺失只报不回落）、`runtime/orchestrator.py`（硬失败）、`gui/param_form.py`（两态显示 +
`values()` 跳过引用键）、`gui/app.py`（`_insert_element` / apply 对账 / 编译校验 /
新提示）、`gui/canvas.py`（必填红标认引用）。

## 判据

见 `.harness/tasks/M52-selector-advisor.md`「S6 实施记录」（含负向验证探针与期望失败集）。
