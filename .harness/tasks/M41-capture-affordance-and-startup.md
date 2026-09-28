# M41 捕获高亮「躲着鼠标」/ 启动加载提示

状态：`done`

由来：维护者 2026-09-28（M40 第二轮收口后）提两条体验要求——

> 捕获元素的框跟影刀的一样，躲着鼠标，否则影响其他元素的捕捉。
> 另外，启动前如果要初始化，可以有个加载提示。

**第二轮澄清（同日）**——第一句的真意不是「框线压住指针像素」，而是**悬浮窗要躲开鼠标**：

> 我的意思是，捕捉元素的桌面悬浮框，在鼠标即将移动到悬浮框的时候，悬浮框移动到
> 屏幕另一侧，避免挡住实际需要捕获的元素。

⇒ 切片因此是三条：**S1** 按第一句的字面（框线别压住指针像素，**实测支持的改进**）、
**S2** 启动加载卡片、**S3** 按澄清做的**捕获悬浮窗避让**（见 §0.3）。三条都不是崩溃类
缺陷，而是**观感与反馈**。沿用本仓纪律——先一手实测归因（探针可复现），再改实现，
最后逐条负向验证 + 全门禁。切片各自独立。

**第三轮追加（同日，收口之后）**——S4，去掉捕获浮窗上的倒计时显示：

> 有个 90 秒的倒计时，是必须的吗？用户角度来说没必要

⇒ 追加切片 **S4**（见 §0.4 / §2），仍属「捕获期反馈面」这条线，故不另立里程碑。

## 0. 一句话结论

1. **红框压住鼠标指针**（纯视觉，**不影响**命中）：桌面 `_HoverOverlay` 的 region 挖空区是
   元素 rect 的**内缩**版，3px 框线带因此占住元素最外 3px——指针贴边（小元素/格子/窗口边）
   时框线正好压在指针上。真机 A/B：13 个采样点里 **6 个**的指针 ±2px 内有红色框线像素
   （最近距离 **0**）。浏览器页内高亮另有 12% 红填充，把元素内容整片染色。
   改成「框线带整条落在元素**之外** [2,5)」并去掉填充后：**0/13**（最近距离 4px）。
2. **冷启动「窗口出现前」约 1 秒无任何反馈**：`load_catalog`（首跑 420.5ms，含惰性 import）
   + `build_application`（275.8ms，QApplication 与 Qt 平台插件）+ 窗口构造（68.5ms）
   + 首帧（211.3ms）≈ **976ms** 同步阻塞，屏幕上什么都没有，用户会以为命令没生效。
   加一张在场约一秒的加载卡片（`gui/splash.py`），把这段变成「有反馈的等待」。
3. **捕获悬浮窗压在鼠标路径上**（第二轮澄清的那一条）：捕获期右下角的状态浮窗是
   **真实窗口**（无 `WS_EX_TRANSPARENT`、不吃点击穿透），压在鼠标路径上时同时挡住视觉
   与**点击**——用户会「点不中」它下方的元素。改成「鼠标临近默认位置时翻到左上角、
   走开再回右下角」：判据落在纯函数 `capture_float_origin`，判定基准恒为**默认位置**
   那一个矩形（与浮窗当前所在处无关）⇒ 无状态、不抖动。
4. **捕获浮窗上的「剩余 90 秒」是多余的显示**（第三轮追加，S4）：90 秒上限**必须留**
   （两条腿都卡死时的唯一兜底，见 §2 S4），但把上限做成**倒计时数字**对用户零信息量——
   正常捕获几秒就结束、这个数字从头到尾读不完，而超时又**没有任何惩罚**（重点一次即可）；
   它只是把「等待上限」表达成了「限时任务」。**去掉显示、保留机制**。

## 1. 证据（一手，可复现）

### 1.1 桌面捕获框：命中和视觉是两回事

探针 `.harness/spike/probe_m41_hover_overlay.py`（真机，造一个 3×3 网格目标窗口，
13 个采样点 = 9 个格中心 + 4 个紧贴窗口边界的点）。

