# Windows 桌面双后端分工（desktop.uia / desktop.win32）

状态：已验证（M2.2）
关联：ADR 0003、`.harness/tasks/M2.2-uia-winforms.md`

## 定位

| | `desktop.uia`（UIA 语义主线） | `desktop.win32`（Win32 消息主线） |
|---|---|---|
| 适合目标 | WinForms / WPF / 现代 UI（有 AutomationId 的语义控件） | 经典 Win32 应用、系统对话框、菜单栏 |
| 定位方式 | `automationId` / `controlType` / `name` | `title` / `className` / `handle` / `controlId` / `menuPath` |
| 独有命令 | — | `hotkey`、`menuSelect` |
| 典型场景 | 自有业务窗体：输入框、按钮、标签读回 | 记事本"打开"对话框（`#32770`）、菜单路径、键盘快捷键 |

## 共享契约

17 个同名命令（`attachWindow / findElement / click / input / getText / closeSession / activateWindow /
setWindowState / setWindowVisible / moveWindow / resizeWindow / getWindowTitle / getSelectedText /
screenshot / select / drag / getWindowList`）在两个后端之间保持对称：相同
`version / kind / risk / stability / effect / capabilities`，`attachWindow` 输出一致
（`sessionId / processId / workWindowId`）。测试锁定：
`tests/contract/test_desktop_contract.py::test_desktop_backend_manifests_share_lifecycle_contract`。
两后端的命令集差异只允许 `hotkey` / `menuSelect` 两个 win32 独有命令
（`test_uia_and_win32_command_sets_diverge_only_in_win32_extras`）。

## 超时层级与元素等待（M30 S2 定案）

一个桌面节点身上有**四层**超时，作用范围从大到小：

| 层 | 来源 | 范围 | 说明 |
|---|---|---|---|
| 工作流 deadline | `workflow.timeout_seconds` | 整个 run | 最终兜底，任何一层都突破不了它 |
| 节点超时 | `node.timeout_seconds` > `manifest.default_timeout_seconds`（桌面命令为 15s） | 单节点单次 attempt | 编排器 `min(该值, 剩余 deadline)`；GUI 在命令自带 `timeoutMs` 时**隐藏**这个输入框（`param_form.has_own_timeout`） |
| 执行器操作超时 | `operationTimeoutMs` 输入 > 执行器默认 15s | 单次 `_execute_sync` | 防 UIA/COM 调用挂死 |
| 元素等待预算 | `timeoutMs` | 等待目标元素出现 | 语义见下 |

**`timeoutMs` 的语义 = 等待目标元素存在的最长时间**（对齐影刀同名参数与浏览器通道的共享定位器：
浏览器侧把它交给「等待选择器」）。它由 `executors.base.wait_for_element` 实现：在预算内按 100ms
轮询定位，命中即返回；超时仍报 `ELEMENT_NOT_FOUND`，但 `details` 会带上 `waitedMs` / `polls`
（「等了多久、查了几次」——否则一次超时在证据里看不出是不是真的等过）。预算为 0/未给出时
**只查一次**，与修复前行为完全一致。

**关键一条：等待预算会把上面两层抬高到至少 `timeoutMs + 1s`**（`model.command.
resolve_node_timeout_seconds` 与两个执行器的 `execute`）。理由很实在——不抬高的话，用户设
`timeoutMs=30s` 会在引擎默认的 15s 收到 `TIMEOUT`，报错原因看上去是「超时」而不是「元素没出现」，
同一个参数在两层里打架。兜底超时是防挂死的，不该反咬用户显式给出的等待预算。

覆盖命令：`{desktop,desktop.win32}.{click,getText,input}`。
`desktop.win32.hotkey` 与 `menuSelect` **不声明** `timeoutMs`：前者是 `send_keys` 全局按键、
后者是同步走菜单栏，都没有「目标元素」可等——兑现不了的参数删掉，不留假开关；删掉之后 GUI 会
重新渲染节点级「超时（秒）」字段，用户又能回答「这个节点到底有没有超时」了。

> 迁移：`hotkey` / `menuSelect` 的 `input_schema` 是 `additionalProperties: false`，
> 旧流程若引用过 `with.timeoutMs` 会在校验期显式失败（`INVALID_INPUT`），去掉该字段即可。
> 这两个参数从未生效，所以迁移不存在行为变化。

## 点击语义（M30 S3 定案）

