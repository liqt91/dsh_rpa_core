# M74：统一元素校验编排（跨窗口查找 + 命中前置 + 桌面活体校验）

Status: `done`

> **2026-11-16 立项**。起因：维护者看完影刀逆向产物后定调——「桌面和浏览器捕获无缝衔接、
> 校验时能在桌面所有窗口内找到元素并前置、捕获粒度细到微信控件」是我们要的效果；明确
> **不许用「技术栈不同」当不做的理由**。本任务承接其中的**编排层**（ROI 最高、与定位后端无关）：
> 统一校验入口 + 跨窗口搜索顺序 + 命中即前置 + 桌面侧活体校验。`planned`，不占 active 位（M52 仍主线）。
>
> 前置：M47.9（Z 序最近激活浏览器定点）、M48（三 mode 校验）、M52-selector-advisor、
> M69-S1（元素编辑器）。**ADR 0016**（GUI 主力 / Web 冻结）、**ADR 0019**（Qt 源码冻结）。
>
> ★ 范围边界（先说清不做什么）：**本任务不碰自绘/Qt/微信控件的定位能力**（那是能力层，
> 见文末「不在范围」与 BACKLOG）。本任务只做「编排」——无论元素来自 CSS 还是 DesktopLocator，
> 点校验时能按统一顺序在桌面窗口里找到它、把它前置、让它可见。

## 一、起因（维护者原话与拆解）

> 「桌面和浏览器的元素捕获无缝衔接，校验元素的体验也很好，应该有一个校验顺序以便在桌面
> 所有窗口内找到元素并前置，还有就是元素捕获的粒度很细，连微信里面的控件也能捕获，这是
> 我们要实现的。桌面 pywinauto 不整体替换的原因是技术栈不同？从期望效果上 pywinauto 能
> 达到影刀效果吗」

拆成两层，本任务只做第一层：

| 层 | 内容 | 归谁 |
|---|---|---|
| **编排层（本任务）** | 桌面/浏览器校验统一入口、跨窗口搜索顺序、命中置前、桌面活体校验 | M74 |
| 能力层（另立） | 自绘/Qt/微信控件的细粒度定位（image/OCR 后端、HLay 式渲染层注入） | BACKLOG，后续里程碑 |

编排层与语言/后端无关：Python 完全可做，不需要替掉 pywinauto。

## 二、取证（动手前的现状，逐条实测）

### 2.1 校验现在只有 Web 一条通道，且是「选一个浏览器」不是「找元素」

`src/rpa_core/capture/verify.py`：
- `ElementVerifier._candidates()`（:217-252）：选择顺序 `endpoint → target → Z 序最近激活浏览器 → 全部端点`。
  这一步是**选出要下发的一个浏览器端点**，不是「在所有窗口里找目标元素」。
- `verify()/preview()/clear_preview()`（:199-215）三 mode 走同一条 `_exchange()`（:254-401），
  下发 `capture_verify` 给扩展 content script 现场 `querySelectorAll`，回传 `count`。
- 置前（:266-272）：仅 `flash` 且拿到 Z 序自动识别的 `hwnd` 才 `bring_to_foreground`；
  `preview`/`clear` 不抢焦点。**这是全仓唯一「校验时置前」的实现，且只对浏览器。**

### 2.2 桌面侧没有等价的活体校验

- 桌面查找只有命令 `commands/desktop/findElement.json` + 执行器 `_find`（`executors/desktop.py:969-1035`），
  在**已 attach 的单个窗口会话内**用 UIA 字段匹配，无「点一下闪框看命中几个」的交互。
- Web 桌面两套校验**不统一**：`capture/hybrid.py` 只统一捕获会话，不碰校验。
- 桌面执行 click/input（`executors/desktop.py:532-573`）**不做窗口级前置**——`set_focus()` 是元素焦点，
  不等于顶层窗口 `SetForegroundWindow`。置前只有显式 `desktop.activateWindow` 命令（:294-312）。

### 2.3 Z 序原语已经具备，可复用不必重造

`src/rpa_core/capture/foreground_window.py`：
- `GetTopWindow(NULL)` → `GetWindow(GW_HWNDNEXT)` 链 = 由近到远的激活顺序（:168-245，上限 2048）；
- `_is_user_window()`（:108-119）过滤可见/标题/TOOLWINDOW/桌面壳；
- `bring_to_foreground(hwnd)`（:147-166）已吞异常；
- 现状只用于 `first_browser_window()`（:122-144）——**只找浏览器**，本任务要把它泛化成「所有可见顶层窗口」。