**§3 A/B（唯一变量 = overlay 在不在）**：同一批点调 `_element_from_point`，差异 **0/13**，
没有任何一点命中 overlay 自己。⇒ **overlay 不参与 UIA 命中**，「影响捕捉」不是功能面
（不会捕错元素），先排掉一个候选。

**§6 真实指针逐点像素判定**（`SetCursorPos` 到采样点 → 跑一帧 hover → 截屏 → 看指针点
±2px 内有没有红色框线像素）：

| 状态 | 压线点 | 说明 |
|---|---|---|
| 改前（框线带占元素最外 3px） | **6/13** | `grid11`、`grid21`、`edge-*` 四点全中 |
| 改后（框线带占元素外 [2,5)） | **0/13** | 全部「空闲」 |

**§7 净外移量扫描**（走真实 `show_rect` 路径，只改传入 rect 的抵消量）：

| 净外移 | 框线带位置 | 压线点 | 指针到框线最近距离 |
|---|---|---|---|
| 0（= 旧实现） | 元素最外 3px（内缩） | **6/13** | 0 px |
| 3 | 元素外 [0,3) | 2/13 | 2 px |
| **5（= 现行实现）** | **元素外 [2,5)** | **0/13** | **4 px** |
| 8 | 元素外 [5,8) | 0/13 | 7 px |

⇒ 解析解 `外移量 = 框线宽(3) + 指针热区(2) = 5` 被实测精确验证，取 5（最小满足值，
框仍紧贴元素，不「飘」）。

### 1.2 启动分段（`probe_m41_startup.py`，offscreen，**取首次采样**）

真实进程只经历一次冷启动，所以看**首次**而不是中位数：

| 阶段 | 首次 | 后续（热） |
|---|---|---|
| `load_catalog(commands/)` | **420.5 ms** | 6.8–8.2 ms |
| `build_application()` | **275.8 ms** | 0.0 ms |
| `HomeWindow(...)` 构造 | 68.5 ms | 47.1–52.4 ms |
| `home.show()` + 首帧 | **211.3 ms** | 0.1–2.9 ms |
| **窗口出现前合计** | **≈ 976 ms** | ≈ 61 ms |

（编辑器 `--workflow` 直开路径：`MainWindow` 构造 27–32 ms + 首帧 8.9 ms，同样被卡片覆盖。）

**中位数会骗人**：按 3 次采样取中位则只有 60.6 ms，据此会得出「加载提示会闪」的错误结论。
冷启动的大头是**惰性 import 与 Qt 平台初始化**，只发生一次。

### 1.3 排除了什么（同一份数据里逐条排除）

- **不是 hit-test 劫持**：§3 差异 0/13（见上）。
- **不是 `QTabWidget`/页签**：M40 已实测页签首帧 0.1–3.0 ms，本里程碑不涉及。
- **不是「框跟不上鼠标」**：§6 逐点截图里框始终精确框住所指元素，跟随正常。

## 2. 修法

### S1 捕获高亮：框线带整条落在元素之外

| 文件 | 改动 |
|---|---|
| `src/rpa_core/capture/desktop_agent.py` | 新增模块常量 `OVERLAY_BORDER = 3` / `OVERLAY_OUTSET = 5` 与**纯函数** `overlay_bounds()`；`_HoverOverlay.show_rect` 先外扩再建 region（region 挖空区因此 = 元素本身），`_last_bounds` 去重改用外扩后的 bounds |
| `extension/content.js` | 新增纯函数区 `[capture-overlay-geometry]`（`OVERLAY_BORDER` / `OVERLAY_OUTSET` / `overlayBoxRect`）；`show()` 改用它定位、改 `box-sizing:border-box`、**去掉 `background` 填充**、边框 2→3px |

