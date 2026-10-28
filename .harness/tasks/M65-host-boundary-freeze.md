# M65：宿主边界冻结 + PPX 实验基座

Status: `wip`

> **2026-10-28 立项**。维护者拍板：PPX 壳继续推进，**Qt 代码留着不动、也不改**，并把这条写进
> 项目要求。沿用 M63 先例**不占 active 位**（M52 未收口）——它是旁支实验，主线仍是 selector-advisor。
>
> 前置：ADR 0019。本任务**不触产品代码**（`src/` / `commands/` / `tests/` 一行不改），
> 且「没触」由 S1 的门禁自己证明，不靠自觉。

## 一、决策来源（维护者拍板，2026-10-28）

| # | 问题 | 拍板 | 处置 |
|---|---|---|---|
| ① | PPX / Tauri 用哪个 | 先做 PPX 初版看效果 | 原型落 `.harness/demo/ppx_probe/`，独立 venv、独立项目 |
| ② | 既有 Qt 代码怎么办 | **留着不动、也不改** | 逐文件 sha256 冻结 + 门禁比对（S1） |
| ③ | 迁移要不要现在谈 | 不谈，先把实验做实 | ADR 0019 §2.3 三条门槛：覆盖层 / 打包分发 / 桌面集成 |

## 二、切片

| 片 | 内容 | 验收口径 | 状态 |
|---|---|---|---|
| **S1** | 新增 `.harness/scripts/check_host_boundary.py` + `.harness/gui-freeze.json`（22 个 `.py` 的 sha256），进 `check_all.py` | 三条判据：GUI 冻结（集合 + 逐文件哈希）、原型不得 import `PySide6` / gui UI 模块、能力层不得依赖 `ppx_py`；负向验证 14 条全中 | ✅ |
| **S2** | 项目要求写入：ADR 0019 / `AGENTS.md`（产品方向 + 规则 14）/ `invariants.md`（#16） | `check_docs` 绿；三条渠道读到的口径一致 | ✅ |
| **S3** | PPX 原型：三栏编辑器 + 真实目录 + 编译校验 | 89 条命令、8 个样例流程全部编译通过；bootstrap 热态 20 ms | ✅ |
| **S4** | PPX 原型：**覆盖层实证**（透明置顶 + 鼠标穿透 + 高亮框跟随） | 见 §五；四条读数全落（`transparent=True` 假透明 98.09% 灰 / 抠色真透明 / 整窗不可命中 / mousemove 0 vs 3 / 轮询跟随可用） | ✅ |
| **S5** | PPX 原型：编辑 + 保存闭环（改参数 → 落盘 → 复编译） | 15 条判据全中（见 §六）；**新增节点不做**（涉及 require/default/oneOf，属 S6） | ✅ |
| **S6a** | 参数面板**覆盖声明的全部字段**（不是「只列文件里已有的键」）+ 未设置三态 + `x-param-groups` 分组 + 联合类型 | `save_probe.py` 26 条判据全中（见 §八） | ✅ |
| **S6b** | 结构节点表单：if / forEach / try / return 的 AST 字段编辑 | `struct_probe.py` 22 条判据全中（见 §八） | ✅ |
| **S6c** | 插入节点 / 删除节点（真改 AST） | `insert_probe.py` 23 条判据全中（见 §七·六）；删除按钮的 GUI 点击未单独取证 | ✅ |

## 三、判据设计（为什么长这样）

**为什么用 sha256 基线，而不是「约定别动」。** 「不改 Qt」此前只是口头共识，下一个人换个说法
就能绕过去（「我只动了这一行」「顺手格式化一下」）。基线把「碰它」变成一次显式动作：改完必须
`--update-freeze` 重建基线，那一步的 diff 会留在提交里，签字与偷改在历史里长得不一样。

**为什么基线是快照而不是允许清单。** 允许清单要人维护「哪些文件可以动」，那就退化成审批；
快照只回答一个问题：跟上次冻结时是否一字不差。