### 2.4 桌面元素模型与定位权威（对接点）

- `model/desktop.py:192-263`：`DesktopLocator`（backend uia/win32 + 字段/path/matchMode/anchor）。
- `capture/desktop_agent.py:584-630`：`_locator_candidates`（候选唯一权威）；`:470-569` hover 快路径。
- 桌面元素与浏览器元素同存 `elements/<name>.json`，靠 `kind` 二分（`model/capture.py`）。
  ⇒ 统一校验入口按 `kind` 分派即可，无需改元素模型。

## 三、做法（一个权威入口 + 两端分派）

### 3.1 统一入口：按元素 kind 分派，形状统一

新增 `capture/verification.py`（编排权威，不碰各端实现）：
- `verify_element(element, *, mode, want_shot=False) -> VerificationResult`：读 `ElementDescriptor.kind`，
  - `kind == "browser"` → 委托 `ElementVerifier`（现有 Web 通道）；
  - `kind == "desktop"` → 委托新增的 `DesktopElementVerifier`（S3）；
  - 未知 kind → 结构化 `{"error": "unsupported-kind"}`，**不静默当 browser**。
- `VerificationResult` 统一字段：`count` / `windowHandle` / `rect` / `error`；两端回传统一到这里。
- **不另建第二套目标选择逻辑**：浏览器的 Z 序选择继续复用 `ElementVerifier._candidates()`。

### 3.2 校验顺序：跨窗口搜索 + 命中即停

桌面校验的搜索顺序（对齐影刀「所有窗口内找到元素并前置」）必须是**单一权威函数**，且顺序确定：
1. 若元素带 `windowHandle`/窗口标题提示 → 先查该窗口（最快路径）；
2. 否则按 **Z 序**遍历所有可见顶层窗口（复用 `foreground_window` 的链与过滤）；
3. 每个窗口内用该元素的 `selector.locator` 走 `_find` 查找；
4. **第一个命中即停**（count>0），记录命中窗口 hwnd + 元素 rect；
5. 全不命中 → `count=0`，不猜、不降级。

> ★ 判据必须钉「顺序」而不只是「找到了」：把命中窗口放到链尾，必须返回更靠前的那个；
> 只断言 `count>0` 是恒绿假判据（M73 教训）。

### 3.3 前置：命中窗口，且只在该前置的模式前置

- 校验命中后，把**命中的那个窗口** `bring_to_foreground(hwnd)`（不是“当前前台窗口”）；
- 与 Web 三 mode 对齐：`flash` 前置，`preview`/`clear` 不抢焦点；
- 置前失败吞异常、只 trace，**绝不让观感优化把校验拖成失败**（沿用 `verify.py:266-272` 口径）。

### 3.4 桌面活体校验通道（DesktopElementVerifier）

- 复用 `desktop_agent` 已有的 UIA/win32 会话能力做现场查找；**不是新起一套定位**（否则又是第二权威）；
- 高亮：桌面侧黄框/闪烁走现有 hover 覆盖层或 capture_kit 浮窗的显隐能力，评估最小实现；
- 返回形状与 Web 对齐（`count`/`windowHandle`/`rect`/`error`），结构化错误不抛异常。

## 三·五、模块边界纪律（2026-11-16 维护者拍板：A 方案）

分支与目录已定：**就在当前 `xhs-workbench` 分支做，模块落 `src/rpa_core/capture/`，不另开分支、不另起目录**。
理由：捕获能力只在 xhs-workbench 上完整（其余分支停在 M52，本分支多 M68~M73 共 1937 行/8 文件）；
`capture/` 属 ADR 0022-R3 的**共享能力层**（三分支都能改、靠显式 cherry-pick 同步），开新分支=从残缺版本起 + 多一条要手动同步的分支；
另起 `capture_v2/` 会造出「两套权威」，正是 M50/M56 的旧坑。

三条纪律，逐片都要守：

1. **单一权威**：Z 序遍历全仓只留一个函数（从 `foreground_window.first_browser_window` 抽出
   「所有可见顶层窗口」原语），浏览器选择与桌面校验都调它，不许各自实现一遍。桌面查找只复用
   `DesktopExecutor._find`，不建第二套定位逻辑（M50 教训）。
2. **按 kind 分派、统一形状**：`verification.py` 是门面，browser 委托现有 `ElementVerifier`、
   desktop 委托 `DesktopElementVerifier`，回传都收口到同一个 `VerificationResult`
   （`count`/`windowHandle`/`rect`/`error`）——不复制 Web 的目标选择逻辑。