两通道几何**完全一致**：框线带都占元素外 `[OUTSET-BORDER, OUTSET)` = `[2,5)`。
提示条（页内 `hint`）**不动**：它固定在元素外侧（上方 24px / 顶不下时下方 4px），
指针在元素内时天然压不到它——不引入无谓改动。

### S2 启动加载提示

| 文件 | 改动 |
|---|---|
| `src/rpa_core/gui/splash.py`（新） | `StartupSplash` 卡片（无边框 / 置顶 / **不收焦点** / 自绘卡片底，与 `run_float`·`capture_float` 同款）；`open_startup_splash()` 显示后**立即泵一轮事件**；`startup_splash()` 上下文管理器保证异常路径也撤卡 |
| `src/rpa_core/gui/app.py` | `run_gui` 用 `with startup_splash(app):` 包住 `load_catalog` → 窗口构造 → `show()`；`QTimer.singleShot(0, …)` 的预热/分片首轮挪到 `show()` 之后、`app.processEvents()` 之前，让**首帧与预热都发生在卡片在场时** |

**实测纠正一处想当然**：PySide6 的 `Qt.WindowType.SplashScreen` 枚举值里**并不含**
`WindowDoesNotAcceptFocus`（契约测试首跑即红）。不抢焦点必须显式声明，已加并有用例钉住。

### S3 捕获悬浮窗躲开鼠标（第二轮澄清）

**先定性**：悬浮窗是 `Qt.Tool | WindowStaysOnTopHint | FramelessWindowHint` 的**真实
窗口**（对比 `_HoverOverlay` 带 `WS_EX_TRANSPARENT`），既不透明也不穿透——所以它同时挡住
**视觉**与**点击**，这正是「影响其他元素的捕捉」的实义。而高亮红框必须框住元素、**不可能**
「移到屏幕另一侧」，故澄清后的落点只能是这个浮窗（排查路径：`capture/` 与 `gui/` 全部
浮层类只有 `_HoverOverlay` / `CaptureFloatWindow` / `RunFloatWindow` 三个，前者的 docstring
虽叫「悬浮高亮框」，几何上排除）。

| 文件 | 改动 |
|---|---|
| `gui/capture_float.py` | 新增**纯函数** `capture_float_origin(area, size, cursor, …)`（默认贴右下角；鼠标落入「默认位置矩形外扩 `AVOID_PAD = 80`」即翻到左上角）+ 公开常量 `AVOID_PAD`；`place_bottom_right()` 换成 `avoid_cursor(cursor)`（`cursor=None` = 不避让）；新增 `user_positioned`——用户拖动过就不再自动挪 |
| `gui/app.py` | `_show_capture_float` 初始定位改 `window.avoid_cursor(QCursor.pos())`（鼠标此刻已在右下角就直接去左上）；新增避让节拍 `_capture_avoid_timer`（**40ms**，比 500ms 的状态节拍快得多——否则浮窗会被快速移动的鼠标追上，「即将移动到」来不及）；`_close_capture_float` 停表 |

**为什么判定基准是「默认位置」而不是浮窗当前位置**：基准若跟着浮窗走，翻到左上之后
鼠标仍在右下、判定继续成立 → 结果依赖调用次数（来回抖）。固定基准后，鼠标在右下角区域
⇔ 浮窗在左上角，鼠标一走开自动回位——**双向避让天然成立且无状态**。

**一处如实交代**：S1（框线整条外移到元素外 `[2,5)`）是我对第一句的**字面理解**产物，
不是第二轮澄清要的东西。它是探针实测支持的改进（框线不再压指针、不吃元素内容），
本轮**保留**；若维护者不要这个视觉变化，改动隔离在 `overlay_bounds` / `overlayBoxRect`
两处，可单独回退。

### S4 去掉捕获浮窗的倒计时显示（第三轮追加）

**先把两件事分开**——维护者问的是「90 秒倒计时必须吗」，而这里其实是一机制一显示：