**白名单为什么按前缀放行。** 首版 `GUI_ALLOWED_IN_DEMO` 用精确相等，结果原型里
`from rpa_core.gui.i18n import load_command_display_names` 被拼成 `rpa_core.gui.i18n.load_command_display_names`
⇒ **判据把自己判红**。改成按模块前缀放行后才对。这条留在这儿，因为「白名单打不中合法用法」
是这类判据最常见的翻车形态。

**删除场景为什么只在临时树里造。** 探针有成批 `unlink` / `rmtree` 会撞上删除守卫
（按 turn 累计），守卫一旦 fail-closed，探针就会在「删得太多」上失败、把真正的判据结论掩盖掉。
所以临时树用序号目录、**不自动清理**，删除语义在沙盒里验，不碰真仓库。

## 四、负向验证（`probe_host_boundary.py`，14 条全中）

| 方向 | 用例 | 期望 |
|---|---|---|
| 绿 | 基线一致 | 无报错 |
| 红 | 改一个字节 | `内容被修改` |
| 红 | 新增文件 | `新增了文件` |
| 红 | 删除文件 | `文件被删除` |
| 绿 | 回填后重新绿 | 无报错 |
| 红 | 基线文件被删 | `缺少冻结基线` |
| 红 | 原型 `import PySide6` | `不得引入 Qt` |
| 红 | 原型 `from rpa_core.gui import app` | `不得依赖 gui` |
| 绿 | 原型只用 `gui.i18n` + 能力层 | 无报错 |
| 红 | 能力层 `import ppx_py` | `不得依赖宿主壳` |
| 绿 | 清掉泄漏 | 无报错 |
| 红 | **真仓库**注入一个字节 | 门禁红 |
| 绿 | 逐字节还原核 md5 | 一致 |
| 绿 | 还原后门禁重新绿 | 无报错 |

取证输出：`.harness/spike/probe_host_boundary.out`。

## 五、S4 覆盖层实证（已做，结论在 ADR 0019 §2.4）

探针 `overlay_probe.py` 跑了四轮，每轮都靠读数而不是观感：

| 轮次 | 改了什么 | 拿到什么 |
|---|---|---|
| v1 | 只用 `transparent=True` | 灰板 `#F0F0F0` 占 98%⇒假透明；穿透时 mousemove 0 次 vs 恢复后 3 次 |
| v2 | 加 `BackColor=洋红` | 背景变洋红但 `SetLayeredWindowAttributes` 返回 0（**但没查 last_error**） |
| v3 | 改走 `form.TransparencyKey` + 报 `last_error` | 灰 0.0% / 洋红 0.0% ⇒ 真透明，截图能看到下层终端文字；API 也 `ok=true, last_error=0` |
| v4 | 抠色后测「哪块吃鼠标」 | 透明区与红框采样点**均未命中自身窗口** ⇒ 覆盖层自身收不到点击，交互控件得另开窗口 |

★ **v2 的误判要记住**：当时用「pid 是否等于自己」判穿透，得出 `clickthrough_effective: false`。
WebView2 的渲染跑在独立进程（`msedgewebview2.exe`）里，`Chrome_RenderWidgetHostHWND` 的 pid
**永远**不等于本进程 ⇒ 这个判据恒假。正确判法是 `GetAncestor(hwnd, GA_ROOT) == 我们的 hwnd`。
判据写错比没判据更坏：它会让「其实穿透了」看起来像「没穿透」。

## 六、S5 保存闭环实证（已做）

两个探针，都是读数：

**`roundtrip_probe.py`（保存保真度，8 个样例流程）**

| 层 | 读数 | 结论 |
|---|---|---|
| 文本层：`json.loads` → Qt 编码口径重排 | **5 个逐字节相同 / 3 个不同** | 3 个不同是原文把数组写在单行（`"menuPath": [ ... ]`），换谁保存都会重排；**不是 PPX 的问题** |
| 模型层：`model_validate(...).model_dump(by_alias=True)` 对原文 | **0 个无损**，每个多 14~70 处 | `elementRefs`/`output_aliases`/`retry_*`/`_exprModes`/`timeout_seconds` 等默认值会被灌进文件 |

