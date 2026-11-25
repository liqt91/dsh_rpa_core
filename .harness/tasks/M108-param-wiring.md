# M108：参数机制接线（B+C 档）——标签 / 模式 / 输出变量 / 改名联动 / 分组与联动

Status: `done`

> 2026-11-25 立项 → 同日落地。维护者拍板原话：「**B 和 C 一起做，改名联动也补上**」
> （B = 显示层接线 + 输出别名入口 + fx/Py 切换 + 变量菜单接 `visible_scope`；
> C = B + 输出变量改名联动 + `x-param-groups` 铺开 + `x-depends` 消费者）。
> 前置：M57/M58/M59（参数面板三件）、M63-S7（门禁盲区四形态）、ADR 0021（编辑器转正到
> `apps/editor`）、`docs/manifest-param-config.md`（缺口表）。
> 本里程碑**不占 active 位**（与 M105~M107 同：`project_state.active_*` 仍是 M52）。

## 一、起因（问题不是「缺一个开关」）

对标影刀参数设置机制时暴露的真问题是**参数的四层里有三层没接线**：

| 层 | 现状（改动前） | 后果 |
|---|---|---|
| 定义层 `input_schema` + `x-*` | 很完整，比影刀细 | —— |
| 标签层 `gui/i18n.js` | 全仓唯一事实源，但**没有 UI 消费者** | 324 个字段在界面上裸显示 `postDelayMs` |
| 模式层 `_exprModes` | 运行期认 fx/python，前端零入口 | 用户只能手改 JSON |
| 提交层 `save_node_params` | 七道闸，但不收 `_exprModes` | 手改出来的模式「不受控地活着」 |
| 输出变量 | 后端算得出 `x-outputs`，但 `_output_descriptors` **没有任何调用点** | 「跨节点传值」只能写 `${steps.n3.outputs.text}` 这种没人记得住的路径 |

## 二、本轮落地清单

### 1. 标签层（显示）

- `_field_descriptors` 的 `label` 改走 `field_label(key, command)`（三级回退：命令字段 →
  公共字段 → 键名），参数名降级进 tooltip。
- 补齐 8 个此前**裸键但无消费者**的标签（`injection` / `hoverMode` / `pressDelayMs` /
  `detachAfter` / `windowHandle` / `imageTemplate` / `imageKeyword` / `templateSize` /
  `minConfidence`）——补完全仓 317 个非 hidden 字段 100% 有中文标签。
- 判据从「覆盖率」改成**等值**：每个非 hidden 字段的 `field_label` 必须 ≠ 键名；
  `x-enum-labels` 键集 == `enum` 值集（双向）。

### 2. 模式层（fx / Py）

- 前端：字段标签行右侧挂 `exprBar`（`Py` / `fx` 两个按钮，门控读 manifest 字段级
  `x-fx` / `x-python`）；`_exprModes` 成为运行期唯一真值。
- 编辑态双缓冲 `_expr_slots[字段] = {text, python}`：切 `Py` **先存当前槽、再取目标槽**，
  再改模式；替换文本走 `_replace_editor_text`（`blockSignals` 防空转）。
- ★ **标签删光 ⇒ 自动撤销 fx 态**（复用 `resolver.has_tags`），后端 `_clean_expr_modes`
  同口径兜底；否则留一个「fx 开着但没有变量」的字段，下次打开面板没人解释得清。
- 变量菜单全部内容来自 `rpa.variableScopes`，权威是 `compiler.visible_scope`：流程参数
  `inputs.x`、变量裸名、循环变量**必须** `loop.x`（编译器只在 `root == "loop"` 查
  `loop_vars`，裸名直接 `Unsupported reference root`）、错误变量裸名。

### 3. 提交层 + 输出变量（新增）

- `save_node_params` 新增 `expr_modes`（全量）与 `aliases`（全量）两条通道：**参数与
  输出变量同一次写盘、同一次编译**。分成两个 RPC 会留下「参数存了、别名被拒」的半成功
  状态，用户既不知道文件到哪一步，也没法撤销。
