# cua（trycua/cua）适配度评估

调研日期 2026-09-21。回答两个问题：**cua 对我们构成什么挑战**、**哪些能借鉴**。
沿用 `docs/element-self-healing-plan.md` 的口径：外部能力只在「观测层 / 执行层」有参考价值，
「决策层」与我们的确定性定位天然冲突。

## 0. 核实（实测，非印象）

```text
GET api.github.com/repos/trycua/cua → 200
  full_name       trycua/cua
  stargazers      25547        forks 1760        open_issues 1034
  license         MIT          created 2025-01-31   pushed 2026-09-21
GET .../contents/libs/cua-driver/contract/manifest.json
  contract_version 0.8.0   transport mcp_stdio   capability_version 1   tools 28
GET .../releases  → nightly-cua-driver-rs-v0.28.3-nightly.20260919（Rust 重写进行中）
```

同名的 `cua-ai/cua` 返回 404；`trycua/lume` 301 重定向回主仓（Lume 已并入 monorepo）。

## 1. 结论

**不取代。** cua 是**基础设施层**（沙箱 + 驱动 + 评测 + 小模型），没有流程 AST、循环、条件、变量、
可回放业务流程——它管「怎么把一个动作可靠地送到屏幕上」，不管「业务流程是什么」。

但它同时踩在我们两条轴线上，一条是**实质压力**，一条是**生态盲区**：

| 轴线 | cua 的位置 | 我们的位置 | 判定 |
|---|---|---|---|
| 桌面输入**契约** | `delivery_mode` 默认 `background`、**永不前台化**，静默回退被明确移除；`uiAccess="true"` 解 UIPI | 全路径真输入（真光标 / 全局键击 / 主动 `set_focus`），**无 background 概念** | **空白**（§3.4 轴一/二/三） |
| Windows 实现深度 | `platform-windows` + `cua-driver-uia`：MSAA / UWP 启动 / 虚拟桌面 / WGC / 80 KB overlay | UIA + Win32 双后端，17 个对称命令 | **它深得多**（§3.4） |
| agent 可调用入口 | MCP stdio 是它的 agent 边界，已被 Hermes / Clicky / H Company / Factory Droid 接入 | **CLI 已是 agent 通道**（ADR 0006 §6；15 个顶层子命令、UTF-8 JSON 输出），缺**接口描述层**与 MCP 封装 | **半盲区**（§3.2，2026-09-22 修正） |
| 浏览器执行 | 沙箱里跑自己的 Chromium（CDP） | 用户真实已登录浏览器（自研 MV3 扩展，无 CDP） | **我们强** |
| 流程编排 | 无 | 86 条命令 + 工作流 AST + compiler 静态校验 | **我们强** |
| 可回放 / 审计 | 有轨迹导出，但为训练用 | checkpoint + result.json + effect 契约 | **我们强** |

> **读法提示**：§3.4 是**补读源码后对初版的修正**。初版只看了文档层，把威胁描述成
> "跨平台 vs Windows-only"——那是**平台轴**；补读后发现真正的差距在**契约轴**
> （后台/前台、拒绝/静默），而这条轴**与平台无关**，Windows 上同样成立。**这才是压力所在。**

## 2. cua 是什么

| 组件 | 职责 | 对我们的相关性 |
|---|---|---|
| **Cua Driver** | 后台驱动原生桌面应用；一个 Rust 二进制 = MCP stdio server / 常驻 daemon / 一次性 CLI（`cua-driver mcp` / `cua-driver call`）；UniFFI 生成 Python/TS SDK | **高** |
| **Cua Sandbox** | 跨 OS 沙箱（Linux/Windows/macOS/Android），本地 Docker/QEMU/Apple VZ 或 Cua Cloud | 低（我们操作用户真实环境，不用沙箱） |
| **Lume** | Apple Silicon 上的 macOS VM（Virtualization.framework） | 低 |
| **Cua-Bench / Fleets** | OSWorld / ScreenSpot / Windows Arena 评测；快照 fork、批量 warm pool、轨迹导出 Arrow/Parquet | **中**（复盘手法可借） |
| **CUA-S1** | 小专用 computer-use 模型家族，首个 checkpoint `cua-s1-form-v0` 做表单任务 | **中**（第 2 层语义闭集判断的落点） |
| **Cua Cloud** | 托管沙箱 + VLM Router | **负**（数据出境） |

### 硬伤与必须标注的宣传语

1. **CUA-S1 没有任何性能声明**。官方 README 原话：
   > "It does not include or download model weights, datasets, demo binaries, or recordings.
   > No checkpoint performance claim is established by this source-only release."

   即 **source-only 研究发布、无权重、无数据集**。MIT 只覆盖源码，不覆盖未来的权重/数据集/托管服务。
   任何把 CUA-S1 当可用能力写进方案的表述都是错的。

2. **macOS 后台输入的两处来源口径不一致**。README 宣称
   "drives native macOS apps without stealing focus"，但 `docs/macos-background-input-v1-plan.md`
   标注 `Status: Proposed implementation plan`（base `d21e344`，2026-08-04），且明确 V1 是
   "deliberately conservative"——大量路径**返回结构化拒绝**而非执行。**承诺是能力，计划是方向，
   两者不能混为一谈**；我们未在真机验证过它。（本评估不下"已实现"的结论。）