⇒ **保存不走 `model_dump`**：在原始 dict 上定点改键 + `Workflow.model_validate` 复校 + Qt 同款
编码（`indent=2` / `ensure_ascii=False` / 末尾换行 / `write_bytes` 保 LF）。

**`save_probe.py`（15 条判据，headless 直调 RPC）**

| # | 判据 | 读数 |
|---|---|---|
| 1 | 沙盒流程进列表 | 9 个流程 |
| 2 | 字段类型：manifest 优先、未声明按既有值推断 | `url/string/manifest`、`timeoutMs/integer/manifest`、`headless/boolean/existing`、`userAgent/string/existing` |
| 3 | 改 3 个参数 | ok、changed、编译通过 |
| 4 | 磁盘值是新的且类型没变 | `timeoutMs: 30000`（int）、`headless: true`（bool） |
| 5 | **未触碰的行逐字节不变** | 改 3 个参数 = 6 条 `+/-`，无其它漂移 |
| 6-7 | 同值再存 | `changed=False`、文件 SHA 不变（不做无谓写盘） |
| 8-9 | 类型不符 / JSON 合法但类型不符 | 都 `INVALID_VALUE`，文件 SHA 不变 |
| 10-11 | 新增键 / 非 action 节点 | `INVALID_VALUE`（指向 S6）/ `NODE_NOT_EDITABLE` |
| 12-13 | 外部改过文件 | `FLOW_CHANGED`，且**不覆盖**外部内容 |
| 14-15 | 真实窗口端到端 | 改 `timeoutMs` 20000→4500 → 保存 → `1924→1923 B`、`sha 6ab2bcea1460`、27 ms、编译通过；磁盘 `diff` **只有那一行** |

**负向验证**：把 `_coerce` 改成恒返回原文（等于关掉类型系统）⇒ 探针 **9 条变红**，还原后 md5
逐字节一致、复绿。

★ **本片自己踩的坑（判据被合法用法打中）**：首版按「manifest 声明类型」强制转换，而未声明的
`headless` 落到 `string` 默认 ⇒ 用户输入 `true` 被存成 `"true"`：**保存成功、编译通过、运行期才炸**。
改成「声明优先、未声明按既有值推断，且改完类型必须一致」才对。

★ **顺带挖到的仓库级漂移**：`examples/baidu-news-top10/workflow.json` 的 `browser.navigate` 节点
带 `headless` / `userAgent` 两个**没有任何 manifest 声明**的参数，且缺 `required` 的 `browserType`；
按 `orchestrator.py:798` 的 `additionalProperties: false` 校验，这个样例真跑会被 `INVALID_INPUT`
拦下。已登记 BACKLOG（不属于 M65 范围，没动它）。

## 七、启动器与多屏（`run_probe.py`）

> **2026-10-28 更正**：本节初版把「窗口 `top = -932`」写成「PPX 把窗口开在屏幕外」，**那个结论是错的**。
> 维护者提示「笔记本外接了屏幕」，实测布局：主屏 work `(0,0,1920,1040)`，副屏在主屏**上方**
> work `(0,-1080,1920,-40)`，虚拟桌面 `1920x2160`、原点 `(0,-1080)`。PPX 把窗口居中到副屏得出
> `y = -1080 + (1080-777)/2 = -929`，与实测 `-932 / -959` 一致 —— **它没做错，是判据只认主屏**。
> 「拉回后又会自己跳走」同理：那是我自己的守窗把用户拖到副屏的窗口拽回来，不是 PPX 在漂移。

★ **这条错误的代价**：守窗逻辑按 `SPI_GETWORKAREA`（只返回主屏）判可见，于是**每秒把用户拖到
副屏的窗口抢回主屏**。维护者手动拖、窗口自己跳回来。一个「修复」变成了新的 bug，而且是我
主动加的、用户没要求过的行为。

**现在的规则（多屏感知）**，`EnumDisplayMonitors` 取所有屏幕工作区：

