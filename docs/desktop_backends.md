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

## 图像定位驱动（`desktop.image`，M75 / ADR 0003 第 4 层）

`desktop.imageLocate` 是 UIA/win32 之外的**第三条显式驱动**，专给自绘 / DirectX / canvas
（微信自绘区等 UIA 语义全空）控件用：拿指定窗口截图 + 目标模板图或文本关键词 → 回屏幕绝对
矩形（`left/top/width/height/count`）。模板匹配复用 ddddocr 的 `cv2.matchTemplate(TM_CCOEFF_NORMED)`
（经 `workers/ocr_worker.slide_match`），文本定位复用 OCR 检测框，不建第二套图像引擎。

**铁律：不静默降级**（ADR 0003）。UIA/win32 找不到元素时**绝不自动**改走图像——必须由 workflow
显式选择 `desktop.imageLocate` 命令，或元素 locator 显式声明 `driver/image backend=image`
（`capture.image_guard.is_explicit_image` 是唯一判定）。执行器回传 `diagnostics.usedLayer="image"`
记录实际使用层。图像坐标受 DPI、缩放、主题、遮挡影响，稳定性低于语义层，能走 UIA 就别走图像。

- 输入：`windowHandle`（必填，不跨窗口盲搜）、`imageTemplate`/`imageKeyword`（二选一）、
  `templateSize`、`minConfidence`（默认 0.8，低于判未找到）。
- 输出：`left/top/width/height`（屏幕绝对）+ `count`（>1 表示多命中歧义）。
- 零命中报 `ELEMENT_NOT_FOUND`，不会返回近似矩形。
- GUI 接线（命令中文标签、属性面板字段、节点卡片）在 `ppx-only` 分支维护；能力层禁止
  `import ppx_py`（ADR 0019）。

## 捕获期图像 proposal（M77）

`rpa-core capture desktop --image`、PPX 的「按图捕获」按钮是**用户显式选择**的图像捕获
入口：从光标周边裁一块窗口截图做模板，产出与 M75 同形状的 `driver=image` locator
（`windowHandle` / `imageTemplate` / `templateSize` / `minConfidence`），随元素一起入库。
没有这个开关时，捕获候选仍只来自 UIA 语义属性——自绘控件语义三属性全空 ⇒ 不产候选，
绝不因为「语义没找到」就自动改走图像（ADR 0003）。

proposal 在候选表里标 `kind=image` / `proposed=true` / `matchedCount=0`：它是「待校验的
模板」，不是实测命中；真正的命中数由之后的 `elements verify --live` 或运行期
`desktop.imageLocate` 给出。语义候选仍由原候选权威产出，图像记录只是追加，不会替换
语义择优逻辑。

## 桌面可见反馈与动作前置（M78）

`flash` 模式现在会在命中矩形外沿画一次瞬时红框（`capture/desktop_highlight.py`，置顶、
点击穿透、只留边框带），`preview`/`clear` 不画也不抢焦点。`--live --shot` 时桌面腿走
`shot_for_desktop` 把 `{dataUrl, box}` 挂到统一回传的 `shot` 键；不带 `--shot` 时输出形状不变。
高亮/截图失败都吞掉，只影响观感，不改 `count`。

执行层 uia 与 win32 的 `click` / `input` / `getText` 在元素动作前会把会话窗口
`bring_to_foreground`——元素 `set_focus()` 只管控件焦点，管不了顶层窗口被谁挡着。
前台锁定导致置前失败时照常执行，不把观感问题升级成功能故障。

## 真机复验结论（M79）

真机跨窗口校验有两条容易漏的接线：元素资产里的 locator 是 JSON `dict`，执行器 `_find`
吃 `DesktopLocator`，必须在 verifier 的生产 finder 入口归一化；元素捕获时记下的
`metadata.windowHandle` 要交给默认桌面 verifier，让它**先查原窗口**再按 Z 序兜底——
两个窗口有相同 locator 时，纯 Z 序会命中遮挡用的那个（M79 真机实锤）。

显式图像捕获（`--image`）不依赖 UIA 语义树：自绘控件上 `ElementFromPoint` 可能直接
`COM 拒绝访问`，这时仍用 `scope_hwnd` 产出图像 proposal；非图像模式保持原行为，异常照旧抛出。
语义空命中不会因此静默降级到图像（ADR 0003 不变）。

## 活体校验入口（M76）

`rpa-core elements verify --live [--mode flash|preview|clear] [--shot]` 是统一校验门面
（`capture.verification.verify_element`）的用户入口——**不加 `--live` 仍是原来的静态文档校验**
（M10 口径不变）。`--mode` 与 Web 三 mode 对齐，`--shot` 只索取截图元数据；命中 0 个不是
调用失败，`error` 非空才表示「校验通道坏了」，两者在 JSON 里分开。

