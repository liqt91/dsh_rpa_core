# M30 桌面通道命令参数漂移收口

状态：`done`

S1–S5 全部完成（2026-09-21）。

关联：M29（浏览器通道同口径收口、`check_param_consumption.py` 诞生）、M7（win32 `timeoutMs` 对称
— 当时只做了 `findElement`）、`docs/yingdao-web-cmds-benchmark.md`（影刀语义基准）

## 为什么有这一里程碑

M29 收口浏览器通道后，BACKLOG 里留了一条「桌面/数据通道参数漂移复核」，理由写的是
**「这两类通道的命令分派不是 `command == "<id>"` 字面量形状（走注册表/helper），静态切片不适用」**。

**这个结论是错的**，M30 开工第一步就核实了：

- `executors/desktop.py` 用 `if command == "desktop.attachWindow":`——字面量；
- `executors/desktop_win32.py` 用 `if command == "desktop.win32.click":`——字面量；
- `workers/python_worker.py` 用 `if invocation.command_id == "data.writeText":`——**也是字面量**，
  只是载体名是 `invocation.command_id` / `invocation.inputs`，而不是裸 `command` / `inputs`。

真实原因很朴素：**当时 `EXECUTOR_FILES` 只登记了 `"browser."` 一条前缀**，其余通道是被跳过的，
不是「不适用」。门禁如实打印了跳过条数，但归因写错了，而错误的归因会让人以为这里没法机器校验、
于是继续靠人工复查。

泛化载体名（`command` / `invocation.command_id`，`inputs` / `invocation.inputs`）并登记实现文件后，
**78 条命令全部可切片，跳过 0 条**，一次挖出 12 条命令的参数漂移。

## 审计结果（泛化后）

| 命令 | 未消费参数 | 处置 |
|---|---|---|
| `desktop.click` | `clickPosition`、`simulateHuman`、`timeoutMs` | 实装 |
| `desktop.getText` | `timeoutMs` | 实装 |
| `desktop.input` | `timeoutMs` | 实装 |
| `desktop.win32.click` | `clickPosition`、`simulateHuman`、`timeoutMs` | 实装 |
| `desktop.win32.getText` | `timeoutMs` | 实装 |
| `desktop.win32.input` | `timeoutMs` | 实装 |
| `desktop.win32.hotkey` | `timeoutMs` | **删除** |
| `desktop.win32.menuSelect` | `timeoutMs` | **删除** |
| `desktop.attachWindow` | `className` | 实装（win32 已支持，补 uia 侧对称） |
| `browser.upload` / `browser.download` / `browser.handleDialog` | 整表 | 豁免（分支体 `COMMAND_NOT_FOUND`，属待实现清单） |

## 口径（沿用 M29，并补一条冲突规则）

1. 能兑现的**实现它**，兑现不了的**从 manifest 删掉**——不留「默认值存在但无作用」的假开关。
2. **能退让就退让，互斥就报错**：
   - 浏览器 `simulateHuman=false` + 双击/右键/带辅助键 → 退回事件链（事件链表达得了，只是不走最短路径）；
   - 桌面 `simulateHuman=false` + `clickPosition=random` → **显式 `INVALID_INPUT`**：`invoke()` 没有坐标
     概念，两条要求互斥，静默按中心点就是新的漂移。

## 任务（切片）

- [x] **S1 门禁口径扩展到全部四类通道**（已完成）
  - `check_param_consumption.py`：命令载体接受 `command` 与 `invocation.command_id`（Attribute/Name
    两种形状），输入容器接受 `inputs` 与 `invocation.inputs`；`EXECUTOR_FILES` 登记
    `desktop.uia` / `desktop.win32` / `python.worker` 的实现文件。
  - 未清项进 `KNOWN_GAPS` 台账（**本里程碑内必须清零**，台账清空是本片收口的判据之一）；
    同时把 docstring 与 BACKLOG 里的错误归因改成事实。
  - 验收：切片覆盖 78 条命令、跳过 0 条；临时插一个假开关（桌面通道）立刻红；
    既有的浏览器通道结论不变（回归）。
  - **结论**：75 checked / 3 exempt（未实现）/ 0 skipped。台账改为**按参数登记**并**自我收紧**
    （登记的参数一旦被消费或从 manifest 删除即报「台账过期」），避免整条命令豁免掩盖新漂移。
    负向验证 4 例全红。命令总数修正 79→78。