| 状态 | 判据 | 动作 |
|---|---|---|
| 在任一块屏上（含跨屏） | **合并**可见面积 ≥ 35% | 不动 |
| 标题栏能抓 | 顶部 40px 与某屏横向重叠 ≥ 100px | 不动 |
| 完全在所有屏之外 | 以上都不成立 | 救回「交集最大的那块屏」中央（无交集用主屏），尺寸夹进该屏工作区 |

合并算面积是必须的：窗口跨两块屏时每块屏单独看都只有一半，按单屏判会把「跨屏摆着」
误判成「跑飞了」——首版就这么错了一次（`(0,-400,1280,377)` 被判不可见）。

`--primary` 是显式开关：只有用户说「我要它在主屏」时才强制归位，默认**不干预**。

**实测（都在真机、两块屏）**：

| 状态 | 结果 |
|---|---|
| 启动后 PPX 放主屏 `(208,208)` | `窗口可见于 主屏，不动它` |
| 人为拖到副屏 `(300,-900)` | 6 秒内**纹丝不动**，无 `[救窗]`（这是维护者报的那个问题） |
| 人为跑到 `(320,-3000)` 全屏之外 | 1~2 秒内救回 `(320,131,1600,908)`，尺寸 1280×777 保真 |

★ **两次判据错误都在同一处**（「窗口可见」这个判定）：
① `last.startswith("移到")` 判「是否搬过」，而 `last` 开头是「窗口原本在」⇒ 恒假，真搬了却
报「位置正常」；② 单屏可见比例判跨屏 ⇒ 把正常摆放判成跑飞。教训不是「字符串要用状态量」，
而是**动手改别人的窗口之前，先确认「我以为的异常」在这个环境里真的是异常**。

## 七·五、S6a / S6b 参数面板与结构节点实证（已做）

### S6a 为什么不是「加个功能」而是修一个偏离

S5 的字段表只遍历 `node["with"]` 里**已有的键**。Qt 的 `ParamForm` 遍历的是
`schema["properties"]`——**声明的全部字段**，空 = 未设置。两者不是「多一个功能」的差别：
只渲染已有键，等于让「命令支持但当前没写」的参数在界面上**根本没有入口**。
`browser.navigate` 的 `browserType`（required）就是这样被彻底藏起来的。

修完之后，那屏直接暴露了 §六末尾记的仓库级漂移：`browserType` **必填但 `set=False`**，
旁边挂着 `headless` / `userAgent` 两个「manifest 未声明」的字段。以前看不见，现在一眼是红的。

### 保存语义随之改成「全量替换」

Qt 是 `holder.raw["with"] = dict(values)`——表单提交整份参数，不是补丁。跟着改之后
必须同时加一道**提交完整性闸门**：前端漏传一个键就成了「悄悄删掉一个参数」。
`INCOMPLETE_SUBMIT` 拒绝了这种可能，宁可报错也不猜。

`save_probe.py` 从 15 条扩到 **26 条**，新增的关键几条：

| # | 判据 | 读数 |
|---|---|---|
| 1 | 声明了但没写的参数也给出入口 | `browserType` / `action` / `onTimeout` / `commandLineArgs` 四个 `set=False` |
| 3 | `type` 为**列表**时取第一个非 null | `data.setVar.value` → `string`（不是 `"['string', 'number', …]"`） |
| 4 | 分组顺序取自 `x-param-groups` | `['常规','高级','未声明']`，`高级` 折叠 |
| 5 | `x-hidden` 字段不给入口 | `data.table.setCell` 的 `flowDir` 被滤掉，`row/column/value` 还在 |
| 11 | **漏传键 → `INCOMPLETE_SUBMIT`** | 文件不动 |
| 13 | 空文本 → 键从 `with` 里删除 | `removed=['userAgent']` |
| 17 | `_` 前缀私有键不受影响 | `_exprModes` 保存后原样留住 |

★ **这一片自己撞出来的两个真 bug**（都是判据逼出来的，不是我读出来的）：