PPX 原型的资产行「校验」按钮走同一个门面：`rpa.verifyElement(path, name, mode, want_shot)`
只读元素文件后转调，不复制 kind 分派或浏览器目标选择。Qt 源码仍冻结（ADR 0019），本分支
只改 CLI 与 `.harness/demo/ppx_probe/app/`。

## 模板 V2 与 DPI 自适应（M80）

图像模板不再只是「一张 base64 + 尺寸」。M80 起 locator 带 provenance：

```json
{
  "driver": "image", "backend": "image", "windowHandle": 10159846,
  "imageTemplate": "<base64 png>", "templateSize": [32, 32],
  "templateSchema": 2, "templateHash": "…",
  "captureDpi": 96, "captureScale": 1.0, "sourceRect": [904, 138, 32, 32],
  "minConfidence": 0.8, "proposal": true
}
```

- **`captureDpi` / `captureScale`** 来自 `capture/dpi.py`（唯一取数权威），由
  `screen_shot.grab_window` 一并回传；拿不到就不产出模板，**不猜 96/1.0**。
- **匹配只有一处缩放逻辑**：`image_locator.locate_by_template(capture_scale, current_scale)`
  按 `current/capture` 比值生成候选尺度，命中后用该尺度的实际模板尺寸还原屏幕矩形，
  并在回传里带 `usedScale`（1.0 = 原尺寸，0.8 = 从 125% 模板缩回）。
- **V1 模板不迁移**：没有 `templateSchema=2` 的旧模板在静态校验（`selector_errors`）与
  活体校验（`verify_element`）两条路径都返回 `template-schema-requires-recapture`，
  要求重新捕获，而不是按错误的像素口径继续匹配。
- `--shot` 给的是**用于匹配的那张图**加图内命中框（同源像素），不重新截图——否则框和
  命中矩形来自不同帧，元素一动就对不上。

跨 DPI 的真机口径要分清：本机 100% 环境下验证的是「真机像素 + 模拟 `captureScale`」，
证明重采样链路成立，不等于真的在 125% 缩放的机器上跑过。

## 微信自绘实测（M81）

微信 4.x 是 Qt 渲染（窗口类 `Qt51514QWindowIcon`），可执行文件是 `Weixin.exe`：

- **UIA 树是空的**：`descendants()` 里 `controlType` 集合为空，没有一个 Button/Edit/ListItem。
  语义定位（uia/win32）在微信上不可能成立，只能显式走图像——这不是"降级"，是唯一可行路径。
- 真机闭环：`--image` 捕获导航栏图标 → V2 proposal（`matchedCount=0`，不冒充实测）→
  活体校验 `count=1`、矩形与 `sourceRect` 逐像素一致、`usedScale=1.0`；
  模板放大到 1.25 倍后 `usedScale=0.8` 且矩形不变；关掉重采样则 `count=0`。
- **识别微信别靠类名**：按进程路径匹配 `weixin.exe` / `wechat.exe` / `wxwork.exe`。
  探针曾只认 `WeChatMainWndForPC` 与标题「微信」，把一个开着的主窗口判成「不存在」。

## 图像元素作为可执行目标（M82）

M77/M80 解决了「图像元素怎么产生」，M82 补的是「产生之后怎么用」。此前 `desktop.findElement`
与所有元素动作都只走 `DesktopLocator`（`backend` 限 `uia`/`win32`，未知字段被 `extra="forbid"`
判非法），于是图像元素捕得到、存得下、`elements verify --live` 也校验得过，**却点不动**——
运行时抛的是 pydantic `ValidationError`，看起来像内部崩了。

现在的口径（实现在 `executors/image_target.py`，两个后端共用同一份）：

| 环节 | 约定 |
|---|---|
| 认不认图像 | 只认元素自己显式声明 `driver="image"`（`image_guard.is_explicit_image`），语义空命中仍**绝不**降级 |
| `findElement` | 接受 image locator，定位成功发 `elementId`；**要求唯一命中**（多处报 `ELEMENT_AMBIGUOUS`） |
| 旧模板 | V1（无 `templateSchema`）报 `INVALID_INPUT` 并提示重新捕获，**一次匹配都不跑** |
| 动作面 | 只有 `desktop.click`：按 `clickPosition` 算落点（center/random，随机带与 UIA 路径同源），合成鼠标，支持双击/右键/辅助键 |
| 其他动作 | `getText` / `input` / `select` / `screenshot` / `drag` 给干净 `INVALID_INPUT`，不读空串、不假装输入成功 |
| 窗口句柄 | 优先用元素自己的；该句柄已失效（目标应用重建了窗口）时退回会话句柄，并把 `windowHandleSource` 写进证据 |
| 证据 | `path="image-mouse"`、`usedLayer="image"`、`usedScale`、`matchedCount`、`point`、`rect` |

