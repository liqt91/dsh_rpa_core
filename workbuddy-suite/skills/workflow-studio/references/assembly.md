# workflow.json 组装规范

本文件是**宿主无关**的：只描述 `rpa-core` 的契约与校验行为，不假定任何特定的 AI 客户端。
任何能执行 shell 命令的 agent，按本文件都能产出可编译、可运行的流程。

文中 `{{RPA_CORE}}` 是安装时替成绝对路径的命令（`<python> <技能目录>/scripts/rc.py`），
原样照抄即可。它比裸 `rpa-core` 多的那层是「自己找运行时」：技能被拷进宿主目录后，
`rpa-core` 未必在 PATH 上，`rc.py` 会按技能目录下的 `runtime.json` 定位，找不到时打印
可照抄的修法而不是 `command not found`。

## 0. 两条铁律

1. **能力清单现取，不靠记忆。** 生成前跑一次

   ```bash
   {{RPA_CORE}} catalog --view agent
   ```

   产出全部语义能力的卡片（参数契约、元素参数、输出字段、约束）。**条目数以输出里的
   `semantic_commands` 为准，不要记数字**——manifest 一变它就变。命令 id 必须来自
   它——写一个不存在的 id 编译器会报 `Unknown command`，但写一个**存在却不合适**的
   命令编译器不会拦，所以选型要看 `params` 的 description 而不要凭直觉。

2. **生成后必须过 validate，但过绿不等于能跑。** 见 §6——这是本文件存在的理由。

## 1. 文件外形

```json
{
  "schema_version": "1.0",
  "id": "my-flow",
  "name": "人类可读的流程名",
  "inputs": {"query": "默认值", "workspace": "run_artifacts"},
  "root": { "type": "sequence", "id": "root", "children": [] }
}
```

| 字段 | 必填 | 约束 |
|---|---|---|
| `schema_version` | 是 | 常量 `"1.0"` |
| `id` | 是 | `^[A-Za-z][A-Za-z0-9_.-]*$` |
| `name` | 是 | 任意字符串 |
| `inputs` | 否 | `{名称: 默认值}` 扁平映射，**没有** type/required/description 三个字段 |
| `timeout_seconds` | 否 | 默认 3600，上限 86400 |
| `root` | 是 | 一个节点 |

`inputs` 的名字会被 `${inputs.<名>}` 引用，因此：

- 名字必须匹配 `[A-Za-z_]\w*`（**不含点号**，点号是引用路径分隔符）；
- 不得占用内置作用域根名 `inputs` / `steps` / `loop`；
- 引用一个没在 `inputs` 里声明的名字 → 编译期 `Unknown workflow input reference`。

## 2. 结构节点

控制流**永远是 AST 节点，不是命令**（把 `sequence` 之类写进 `command` 会报
`Control flow cannot be a command`）。五种形状：

```jsonc
// 顺序
{"type": "sequence", "id": "root", "children": [/* 节点 */]}

// 分支：op ∈ eq ne gt gte lt lte contains truthy
{"type": "if", "id": "guard",
 "condition": {"op": "eq", "left": "${inputs.mode}", "right": "live"},
 "then": [/* 节点 */],
 "else": [/* 可选 */]}

// 循环
{"type": "forEach", "id": "each",
 "items": "${steps.grab.outputs.items}",
 "item_var": "row",                 // 可选，默认 "item"
 "children": [/* 节点 */]}

// 异常兜底
{"type": "try", "id": "safe",
 "children": [/* 节点 */],
 "catch": [/* 可选 */],
 "error_var": "err"}                // 可选，默认 "error"

// 提前返回
{"type": "return", "id": "done", "value": "${steps.grab.outputs.count}"}
```

**循环内变量只用 `${loop.<item_var>}` 和 `${loop.index}`**，直接写 `${row}` 会报
`Unsupported reference root`。

## 3. 动作节点