`{desktop,desktop.win32}.click` 的 `simulateHuman` / `clickPosition` 此前是**声明了没生效**的
参数：不论填什么，实现都调 `click_input()`（真实鼠标）并且永远点在元素中心。现在按
「**能退让就退让，互斥就报错**」定案，两后端共用 `executors.base.plan_click_for_element`：

| 输入 | 实际路径 | 证据里的字段 |
|---|---|---|
| `simulateHuman=true`（默认，任意按钮/次数） | `click_input()`（真实鼠标） | `path="input"` |
| `simulateHuman=false` + 普通左键单击 | `invoke()`（不移动鼠标） | `path="invoke"` |
| `simulateHuman=false` + 双击 / 右键 / 带辅助键 | `click_input()`（退回，`invoke()` 表达不了） | `path="input"` + `note` |
| `clickPosition=random` | `click_input(coords=...)`，点落在元素矩形内偏中心带（15%~85%） | `path="input"` + `coords` |
| 元素 `click_input()` 不收 `coords`（列表/树/表格包装类）或取不到矩形 | `click_input()` 中心 | `path="input"` + `note` |
| `simulateHuman=false` + `clickPosition=random` | **显式失败 `INVALID_INPUT`** | — |

三条设计约束：

1. **退让必须留证据**。`note` 写清「为什么没按声明执行」——静默退让就是新的参数漂移，
   只是从 manifest 挪到了运行时。证据挂在 `effect.details` 上（`{"operation":"click", path, coords?, note?}`）。
2. **互斥就报错，不猜**。`invoke()` 没有坐标概念，`simulateHuman=false` + `clickPosition=random`
   是两条互斥要求；静默按中心点点下去，用户会以为「随机」生效了。报 `INVALID_INPUT`（已补进
   两份 manifest 的 `errors`，门禁见 `.harness/scripts/check_error_contract.py`）。
3. **未知 `clickPosition` 报错而不是退 center**。manifest 是 enum，能走到执行器说明输入已越过 schema；
   浏览器扩展侧对未知值退 center，桌面侧**不猜**（`plan_click` 抛 `ValueError` → `INVALID_INPUT`）。

`modifiers` 的按住/抬起走 `executors.base.click_with_modifiers`：`pywinauto` 没有
`key_down` / `key_up`，表达「按住某键」要用 `send_keys("{VK_CONTROL down}")`，抬起写在
`finally` 里（点击抛异常也不能把 Ctrl 永久留住）。

> 顺手修掉的两个真 bug（都不是「参数漂移」，门禁查不出来——参数确实被读了，错的是读完之后调用的 API）：
> - 双击写 `click_input(click_count=2)`，而 `click_input()` **没有** `click_count` 参数 → 一直 `TypeError`；
>   正确写法是 `double=True`。
> - 辅助键写 `pywinauto.keyboard.key_down("control")`，而 pywinauto 0.6.9 的 `keyboard` 模块
>   **没有** `key_down` → 带辅助键的点击一直 `AttributeError → EXECUTOR_FAILED`。

**契约测试覆盖到哪里**：`tests/contract/test_desktop_click_plan.py` 用纯函数（路径矩阵、随机落点
带内且非常量、互斥报错）与伪元素（能力探测、修饰键序列、`finally` 抬起）锁住决策逻辑。
**不覆盖真机时序**——`invoke()` 与 `click_input()` 在真实控件上的效果差异（例如某些控件
`invoke()` 不触发与鼠标一致的焦点/悬停链路）、按住修饰键时 `click_input()` 是否稳定继承键态，
都要在真机操作台复验（桌面 E2E 会抢前台，按 AGENTS 的约定按需启用）。

## 窗口附着筛选（M30 S4 定案）

`attachWindow` 两后端共享同一套筛选维度，`title` / `className` / `processId` 之间是 **AND**：

| 维度 | 匹配方式 | 说明 |
|---|---|---|
| `title` | 由 `matchMode` 决定（`exact` / `contains` / `regex`） | 唯一的模糊维度 |
| `className` | **恒为等值比较** | `matchMode` **不作用于它**；大小写敏感、不支持前缀 |
| `processId` | 等值 | — |
| `handle` | win32 独有，直接按句柄附着 | uia 侧没有对应入口（唯一的合法参数差异） |

**至少要给一个筛选条件**，否则返回 `INVALID_INPUT`。此前两后端都是「一个都不给 → 枚举全桌面 →
`ELEMENT_AMBIGUOUS`」，把用户的输入错误伪装成「窗口不唯一」，报错原因指错了方向。

### exact 与 contains/regex 的能力差异（重要）