- **90 秒这个上限必须留**：它是两条腿都卡死时的**唯一兜底**。`hybrid.py` 的报障现场
  （bridge 端点在线但扩展没在听 ⇒ 扩展腿收不到 `capture_arm` ⇒ 桌面腿又在浏览器内容区
  让位 ⇒ 两条腿都产不出东西）正是「静默等满」的形状；没有上限则主窗永远最小化、
  `_capture_session` 永不复位，用户只能重启进程。且该链路已有 3s 无 ack 判死 + 30s
  降级预算，把「假在线」的等待收窄。
- **倒计时数字这个显示没必要**：正常捕获几秒就结束，这个数字从头到尾读不完；它把
  「等待上限」表达成「限时任务」，而超时**没有任何惩罚**（重点一次即可）。浮窗的信息位
  要留给可行动的东西——怎么操作（手势）、去哪操作（两条腿的真实状态）、怎么退出。

| 文件 | 改动 |
|---|---|
| `gui/capture_float.py` | 删 `time_label` 与 `tick()`；`show_capture()` 去掉 `timeout_seconds` 参数（不再收预算）；模块 docstring 记「为什么不显示倒计时」 |
| `gui/app.py` | `_tick_capture_float` 只留腿状态刷新；删 `_capture_budget` / `_capture_started_at`（二者唯一读者就是倒计时）；状态栏超时文案「捕获超时（90 秒无手势）」→「捕获超时：一直没有检测到手势，请重试」（**用户可见的秒数一并去掉**）；`CAPTURE_TIMEOUT_SECONDS` 的注释改口径（唯一读者 = 会话层） |

**顺带清掉的**：`_capture_budget` / `_capture_started_at` 改动后若留着，就是本仓最忌讳的
「申明了但没人读」形状（假字段）。**保留**：500ms 状态节拍——它还承担「扩展腿判死后
把网页状态行改红」这件功能面的事，与倒计时无关。

## 3. 验收

| 口径 | 结果 |
|---|---|
| 桌面像素判据（探针 §6/§7） | 压线点 **6/13 → 0/13**（净外移 5）；净外移 0 复现旧行为 6/13 |
| 桌面几何单测 | `tests/unit/test_desktop_overlay_geometry.py` **10 passed**（含「旧几何确实压线」的反证项） |
| 页内几何门禁 | `scripts/check_capture_overlay_geometry.mjs` **27 项全过**（4 种 rect × 4 条带 + 元素内逐点热区 + 无填充） |
| splash 契约 | `tests/contract/test_gui_splash.py` **6 passed**（可见→关闭 / 异常路径 / 不抢焦点 / 幂等 / 泵事件 / 接线顺序） |
| 浮窗避让几何 | `tests/unit/test_capture_float_geometry.py` **11 passed**（翻转 / 提前量 4 档 / 走开回位 / 无状态 / pad 下限） |
| 浮窗避让接线 | `tests/contract/test_gui_capture.py` 新增 3 项（行为：翻转与回位 / 用户拖过不再自动挪 / 四处接线各钉一条） |
| 真机截图 | `.harness/spike/_m41_hover_shots/*.png`（13 张，指针位置画十字）、`_m41_splash_shot.png` |
| 浮窗无倒计时 | `tests/contract/test_gui_capture.py::test_capture_float_never_shows_a_countdown`（1 项，三条断言：文案无「剩余」/无「N 秒」· 无 `tick` 方法 · `show_capture` 不收 `timeout_seconds`） |

## 4. 负向验证

新增/改动的每条判据都做「注入破坏 → 确认**精确**变红 → 逐字节还原」。
探针脚本：`.harness/spike/probe_m41_negative.py`。