3. **1034 个 open issues + nightly 节奏**。`cua-driver` 正从原实现重写为 Rust
   （`nightly-cua-driver-rs-v0.28.3`），接口面仍在动。

4. **厂商自评基准**。OSWorld / ScreenSpot / Windows Arena 的成绩是它自己跑的（`libs/cua-bench`），
   无第三方复现标注。

5. **数据出境**。Cua Cloud 托管沙箱意味着页面内容/桌面画面要出本机。

## 3. 挑战：逐条给证据

### 3.1 桌面后台驱动 —— 我们的结构性空白

**实测证据**：

```text
src/rpa_core/executors/desktop.py:52
    if sys.platform != "win32":
        return CommandResult.failure(ErrorCode.PLATFORM_UNSUPPORTED,
            "Windows UI Automation is only available on Windows")
src/rpa_core/capture/desktop_agent.py  → 全文件 ctypes.windll.*（GetAsyncKeyState / GetCursorPos）
docs/desktop_backends.md              → 「Windows 桌面双后端分工（desktop.uia / desktop.win32）」
```

即：**桌面侧不但在语义上是前台（真的移动鼠标/发键击），而且在平台上是 Windows-only。**
这与我们「跨平台 = 稳定契约 + 能力感知的驱动，而非各 OS 行为一致」（规则 9）并不冲突——
Windows-only 是有意的产品边界（对标影刀）。**真正的差距不是平台，是"后台"这个语义维度我们有空白**：
`RPA_DESKTOP_E2E` 类真机测试在 M36 出过事故（剪贴板用例往维护者前台粘贴 "hi"），
根因就是 `send_keys` 走 SendInput 是**全局键击**——前台语义的必然代价。cua-driver 走的是
「精确 `(pid, CGWindowID)` 归属 + 投递不命中就拒绝」这条正交轴。

> 补充证据（这条很重要）：cua 的 macOS V1 计划与我们的 M28 是**同一个哲学**——
> "A refused action is preferable to a process-scoped key event that appears successful
> after mutating a sibling window."（宁可拒绝，也不要一个改错了兄弟窗口却看起来成功的键事件。）
> 这跟 M28「失败不盲重试、显式报错」、《element-mvp-boundaries.md》的诚实边界表是同一套价值观。
> **它在这条轴上比我们走得更远，但方向和我们一致**——所以这是差距，不是路线分歧。

### 3.2 agent 生态入口 —— 生态盲区

cua-driver 的 agent 边界是 **MCP stdio**（`cua-driver mcp`），28 个工具的完整清单来自它
自动生成的 `contract/manifest.json`（`contract_version 0.8.0`）：

```text
观测   get_window_state / get_desktop_state / list_windows / list_apps / get_screen_size
       get_cursor_position / verify_state
动作   click / drag / scroll / type_text / press_key / hotkey / invoke_menu
       set_window_frame / move_cursor
剪贴板 clipboard_read / clipboard_write
会话   start_session / get_session / list_sessions / end_session / set_agent_cursor_*
```

对比我们的 86 条命令：**它只有原子动作 + 观测 + 会话，没有一条流程语义**。
但生态事实是：Claude Code / Cursor / Codex 这类宿主通过 MCP 接入它，
"Muse Code can use Cua Driver as a local stdio MCP server"。

> **2026-09-22 修正（补读 `cli.py` 后）**：初版这里写「我们在 agent 驱动桌面这条新入口上
> 不可见——不是能力不够，是没有那个门」。**这个判断偏重了。** 实测：
> `cli.py:636` 已有 **15 个顶层子命令**（`validate` / `run` / `resume` / `pause` /
> `runs(list|show)` / `catalog` / `capture(browser|desktop)` / `elements(list|show|verify)` /
> `auth` / `unauth` / `status` / `env-status` / `install-extension` / `gui` / `devserver`），
> 输出按 UTF-8 JSON 规整（注释写明"WorkBuddy statusMatch 契约依赖可解析输出"），
> 且 `ADR 0006 §6`（2026-09-03）**明文把 CLI 定位为"agent/脚本通道，M15 WorkBuddy 主路径"**
> ——即**已经有 agent 在实际调用**，且该 ADR 当初列的"CLI 无入口"缺口（catalog / capture /
> elements）**现已全部补齐**。
>
> 准确表述：**门是有的，缺的是"门的说明书"**——接口描述层（agent 不知道我们有哪些命令、
> 怎么调、什么语义）与可选的 MCP 封装。修正后的优先级见
> `computer-use-x-rpa-strategy.md` §5.1 / §6。

### 3.3 我们强的地方（护城河，决定"是否被取代"的答案）

- **用户真实已登录环境**。cua 的沙箱是干净环境，我们是用户 profile + 真实 Cookie + 无
  `navigator.webdriver` + 无 CDP（CDP 本身可被检测）。真实业务系统（已登录的后台/ERP）
  它进不去。
- **流程资产**。86 条命令 + AST + compiler 静态校验 + `effect` 契约（`kind`/`replay`/`idempotency`）
  + `risk`/`stability`/`retryable` 的 manifest 声明面。cua 的组织单位是"一次会话里的原子动作序列"。