```jsonc
{"type": "action", "id": "grab",
 "command": "browser.queryAll",
 "with": { /* 参数，键集合必须匹配该命令的 input_schema */ },
 "element_refs": { /* 见 §5，JSON 键是 "elementRefs" */ },
 "timeout_seconds": 30, "retry_count": 0,
 "output_aliases": {"items": "titles"}
}
```

- `command` 取自 catalog 卡片；`with` 的键**必须**是 manifest 声明的参数，多一个就是
  编译期 `Unknown parameter(s)`。
- `id` 是数据流的锚点（`${steps.<id>.outputs.<key>}`），全流程唯一，`^[A-Za-z][A-Za-z0-9_-]*$`。
- `output_aliases` 是给输出起别名，后续用 `${titles}` 引用；等价于直接写
  `${steps.grab.outputs.items}`。别名不得重名、不得占用 `inputs`/`steps`/`loop`。
- JSON 键都是 snake_case，**例外**：`with`、`elementRefs`、`_exprModes` 是别名的写法。

## 4. 数据流：三套语法，选错会静默出错

| 写法 | 语义 | 何时用 |
|---|---|---|
| `"${path}"` | **整串**引用，保留原类型（可传 array/object） | 把一个值原样传给另一个参数 |
| `"[name]"` + `_exprModes: {"字段": "fx"}` | 标签替换，**可与文本混排**，结果始终是字符串 | 拼句子、拼路径 |
| Python 源码 + `_exprModes: {"字段": "python"}` | 子进程求值 | `len(rows)`、数学运算 |

`path` 的合法根：

| 根 | 例子 | 说明 |
|---|---|---|
| `inputs` | `${inputs.query}` | 必须在 `inputs` 里声明过 |
| `steps` | `${steps.grab.outputs.items}` | `grab` 必须是**前面**已执行的节点 |
| `loop` | `${loop.row}`、`${loop.index}` | 只在循环体内 |
| 变量名 | `${titles}` | 前序节点通过 `output_aliases` 或 `x-var-write`（如 `data.setVar` 的 `varName`）声明 |

★ **整串 vs 混排是最容易静默失败的地方。** 实测：

```jsonc
// ✗ 原样输出字面量 "第 ${loop.index} 项：${loop.row}"，编译器不报错
{"command": "data.log", "with": {"message": "第 ${loop.index} 项：${loop.row}"}}

// ✓ fx 模式，输出 "第 2 项：c"
{"command": "data.log",
 "with": {"message": "第 [loop.index] 项：[loop.row]"},
 "_exprModes": {"message": "fx"}}
```

另一面：整串引用**保留原类型**，所以把数字/数组塞进 string 参数，运行期会报
`3 is not of type 'string'`——这种错要到运行期才出现，静态看不出来。

`_exprModes` 的键是**字段名**，值只能是 `"fx"` 或 `"python"`。python 模式里写的是
Python 源码（`"2+3"` → `5`），在子进程里求值，能用 `len(rows)` 这类表达式。

## 5. 元素引用

页面上/桌面上的交互元素**不要写选择器字面量**。卡片里的 `element_params` 列出了
哪些参数可以走引用：

```jsonc
{"type": "action", "id": "fill", "command": "browser.input",
 "with": {"text": "${inputs.query}", "pressEnter": true},   // 注意：没有 selector
 "elementRefs": {"selector": "baidu_kw"}}                    // 参数名 → 元素库元素名
```

- 被引用的参数**不进 `with`**（引用即唯一真值）。同时写两处 → 模型校验直接报错。
- 值存在流程目录的 `elements/<元素名>.json` 里，由人捕获或手写；运行期解析不到即
  `ELEMENT_REF_MISSING` 硬失败，不会回落到旧值。
- 元素名是流程内的稳定标识（如 `baidu_kw`），捕获时用同名即可绑定，没有额外的绑定步骤。
- 桌面腿只有 `desktop.findElement` 吃 `locator`；`click`/`input` 用的是它产出的会话
  `elementId` 句柄，不只标元素参数。