- `rpa.saveNodeAliases` 独立入口保留（API 层 / 测试用），与上一条共用
  `_prepare_aliases` 这一份闸门：键必须是 `x-outputs` 声明的**非 hidden** 输出、名字是
  ASCII 标识符、不占内置根名（`inputs`/`steps`/`loop`，与 compiler 同一份
  `RESERVED_ALIAS_ROOTS`）、全流程不重名、**不与循环/错误变量同名**。
- 最后一道是新加的：同名不报错，只会被 `resolver._lookup` 静默遮蔽（先查
  `scopes["variables"]`），循环体里 `[item]` 拿到别名值——「跑得通但结果不对」。

### 4. 改名联动（本轮的重头）

`save_node_aliases` / `save_node_params` 检测「同一输出字段的旧名 → 新名」，然后全流程
改写引用。覆盖 `model/workflow.py` 里**全部**吃表达式的字段：

| 站点 | 改什么 |
|---|---|
| action `with`（普通模式） | `${旧}` / `${旧.sub}` → `${新}` / `${新.sub}` |
| action `with`（fx 模式） | `[旧]` / `[旧.sub]` 也要改（`${}` 同样改） |
| action `with`（python 模式） | `tokenize` 的 NAME token，**字符串与注释不碰** |
| if `condition.left/right` | `${...}` |
| forEach `items` | `${...}` |
| return `value` | `${...}` |

三条口径刻意不同：
- 普通模式字段里的 `[旧]` 是**字面文本**（`resolve()` 不解析它），改了就是篡改数据；
- fx 字段里 `${...}` 整串仍被 `resolve_tags` 特判，所以两种都要改；
- python 源码用正则改会把 `"旧"` 字面量和注释一起改掉，必须按 token 定位。

语法事实源只有一份：`model/expr_tags.py` 的 `TAG_PATTERN`（编译期与运行期共用），
`model/ref_rename.py` 的 `REF_PATTERN` 与 `resolver._REFERENCE` 同形。放 `model` 包是为了
避开 `compiler → runtime` 的循环导入（`runtime/__init__` 会拉起 orchestrator）。

py 源码读不出 token（语法错/未闭合）时**不猜**：这一格不动，回执里 `pySkipped` 点名，
前端状态栏提示「请手动改」。结构化引用照旧改——不能因为一格改不动就整单放弃。

### 5. `x-param-groups` 铺开 + `x-depends` 消费者

- 11/84 条命令加分组（click / input / scroll / getText / navigate / attachWindow，含
  `desktop` 与 `desktop_win32` 两个后端）；常规组一律用 `auto: true` —— 显式列字段的分组
  **必然**会在新增参数时漏进「其他」，而「其他」这个标题用户读不出是分组还是警告。
- `x-depends` 从 1/84 铺到 7/84，全部按 manifest 已有的 `description` 写（「仅
  `injection=cdp` 生效」「仅 `mode=type` 生效」「`position=point` 时才用 x/y」…），
  **不发明语义**。
- 前端消费者：条件不满足时**置灰**（`disabled` + 整行 `.depOff` + `仅当 X = Y 时生效`），
  **不隐藏**——隐藏会让全量提交少一个键，服务端按 `INCOMPLETE_SUBMIT` 整单拒收，用户还
  以为参数被删了。fx/Py 模式的格子、以及控制值是引用（`${...}` / `[var]`）的情况一律
  不置灰：静态判不了运行期的值。
- ★ `CommandManifest` 里那个**顶层** `x_depends` 字段删掉了：全仓零消费者，写在顶层会被
  pydantic 收下、然后被界面静默忽略。删掉之后顶层再写就是 `extra="forbid"` 的加载错误。
  声明位置钉在 `input_schema` 根（消费者读的那一层），并由契约判据钉住。

## 三、判据（新增）