- [x] **S2 `timeoutMs`：实装 6 条 + 删除 2 条**（已完成）
  - 语义（对齐影刀「等待目标元素存在的超时时间」与浏览器通道的共享定位器）：**等待目标元素存在的
    最长时间**。现在 `desktop.click/getText/input` 是一次性 `_find`，元素还没渲染出来就直接
    `ELEMENT_NOT_FOUND`；浏览器通道同一个参数却是真等，属跨通道不一致。
  - 做法：把一次性解析换成共享的**等待式解析**（deadline 轮询，命中即返回；超时仍报
    `ELEMENT_NOT_FOUND`，`details` 带等待耗时与轮询次数）。`timeoutMs` 为 0/未给出时行为不变
    （不等待）——**向后兼容**。
  - 删除 `desktop.win32.hotkey.timeoutMs`（`send_keys` 是全局按键，没有目标可等）与
    `desktop.win32.menuSelect.timeoutMs`（`_menu_select` 同步走菜单栏，等待无意义）。
  - 验收：给定 `timeoutMs` 后元素「稍后出现」能成功、「始终不出现」在超时后失败且**真的等满了**；
    未给 `timeoutMs` 时不引入任何额外等待（用单调时钟断言）。
  - **结论**：`wait_for_element` 落地在 `executors/base.py`；等待预算把节点超时与执行器操作超时
    抬到 `timeoutMs + 1s`（`resolve_node_timeout_seconds`），解决「两个超时打架」。
    踩坑：初版用 `time.monotonic()`（Windows 上分辨率 15.625ms）导致 50ms×2 读成 94ms，
    改 `perf_counter()`。`tests/contract/test_desktop_params.py`（13 项，含负向验证）。
- [x] **S3 `click.simulateHuman` / `clickPosition` 实装（uia + win32）**（已完成）
  - `simulateHuman=false` → 走 `invoke()`（UIA/MSAA Invoke，**不移动真实鼠标**）；`true`（默认）→
    `click_input()`（真实鼠标）。这正是两后端代码里已经存在、但当前永远优先走后者的一对分支。
  - 仅**普通左键单击**时 `false` 走最短路径；双击/右键/带辅助键时 `invoke()` 表达不了 → 退回真实鼠标
    路径（与浏览器通道同一条规则，manifest 说明里写清）。
  - `clickPosition=random` → 元素矩形内偏中心带（15%~85%）的随机点，`click_input(coords=...)`；
    落点写进执行证据。与 `simulateHuman=false` 互斥 → 显式 `INVALID_INPUT`。
  - 验收：路径选择矩阵（单击/双击/右键/辅助键 × true/false）、随机点落在元素矩形内且非恒定、
    互斥组合报错而不是静默。
  - **结论**：决策层是 `executors/base.py` 的 `plan_click` / `plan_click_for_element`（纯函数 +
    能力探测），两后端共用；退让原因写进 `effect.details.note`。manifest 说明改成诚实版本
    （写明 `invoke()` 路径的限制与互斥），`INVALID_INPUT` 补进两份 `errors`。
    测试 `tests/contract/test_desktop_click_plan.py`（60+ 项）。
