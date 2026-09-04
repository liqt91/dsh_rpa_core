# workflow.json 格式契约

rpa-core 工作流是 JSON AST。根节点是 `sequence`，子节点按序执行。

## 顶层字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `schema_version` | string | 固定 `"1.0"` |
| `id` | string | 工作流标识 |
| `name` | string | 显示名 |
| `inputs` | object | 默认输入（`run` 的 inputs 可覆盖同名键） |
| `root` | object | 根节点（`sequence`） |

## 节点类型

| type | 说明 | 关键字段 |
|---|---|---|
| `sequence` | 顺序执行 children | `children: [...]` |
| `action` | 调用一条命令 | `command`（如 `browser.click`）+ `with`（参数） |
| `if` | 条件分支 | `condition` + `then` / `else` |
| `forEach` | 循环数组 | `items` + `item_var` + `children` |
| `try` | 异常捕获 | `children` + `catch` |
| `return` | 返回并结束 | `value` |

## 引用语法

- `${inputs.<key>}` — 工作流输入
- `${steps.<节点id>.outputs.<键>}` — 某节点的输出
- `${loop.item}` — forEach 循环变量

## 会话（session）

`browser.launch` / `desktop.attachWindow` 等生命周期命令返回 `sessionId`，后续命令经 `"sessionId": "${steps.launch.outputs.sessionId}"` 复用同一会话，结束用 `close`。

## 校验

`rpa-core validate wf.json` 做静态校验（未知命令、坏引用、能力缺失），不进 run。通过后再 `run`。