3. **不反向依赖**：`capture/` 属共享能力层，禁止 import `ppx_py`，禁止依赖
   `apps/xhs-workbench/`（ADR 0019 门禁已盯）；`capture_kit/`（交互层）本任务不碰。

> 工作区现存未跟踪的 `rpa-ext-bridge-*`/`rpa-m68-*` 等手测残留不属本任务，一律不动。

## 四、切片

| 片 | 内容 | 验收口径 | 状态 |
|---|---|---|---|
| **S1** | 统一入口 `capture/verification.py` + `VerificationResult`，按 kind 分派到现有 Web 通道；未知 kind 结构化报错 | 契约：browser 元素走 Web 通道、形状统一、未知 kind 报错；不改 Web 现有行为（M47/M48 测试全绿）。负向：把 kind 改错必须红 | done |
| **S2** | Z 序跨窗口搜索泛化：从 `first_browser_window` 抽出「遍历所有可见顶层窗口」的原语，桌面校验按序逐窗查找、命中即停 | 契约：搜索顺序可断言（命中窗口在链尾时返回更靠前者）、过滤规则生效、全不命中返 count=0。负向：反转顺序/去掉「命中即停」必须红 | done |
| **S3** | 桌面活体校验 `DesktopElementVerifier`：复用 `_find` 现场查找 + 桌面高亮，回传统一形状 | 契约：命中/不命中/结构化错误三态、桌面 locator 真解析（非替身）、与 Web 回传字段名一致。负向：去掉窗口查找必须红 | done |
| **S4** | 命中窗口置前：flash 模式前置命中 hwnd，preview/clear 不抢焦点，失败无害 | 契约：三 mode 置前行为成对（flash 有、preview/clear 无）、置前目标是命中窗口而非当前前台、失败吞异常。负向：preview 也置前必须红 | done |
| **S5** | 收口：台账四处同步（PROGRESS / feature_list / STATE / 当日 memory）+ 全门禁 | 末行 `FULL GATE PASSED` + 退出码 0；负向验证逐字节还原 | done |

## 五、判据总则（每片都要过）

1. **门禁末行 `FULL GATE PASSED` + 退出码**，只认这一条；报绿必做负向验证。
2. **负向验证四条**：对照跑 / `N failed` 且无 `N error` / 超时=挂住要报出 / 逐字节还原核 md5。
3. **期望失败集按推理补全、不照抄实际**；注入必须是合法语法的精准放宽。
4. **顺序判据写等值断言**，不用 `>=`（阈值松=假绿）；「命中即停」「只找一个窗口」这类负向必须能红。
5. 断言覆盖**用户实际走的路**（统一入口），不是只测内部纯函数；纯函数层绿 ≠ 接线层绿。
6. 新增/修改命令若牵动 GUI / i18n / manifest，按既有四契约同步（本项目 GUI 源码冻结 ADR 0019，
   本任务不新增命令 manifest，若评估需要再单列前置拍板）。

## 六、不在本任务范围（登记 BACKLOG）

- **自绘/Qt/微信控件细粒度定位（能力层）**：影刀靠 `HLayFrontend.dll`（导出 `SelectorFromPoint`/
  `HtmlFromPoint`/`QuerySelectorAll`/`GetAllAttributes`，给自绘界面造 DOM 树）、
  `ShadowBotJavaBridgeHook.dll`、CV/OCR 兜底。我们 `_locator_candidates` 只依赖
  controlType/automationId/name，自绘控件这些为空 ⇒ 候选为空。影像式补齐需新增
  image/OCR 定位后端或 DLL 注入自研控件树，**另立里程碑**，不塞进本次编排改造。
- **操作前自动前置目标窗口**（click/input 前）：与校验前置同族但属执行层，可在 S4 验证后单列。
- **XPath 后端（M69-S2）**、**元素编辑器校验元素/锚点/相似元素（M69-S3）**：各自的既定欠账，不重复立。

## 七、风险与已踩坑

- 真机前台锁定：`SetForegroundWindow` 可能被 Windows 拒（跨进程/无用户输入），置前失败必须无害（已定口径）。
- 沙箱/环境：计时用例易受 `tmp_path` 落盘影响，判红前先 `--basetemp=<系统temp>` 复跑；
  退出阶段非零三条件（rc≠0 / failed==0 / passed>0）缺一不可。
- 别把「选一个浏览器」（现有）误当成「跨窗口找元素」（本任务新增）——这是两个不同函数、两类判据。