- [x] **S4 `attachWindow.className` 后端对称**（已完成）
  - `desktop.attachWindow`（uia）声明了 `className` 但实现忽略，而 `desktop.win32.attachWindow`
    按类名过滤——同一份 manifest 参数面，两个后端行为不一致。
  - 做法：uia 侧补窗口类名过滤（走已有的 `FindWindowW` 快路径，不做全桌面枚举）；
    补对称性契约测试（两后端同一 `className` 语义）。
  - 验收：指定类名后不匹配的窗口被排除；`className` 为空时行为不变。
  - **结论**：`_find_windows_by_title` 增加第 4 个参数 `class_name`——
    `exact` 路径用 `FindWindowW(class_name, title)` 顺带过滤（省一次枚举），
    再用 `GetClassNameW` 复核；`contains`/`regex` 路径在枚举回调里做等值比较。
    **`matchMode` 只作用于 title，className 恒为等值**（与 win32 侧口径一致，写进 manifest 说明）。
  - **顺手补齐的两处不对称**（都在 `test_desktop_attach_window.py` 里钉住）：
    1. uia 侧 `title` 原本是 `required`，win32 侧不是——同一命令两套必填口径。
       现统一为「都可省」，并给两侧都加「一个筛选条件都不给 → `INVALID_INPUT`」的前置守卫
       （否则会退化成枚举全桌面，报错落在 `ELEMENT_AMBIGUOUS`，原因误导）。
       win32 侧原本也没有这个守卫，S4 一并补上（它多了个 `handle` 分支，守卫条件相应放宽）。
    2. `className` 的说明原本只写「窗口类型名（可选）」，看不出与 `matchMode` 的关系；
       现写明「两个后端都是等值比较（不受 matchMode 影响）」。
  - **如实记录的固有局限**（`test_exact_path_sees_only_one_candidate`）：
    `exact` 走 `FindWindowW`，该 API **只返回第一个**匹配句柄，因此「同标题同类名的两个窗口」
    在 exact 下不会报 `ELEMENT_AMBIGUOUS`，而是静默附着第一个——与 win32 侧（全枚举后逐个过滤，
    能发现歧义）行为不同。这是**取舍不是 bug**：exact 的价值就是绕开全桌面 UIA 枚举
    （慢 provider 可达 ~60s）。需要歧义检测时用 `matchMode=contains`（两后端都能报）。
  - 副作用：`KNOWN_GAPS` **清零**（最后一条 `desktop.attachWindow.className` 已消费）。
- [x] **S5 测试 + 文档 + 收口**（已完成）
  - 契约/单测：等待语义（含「真的等满」）、click 路径矩阵、className 过滤、互斥参数报错；
  - 文档：`docs/element-mvp-boundaries.md` §3 补桌面通道口径（与浏览器通道的差异要写清），
    `docs/command-optimization-plan.md` / `docs/yingdao-web-cmds-benchmark.md` 里受影响的对齐结论同步；
  - `KNOWN_GAPS` 清零；任务单/`project_state`/`feature_list`/PROGRESS/WORKLOG/BACKLOG 收口；full gate。
  - **结论**：三份测试齐备（`test_desktop_params.py` 13 + `test_desktop_click_plan.py` 64 +
    `test_desktop_attach_window.py` 29 = 106 项）。
  - **文档同步清掉一处过期结论**：`element-mvp-boundaries.md` §3.4 原来还写着「桌面/数据通道的分派
    不是 `command == "<id>"` 字面量形状，静态切片不适用」——**这正是 S1 推翻的那句**（S1 当时只改了
    门禁 docstring 与 BACKLOG，漏了这份面向维护者的文档）。§3.4 已改写为「M30 已全部纳入、跳过 0 条」
    并补上四条通道的处置表 + 与浏览器通道的两条口径差异（`timeoutMs` 抬高上两层超时；互斥显式报错）。
    **教训**：纠正一个错误结论时，要搜全仓所有记录该结论的地方，不能只改眼前那一处。
  - `command-optimization-plan.md`：`click` 族补 M30 落地记录（含两个真 bug）；`attachWindow` 段补
    `className` 对称与 `matchMode` 只管 `title` 的口径；验收清单两条更新为如实状态。
  - `yingdao-web-cmds-benchmark.md`：横切模式 3（等待尾参）补 M30 更新——
    **「声明了不生效」已收口，但「统一带上」仍未做**（不把两件事混为一谈）。

## S4 追加发现（门禁的负向验证本身踩了一个坑）

`check_error_contract.py` 是**单向**门禁（只查「实现了但没声明」）。S4 给它做负向验证时，
第一次用的是「删掉一个已声明的返回点」——**门禁不报红**，因为声明比实现多是**设计允许**的
（防御性声明、跨后端兼容）。当时差点把它当成「门禁失效」去改门禁；其实是我打错了方向。

正确做法：往命令分支里注入一个**未声明**的错误码，门禁立刻报红：
`desktop.win32.attachWindow: 实现会返回但 manifest.errors 未声明 → SANDBOX_VIOLATION`。

这条已写进该门禁的 docstring（「单向 = 单向的负向验证」），免得下一个人重复踩。
**教训**：负向验证要打在门禁实际检查的方向上，否则「删了也不红」会被误读成门禁没生效。