| 文件 | 条数 | 验什么 |
|---|---|---|
| `tests/contract/test_editor_param_form.py` | 13 | 标签来自 i18n 非键名、`fx/python/mode/varWrite` 元数据、fx 保存 + 运行期解析、标签删光撤 fx、空模式整体删键、未声明模式拒绝且**文件逐字节未变**、未知标签编译期报红、菜单三组口径、菜单每条真能编译、**分组来自 manifest 且 auto 兜住漏项**、**depends 只标说明里真的「仅某模式」的字段** |
| `tests/contract/test_editor_rename.py` | 11 | 四类站点各一条、子路径只换根名、普通字段的 `[..]` 不动、py 字符串/注释不动、py 语法错只报不改、首次设别名不算改名、重名拒绝且**逐字节未变**、循环变量同名拒绝、`saveNodeParams` 一笔写盘、别名被拒时参数也不落地、`nodeDetail.outputs` 行数据 |
| `tests/contract/test_param_meta.py` | 7 | 字段必有中文标签（等值）、`x-enum-labels` 双向等值、分组引用真实字段且一次性、`x-fx` 不与 `enum` 共存、顶层 `x-fx/x-python` 保持 0、`x-depends` 两侧字段存在、**条件值必须是标量** |
| `tests/contract/test_variable_scope_visibility.py` | 3 | 用例表 vs `visible_scope` 逐格等值 + 编译器当裁判 + 正负样本下限 |
| `scripts/check_param_form_ui.mjs` | 7 | 渲染/行为/提交三层之外新增：**输出变量列按描述符渲染 + 改名随参数同一次提交 + 回执报「同步 N 处引用」**、**depends 置灰与即时重算、置灰值仍随提交**、**引用型控制值不置灰** |

`x-depends` 的条件值只支持标量这一条是补的一个**假绿灯陷阱**：文档里出现过
`{"action": {"in": ["launch"]}}`，而 `_depends_of` 只实现等值——比较
`值 == {"in": [...]}` 恒为假，表现不是报错而是**这一格永远置灰**。

## 四、负向验证（8/8）

`.harness` 之外的一次性探针（`%TEMP%/m108_negative.py`，不入库）注入 8 处破坏，
每处都「注入后红 → 还原后绿 → md5 一致」，并各自钉住一条**必须仍然绿**的用例：

1. `_rename_refs` 直接返回 ⇒ 5 条改名判据红，`test_alias_write_without_rename` 仍绿；
2. 去掉循环变量同名闸门 ⇒ 只有那条红；
3. 关掉重名闸门 ⇒ 只有重名那条红；
4. manifest 写非标量 `depends` ⇒ 只有标量判据红（`引用真实字段` 那条仍绿）；
5. 把 `browser.click` 的分组键改名 ⇒ 分组判据红，depends 判据仍绿；
6. 前端不提交 `aliases` ⇒ 输出变量判据红，联动判据仍绿；
7. 前端不置灰 ⇒ 置灰判据红，「引用型不置灰」仍绿；
8. 别名输入框丢 `data-alias` ⇒ 渲染/提交判据红，模式按钮判据仍绿。

★ 第一次跑在这里翻车并修好：JSON 里塞 `# 注释` 会让整份 manifest 解析失败、整个文件全红，
看起来像「判据很灵」——**红的其实是注入自己的语法错**。现在探针把 `ERROR` 行单列判红，
并要求期望红的用例精确对号 `FAILED <file>::<name>`，同时校验一条期望绿的用例。

## 五、门禁

`FULL GATE PASSED`（1226 passed）。★ 中途踩到一条：编辑器前端产物 `gui/dist` 未重建导致
xhs-workbench 的「产物必须由源码重建」判据红（`dist JS 产物缺源码里的 rpa.variableScopes`）
—— 改 `main.js` 后必须 `npx vite build`，否则门禁验的是旧产物。

## 六、遗留（写进 BACKLOG）

- 47 条命令仍未分组（低字段数，暂时够用）；`x-param-groups.auto` 已在，铺开是纯声明工作。
- `x-depends` 只支持「控制字段 == 标量」。真正需要 `in` / `!=` / 与或时**先改 schema**，
  不要在前端猜（`_depends_of` 的文档已经这么写）。
- `x-widget`（控件类型）仍未落地，`varName` / `sessionId` / `selector` 三处前端特判还在。