| # | 注入（破坏点） | 期望变红的判据 |
|---|---|---|
| 1 | `desktop_agent.py` `overlay_bounds` 改成内缩（回退旧几何） | `tests/unit/test_desktop_overlay_geometry.py` |
| 2 | `desktop_agent.py` `show_rect` 不再调 `overlay_bounds` | 同上 |
| 3 | `desktop_agent.py` `OVERLAY_OUTSET` 改成 3（热区不够） | 同上（`test_outset_is_border_plus_cursor_hotspot` + 逐点热区） |
| 4 | `content.js` `overlayBoxRect` 去掉外扩（left/top 不偏移） | `check_capture_overlay_geometry.mjs` |
| 5 | `content.js` `overlayBoxRect` 的 width/height 不加 `OUTSET*2` | 同上 |
| 6 | `content.js` `show()` 把 `background` 填充加回来 | 同上（「高亮框没有填充」） |
| 7 | `content.js` `show()` 不声明 `box-sizing:border-box` | 同上 |
| 8 | `splash.py` 去掉 `WindowDoesNotAcceptFocus` | `test_splash_does_not_take_keyboard_focus` |
| 9 | `splash.py` `startup_splash` 去掉 `try/finally`（异常不撤卡） | `test_splash_closes_when_initialization_raises` |
| 10 | `splash.py` `open_startup_splash` 去掉 `processEvents()` | （见 §4.1：契约测试覆盖不到，改由源码判据 + 探针覆盖） |
| 11 | `app.py` `run_gui` 把 `load_catalog` 移出 `with` 块 | `test_run_gui_wraps_initialization_in_startup_splash` |
| 12 | `app.py` `run_gui` 把 `app.processEvents()` 挪到 `show()` 之前 | 同上 |

**S3（浮窗避让）另做 11 处**，探针 `.harness/spike/probe_m41b_negative.py`：

| # | 注入（破坏点） | 期望变红的判据 |
|---|---|---|
| 1 | `capture_float_origin` 摘掉翻转分支 | `test_cursor_on_the_window_flips_it_to_the_other_side` |
| 2 | 避让余量归零（判定式里 `avoid_pad` → `0`） | `test_cursor_about_to_reach_the_window_moves_it_early` |
| 3 | `AVOID_PAD` 80 → 10 | `test_avoid_pad_covers_a_fast_flick_between_refresh_ticks` |
| 4 | 判定改成恒真（一去不返） | `test_decision_does_not_depend_on_where_the_window_currently_is` |
| 5 | `avoid_cursor` 不理 `user_positioned` | `test_capture_float_keeps_place_once_the_user_moves_it` |
| 6 | `mouseMoveEvent` 忘了置位 | 同上 |
| 7 | `_show_capture_float` 初始定位不避让 | wiring |
| 8 | `_tick_capture_avoid` 空转 | wiring |
| 9 | 避让节拍 40ms → 500ms | wiring |
| 10 | `_close_capture_float` 不停表 | wiring |
| 11 | `avoid_cursor` 自己算位置、不走纯函数 | wiring |

（wiring = `test_capture_float_avoidance_is_wired_into_the_capture_flow`。#9 特意打「节拍
必须 ≤50ms」这条——它没有对应的行为判据，若不做源码断言，把 40 改成 500 会静默通过。）

**S4（去掉倒计时显示）另做 3 处**，探针 `.harness/spike/probe_m41c_negative.py`
（自备份还原 + 收尾 md5 逐字节核对；**注意别用 `git checkout` 还原**——被测文件当时带着
未提交改动，checkout 会把它们一起退回「带倒计时」的 HEAD 版本）：

| # | 注入（破坏点） | 期望变红的断言（判据文件行号） |
|---|---|---|
| 1 | 把「剩余 90 秒」文案加回浮窗 header | `:624` 文案断言（失败消息里能看到浮窗的完整真实文案） |
| 2 | 只把 `tick()` 加回来（**不渲染任何文案**） | `:629` 「无 `tick` 方法」断言——且文案断言**仍绿** |
| 3 | `show_capture` 加回 `timeout_seconds` 参数 | `:630` 签名断言 |

探针一次跑完并输出 `NEGATIVE VERIFICATION PASSED`（对照绿 → 三处各自精确命中自己的
断言行 → 逐字节还原 → 收尾复跑绿）。**为什么值得单独立三条**：这条判据里塞了三件不同的
事（文案 / 缺口是否清干净 / 签名是否还收预算），只做一处注入的话，另外两条照样可能是
**永远绿**的摆设——#2 就是这一点的实证（它只让第三条断言红，前两条完好）。