1. `_SKIP_KEYS = ("_",)` 是**精确匹配字符串 `"_"`**，不是前缀判断。写成 `_is_reserved()` 才对。
2. `with` 里带着 `x-hidden` 字段（`data.table.*` 的 `flowDir`）时，前端不渲染它、也就不提交它，
   于是永远撞 `INCOMPLETE_SUBMIT`——**这类节点一个都保存不了**。修法是 hidden 字段既不作
   提交要求、也不参与替换，原样透传。

### S6b 结构节点：四种节点各有各的语义

`ControlNodeForm`（Qt）的做法是「直接编辑 AST 结构字段」，`apply_values()` 返回字段 dict、
由调用方写回。原样搬过来：

| 节点 | 字段 | 解析口径 |
|---|---|---|
| `if` | `op`（enum，8 个操作符）/ `left` / `right` | 字面量：`${...}` 原样保留；数字与 `[ { t f n " -` 开头尝试 JSON；**右值留空 = 删键** |
| `forEach` | `items` / `item_var` | items 走 JSON；item_var 必须匹配 `^[A-Za-z_]\w*$` |
| `try` | `error_var` | 同上标识符约束 |
| `return` | `value` | JSON；留空 = `null` |

字段形状与指令参数**完全一致**（复用同一个前端渲染器），只是 `semantic` 不同。
操作符选项**从 `Condition.op` 的 Literal 实参现取**，不手抄一份枚举。

`struct_probe.py`（新，22 条）覆盖四种节点的字段表 + 写回 + 拒收 + 编译。

★ **两次「期望值写错」**（都是我推理错、实现对）：

1. 断言「改 if 只动 condition 那几行 = 3 删 3 增」，实际 **5 条**——原 `condition` 没有
   `right` 键，所以是 2 删 3 增。**我按想象里的文件写期望，没按实际文件写。**
2. 夹具里写 `"${row}"` 引用循环变量，编译报 `Unsupported reference root: row`。
   循环变量在作用域里是 `loop.<变量名>` → 必须写 `"${loop.row}"`。顺带把这条写进
   `item_var` 字段的描述里（用户同样会踩）。

★ **一次「不该加的严」**：首版给 `forEach.items` 加了 `isinstance(items, list)` 硬校验。
但 `resolve()` 对纯引用字符串会求值成实际列表，`items: "${inputs.rows}"` 是**合法写法**，
硬拦等于禁掉整个引用写法；而编译期本来就只查引用可解析性、不查类型。改成
**接受 + 警告**（`warnings: [{key, reason}]`），并在状态栏显示——不接受「编译通过、
运行期 INVALID_INPUT」这种沉默。

### 前端渲染

同一屏里并排验证过两种节点：参数表单（`4/8 已设置`、分组折叠、`（未设置 · 默认 msedge）`）
与结构表单（`2/3 已设置`、`条件操作符` 显示中文「为真（非空）」、`右值` 显示 `未设置` 占位）。
manifest 的 `description` 动辄四五句，收成两行 + `title` 全文，否则 340px 侧栏被撑成一片空白。

★ 顺带把默认演示流程从 baidu 副本换成 `_sandbox/struct.json`：它同时含 action / if / forEach /
try / return，五种形态都能点到；原来那份一个 if 都没有，结构表单等于没法看。

## 七·六、S6c 插入 / 删除实证（已做）

### 落点语义是这一片唯一容易做错的地方

Qt `FlowTreeModel.insert_command` 的规则是**插到 anchor 所属分支的末尾**，不是「紧接 anchor
之后」。这条如果理解错，界面看着"能插入"，但每次点第 2 行都插到末尾——用户会以为程序坏了。

对齐之后的落点表：

| anchor | 落点 |
|---|---|
| 空 | root 的容器末尾 |
| 叶子（action / return） | **父容器那条分支的末尾**（不是紧跟其后） |
| `if` 容器 | `then` 末尾 |
| `then` 里的叶子 | 仍在 `then` 末尾 |
| `else` 里的叶子 | `else` 末尾（不串到 then） |
| `forEach` / `try` 容器 | 自己的 `children` 末尾 |