**能力面差异**：图像元素没有 UIA 控件，`simulateHuman=false` 的 `invoke()` 路径不存在，
所以图像点击永远是合成鼠标，退让会写进证据的 `note`；`simulateHuman=false` 与
`clickPosition=random` 仍互斥（与 UIA 路径同一条口径）。

**宿主入口**：PPX 工作台的「按图捕获」（`.harness/demo/ppx_probe/app/gui/index.html`）
已经接通 `rpa.startCapture(image=true)` → `build_hybrid_session(image=...)` →
`DesktopCaptureSession(hover=True, image=True)`。M82 当时写的「PPX 编辑窗口不在本仓」
是错的——`Grep` 默认跳过 `.harness/` 隐藏目录，那次搜索没看到消费者。
这条链路真正跑不通的原因是 M83（桌面腿子进程秒退），不是 UI 缺开关。

## 桌面捕获子进程的导入路径（M83）

`DesktopCaptureSession` 用 `[sys.executable, "-m", "rpa_core.capture.desktop_agent"]`
拉子进程。宿主若用 `sys.path.insert` 把 `src/` 挂进来（PPX 就是），父进程能 `import
rpa_core`，**子进程不能**——`sys.path` 不出进程。表现是子进程 `ModuleNotFoundError`
秒退，而界面侧只有「按 F9 没反应」。

统一口径：`rpa_core.procenv.child_env()` 是唯一权威，它读 `rpa_core.__file__` 的父目录
前置进 `PYTHONPATH`。四处于 `-m rpa_core.*` 的 spawn 必须传 `env=child_env()`：
捕获 agent、点击钩子（`desktop_click_hook`）、OCR worker、Python worker。各写一份拼接
就会漂移，漏一处就是一条静默失效的腿。

配套的可见性：桌面腿出局而扩展腿还活着时，会话不会收场（`_exhausted` 不成立），
所以 `HybridCaptureSession.desktop_failure` 会记下**带 `error` 的**结果，
`desktop_status()` 据此把浮窗文案从「桌面：就绪」改成「桌面：捕获不可用（原因）」。
纯 `timeout`（用户还没按热键）不算故障，不弹红字。前缀刻意中性——原因可能是进程起不来、
捕获期内部错误（M85）或「读不到该位置的元素」，写死「起不来」会把用户引去查环境。

## 图像模式的粒度与让位（M84）

**「红框」必须就是「要截的那块」。** 图像模式真正落盘的是光标周围 `2×radius` 的模板
（默认 32×32），而 hover 高亮此前画的是**语义元素**矩形。对自绘控件（微信 Qt）没有控件树
可言，语义命中只能退化到整窗 ⇒ 用户看到框住整个窗口、确认框写着「桌面控件」，既看不出
粒度也没法对准按钮。现在两边都走 `image_proposal.clip_template_rect()`：

- 高亮：`desktop_agent._image_box_at()` → `show_rect(四边)`，win32 取窗口矩形，**不查 UIA**
  （UIA 单次命中真机 200~900ms，框追不上光标，更别说对准 32×32）；
- 落盘：`make_image_proposal()` 用同一函数算 `sourceRect`，真机负坐标窗口逐值相等；
- 预览：图像主定位时 `metadata.rect` 是这块模板，不是整窗；
- 文案：PPX 确认框显示 `图像 32×32 @ (x, y)`，不再落回「桌面控件」。

**「有没有网页内容可让」按进程名判，不按类名。** `Chrome_RenderWidgetHostHWND` 不只浏览器有：
Electron 定制客户端（WorkBuddy / 咚咚 / VS Code）的渲染子窗同名。只看类名会把它们整片当成
网页内容区——高亮被隐藏、`Ctrl+Click` 被放行，而它们页面里没有我们的扩展，用户侧就是
「悬停毫无反应、连窗口都认不出」。权威是 `foreground_window.is_browser_content()`
（类名 + 进程名，进程名取不到就不认）。agent 与点击钩子共用这一份，不许各自再抄类名集合。
另外：**图像模式永远不让位**——扩展没有图像捕获能力，让位只会让「按图捕获」在这些窗口上变成哑的。

**hover 循环的变量槽位不能串用（M85）。** `_hover_capture` 里 `last_rect` 专放 **UIA RECT
对象**（`_rect_contains` 按 `.left/.right` 读），图像分支的模板框是四元组——曾经把后者写进
前者，下一帧就 `AttributeError`，而 hover 循环没有兜底，整个 agent 秒退，父端只报
`exited with code 1`，用户看到「鼠标一动就报捕获进程起不来」。教训是双向的：
① 图像框只进 `last_box`；② `main()` 现在会把未处理异常转成一行结构化
`{"error": "桌面捕获内部错误：…"}`（traceback 仍进 stderr），**任何**新的循环内异常
都不再伪装成「环境没装好」。

