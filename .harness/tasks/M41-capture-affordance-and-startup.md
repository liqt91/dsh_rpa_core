# M41 捕获高亮「躲着鼠标」/ 启动加载提示

状态：`active`

由来：维护者 2026-09-28（M40 第二轮收口后）提两条体验要求——

> 捕获元素的框跟影刀的一样，躲着鼠标，否则影响其他元素的捕捉。
> 另外，启动前如果要初始化，可以有个加载提示。

两条都不是崩溃类缺陷，而是**观感与反馈**：①捕获红框压在鼠标指针上；②冷启动约一秒的
空白等待。沿用本仓纪律——先一手实测归因（探针可复现），再改实现，最后逐条负向验证 +
全门禁。两条各自独立成切片，互不依赖。

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

## 3. 验收

| 口径 | 结果 |
|---|---|
| 桌面像素判据（探针 §6/§7） | 压线点 **6/13 → 0/13**（净外移 5）；净外移 0 复现旧行为 6/13 |
| 桌面几何单测 | `tests/unit/test_desktop_overlay_geometry.py` **10 passed**（含「旧几何确实压线」的反证项） |
| 页内几何门禁 | `scripts/check_capture_overlay_geometry.mjs` **27 项全过**（4 种 rect × 4 条带 + 元素内逐点热区 + 无填充） |
| splash 契约 | `tests/contract/test_gui_splash.py` **6 passed**（可见→关闭 / 异常路径 / 不抢焦点 / 幂等 / 泵事件 / 接线顺序） |
| 真机截图 | `.harness/spike/_m41_hover_shots/*.png`（13 张，指针位置画十字）、`_m41_splash_shot.png` |

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

### 4.1 已知覆盖缺口（登记，不掩盖）

**#10「`open_startup_splash` 是否泵了事件」契约测试证明不了**：offscreen 下 `show()` 之后
`isVisible()` 立即为 True，泵不泵事件都是 True。这条只能靠**探针在真机截屏**证明
（`_m41_splash_shot.png`：卡片确实画在屏幕上——不泵事件时它是白的/不会出现）。
本任务单如实登记：该点由真机证据覆盖，**不是**由自动化门禁覆盖。

## 5. FULL GATE

`uv run python .harness/scripts/check_all.py` → **exit 0 / 末行 `FULL GATE PASSED`**（报告 `_m41_gate2.txt`）：

| 检查 | 结果 |
|---|---|
| pytest（默认套件） | **1190 passed / 21 skipped / 2 xfailed in 177.08s** |
| ruff | `All checks passed!` |
| 架构门禁 | `ARCHITECTURE CHECK PASSED (66 python files, 86 manifests)` |
| 任务门禁 | `TASK CHECK PASSED (61 features, 1 active task)` |
| 参数消费门禁 | `PARAM CONSUMPTION CHECK PASSED (77 checked / 3 exempt / 0 skipped = 80 条命令)` |
| 错误契约门禁 | `ERROR CONTRACT CHECK PASSED (77 checked / 3 exempt / 0 skipped = 80 条命令)` |
| 命令矩阵门禁 | `COMMAND MATRIX CHECK PASSED（86 条命令 / 死参数台账 0 / 实现缺口 0 / l2 块 109 个）` |
| 纯函数门禁（node） | 含新增 `check_capture_overlay_geometry.mjs`（27 项）在内全部「全部通过」 |

首跑（`_m41_gate1.txt`）曾因 `check_tasks` 在中途中止（M40 仍挂 `active`，见 §4 后的收口记录），
修正台账后重跑得上述结果。报告里另有 `SystemExit: 1` 一行——是 pytest `atexit` 批量删除回调的
已知噪声（M40 已实测：**atexit 里抛 SystemExit 不改进程退出码**），判门禁看**末行 + 退出码**。

## 6. 登记不做 / 残留

- **元素命中粒度**：鼠标移到窗口边界时，命中对象退化为「窗口本身」，框会一下铺满整窗
  （探针 §4 的 `edge-*` 点）。这是**命中语义**问题（那 1px 确实属于窗口），不是框的几何
  问题；改「框」治不了它，需另立选题（是否要 UIA 继续下钻到最近子元素）。
- **页内提示条跟随鼠标**：现行提示条跟元素、不跟鼠标（在元素外侧，压不到指针）。
  影刀式「跟鼠标并翻边」是另一种观感，本轮不改，理由见 §2 S1。
- **splash 的可取消性**：初始化约 1 秒，未提供「取消启动」入口（收益低）。
