# ADR 0019：编辑器宿主形态三 —— PPX 壳并行实验，Qt 源码冻结

- 状态：**已接受（2026-10-28）**
- 日期：2026-10-28
- 关联：ADR 0014（编辑器宿主形态二 —— 方案 E 原生 PySide6）、ADR 0016（GUI 为唯一主力形态）、
  ADR 0010（保留 Web）、ADR 0011（运行控制 proxy）、ADR 0012（devserver 能力层复用）
- 目的：在不动既有 Qt 交付物的前提下，给「PPX（WebView 壳）能不能当编辑器宿主」一次**可证伪**的
  实验，并把「Qt 不动」从一句口头共识变成机器判据。

## 1. 背景

ADR 0014 去年把候选列成 A–E（pywebview 薄壳 / Electron / Tauri / QWebEngine / 原生重写），选了
方案 E 并落地五个切片；ADR 0016 进一步定「GUI 是未来唯一主力形态」。此后 GUI 已 14.7k 行、二十余个
模块。2026-10 出现新输入：维护者要求评估 PPX 与 Tauri，并做一份 PPX 初版看效果。

初版已落地在 `.harness/demo/ppx_probe/app/`（独立 venv、独立 PPX 项目），**接的是真实能力层**，
不是官方 hello-world：

| 项目 | 实测 |
|---|---|
| 指令目录 `load_catalog()` | 89 条命令；热态 3.8 ms（`lru_cache` 生效） |
| `bootstrap`（目录 + i18n 中文名 + 扫样例流程） | 20 ms |
| `openFlow`（`Workflow.model_validate` → AST 摊平） | 3.8 – 22 ms（2~18 个 action） |
| `nodeDetail` RPC 往返 | 2.2 ms |
| `compileCheck`（`WorkflowCompiler.compile`） | 5 – 231 ms；8 个样例流程全部编译通过 |

结论层面只证明了一件事：**三栏编辑器（指令树 / 流程卡片 / 节点详情）在 WebView 里撑得住**，
中文渲染、选中态、等宽参数摘要、深度色线都接近 Qt 版。它**没有**证明覆盖层能跑 —— pywebview
官方仍标 `transparent` 在 Windows 不支持，元素拾取的透明置顶框需要单独实证。

同时暴露两处 PPX 自身的窗口策略缺陷（已在原型内绕过，属宿主限制不是配置写错）：

1. `heightRatio` 按整屏算而不看工作区：`0.8 × 1080 = 864`，居中后底边 1072，被任务栏切掉最后一行。
2. `ppx dev` 默认弹 640×640 的 DevTools 悬浮窗盖在界面上，未找到关闭开关。

## 2. 决策

### 2.1 Qt 源码冻结

`src/rpa_core/gui/**` **留着不动、也不改**：不改功能、不重构、不删文件、不为原型做适配。

冻结由机器判据保证，不是文档承诺：

- `.harness/gui-freeze.json` 记录 22 个 `.py` 的逐文件 sha256；
- `.harness/scripts/check_host_boundary.py` 在门禁里比对「文件集合 + 每个 sha256」，
  新增 / 删除 / 修改任一项都红；
- 若确需解冻，跑 `check_host_boundary.py --update-freeze` 重建基线 —— **那一步就是签字**，
  会以基线文件的 diff 出现在提交里，无法悄悄发生。

### 2.2 PPX 作为并行实验宿主

1. 原型只待在 `.harness/demo/ppx_probe/`：**不进 `src/`、不进主 `pyproject.toml`、不往主 venv
   装 PPX**。原型与主仓库的耦合面只有一个 RPC 薄层（`app/api/api.py`）。
2. 能力层仍是唯一事实来源。原型通过 `import` 消费 `rpa_core.{catalog,compiler,model}`，与
   ADR 0012 的语义一致；**能力层不得反向依赖 `ppx_py`**（判据在同一个门禁脚本里）。
3. 原型不得 import `PySide6`，也不得 import gui 包里的 UI 模块 —— 否则「WebView 壳不靠 Qt」
   这个结论自我作废。唯一豁免是 `rpa_core.gui.i18n`（见 §4 的债）。

### 2.3 实验的退出条件（三条实证，全过才谈下一步）

按「先证生死项、再证体力活」排序：

1. **覆盖层**：透明置顶窗口 + 鼠标穿透 + 捕获高亮框。这条不过，PPX 直接出局（GUI 的
   `capture_float` / `run_float` 没有替代品）。
2. **打包分发**：能产出一个双击可跑、体积可接受、带 Python 运行时的产物（与 M61 同题）。
3. **桌面集成**：单实例、全局热键、托盘、最小化到托盘。

三条过完之后才讨论 14.7k 行 Qt 的处置 —— 在此之前**不讨论迁移**，也不动 `src/rpa_core/gui`。

### 2.4 覆盖层实证结果（2026-10-28 当天，第一条已验）

探针 `.harness/demo/ppx_probe/overlay_probe.py` 跑了四轮，每轮都把结论落成读数
（截图 + 像素占比 + `WindowFromPoint` 归属 + 页面内 mousemove 计数）：