### 4.1 已知覆盖缺口（登记，不掩盖）

**#10「`open_startup_splash` 是否泵了事件」契约测试证明不了**：offscreen 下 `show()` 之后
`isVisible()` 立即为 True，泵不泵事件都是 True。这条只能靠**探针在真机截屏**证明
（`_m41_splash_shot.png`：卡片确实画在屏幕上——不泵事件时它是白的/不会出现）。
本任务单如实登记：该点由真机证据覆盖，**不是**由自动化门禁覆盖。

## 5. FULL GATE

### 5.1 S1+S2 收口（2026-09-28，已完成）

`uv run python .harness/scripts/check_all.py` → **exit 0 / 末行 `FULL GATE PASSED`**：

| 检查 | 结果 |
|---|---|
| pytest（默认套件） | **1190 passed / 21 skipped / 2 xfailed in 177.08s** |
| ruff | `All checks passed!` |
| 架构门禁 | `ARCHITECTURE CHECK PASSED (66 python files, 86 manifests)` |
| 任务门禁 | `TASK CHECK PASSED (61 features, 1 active task)` |
| 参数消费门禁 | `PARAM CONSUMPTION CHECK PASSED (77 checked / 3 exempt / 0 skipped = 80 条命令)` |
| 错误契约门禁 | `ERROR CONTRACT CHECK PASSED (77 checked / 3 exempt / 0 skipped = 80 条命令)` |
| 命令矩阵门禁 | `COMMAND MATRIX CHECK PASSED（86 条命令 / 死参数台账 0 / 实现缺口 0 / l2 块 109 个）` |
| 纯函数门禁（node） | 含新增 `check_capture_overlay_geometry.mjs`（27 项）在内全部通过 |

首跑曾因 `check_tasks` 中途中止（M40 仍挂 `active`），修正台账后重跑得上述结果。

### 5.2 S3 收口（2026-09-28）——**全门禁未 PASSED，如实记**

`check_all.py` 连跑 3 次，pytest **稳定 2 failed**（304.5s / 316.4s / 329.8s）：

| 检查 | 结果 |
|---|---|
| pytest（默认套件） | **2 failed / 1202 passed / 21 skipped / 2 xfailed** |
| ruff / 架构 / 任务 / 参数消费 / 错误契约 / 命令矩阵 / 9 个 node 纯函数门禁 | **全部通过** |

（`check_all.py` 在首个失败即 `raise SystemExit`，故上表第二行是本轮**单独逐条**跑出来的真值：
ruff `All checks passed!`；`ARCHITECTURE CHECK PASSED (66 python files, 86 manifests)`；
`TASK CHECK PASSED (61 features, 0 active task)`；param consumption 与 error contract 各
`77 checked / 3 exempt / 0 skipped`；`COMMAND MATRIX CHECK PASSED（86 条 / 死参数台账 0 /
实现缺口 0 / l2 块 109）`；9 个 `scripts/check_*.mjs` 全 OK。）

**1202 = 1190 + 14**：本轮新增的 14 条判据（unit 11 + contract 3）**全部通过**；红的**只有**两条既有用例。

**与本次改动无关（对照实验）**：把 `app.py` / `capture_float.py` 还原到 HEAD 再跑同一组合
→ **同样 `2 failed, 111 passed in 94.14s`**。

**这两条用例本身没坏**：`-k "streams_jsonl or shutdown_kills"` 单跑恒绿
（`2 passed in 1.17s`，多次复现）；整文件单跑也曾 3 连绿（`22 passed in 4.5–6.2s`）。

**失败形态**：两条各有 **30s 固定预算**，超时点分别是「子进程还在跑，结果行却没实时出现——
轮询没接上」与「子进程没起来」——即**子进程在 30s 内没有产出**。