Qt 用「否则」虚拟行的**行号**来表达分支，AST 里 `then`/`else` 本来就是两个列表，直接按列表分即可。
新节点的 `with` 是**空 dict**（与 `create_action_item` 一致），id 用 `n1`/`n2` 跳过已占用。

★ **判据必须能区分「紧跟其后」与「分支末尾」**，否则这条最容易漏过：所以夹具在每个分支里
都放**两个以上**节点，锚点一律取**第一个**。如果实现成「紧跟其后」，断言立刻红。

### `insert_probe.py`（23 条，节选）

| # | 判据 | 读数 |
|---|---|---|
| 2 | 锚点是叶子 → 父容器末尾，不是紧跟其后 | `placed={parent:'root', index:6, anchor:'a1'}`，root 变成 `[…,'n2']` |
| 3 | 锚点是 if → `then` 末尾 | `placed={parent:'if1', branch:'then', index:2}` |
| 5 | 锚点是 else 里的叶子 → `else` 末尾 | `branch:'else'`（没有串到 then） |
| 7-8 | id 不冲突、`with` 为空 | 17 个 id 去重后 17；`with == {}` |
| 13 | 删容器 → 子树一起没 | `removed.ids = [if1, t1, t2, n3, n4, e1, n5]` |
| 14 | 删根 → `ROOT_NOT_DELETABLE` | — |
| 21-23 | SHA 闸门 | 插入与删除都被 `FLOW_CHANGED` 拒，磁盘保持外部内容 |

★ **夹具自己错了两次**：`inputs: {}` 而 condition 引用了 `${inputs.flag}` ⇒ 编译红
`Unknown workflow input reference`（是夹具的错，不是插入的错）；`placed.after` 这个名字
骗人——它其实是 anchor 本身，改成 `anchor`。

### GUI 端到端

点左栏命令 → 真的改 AST → 状态栏 `插入 终止浏览器 → if1/else[1] · 1196→1345 B · 7.9 ms
（anchor r_else）`：因为当时选中的 3 号「返回」在 `else` 分支，落点正确进了 else 末尾；
新卡片出现并**自动选中**——空 `with` 的节点参数全未设置，正好跳到该填的地方（`0/3 已设置`，
`browserType` 显示「未设置 · 默认 msedge」）。

★ **删除按钮的 GUI 点击没验成**（不隐瞒）：`PostMessage` 的鼠标消息在 WebView2 上时灵时不灵
（同一套坐标点卡片能中、点 `×` 不中，`window.confirm` 也没弹）。这不是接线问题——
`×` 和卡片本体用的是**同一个循环里对同一个 `card` 的闭包**，后者已证明「点哪张选哪张」；
后端删除路径 23 条全过。但**「没验过」就是没验过**，记在这里而不是含糊过去。

## 八、未决

- **S6c 剩下的**：拖拽重排（Qt 有 `_insert_item_at_drop`，跨容器移动）；插入控制流节点
  （现在只能插 action，if/forEach 得先有）。
- **覆盖层的工程形状**已清楚但未实现进原型：抠色基色 + 整窗穿透 + 轮询 `GetCursorPos` 三件套，
  交互控件必须另开非穿透小窗口。轮询频率与 CPU 占用未测。
- **打包分发**（ADR 0019 §2.3 第二条）与**桌面集成**（第三条：单实例/全局热键/托盘）都还没碰。
- **`gui.i18n` 的位置债**（ADR 0019 §4-1）：原型依赖它，但冻结期内不能搬。
- 原型目录在 ruff `exclude` 里，不进 lint/文档门禁 —— 有意如此（脚手架 ≠ 产品代码），
  代价是质量只能靠人工与实证。**S6a/S6b 之后这笔债更实了**：`api.py` 里的
  `_Flattener` / `_field_descriptors` / `_parse_literal` 已经是真业务逻辑，却仍无 lint 无单测，
  只有手写探针盖着。
- 原型**只写 `_sandbox/` 里的副本**（`sandbox/` 在流程下拉里单独分组）：保存试验绝不碰
  `examples/` 与 `workflows/` 的真实流程。