uia 后端的 `exact` 路径走 `FindWindowW`（毫秒级，绕开全桌面 UIA 枚举——慢 provider 可达 ~60s），
但该 API **只返回第一个**匹配句柄：

| | `exact` | `contains` / `regex` |
|---|---|---|
| 实现 | `FindWindowW` 单窗口直连 | `EnumWindows` 枚举全部候选 |
| 能否发现歧义 | **不能**——同 (标题, 类名) 的两个窗口只附着第一个 | 能，报 `ELEMENT_AMBIGUOUS` |
| 耗时 | 毫秒级 | 随窗口数增长 |
| className 过滤 | `FindWindowW` 的 `lpClassName` + `GetClassNameW` 复核 | 枚举回调里等值比较 |

> `FindWindowW` 的类名匹配**不是逐字节等值**（按类名前缀、忽略大小写），所以拿到句柄后必须用
> `GetClassNameW` 复核，才能与 win32 侧的 `==` 口径一致。

**需要歧义检测时用 `matchMode=contains`**。exact 静默附着第一个是**有意取舍**，不是 bug——
契约测试 `tests/contract/test_desktop_attach_window.py::test_exact_path_sees_only_one_candidate`
把这个差异如实钉住（含「换 contains 就能看见两个」的对照）。

## 已验证的 E2E

- `desktop.uia`：确定性 WinForms 测试应用（`testapps/desktop/Program.cs`，运行时由 .NET Framework 自带 csc 编译）。链路：attach → 按 automationId 定位输入框/按钮 → 输入 → 提交 → 读回标签 → 打开模式对话框 → attach 对话框（第二个 session）→ 输入 → 接受 → 主窗读回 `dialog:<文本>`。测试：`tests/e2e/test_uia_desktop.py`，含连续 3 次运行一致性。
- `desktop.win32`：记事本菜单打开文件链路（M2）。

## 实现注意（已知边界）

1. **UIA 桌面枚举盲区**：部分环境（如远程会话 + 安全软件）下，UIA 桌面子树遍历会漏掉 owned 弹出窗口（`find_elements(title=...)` 返回空），但按 HWND 直连该元素的 UIA provider 完全正常。因此 `desktop.uia.attachWindow` 在树遍历 0 匹配时回落到 Win32 `EnumWindows`（按标题 + 可见性 + 进程过滤）再构造 UIA wrapper——窗口发现跨 UIA/Win32 边界是合理的，UIA 本身也以 HWND 为顶层锚点。
2. **窗口与元素的异步出现**：`attachWindow`（两后端）与 `findElement` 支持 `timeoutMs`（轮询直至唯一匹配或超时）；`click` / `input` / `getText` 的 `timeoutMs` 是 M30 S2 才真正生效的（此前声明了但实现是一次性查找），语义与超时层级见上文「超时层级与元素等待」。
3. **Modeless vs Modal**：测试应用使用 modeless 对话框。模态对话框在点击发起的 UIA `Invoke` 调用内部嵌套消息循环，会阻塞执行器的 COM 调用直至对话框关闭（表现为 attempt 超时）；需要驱动模态对话框时应拆分为独立 workflow 或使用 win32 后端的 `menuSelect`/`hotkey` 路径。
4. **测试应用 GC 陷阱**：modeless form 必须保留托管引用（存为字段），否则 UIA 枚举的 COM 调用会触发 GC 回收未引用的窗体——表现为"窗口短暂存在又消失"。
5. **`attachWindow` 的 exact 路径不报歧义**：见上文「exact 与 contains/regex 的能力差异」。同一标题的多个实例（多标签记事本、同一应用的多个窗口）在 `exact` 下会静默附着**第一个**；要区分请用 `contains`（能报 `ELEMENT_AMBIGUOUS`）或加 `processId` / `className` 收窄。
6. **`attachWindow` 的 +className 过滤尚未在真机复验**：M30 S4 的契约测试用假 `user32`（伪 `FindWindowW`/`GetClassNameW`）覆盖过滤口径，
   **不冒充真机结论**——真实桌面上的类名大小写形态（如 `#32770` 这类数字类名、不同 Windows 版本的 `Chrome_WidgetWin_1`）
   仍待需要时按任务单复验。

## 选择指引

- 目标是自有 WinForms/WPF 业务应用 → `desktop.uia`（优先 `automationId`，重命名控件不影响 RPA）。
- 目标是系统对话框、菜单操作、无 UIA 暴露的老应用 → `desktop.win32`。
- 混合场景（如记事本打开对话框）可在同一 workflow 中混用两个后端的命令，session 互不干扰。
