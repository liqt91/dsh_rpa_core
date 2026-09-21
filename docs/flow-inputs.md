# 流程输入声明（workflow `inputs`）

状态：已实现（M26，2026-09-21）
关联：M21（运行参数对话框只读消费）、M25（运行历史用历史 inputs 再跑）、ADR 0016（GUI 为主力形态）

## 1. 形状（唯一事实来源）

`workflow.json` 顶层 `inputs` 是 **`{名称: 默认值}` 的扁平映射**：

```json
{
  "schema_version": "1.0",
  "id": "demo",
  "name": "演示",
  "inputs": {
    "url": "https://example.com",
    "retries": 3,
    "opts": {"timeout": 5000},
    "note": null
  },
  "root": { "type": "sequence", "id": "root", "children": [] }
}
```

**没有** `type` / `required` / `description` 三个字段。类型由默认值的 JSON 类型隐含
（`"url"` 是字符串、`"retries"` 是数字、`"opts"` 是对象）；不写默认值就是 `null`。

> **为什么不做成带类型的声明对象？** 那是一次**形状变更**：`Workflow.inputs` 的类型、
> `${inputs.<名>}` 的引用文法、运行期 `scopes.inputs` 的合并方式都要跟着改，
> 属 ADR 级决定。M26 只把现有形状做成可编辑，不夹带形状变更。

## 2. 三个消费方

| 消费方 | 位置 | 行为 |
|---|---|---|
| 运行参数对话框 | `gui/app.py:RunInputsDialog` | 每个声明渲染一行输入框；**空文本 = 沿用默认值**；填写的内容按 **JSON** 解析 |
| 运行期作用域 | `runtime/orchestrator.py` | `{"inputs": {**plan.workflow.inputs, **inputs}}` 合并进 `scopes.inputs`（用户填的覆盖默认值） |
| 编译期引用校验 | `compiler/compiler.py:validate_refs` | `${inputs.<名>}` 的 `<名>` 不在声明里 → `Unknown workflow input reference` |

## 3. 名称规则（比 `Workflow.id` 严）

```
^[A-Za-z_]\w*$        # 字母/下划线开头，后接字母数字下划线；**不含点号**
```

两条必须守住的边界：

- **不含点号**。`${...}` 的文法（`compiler._REFERENCE`）把点号当**路径分隔符**，
  所以 `${inputs.a.b}` 会被解析成「`inputs` 下的 `a`，再取字段 `b`」。
  若声明叫 `a.b`，它**永远引用不上**——**静默失败比报错更坏**，因此保存时就拒绝。
- **不占用保留名**。`inputs` / `steps` / `loop` 是内置作用域根名
  （与 `compiler._RESERVED_ALIAS_ROOTS` 同口径），占用会使 `${...}` 引用产生歧义。

`Workflow.id` 的规则是 `^[A-Za-z][A-Za-z0-9_.-]*$`（**允许点号**）——它标识的是流程/文件，
不是引用标识符，两者角色不同，规则也就不同。

## 4. 编辑入口（GUI）

工具栏「流程输入」或 文件菜单 →「流程输入」→ 两列表格（名称 + 默认值 JSON）。

- **校验在「确定」之前**：不通过则**对话框不关闭、不产出结果**，错误就地显示且**一次列全部问题**
  （表格里可能有多个错，逐个报会让人改一个点一次再被拒一次）。
- **非法声明仍可打开编辑**：既有声明不合法时先弹警告说明哪里不合法，但不阻止进入——
  否则用户无法修正（「读取宽松、保存严格」的 UI 层对应做法）。
- **落盘走既有编辑链路**：对话框只把结果写进 `_workflow_meta["inputs"]` 并置脏，
  文件由 `_build_document` → `Workflow.model_validate` → `save_workflow` 写，
  因此脏标记与关闭确认与其它编辑完全一致。

## 5. 「读取宽松、保存严格」

`model/inputs.py` 刻意让两个方向不对称：

| 方向 | 函数 | 行为 |
|---|---|---|
| 读（打开文件、载入对话框） | `entries_from_declaration` | **不校验**。历史声明不合法也要能读出来，否则用户卡在「打不开也改不了」的死局 |
| 写（保存、编译） | `declaration_from_entries` / `validate_declaration` | **严格校验**，失败即拒绝 |

编译期也接入了校验（`WorkflowCompiler.compile` 在引用校验之前），保证规则**真的拦得住**，
而不是躺在库里当纯函数。

## 6. 声明不是运行输入的过滤器

`inputs` 声明只用于两件事：**渲染运行参数表单** 与 **编译期校验引用**。它**不**裁剪实际下发的输入：

- M25「用历史输入再跑」时，历史 inputs 可能含**当前已删除的声明**——按历史**原样透传**，
  不因当前声明缺失而静默丢弃（任务单 §风险 明确要求，测试 `test_history_inputs_are_passed_through_unfiltered` 钉住）；
- 运行期合并是 `{**声明, **用户输入}`，用户的键即使不在声明里也会进作用域。

## 7. 测试与门禁

| 文件 | 项数 | 覆盖 |
|---|---|---|
| `tests/contract/test_flow_inputs_model.py` | 67 | 能力层：往返恒等矩阵、名称合法性、默认值 JSON 语义、整份校验一次报全、与 `Workflow` 形状互认、编译器正反例 |
| `tests/contract/test_gui_flow_inputs.py` | 29 | 对话框：两列形状、校验矩阵、`accept` 不可绕过、编辑操作、往返恒等、主窗口接线三态、声明到文档的端到端形状 |
| `tests/contract/test_gui_flow_inputs_linkage.py` | 19 | 联动：运行对话框随声明增删、补全路径随声明增删、**跨层一致性**（补全产出必须被 `compiler._REFERENCE` 匹配）、残留引用报错可见、历史输入不被裁剪 |

**负向验证**：摘掉保留名检查 / 放宽允许点号 / 切断补全接线 / 让历史再跑丢弃 inputs —— 四处均按预期报红。
（本仓约定：校验与门禁必须做负向验证，否则等于没有。）

## 8. 范围外（明确不做）

- 输入分组 / 排序（表格按名称排序，够用）；
- 敏感值（密码）掩码——形状里没有 `type`，无法表达「这是密码」；
- 跨流程共享的输入模板；
- `type` / `required` / `description` 字段（见 §1 的说明，属形状变更，需独立 ADR）。