- **可复现与审计**。`checkpoint-recovery`、`pause-resume`、`run_history`、`result.json` 持久化。
  cua 的轨迹是为训练数据服务的，不是为业务回放。
- **两种通道的收敛度**。我们在 M29/M30 用 AST 门禁把「声明了不生效」逐条清零
  （参数消费门禁 75 checked / 3 exempt / 台账 0 条），这是它 1034 个 open issues 的阶段不会有的收敛。

### 3.4 Windows 侧深挖：四条我们没覆盖的轴（补读源码后）

> 本节是第一版评估的**修正**：初版只读了文档层（README / `docs/` / `contract/manifest.json`），
> 漏了 `rust/crates/platform-windows`（含 `uia/` `win32/` `input/` `tools/` 四个子模块）
> 与独立的 `cua-driver-uia` crate。**Windows 是我们的主战场，这一段才是最该先看的。**
> 实测事实：它的 Windows 实现规模是 `tools/impl_.rs` **519 KB 单文件**，而我们是
> `desktop.py` 829 行 + `desktop_win32.py` 772 行。

#### 轴一：UIPI 与 UWP —— `uiAccess="true"`

`cua-driver-uia.manifest` 的核心是这一行（原文注释）：

```xml
<requestedExecutionLevel level="asInvoker" uiAccess="true"/>
<!-- gives this worker UIAccess integrity at launch, which lifts UIPI for
     SendInput / UI Automation against AppContainer'd UWP apps
     (Calculator, modern Notepad, Settings). -->
```

`UIPI`（User Interface Privilege Isolation）使普通完整性进程**无法**向更高完整性级别或
AppContainer 进程投递输入。代价与硬性门槛（它自己列的三条）：