## 选择指引

- 目标是自有 WinForms/WPF 业务应用 → `desktop.uia`（优先 `automationId`，重命名控件不影响 RPA）。
- 目标是系统对话框、菜单操作、无 UIA 暴露的老应用 → `desktop.win32`。
- 目标是自绘 / 无语义树控件（微信 4.x、canvas）→ `desktop.imageLocate`，**显式选择**且接受
  图像定位的遮挡风险；模板带 V2 provenance，跨缩放由重采样处理，但同一模板在不同主题/皮肤下
  仍需重新捕获。先给窗口句柄，模板或关键词二选一。
- 混合场景（如记事本打开对话框）可在同一 workflow 中混用两个后端的命令，session 互不干扰。

## 无障碍树「休眠」与唤醒（M86）

有些 Qt 应用（微信 4.x 是典型）把无障碍树做成**按需暴露**：`QAccessible` 的 active
开关冷启动为 0，标准 UIA / MSAA 只拿得到 Qt 外壳——主窗 `Descendants` 恰好 1 个
`MMUIRenderSubWindowHW`，MSAA `accChild` 全部 `E_INVALIDARG`，`QueryInterface`
到 `IAccessible2` 直接 `E_NOINTERFACE`。**这不是「应用没有控件树」，是开关没开**，
而捕获 UI 上看起来完全一样：框得出窗口，框不出控件。

唤醒机制（`capture/accessibility.py`，对齐影刀 `activity_weixin_v4/wechat.py`）：

1. 在目标 dll 里定位那个 active byte —— `qt.accessibility.core` 的 RIP 引用点附近
   找 `48 85 C9 0F 84 ?? ?? ?? ?? 80 3D <disp32> 00 0F 84`，目标必须落在**可写**段。
2. 读当前值；为 0 才写 1（写前留原值，`restore()` 可还原），写完**读回校验**。
3. 发 `WM_GETOBJECT(OBJID_CLIENT)`，目标进程立刻开始返回真正的 provider（微信是
   `mmui::MainWindow`），**不需要重启**。

实测（微信 4.2.0.13）：gate 在 `Weixin.dll+0xB50ABC8`，磁盘文件里是 1、进程内存里是 0；
写 1 后主窗 `Descendants` 从 **1 变 118**，出现 `mmui::MainView`(aid=`MainView`)、
`mmui::MainTabBar`(name=`导航`, aid=`MainView.main_tabbar`)、`mmui::XTabBarItem`×4。

开关与边界：

- 宿主通过 `DesktopCaptureSession(wake_accessibility=True)` 下发，默认**开**；
  `desktop_agent --wake-accessibility` 是唯一实际执行点。
- 只在「类名是 Qt 窗口壳 + 子节点 ≤1」时尝试；失败写 trace
  （`accessibility_wake_failed` / `accessibility_wake_error`），**不打断捕获**。
- **不是「一次成功就永久有效」**（M87）：目标会在若干秒后自己把 gate 关回去（真机 trace
  里下一次唤醒是 `before=0`）。所以改成**节流重试**——同一进程每 `_WAKE_INTERVAL`（1s）
  最多试一次，`activate` 幂等（gate 已是 1 就只读一字节 + 补发 `WM_GETOBJECT`）。
  「按下捕获键」那一刻用 `force=True` 绕过节流，保证读到的树是真实的。
- **唤醒必须发生在 hit-test 之前**：树休眠时 `ElementFromPoint` 会抛 `E_ACCESSDENIED`，
  写在它之后的唤醒会被异常跳过，形成「休眠 → 命中失败 → 不唤醒」死锁。
- hover 的命中测试只在鼠标移动时跑，所以另有**与移动无关的保活块**：每 1s 兜一次；若这次
  把 gate 从 0 写成 1（`_maybe_wake_accessibility` 返回 True），立刻强制重做命中测试，
  红框不会停在整窗外框上等用户再挪鼠标。
- 不注入 dll、不自建 provider、不做图像匹配；只读写目标进程的**一个字节**。
- 只对模块名 `Weixin.dll` 生效（`activate(dll_name=…)` 可换）。其它 Qt 应用要通用化，
  需要「遍历模块找含该字符串的那个」，暂未做。
- 没做「设系统读屏标志 + 重启目标」那条持久路径（`SPI_SETSCREENREADER`）。若目标关 gate
  的周期短于 1 秒，保活会来回抖动 —— 那时才需要持久路径。