| 项 | 读数 | 结论 |
|---|---|---|
| `transparent=True` + `frameless` + `on_top` | 窗口能建、红框能画，但截图 **98.09% 的像素是 `#F0F0F0`** | pywebview 只把 **WebView2 页面**背景设透明，Form 仍是 WinForms 默认 `Control` 灰 ⇒ **视觉上是块灰板**，官方文档说「Windows 不支持」并没有完全过时 |
| `BackColor=洋红` + `TransparencyKey` / `SetLayeredWindowAttributes` | 灰 `0.0%`、洋红 `0.0%`，截图里能看到下层 Windows Terminal 的真实文字 | **抠色能做真透明**，但页面里不能使用该基色，抗锯齿边缘会留毛边 |
| 穿透 | 抠色后 `WindowFromPoint` 返回下层窗口（`CASCADIA_HOSTING_WINDOW_CLASS`, pid 5020）；透明区与 6 处红框采样点**均未命中自身窗口** | 抠色区对鼠标天然透明；且**不能依赖覆盖层自身接收点击** ⇒ 确定/取消这类控件必须**另开一个非穿透小窗口**（与 Qt 版 `capture_float` + tooltip 面板的两窗口结构一致） |
| 穿透下的 mousemove | 页面计数 **0 次**；去掉穿透后同操作 **3 次** | pywebview **没有** Electron 的 `forward:true` ⇒ 高亮框跟随只能靠轮询 `GetCursorPos` |
| 轮询跟随 | `GetCursorPos` → `evaluate_js` 推坐标，红框从 `(0,0)` 走到 `(412,252)`，HUD 同步显示 `mousemove=0` | 方案可用；频率与 CPU 占用待定（拾取场景通常 30–60 Hz 足够） |

即：**PPX 能替代 `capture_float`，但不是"打开一个开关"**，而是三件事拼起来
（抠色基色 + 整窗穿透 + 轮询驱动）。Qt 里对应的是 `WA_TranslucentBackground` +
`WindowTransparentForInput` 两行属性。这笔复杂度差要记在迁移账上，不是"做不到"。

## 3. 后果

**正面**

- 既有 GUI 交付物零风险：不重构、不双写，冻结期内的任何回归都不可能来自本实验。
- 冻结让「共识」可验证。此前「不改 Qt」只是口头约定，下一个人换个说法就能绕过去。
- 原型成本可控：一个 `ppx new` + 一个 RPC 文件 + 一份前端，一小时内能看到真实数据跑起来。

**代价 / 约束**

- 冻结期内 GUI 的**缺陷修复与观感治理一并停摆**。M31（GUI 跨平台观感审计）仍 `planned`，
  开工前必须先解冻；解冻要走 §2.1 的签字流程。
- 原型不进 lint 与文档门禁（`.harness/demo/` 在 ruff `exclude` 里），质量只能靠人工与
  即将到来的实证 —— 这是「脚手架」与「产品代码」的分界，故意如此。
- 多一套前端要读：同一个界面在两处的实现会漂移。缓解手段是**原型只做实验需要的部分**，
  不追平 GUI 的功能面（与 ADR 0016 对 Web 编辑器的处置同口径）。

## 4. 已知债

1. **`gui.i18n` 的位置债**：中文标签的唯一 Python 入口物理上住在 `gui/` 包里，而它其实只是
   `devserver/static/i18n.js` 的解析器（不 import Qt）。原型依赖它是**只读依赖**，冻结期内
   不能搬走。解冻后应迁到能力层侧（如 `rpa_core.catalog.i18n` 或独立模块），届时两边都受益。
2. **冻结基线与「有意解冻」的同步**：基线是快照，不是允许清单。任何一次 `--update-freeze`
   都必须能在提交信息里找到理由，否则下一个人无法区分「签字解冻」与「偷偷改完再刷基线」。
3. **`heightRatio` / DevTools 悬浮窗**是 PPX 6.0.1 的行为，不排除后续版本修掉；原型里的
   `0.72` 是为了当下能看全，不是结论。

## 5. 与 ADR 0014 / 0016 的关系

**本文不改变 ADR 0016 的结论，只给它加一个到期复审条件。**

- ADR 0014 的方案 E 仍是**已交付形态**，继续是默认桌面入口；本文只是把当年被列为方案 A 家族的
  「WebView 薄壳」重新拉回可比较的位置 —— 当年否掉它的理由是「没有独立桌面体验」，而 PPX 这类
  「壳 + 打包 + CLI/RPC」的成熟度已经变了。
- ADR 0016 的「GUI 是唯一主力」在实验期内**仍然描述现状**（GUI 仍是默认形态、仍不新增 Web 能力）。
  只有当 §2.3 三条实证全部通过、并且有第二份 ADR 明确写下迁移决策时，该结论才被改写。
- 在此之前，「PPX 更好」与「PPX 不行」都是待验假设。§2.3 就是那个能判它死的判据。