1. **必须 Authenticode 签名**（生产要 EV 证书）
2. **必须经 `ShellExecute` 启动**（`CreateProcess` 会失败）
3. 二进制须在"安全路径"（`\Program Files\`），或管理员设 `EnableSecureUIAPaths=0`

**对我们的意义**：我们的输入全走 pywinauto —— `send_keys`（`desktop_win32.py:402,512`、
`base.py:66`）是 **SendInput**，`click_input`（`desktop.py:467`、`base.py:63`）是**真实鼠标**。
两者都在 UIPI 的管辖范围内。**目标应用是 UWP（计算器 / 新版记事本 / 设置）或运行在更高完整性
级别时，输入可能被静默丢弃**——我们既没有处理，文档里也没有这条边界。这是本次评估
**最该自查的一项**。

#### 轴二：`invoke()` 在 XAML / Chromium 宿主上会**静默**抢用户前台

`uia/fg_bypass.rs` 描述了一个我们完全没意识到的机制：

> UWP/XAML/WinUI apps **self-foreground during UIA `InvokePattern.Invoke`**……The XAML host
> unconditionally calls `SetForegroundWindow(self)` when handling those, stealing focus from
> whatever the user had on top.

它的实测数据（可复现的 `flash-repro/*.ps1`，2026-05-24）：

| 场景 | 基线（无盾） | 加盾后 |
|---|---|---|
| UWP Calculator 的 num5 按钮，用户前台窗口 z 序跌落 | **91% 的轮询采样** | **0 / 507**（Calculator + Clock + Settings） |
| Chromium / Electron（`Chrome_WidgetWin_*`）后台动作抢焦点 | **7 / 8** | 已解决 |
| 经典 Win32（记事本） | 0 / 45（**无此问题**） | —（故绕过按宿主门控，非 XAML/Chromium 是 no-op） |

解法是 `EnableWindow(host, FALSE) / call / EnableWindow(host, TRUE)` 的 RAII guard——因为
**UIA pattern 走内核无障碍通道，不受 `EnableWindow` 门控的输入队列约束**，所以调用照样生效。

它自己声明的**局限**（诚实度值得学）：WPF 的 automation peer 在 Invoke 处理器里**同步**调
`UIElement.Focus()`，走 `SetForegroundWindow`、**不受 `EnableWindow` 门控**，那里绕过无效，
daemon 仍会短暂抢前台。

**对我们的意义**（高价值、可直接抄）：M30 S3 引入的 `simulateHuman=false → invoke()`
（`base.py:199`、`desktop.py:469,541`）在这三类宿主上会**静默抢走用户前台**。
而我们的产品形态是「GUI 是唯一主力形态」——用户一边看 GUI 一边跑流程，
**每点一次 UWP/WinUI/Chromium 按钮就把他的窗口抢走一次**，且没有任何报错。
现成解法就是一个 RAII guard，约 40 行。

#### 轴三：`delivery_mode` —— background 默认、静默回退被移除

`input/delivery.rs` 是本次**最有工程借鉴价值**的一份。两档，逐次调用传入
（原文："**never a stored setting**"）：

- `background`（**默认**）：只走 PostMessage / UIA，**永不前台化**；命中"会静默丢弃"的组合时返回
  结构化的 `background_unavailable` 错误，让调用方**显式**改用 `foreground` 重试。
  理由原话：**"surfacing an honest error beats silently fronting."**
- `foreground`：SendInput + 短暂 `SetForegroundWindow(target)`，事件 flush 后恢复先前前台。

**最关键的一条**：legacy Windows-only 的 `auto` 模式（**静默 SendInput 回退**）被
**明确移除**，理由是会"front without the caller opting in, breaking the no-foreground contract"。
且 `DeliveryMode::parse` 对**任何未知值一律回落 `background`**——省略 / 写错 / 用已被移除的
`"auto"`，**都绝不会静默前台化**。

**对我们的意义**：实测我们的全路径都是"真输入"，**没有 background 概念**：

```text
desktop.py:266        window.set_focus()          ← 主动抢前台
desktop.py:467        element.click_input()        ← 真实鼠标，移动光标
desktop.py:473-474    element.set_focus()
desktop.py:486        pywinauto.keyboard.send_keys("^v")   ← 全局键击
desktop_win32.py:402  send_keys(str(inputs["keys"]))       ← 全局键击
```

M36 那次"剪贴板用例往维护者前台粘贴 hi"的事故，本质就是**静默落到前台窗口**——
正是 cua 用"移除 auto 模式"来消灭的那类行为。**它把这件事上升成契约，我们还没有。**

#### 轴四：`would_be_silently_dropped` —— Windows 特有的静默丢弃矩阵

同一份 `delivery.rs` 里有一张按 `(窗口类, EventKind)` 判定的矩阵，`EventKind` 六类：
`MouseClick` / `MouseMove` / `MouseScroll` / `Keystroke` / `KeyCombo` / `TextInput`。
原文注明**这是 Windows 专有、macOS 无对等物**（"CGEvent posting does not have the same
per-framework silent-drop problem"）。

**对我们的意义**：实测我们的 grep **未命中** `post_message` / `send_message`，所以
`desktop.win32` 目前**可能不走 Win32 消息投递**（走的是 pywinauto 的 SendInput 路径）。
但这仍需确认 pywinauto 内部行为——**列为待确认项，不下结论**。

#### 一条我们已天然规避的（如实记录，避免误判）

`uia/cache_uaf_repro.rs`（20 KB）记录了一个**已修**的 UIA 元素缓存 UAF：
两个并发会话驱动同一 `(pid, hwnd)` 时，A 替换快照 → 对缓存的每个
`IUIAutomationElement` 调 COM `Release`，而 B 正握着同一指针在动作中 → **daemon 崩溃**。
修法是 `RetainedElement`（**在锁内 AddRef**）。它自己点名这是 macOS `#1796` fault 的 Windows 对等物。

**我们的架构天然没有这个风险**：实测 `desktop.py:418`

```python
session.elements[element_id] = locator.model_dump(by_alias=True)
```

——我们缓存的是**序列化后的 `DesktopLocator` 描述符**，**不持有 COM 对象**，每次用时重新解析。
这正是 `element-self-healing-plan.md` 里「可持久化的只有页面自身的属性」在桌面侧的自然延续。
**结论：这一类缺陷我们不需要修，但要保持这个纪律**——一旦哪天为了"性能"改成缓存
`UIAWrapper`，就同时引入了这一类 UAF。

它**可抄的是测试手法**（不需要真 GUI 就能确定性复现）：

1. 喂一个**自实现、独立引用计数的 COM-ABI 对象**（手写 IUnknown vtable），
   让真实的 `with_snapshot` 锁、真实的 `CachedSnapshot::drop` 原样运行；
2. 双重插桩：refcount ≤ 0 时仍见 AddRef/Release → 计数（**内存保持映射，断言确定性、进程不崩**）；
   另加"毒化"路径（Release 到 0 时覆盖 vtable 指针 → 下次 AddRef 真 `STATUS_ACCESS_VIOLATION`），
   用 `#[ignore]` 门控，不炸套件；
3. `force_interleave` **用 channel 钉死危险时序**，不靠运气 → **修复前必红、修复后必绿**。

这三条把「并发缺陷不可测」变成了「可确定性复现」，比它修的 bug 本身更值钱。

#### 其余能力清单（`platform-windows/src`，供后续对标参考）

| 文件 | 大小 | 说明 |
|---|---|---|
| `msaa.rs` | 13 KB | **MSAA（旧一代无障碍 API）**。我们只有 UIA + Win32——VB6/Delphi/老 MFC 的树在 MSAA 里，可能空白 |
| `launch_uwp.rs` | 27 KB | 启动 UWP 应用 |
| `virtualdesk.rs` | 16 KB | 虚拟桌面 |
| `wgc.rs` | 10 KB | Windows Graphics Capture（截图/录制） |
| `overlay.rs` | 80 KB | overlay（agent 光标 / 高亮可视化） |
| `uia/cache.rs` `windows_enum.rs` | 4 KB / 48 KB | 元素缓存、窗口枚举 |
| `browser_platform.rs` `browser_consent_ui.rs` | 98 KB / 29 KB | 浏览器平台层与授权 UI |
| `terminal.rs` | 9 KB | 终端（PTY/TUI 语义） |

**另一处工程手法**：`cua-driver-uia/src/lib.rs` 的注释解释了为什么把纯策略拆出来 ——
> The production binary carries a `uiAccess=true` manifest and therefore **cannot be launched by
> an unelevated test runner**. Keeping the pure policy in this library lets CI execute the security
> checks **without weakening the production manifest**.

把"可直接测的纯策略"从"测试环境跑不起来的生产二进制"里剥离，让 CI 能跑安全断言、
又不削弱生产安全属性。我们的 `capture/desktop_agent.py` 是同类子进程形态，面临同样的可测性张力。

## 4. 借鉴：按可抄性排序（纯确定性优先）

### 4.1 【最高】精确窗口归属贯穿 —— 认知模型，不是代码

cua 的 macOS V1 把「精确 `(pid, CGWindowID)`」当作**贯穿目标解析 → 路由选择 → 派发 → 事后验证**
的不变量，并明确：事后验证的证据必须属于**那一个窗口**，
"State from another same-process window is never accepted as evidence."

我们对应的问题域：`desktop.py` 的 `plan_click_for_element` / `click_with_modifiers` 在
**同进程多窗口**时（同一 app 的两个窗口、同标题的对话框）是否有等价的归属校验？
UIA 的 `element` 句柄天然带进程/窗口归属，但**坐标路径**（`clickPosition=random` 走
`click_input(coords=...)`）没有。**这是我们该自查的第一件事**（未验证，列为待核查项）。

### 4.2 【高】后台路由排序 + 拒绝优先

它的后台路由有**明确优先级**，且任何一条都无法证明时不发送输入：

```text
语义 Accessibility  →  精确 browser/CDP  →  精确窗口局部指针  →  PID 键盘（需独立精确投递证明）
```

对我们有价值的是这个**形状**：把「多档降级」写成有序路由，并在最后加一条
「证明不了就拒绝」。我们的 `browser.py` 已有三档输入（`fill` / `type` / `clipboard`）
和 `ELEMENT_COVERED`，但**没有把降级写成显式有序路由 + 拒绝兜底**——这个形状可以照搬。

### 4.3 【高】权限模式：启动时固定，owner 决定，运行中不可变

```text
standard      promptless 默认
bounded       只准入 reviewed manifest 里的工具与资源
unrestricted  需要 --dangerously-bypass-approvals
```

关键三条：
1. 模式属于**拥有 runtime 的那个进程**，启动时用 flag 固定（`cua-driver serve`），
   嵌入宿主用环境变量 `CUA_DRIVER_PERMISSION_MODE` / `CUA_DRIVER_CAPABILITY_MANIFEST_FILE`。
2. **运行中的 daemon 必须重启才能改**——没有"运行中提权"这条路径。
3. 附着已有登录 profile 是**显式授权**：`cua-driver mcp --grant existing-profile`，
   或 bounded manifest 里声明 `kind: existing_profile`。它自己**不画授权弹窗**。

对照我们：`extension/README.md` 的执行权限已有三档（`browser` / `tabs` / `origins`），
但**"启动时固定、运行中不可变"这条没做到**（M32→M35 的 `closeBrowser` 反复改语义，
正是"运行时可变的授权"在业务层的体现）。另：`--grant existing-profile` 这个显式授权措辞
比我们默认 `{"mode": "browser"}`（全窗口全标签全 Cookie）更保守，值得在文档里对齐口径。

### 4.4 【中高】Computer History 的元数据 allowlist

它的动作历史用**严格白名单**存储，且原话是"never"清单：

> "It never stores screenshots, typed text, clipboard contents, raw arguments or results,
> accessibility trees, paths, window titles, or URLs."

只存严格 metadata 白名单，本地存储，读取要权限门（`history_status` / `history_query`）。
**这是审计设计的范本**：把"不存什么"写成硬约束而不是配置项。
对我们的直接价值：`run_history` / `checkpoint` 里的 `result.json` 是否可能带上
输入文本（密码字段！）、剪贴板内容、页面 URL？**这是我们该自查的第二件事**（未验证）。

### 4.5 【中】contract-first：manifest 自动生成 + CI `--check` 防漂移

它没有手写 manifest：Rust contract crate **生成** `contract/manifest.json`（119 KB）与
`rust/include/cua_driver_abi.h`，CI 跑同一个生成命令带 `--check`，"so the implementation and
distributed header cannot drift"。

对照我们：80 个手写 manifest JSON + 三个 AST 门禁（`check_param_consumption.py` /
`check_error_contract.py` / `check_close_ops.mjs`）。**我们已经用门禁拿到了同样的效果，
而且做法更适合我们的规模**（手写 manifest 可读性更好、便于中文说明）。
它的可借鉴点很具体：**生成物必须由 CI 反向校验**——我们的等价物已经有了，
但缺一条「门禁自身也进 CI 且必须跑」的显式声明（`check_all.py` 已含，属已完成）。

### 4.6 【中】`verify_state`：driver 自己求值谓词

"Deterministically verify bounded predicates against one exact window. **The driver evaluates**..."
——**验证由驱动求值，不交给模型判断**。这正好是决策栈第 1 层的正确做法，
也是我方向未来 AI 能力开放时最重要的护栏：**模型可以提谓词，但不能当裁判**。
我们的 `waitFor` 四态（`visible`/`hidden`/`present`/`detached`）本质同类，
但它是"一个命令可以验证任意有界谓词"，我们是"四个固定状态"。

### 4.7 【中】CUA-S1 的保守边界设计（可抄的工程习惯，与模型无关）

它的安全边界原话：

> "Planning and execution are separate. The optional runtime defaults to a **dry run**,
> requires **one unambiguous target window**, uses **snapshot-bound element tokens**,
> and **reobserves the window after each mutation**. `execute` and `submit` are
> **independent opt-ins**."

以及 submit 的刻意收窄：只允许**最多一个**高置信度 `Button`/`AXButton`，
且归一化 label **精确等于** `Submit` 或 `Submit Form`。

对照我们：这三条我们都有对应物（`dry run` ↔ 无；`one unambiguous target window` ↔
`ELEMENT_AMBIGUOUS`；`snapshot-bound element tokens` ↔ `selector.candidates`；
`reobserve after each mutation` ↔ M28 自愈）——**除了 dry run**。
「默认 dry run + 两个独立 opt-in 开关」这个组合，对我们未来放任何 AI 生成能力都是现成模板：
**生成 → dry run 展示 → 用户 opt-in → 才执行**。

## 5. 不可借鉴 / 与定位冲突

- **Cua Sandbox / Cua Cloud 的沙箱模型**：我们操作用户**真实已登录**环境，不用干净沙箱。
  采用它等于丢掉「真实 profile / 无 CDP」的护城河。
- **CDP 路线**：它沙箱内走 CDP；我们已在 ADR 0013/0015 收敛到自研扩展单通道，
  与 `element-self-healing-plan.md` §3 的结论一致（CDP 本身可被检测）。
- **模型决策层**：它的上层 agent 自己决策；我们规则 6 禁止运行时动态执行。
- **`skills/jev-use`**：注意 cua 官方也把 `jev-use` 收成了自己的 skill，
  而我们在 M28 评估的正是 `browser-use/jev-ultrafast`——**双方看的是同一个参考对象**，
  再次印证「决策层无价值、观测/执行层有价值」这个判断。

## 6. 结合点与优先级

| 优先级 | 结合点 | 具体位置 | 收益 | 成本 | 风险 |
|---|---|---|---|---|---|
| **P0** | `invoke()` 在 XAML / Chromium 宿主上的**静默抢前台** —— 抄 `EnableWindow` RAII 盾，按宿主门控 | `executors/base.py:199` 的 `invoke` 分支；调用点 `desktop.py:469,541` | 消除一个用户可感知、我们零报错的副作用（它实测基线 91% z 序跌落 → 加盾 0/507） | 低（约 40 行 + 桩测） | 低（非 XAML/Chromium 宿主为 no-op，与该文件已验证的门控一致） |
| **P0** | 引入 `delivery_mode` 两档契约：**默认不许前台化**，做不到就报结构化错误 | 新字段落在 `{desktop,desktop.win32}.{click,input,hotkey}`；`set_focus` / `send_keys` / `click_input` 三条路径需分档 | 把 M36 那类"静默落到前台窗口"从事故上升为契约 | 中高（动既有默认行为） | 中（老流程行为变化 → 需迁移说明，manifest 是 `additionalProperties:false`） |
| **P0** | **UIPI / UWP 自查**：`send_keys`（SendInput）在 UWP 与高完整性目标上是否被静默丢弃 | `desktop_win32.py:402,512`、`base.py:66` | 补上一条现在完全没记录的边界（§3.4 轴一） | 低（真机核查即可） | 低 |
| **P0** | 敏感字段落盘自查：`result.json` / checkpoint 是否可能带输入文本（**密码字段**）、剪贴板、URL | `run_history.py` / checkpoint 写入点 | 审计合规硬需求（借 §4.4 的 "never stores" 口径） | 低（核查 + 需要时加 allowlist） | 低 |
| **P1** | 执行权限「启动时固定、运行中不可变」 | `extension/README.md` §执行权限 | 授权语义收敛，免重复 M32→M35 式的返工 | 中 | 中 |
| **P1** | 浏览器侧三档输入写成**显式有序路由 + 拒绝兜底** | `executors/browser.py`（fill / type / clipboard） | 把隐式降级变可审计 | 中 | 中 |
| **P2** | MSAA 覆盖评估：老应用（VB6 / Delphi / 老 MFC）的无障碍树是否可及 | `desktop.win32` 的 `title` / `className` 路径 | 补一类目前可能不可及的目标应用（§3.4 其余清单） | 中高 | 低 |
| **P2** | `verify_state` 式「有界谓词验证」命令 | 新命令，落在 `waitFor` 之旁 | 让流程自带断言 | 中高 | 低 |
| **P2** | 并发缺陷的**确定性复现范式**（自实现 COM-ABI 对象 + channel 钉时序） | 仅当未来改元素缓存策略时用得上 | 把"不可测的并发缺陷"变可测 | 低（抄手法） | 低 |
| **—** | MCP 面（agent 入口） | 未立项 | 生态可见性 | 高 | 中（需先定 ADR） |

**主推 P0 前三项**——它们是**同一条轴**（桌面输入的前台化契约）的三个面，
且它给了可复现的实测数据与现成解法。第四项（敏感字段）成本同样极低。
四项都属"自查 + 小改"，符合 M28–M30 一路在收的「声明了不生效」这条主线。

**已从初版 P0 降级**：「坐标路径是否校验窗口归属」。第二轮判断它主要是**理论风险**——
我们的 `click_input(coords=…)` 是真实鼠标，落点由 `plan_click_for_element` 的矩形
计算域决定，与 cua 的"同进程兄弟窗口"场景**不同构**（它是进程级键盘投递的归属问题）。
**降为待观察，不再占 P0 名额。** 初版把它排 P0 是读文档时的高估。

## 7. 能力层面划分

### 7.1 七个能力层面 + 一项横切

按**能力栈**切分（L0 环境 → L6 接入），比 §7.2 的四环节更细，用来定位「差距在哪一层」。
每格内容都有前文实测证据支撑，未验证的一律标"未核查"。

| 层面 | rpa_core | cua | 判定 |
|---|---|---|---|
| **L6 接入 / 生态** | CLI（**ADR 0006 §6 明文定位为 agent/脚本通道**，15 个顶层子命令、UTF-8 JSON）+ GUI（唯一主力形态，ADR 0016）+ 可选 devserver；缺**接口描述层**与 MCP 封装 | MCP stdio（`cua-driver mcp`，28 tools）+ 常驻 daemon + 一次性 CLI；UniFFI 生成 Python/TS SDK；已被 Hermes / Clicky / H Company / Factory Droid / Claude Code / Cursor 接入 | **半盲区**（§3.2 修正：门已有，缺说明书；策略见 `computer-use-x-rpa-strategy.md`） |
| **L5 契约 / 治理** | 86 条命令各自手写 manifest（单一来源，规则 2）+ 3 个 AST 门禁（参数消费 75 checked / 3 exempt / 台账 0；错误契约；close ops）+ compiler 静态校验 | Rust contract crate **生成** `contract/manifest.json`（119 KB，`contract_version 0.8.0`）+ `cua_driver_abi.h`；CI 跑同一命令带 `--check` 防漂移 | **路线不同、效果相当**；我们收敛度更高（台账 0 vs 它 1034 个 open issues） |
| **L4 审计 / 回放** | checkpoint-recovery / pause-resume / `run_history` / `result.json`（规则 12：双写成功才算成功）/ `effect` 契约（kind·replay·idempotency） | Computer History：严格 metadata allowlist，明列 *never stores* 截图 / 输入文本 / 剪贴板 / 原始参数 / 无障碍树 / 路径 / 窗口标题 / URL；轨迹导出 Arrow/Parquet（**为训练数据服务**） | **互有胜负**：我们强在业务回放，它强在"不存什么"的硬约束（§4.4） |
| **L3 执行 / 投递** | 全路径真输入（`click_input` 真光标 / `send_keys` 全局键击 / `set_focus` 主动抢前台），**无 background 概念**；另有 `simulateHuman=false → invoke()` 分支 | `delivery_mode` 两档契约（`background` 默认、**永不前台化** / `foreground`）；legacy `auto` 静默回退被**移除**；未知值一律回落 `background`；`uiAccess="true"` 解 UIPI；`invoke` 前台化 RAII 盾；静默丢弃矩阵（Windows 专有） | **它更强**（契约轴，与平台无关；§3.4 轴一/二/三） |
| **L2 决策 / 编排** | 工作流 AST（循环 / 条件 / 变量 / 子流程）+ compiler 静态校验 + 确定性预检 + `effect` 契约；86 条命令 | **无流程原语**；决策整层交给上层 agent；可选 CUA-S1（source-only，无权重、**无性能声明**） | **我们强 · 护城河**——它整层缺失，这是"是否被取代"的答案（§3.3） |
| **L1 感知 / 观测** | 元素语义快照（role 归一化 + accessibleName 优先级链，M10）；桌面 UIA + Win32 双后端（17 条对称命令）；MV3 扩展 DOM 观测 | 截图 + AX tree + snapshot 元素表；`get_window_state` / `get_desktop_state` / `list_windows` / `list_apps` / `verify_state`；Windows 侧另有 MSAA、WGC 截图、80 KB overlay、48 KB 窗口枚举 | **它覆盖面宽**（老应用 MSAA、录制、跨平台）；我们浏览器 DOM 侧精度高 |
| **L0 环境 / 会话** | 用户**真实已登录**环境：真实 profile + Cookie + 无 `navigator.webdriver` + 无 CDP，可进已登录业务系统 | Cua Sandbox 干净环境（Docker / QEMU / Apple VZ）+ 会话原语（`start_session` / `end_session` / `list_sessions`）；附着真实 profile 需**显式授权**（`--grant existing-profile`） | **互有胜负**：我们强在真实系统可达，它强在环境隔离与快照重放 |
| **横切 · 权限 / 安全** | 三档执行权限（`browser` / `tabs` / `origins`），启动时可配，**运行中可变**（M32→M35 的 `closeBrowser` 反复改语义即其表征） | 三档 permission mode（`standard` promptless 默认 / `bounded` 只准入 reviewed manifest / `unrestricted` 需 `--dangerously-bypass-approvals`），**启动时固定、运行中必须重启** | **它更严**（§4.3，P1） |

**读法**：差距**不在同一层堆叠**，而是错开的——
它压在 **L3（执行投递契约）** 与 **L6（agent 入口）**，我们压在 **L2（流程编排）** 与
**L5（契约收敛）**。任何"谁取代谁"的判断只有在**同一层**才成立，而我们与它在决定性的一层
（L2）根本不重叠。

### 7.2 四环节速查（原对照，保留）

| 环节 | cua | 我们 | 判定 |
|---|---|---|---|
| **感知** | 屏幕截图 + AX tree + snapshot 元素表 | 元素语义快照（role 归一化 + accessibleName 优先级链，M10） + MV3 扩展 DOM 观测 | 各有擅长；它跨平台，我们精度高 |
| **判断** | 上层 agent（Claude Code/Cursor）+ 可选 CUA-S1 打分 | 用户编排 + compiler 静态校验 + 确定性预检 | **它不构成对流程编排的替代** |
| **执行** | 沙箱桌面 / 真实 desktop 后台驱动 | 用户真实已登录浏览器 + Windows UIA/Win32 桌面 | **它强在后台与跨平台** |
| **审计** | Computer History（严格 allowlist，本地加密） | checkpoint + result.json + effect 契约 + run_history | **各有优点，可互补** |

**结论：不取代。** 它缺"流程"这一整层，而流程是我们的全部；我们缺"后台执行"与"agent 入口"
这两块，而那是它的全部。**互补关系，不是竞争关系。**

## 8. 建议

**短期（本里程碑间隔期内，不需立项）**

1. **做四项 P0 自查**，逐项把结论写回本文档——**包括"核查后无问题"这个结论**，
   否则下次会有人重新核一遍（M30 S5 的教训：结论要改就改全仓，
   没结论的地方后人就得重来）。核对方法建议：
   - **UIPI / UWP**：在 Windows 真机上对**新版记事本 / 计算器**各跑一次 `desktop.input`
     与 `desktop.click`，看是否"报成功但目标无变化"。不动真机就如实标"未核查"，不要猜。
   - **敏感字段**：在 `run_artifacts/` 里找一份含 `desktop.input` 或浏览器 `input` 步骤的
     历史 run，直接看 `result.json` / checkpoint 是否落盘了输入文本。
2. **`EnableWindow` 盾（§3.4 轴二）可以立刻做**：门控条件明确
   （`is_xaml_host_hwnd || is_chromium_target_window`）、非目标宿主是 no-op、
   有第三方可复现的实测数据支撑，不需要先立项。照抄时**一并把它声明的 WPF 局限写进注释**。
3. **`delivery_mode` 不夹带**：默认"不许前台化"会改变既有流程语义，
   属行为变更，需先写迁移说明（manifest 是 `additionalProperties:false`，老流程会在校验期失败），
   建议立成独立里程碑。

**中期（若立项）**

4. 优先级：`delivery_mode` 契约 > 执行权限不可变 > 浏览器侧显式有序路由 >
   `verify_state` 式谓词验证。把 §4.2 的「有序路由 + 拒绝兜底」形状记进
   `element-mvp-boundaries.md` 的待办，先记不做。
5. ~~**不动 MCP**~~ → **2026-09-22 修正**：初版的两条理由都不成立——
   ADR 0016 管的是**宿主形态**（GUI vs Web 编辑器），既不涉及"对谁暴露能力"，
   也**未覆盖 agent 这一新调用方**；而实测表明 CLI 早已是 agent 通道（§3.2 修正）。
   修正后的结论与优先级见 `computer-use-x-rpa-strategy.md` §5.1 / §6：
   **P0 = 补"接口描述层"（低成本），MCP 封装降为 P2**
   （它仍是 ADR 0006 §3 的明确排除项，需新 ADR 才放行）。

**长期（若引入 AI 生成）**

6. 沿用 `element-self-healing-plan.md` §6 的形态建议（设计期生成流程草稿 AST →
   compiler 校验 → 用户审阅），**叠加 CUA-S1 的 dry run + 独立 opt-in 模板**。
7. 硬约束不变：**模型可以提谓词/候选，不能当裁判**（§4.6）；
   **模型输出永不成为选择器/JS/坐标**（规则 6）。

## 9. 附：本次调研过程的一处教训

初版评估（同一天早些时候）读的是 README + `docs/` + `contract/manifest.json`，
**没下到 `rust/crates/`**——于是把威胁描述成"跨平台 vs Windows-only"这条**平台轴**，
并在 §6 里把一个**理论风险**（坐标路径窗口归属）排到了 P0。
补读源码后，真正的差距（`delivery_mode` 契约、`uiAccess` / UIPI、`invoke` 静默抢前台）
全部浮现，且都与平台无关。

**教训**：评估基础设施类项目时，**`docs/` 是意图，`rust/`（或对应源码）才是事实**。
`docs/` 里的 `*-plan.md` 甚至可能只写着一个平台（本项目初版就只读了 macOS 的计划文档，
而我们是 Windows RPA）。**先列源码目录树，按"与我们同平台的那份"深入**，
再回到文档层补充意图——顺序反了就会像初版那样跑偏。