**触发面在环境里，不在仓库里**（已确证的两段）：本机 python 走沙箱 shim
`…\WorkBuddy\resources\app.asar.unpacked\cli\vendor\shim\sitecustomize.py`——它把
`os.remove` / `shutil.rmtree` 全部改道回收站，**每一次**删除都
`subprocess.run([node, guard, "check", "--target", …])` 起一个 Node 守卫进程（timeout 10s）；
当「本 turn 累计删除数」超过阈值 50 时它直接
`[SAFE_DELETE_BULK_CONFIRM_REQUIRED] → raise SystemExit(1)`——本轮实测该计数已到 **2248**，而
pytest 自己的临时目录清理正是往这个计数器里加数。全量套件删除密集 ⇒ 每次删除付一次 Node 激活
⇒ 全量套件从上午的 177s 变成 300s+，两条 30s 预算的用例被顶穿；单跑时计数低、不触发 ⇒ 恒绿。
**（机制为高度疑似，未坐实最后一跳）**：已确证的是「守卫每次删除起 Node 进程」与「阈值 50 /
本 turn 2248 时抛 SystemExit」这两段代码，以及门禁报告里那段 `sitecustomize.py` 栈；
**未确证**的是这两条用例被顶穿的具体那一步。

**排查中被打掉的三个假设（留证，免得后人重走）**：
① 一度指向 `tests/contract/test_browser_precheck.py`（六轮二分：`[1-6]+[29]` 红 → `[1-3]+[29]`
绿 → `[4-6]+[29]` 红 → `[6]+[29]` 红），但**它自己单跑只花 0.41–1.12s**——同一份会话里它当过
64s 的「受害者」，不是元凶；
② 曾指向 `tests.commands.desktop_harness` 的 import（用 `-p` 引导插件实测「只 import 它就 2 failed」）
→ 补一个只 `import json` 的**空对照插件**同样 2 failed ⇒ 被对照否定；
③ 曾指向 `PYTHONPATH=.`（`2 failed`）→ 几分钟后同一条命令 `1s 全绿` ⇒ 也被否定。
三条的共同教训：**这条抖动随时间开合，任何「单次实验的相关性」都不成立，必须带对照。**

已按上述结论登记 BACKLOG「QProcess 型 GUI 用例在机器高负载下会 30s 超时」，
**不通过改断言/放宽超时来消红**（那会把真实缺陷一起放掉）。

### 5.3 S4 收口（2026-09-28）——**全门禁仍未 PASSED**，红的与 S4 无关

`check_all.py` 连跑 2 次：**2 failed / 1203 passed / 21 skipped / 2 xfailed**
（292.25s / 313.16s）。

| 检查 | 结果 |
|---|---|
| pytest（默认套件） | **2 failed / 1203 passed / 21 skipped / 2 xfailed** |
| ruff / 架构 / 任务 / 参数消费 / 错误契约 / 命令矩阵 / 9 个 node 纯函数门禁 | **全部通过**（逐条单跑，见下） |

**1203 = 1202 + 1**：S4 新增的 1 条判据**通过**；红的只有 §5.2 那两条既有 QProcess 用例。
（`check_all.py` 在首个失败即 `raise SystemExit`，故其余检查必须**分别取证**：ruff
`All checks passed!`；`ARCHITECTURE CHECK PASSED (66 python files, 86 manifests)`；
`TASK CHECK PASSED (61 features, 0 active task)`；参数消费与错误契约各通过、各有 3 条
未实现命令整表豁免（`browser.download` / `browser.handleDialog` / `browser.upload`）；
`COMMAND MATRIX CHECK PASSED（86 条命令 / 死参数台账 0 / 实现缺口 0 / l2 块 109）`；
9 个 `scripts/check_*.mjs` 全 OK。）

**本轮把 §5.2 的「抖动」再收窄一层——它可复现，不是偶发**：