检查缺口：

```bash
{{RPA_CORE}} elements list --flow <流程目录名>     # 已有的元素资产
{{RPA_CORE}} elements verify --flow <流程目录名> <元素名>
```

## 6. 编译器能兜住什么，兜不住什么

**能兜住（编译期 `rpa-core validate` 报错）**

- 未知命令、未知参数键、缺必填参数（`elementRefs` 覆盖的键算已提供）
- 节点 id 重复、别名重复、别名占用保留名
- 引用根不存在 / 前向引用（引用了后面才声明的变量）/ 自引用（引用自己声明的变量）
- `${inputs.x}` 里 `x` 未在 `inputs` 声明
- 把控制流类型当命令写

**兜不住（validate 全绿，运行期才炸或静默出错）**

| 缺口 | 后果 | 自保办法 |
|---|---|---|
| `steps.<id>.outputs.<key>` 的 **key** 不校验 | key 写错（`items` 写成 `item`）→ 运行期拿到 null | 卡片 `x_outputs` 里的键名照抄，不凭记忆 |
| 参数**值**的类型/枚举/范围不静态校验 | 运行期 `INVALID_INPUT` | 照卡片 `params.type`/`enum` 写 |
| 跨字段条件只在 `description`/`constraints` 里 | `data.writeText` 同时给 `text` 和 `lines` → 运行期报错 | 看卡片 `constraints.oneOf` |
| `${...}` 混在文本里不解析 | 日志/字段里出现字面量 `${x}` | 混排一律用 fx 模式 |
| `workspace`/`path` 的语义 | 相对路径按运行进程工作目录解析，`path` 必须落在 `workspace` 内，否则 `CAPABILITY_DENIED` | 要确定性就用绝对路径 |
| 循环内节点的 outputs 只保留**最后一轮** | 想收集每轮结果却只拿到最后一条 | 循环内用 `data.appendText` 等追加命令 |
| 元素引用指向不存在的元素 | 运行期 `ELEMENT_REF_MISSING` | 生成后先 `elements list` 对一遍 |

## 7. 生成后自检清单

1. `{{RPA_CORE}} validate <workflow.json>` → `{"valid": true}`
2. 每个 `with` 的键都在对应卡片 `params` 里，没有多也没有少（`elementRefs` 覆盖的键除外）
3. 每个 `elementRefs` 的元素名都能在 `elements/` 下找到（或明确标为「待补」交给用户捕获）
4. 每个 `${steps.x.outputs.y}` 的 `x` 在前、`y` 在卡片 `x_outputs` 里
5. 混排文本全部走 fx 模式
6. 跨字段约束（`constraints`）已满足
7. 落盘类命令的 `workspace` 是绝对路径或明确的相对根

## 8. 常见失败对照

| 报错 | 原因 |
|---|---|
| `Unknown command: X` | 命令 id 不在 catalog 里，重取 `--view agent` |
| `Unknown parameter(s) [...]` | `with` 里多了 manifest 没声明的键 |
| `Missing required parameter(s) [...]` | 缺必填，或本该用 `elementRefs` 的键写在了 `with` 里 |
| `Unsupported reference root` | 变量名拼错，或循环里用了 `${row}` 而非 `${loop.row}` |
| `Forward variable reference` | 引用了后面节点的输出 |
| `Self variable reference` | 在声明该变量的同一个节点里引用它 |
| `Unknown workflow input reference` | `${inputs.x}` 的 `x` 没在 `inputs` 声明 |
| `ELEMENT_REF_MISSING` | `elementRefs` 指向的元素在 `elements/` 里不存在 |
| `CAPABILITY_DENIED` | 写入路径逃出了 `workspace` |
| `N is not of type 'string'` | 整串引用把非字符串值传给了 string 参数（如把数字塞进 `data.log.message`） |