## S3 追加发现（不在原审计清单里，收在同一片）

S3 动的是「参数读完之后怎么用」，于是挖出两个**参数消费门禁结构上查不出**的真 bug——
参数确实被读了，错的是读完之后调用的 API：

1. **双击一直是坏的**。两处点击分支写 `click_kwargs["click_count"] = 2`，而 pywinauto 的
   `click_input()` **没有** `click_count` 参数（基础包装类与 `controls/common_controls` 的包装类
   都没有）→ 一直是 `TypeError`。正确写法 `double=True`。
2. **辅助键一直是坏的**。写的是 `pywinauto.keyboard.key_down("control")` / `key_up(...)`，
   而 pywinauto 0.6.9 的 `keyboard` 模块**没有** `key_down` / `key_up`（只有
   `send_keys` / `parse_keys` / `KeyAction` 家族）→ 带辅助键的点击一直
   `AttributeError → EXECUTOR_FAILED`。正确写法 `send_keys("{VK_CONTROL down}")`，
   抬起写在 `finally` 里（新增 `executors.base.click_with_modifiers` 与 `modifier_key_sequences`）。

第 2 条同时暴露了 `desktop.win32.menuSelect` 早就在返回 `INVALID_INPUT`（空 `menuPath`）却没声明。
顺着这条线做了一次全库统计：**实现会返回但 manifest.errors 未声明的命令只有 3 条**
（`desktop.click`、`desktop.win32.click`、`desktop.win32.menuSelect`，全是 `INVALID_INPUT`），
另外 3 条是 `COMMAND_NOT_FOUND` 的「本期未实现」占位（与参数门禁同一套豁免）。
于是把这类漂移也做成了门禁：`.harness/scripts/check_error_contract.py`（复用
`check_param_consumption.py` 的命令切片基础设施），已进 `check_all.py`，负向验证 2 例全红。

**未收（留给后续切片）**：`simulateHuman` 的**归一化口径四端不一致**——
桌面/浏览器执行器写 `bool(inputs.get("simulateHuman", True))`，于是 `"simulateHuman": "false"`
（手写/导入的工作流 JSON）被当成 **true**，而 `null` 又被当成 false；浏览器扩展侧是
`String(raw ?? "").trim().toLowerCase() !== "false"`（`null` → 开、`"false"` → 关）。
桌面侧 `executors/base.py` 已有容错查表（`MODIFIER_VIRTUAL_KEYS_LOWER`）证明这个方向是可行的。
统一会牵动 `browser.py` 与 `scripts/check_click_helpers.mjs` 的反漂移断言，属独立切片。
`tests/contract/test_desktop_click_plan.py` 用 `xfail(strict=True)` 钉住了现状：真去统一时
会由 xfail 变 xpass 并立刻报红，提醒摘掉标记。

## 验收

- 四类通道（浏览器/桌面 UIA/桌面 Win32/数据 worker）**全部**纳入参数消费门禁，跳过 0 条；
- 桌面通道不再存在「声明了、但执行器不会读到」的参数，`KNOWN_GAPS` 为空；
- 每条删除/实现都有可追溯理由（代码注释 + manifest 说明 + 文档 + BACKLOG）；
- 每切片：契约测试 + `check_all.py` 全门禁通过；PROGRESS 追加一行。

## 风险 / 注意

- **桌面通道没有默认可跑的 E2E**（真实桌面 E2E 会抢前台，AGENTS 已定按需启用）。因此等待语义与
  路径选择用 `unittest.mock.patch` 打桩 `_find` / 伪元素对象做契约测试，真机验证留待维护者需要时按
  任务单「未覆盖」清单复验——**这一点要在文档里写明，不能拿打桩测试冒充真机结论**。
- `test_desktop_contract.py::test_desktop_backend_manifests_share_lifecycle_contract` 要求
  `attachWindow`/`findElement` 两个后端都有 `timeoutMs`——删的是 win32 独有的
  `hotkey`/`menuSelect`，不冲突。
- 各后端 `pywinauto` 版本差异：`click_input(coords=...)` 的坐标是**元素相对坐标**，实现前先按
  安装版本核对签名（M30 内已核对）。