```text
门禁【前】单跑那两条           → 2 passed in 1.25s     ← 绿
跑完一次全量门禁（292s）后立刻单跑 → 2 failed in 62.21s   ← 红（同一条命令、同一份代码）
再隔几分钟单跑                 → 2 passed in 1.27s     ← 又绿
```

即**「跑完一次全量套件」本身会把这两条推入红窗**，然后自行恢复。与上午的对比也一致：
上午 `FULL GATE PASSED` 那轮全量只花 **177s**，现在 **292–330s**——套件变慢与这两条变红同现。

**本轮同时否证两条候选机制**（都实测过，都排除）：

- **不是「子进程启动慢」**：红窗内实测 `.venv/Scripts/python.exe -c pass` =
  0.91 / 0.93 / 0.82s、`node -e 0` = 0.66s、`uv run python -c pass` = 1.32s，`rc` 全 0。
  30s 预算被 1 秒级的启动顶穿讲不通 ⇒ §5.2 那条「删除守卫每次起 Node ⇒ 慢」**不是**直接原因。
- **不是调用口径**：`.venv\Scripts\python.exe -m pytest` 与 `uv run pytest` 同绿同红。

⇒ 结论仍是「**与仓库改动无关的既有环境问题**」，但描述要更新：不是「高负载下的偶发超时」，
而是「**全量套件跑完后的一段时间窗内稳定超时**」。已据此更新 BACKLOG 条目。

## 6. 登记不做 / 残留

- **元素命中粒度**：鼠标移到窗口边界时，命中对象退化为「窗口本身」，框会一下铺满整窗
  （探针 §4 的 `edge-*` 点）。这是**命中语义**问题（那 1px 确实属于窗口），不是框的几何
  问题；改「框」治不了它，需另立选题（是否要 UIA 继续下钻到最近子元素）。
- **页内提示条跟随鼠标**：现行提示条跟元素、不跟鼠标（在元素外侧，压不到指针）。
  影刀式「跟鼠标并翻边」是另一种观感，本轮不改，理由见 §2 S1。
- **splash 的可取消性**：初始化约 1 秒，未提供「取消启动」入口（收益低）。
- **运行浮窗（`RunFloatWindow`）不避让**：它同样是右下角的真实窗口，运行期间也可能挡住
  右下角元素。本轮只做捕获链路（维护者报的是捕获场景），未扩大范围。
- **避让只按主屏几何**：`QGuiApplication.primaryScreen()`——多显示器下即便浮窗被拖到副屏，
  避让仍按主屏算（沿用既有定位口径，未扩大范围）。
- **避让是「翻到对角」而不是「连续跟随」**：鼠标缓慢逼近时浮窗一次性翻到左上，不做位移
  动画（收益低；动画期间浮窗反而更容易与鼠标重叠）。
- **全门禁那 2 条红是既有环境问题，不在本里程碑范围内**：`test_gui_command_matrix.py` 的
  `test_panel_streams_jsonl_into_rows` / `test_shutdown_kills_running_child`（各 30s 预算）在
  全量套件里被顶穿——HEAD 对照同样失败、单跑恒绿。已登记 BACKLOG，**不在本轮改测试**。
  （**订正（同日 S4 轮）**：旧表述说「触发面在沙箱删除守卫」——§5.3 已把它否证（红窗内
  子进程启动只要 0.9s），准确表述是「**跑完一次全量套件后的一段时间窗内稳定超时**」。
  旧结论按本仓惯例保留、不删，订正另记于此。）
- **90 秒上限本身保留，只去掉显示**（S4）：若将来要在「等太久」时给用户信号，正确做法是
  「等待超过 N 秒才出现一行中性提示 + 出口」，**不是**把倒计时加回来——判据
  `test_capture_float_never_shows_a_countdown` 会拦住后者（它对「任何 N 秒」都判失败）。
- **`RunFloatWindow` 的运行时长仍会显示**：本轮只处理捕获链路；运行浮窗是另一套语义
  （运行有时长预期，用户确实可能关心还剩多久），不在本次范围。
