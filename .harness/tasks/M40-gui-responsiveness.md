# M40 GUI 响应性：启动阻塞 / 切页无响应 / 捕获静默等待

状态：`active`

（第二轮，2026-09-28 追加报障；见 §10。第一轮曾判 `done` 并提交，第二轮实测发现其修复把阻塞
**搬了位置**而非消除，故重开。）

由来：维护者 2026-09-28 报障三句——「冷启动很慢，启动后，内部切换标签也很慢，捕获元素第一次
点击没有红框出现，但再次点击提示已有捕获任务进行中」。本任务单把三句拆成**可归因的三条**，
每一条都有一手实测或现场日志支撑，不做推测性归因。

第二轮（同日）：维护者指认「内部标签」就是工作台三页签，并追加「第一次点击很明显 / 第一次开
元素库会卡 / 重启再测就不卡了」。实测发现第一轮的修复把阻塞**搬了位置**而非消除，故重开
（§10）。

## 0. 一句话结论

三条报障是**两个 bug + 一个误导性判据**：

1. **启动在关键路径上同步扫运行产物**：`HomeWindow.__init__` → `refresh_flows()` → `list_runs()`
   扫 `run_artifacts/` 全部目录，且**同一份扫描在 `refresh_history()` 里又来一遍**。窗口要等它
   扫完才出现，主线程在扫描期间不响应任何输入——**「冷启动慢」与「启动后切标签慢」是同一件事**。
2. **捕获链路有一条「端点在线但扩展没响应」的静默通道**：`ExtensionCaptureSession.start()` 用
   「端点存在 + 能连上」判定扩展在线，但**扩展是否真的收到 `capture_arm` 没有任何校验**（扩展侧
   明明回了 `capture_armed` ack，父端把它当无关消息丢弃）。于是插件没响应时，扩展腿既不失败也
   不出结果，桌面腿又在浏览器内容区让位（hybrid）→ 两条腿都产不出东西 → **静默等满 90 秒**。
3. **捕获期间用户看不到任何反馈、也没有取消入口**：主窗已 `showMinimized()`，唯一的反馈面
   （状态栏）随之不可见；第二次点「捕获元素」只得到一句「已有捕获会话进行中（Esc 取消）」——
   而 Esc 是**腿内部**的手势，不响应时用户无路可走。

## 1. 证据（一手，可复现）

### 1.1 现场日志：维护者那次运行的线程栈

`%TEMP%\rpa_gui_crash.log`（2026-09-28 09:17，六处证据同源）：

- 主线程栈 `run_gui` → `HomeWindow.__init__` → `refresh_flows` → `list_runs` → `_read_json` →
  **`KeyboardInterrupt`**：启动卡在扫产物，维护者按 Ctrl+C 想中断。
- 同时在场三个捕获相关线程：
  - `capture/hybrid.py:136 pick`（轮询 sleep）← `gui/app.py:2755 work` → **pick 仍在等**；
  - `capture/desktop.py:101 pick`（`queue.get`）→ 桌面 agent 子进程活着、没回结果；
  - `local_transport.py:799 _read_once` ← `capture/extension.py:147 _read_loop` → **扩展腿通道已连上**
    （只有 connect 成功才会起读线程）→ `extension_offline == False` → 不弹离线确认、不降级。

### 1.2 本机通道实测（2026-09-28）

```text
$ rpa-core 侧探测
endpoints: ['rpa_core_ext_msedge_33738899c67a8455']      # 端点存在 → 判「在线」
channel_diagnostics:
  edge: running=true, extensionLoaded=true, extensionInjected=false
  reason: host-not-reachable
  summary: bridge 已注册 · 插件已安装 · 浏览器在运行 · host 未上线
$ tasklist
rpa-core-ext-host.exe   25776   4,240 K                   # 残留 host 进程，占着端点
```

**「端点存在」与「扩展真正在响应」是两件事**，而链路只用前者。同时暴露一个**判据与注释不符**：
`classify_offline_reason` 的注释写「扩展加载着**却没端点**」，实现里根本没有端点这一项，
只要 `running and extensionLoaded` 就报 `host-not-reachable`（见 §4 自挖项）。

### 1.3 启动/切页分段实测（热态，本机 Windows）

`offscreen` 与真机两版探针各跑一遍（`.harness/spike/probe_gui_startup_cost.py` /
`probe_gui_startup_real.py`），真机版关键段：

| 段 | 耗时 |
|---|---|
| 解释器 + site | ~137 ms |
| import `rpa_core.cli`（含 catalog/pydantic/jsonschema） | ~510 ms |
| import `rpa_core.gui.app`（含 PySide6） | ~600 ms |
| `QApplication` + `apply_theme`（QSS 生成 145 + 应用 ~610） | ~757 ms |
| `HomeWindow.__init__`（其中 `list_runs` 跑了两遍 ≈ 210 ms） | ~663 ms |
| `show()` + 首帧 | ~325 ms |
| **合计到工作台可见** | **~2.4 s** |

**切页实测：首次显示「流程库」2.6 ms / 「运行历史」7.1 ms / 「指令测试」3.5 ms（offscreen）；
真机 5.6 / 15.4 / 8.1 ms。** —— 也就是说，**QTabWidget 本身不慢**；维护者感受到的「切换标签很慢」
只能是**启动头几秒主线程被 `refresh_flows` 占住时窗口不响应**（探针里 `refresh_flows` 是被同步
调用完的，所以量不到这段阻塞）。这条推论**尚未有维护者侧的确认**，故本片只按「消除启动期同步
阻塞」处理，不额外改 QTabWidget（见 §5 待澄清）。

### 1.4 ack 早已在协议里，只是被丢了

```js
// extension/background.js
case "capture_arm":
  captureSessionId = msg.sessionId || null;
  setCaptureArmed(true);
  broadcast(true);
  post({ type: "capture_armed", sessionId: captureSessionId });   // ← 扩展的 ack
```

```python
# capture/extension.py::_read_loop
if message.get("type") != "capture_result":
    continue        # ← capture_armed 在这里被丢弃
```

host 侧 `ext_bridge` 对非 `result` 消息一律 `_broadcast`（`capture_armed` 会到达父端），
所以**无需改协议、无需改扩展**，父端接上这个 ack 即可判活。

## 2. 本里程碑要做的四件事

### 2.1 捕获：用 `capture_armed` ack 判活（根因修复）

- `ExtensionCaptureSession` 记录 ack（新增 `armed_event` / `armed` 属性），`_read_loop` 里对
  `capture_armed`（`sessionId` 匹配）置位；
- `HybridCaptureSession.pick()` 在扩展腿「已连接但超期未 ack」时把它**判死**（记入
  `extension_failure`，原因写明「端点已连接但扩展未确认 capture_arm」），继续等桌面腿；
- 两条腿都出局 → `_exhausted` 立即返回真实原因（**不再等满 90 秒**）。

超时取 3.0 秒（host 冷启动 + 扩展广播的宽限），可注入以便测试。**正常路径零影响**：
扩展活着时 ack 在毫秒级返回。

### 2.2 捕获：可见反馈 + 取消入口

- 新建 `gui/capture_float.py::CaptureFloatWindow`（置顶、无边框、右下角，沿用 `run_float.py`
  的观感与 `WA_MacAlwaysShowToolWindow` 约定）：显示手势提示、**两条腿的真实状态**、剩余秒数，
  并提供「取消捕获」按钮；
- `app._capture_element`：捕获期间显示浮窗；**第二次点击 = 取消当前捕获**（而不是只拒绝），
  并提示「已取消上一次捕获，请重试」；
- `_on_element_captured` / 窗口关闭 / `_shutdown_run_manager` 一律关闭浮窗。

### 2.3 捕获：`work()` 保底 emit（消除「永久卡住」）

`work()` 现在只有 `finally: session.close()`；`pick` 若抛异常（含 `KeyboardInterrupt`），
`finished` 永不 emit → `_capture_session` 永不复位、主窗永不还原。改为 try/except 构造错误
结果并**保证 emit**。

### 2.4 启动：把产物扫描移出关键路径

- `refresh_flows()` 的 `list_runs` 结果**复用**给 `refresh_history()`（去掉第二遍全量扫描）；
- `HomeWindow.__init__` 新增 `defer_refresh` 开关（默认 `False`＝同步，**既有测试零改动**）；
  `run_gui` 传 `True` 并在 `show()` 后 `QTimer.singleShot(0, ...)` 触发——**窗口先出现，数据随后填**；
- 表格填充加 `setUpdatesEnabled(False)` 包裹。

## 3. 验收口径

1. `HybridCaptureSession` 在「扩展已连接但不 ack + 桌面腿不可用」下**立即**返回 `unavailable`
   且原因可辨；在「扩展 ack 正常」下行为与改动前逐字一致（有回归用例）。
2. `_capture_element` 第二次点击**取消**进行中的会话（状态机可达 `_capture_session is None`），
   且 `pick` 抛异常时也会复位 + 还原窗口。
3. 浮窗在捕获期间显示、结束即关；取消按钮接线到 `session.cancel()`。
4. `HomeWindow(defer_refresh=True)` 不在构造期扫产物（可用桩计数证明），`defer_refresh=False`
   行为不变。
5. `check_all.py` FULL GATE PASSED；新增/修改的判据逐条做**负向验证**（两个方向）。

## 4. 自挖项（登记，不在本片清）

- `classify_offline_reason` 的 `host-not-reachable` 判据与注释不符（注释说「加载着却没端点」，
  实现没有端点项）→ 进 BACKLOG，本片不顺手改（它不是本次报障的现场，且改它要连带改文案与测试）。
- 残留 `rpa-core-ext-host.exe` 占着端点（M34 的空闲自杀阈值 30 分钟）：ack 判活后**用户体验已
  不受影响**（会被判死并提示），进程侧靠既有 M34 机制收敛 → 只登记。

## 5. 待维护者澄清 → 已结案（2026-09-28 第二轮）

- 问的是：「内部切换标签」若指的不是工作台三个页签（实测 5–15 ms），请指认具体界面。
- **维护者答**：就是内部标签切换，第一次点击很明显；另外点元素库第一次也会卡一下、关掉重开就
  好多了；且重启 `uv run rpa-core gui` 再测就不卡。
- 代码侧复核：`grep -rn QTabWidget src/` **全仓只有一处** → `HomeWindow` 三页签，确认所指。
  「元素库」不在工作台，是**编辑器**（`MainWindow`）的 dock（入口：工具栏 action「元素库」/ 切 G）。
- 该报障的根因与修法见 §10（**不是** QTabWidget 本身）。

## 6. 验收结果（一手实测）

| 口径 | 结果 |
|---|---|
| 三份契约测试 | `test_gui_capture.py` 16 + `test_capture_hybrid.py`/`test_capture_extension.py` 21 + `test_gui_home.py` 9 = **46 项全绿**（本轮合跑 37 项 19.6 s / exit 0） |
| 大范围回归 | `tests/contract -k "gui or editor"` **435 passed / 531 deselected / 38.92 s** |
| 静态门禁 | `check_architecture`(65 py / 86 manifest)、`check_param_consumption`(77 checked/3 exempt/0 skipped)、`check_error_contract`、`check_command_matrix`(86 条命令)、`check_tasks` 全 PASSED |
| 负向验证 | **8 处注入全部「对照绿 → 注入精确红 → 逐字节还原」**（见 §7） |
| FULL GATE | `check_all.py` 见 §9 |

## 7. 负向验证（15 处，探针 `.harness/spike/probe_m40_negative.py`）

探针有四条**防假绿灯**设计，缺一条就可能是假的：

1. **对照跑**：注入前先用原始文件跑同一个 nodeid，必须**绿**。只看注入跑红、不看对照跑绿的话，
   一个「无论跑什么都红」的环境（语法坏、收集错、沙箱退出码污染）会让 N 处全部报 OK。
2. **失败类型判定**：注入跑必须出现 `N failed` 且**不出现** `N error`——`returncode != 0` 既可能是
   断言失败，也可能是语法/收集错误，后者根本证明不了判据承重。
3. **超时按「挂住」处理**：判据被破坏后若走进真模态对话框就会永久阻塞（offscreen），
   那既不是红也不是绿，必须单独报出来**修判据**，而不是硬等超时。
4. **逐字节还原**：每处注入前后核 md5。

| # | 注入（破坏点） | 期望变红的 nodeid |
|---|---|---|
| 1 | `hybrid.py` 摘掉 `self._extension_unresponsive = True` | `test_hybrid_extension_without_ack_is_marked_dead` |
| 2 | `hybrid.py` 摘掉 `and not self._extension_armed()` | `test_hybrid_extension_ack_keeps_leg_alive` |
| 3 | `extension.py` 不记录 `capture_armed`（写侧） | `test_hybrid_extension_ack_keeps_leg_alive` |
| 4 | `app.py` 第二次点击只提示、不取消 | `test_capture_second_click_cancels_running_session` |
| 5 | `app.py` 去掉 `work()` 保底 emit | `test_capture_pick_exception_still_resets_and_restores` |
| 6 | `app.py` 不拦迟到结果 | `test_capture_late_result_from_replaced_session_is_ignored` |
| 7 | `home.py` `defer_refresh` 失效（恒同步扫） | `test_home_defer_refresh_skips_artifact_scan_at_construction` |
| 8 | `home.py` 两页签各扫一遍 | `test_home_refresh_scans_artifacts_once` |

第二轮新增（#9–#15）：

| # | 注入（破坏点） | 期望变红的 nodeid |
|---|---|---|
| 9 | `run_history.py` `sort_runs` 去掉 `reverse=True` | `test_iter_run_summaries_plus_sort_runs_match_list_runs` |
| 10 | `home.py` 分片预算失效（一轮扫完） | `test_home_incremental_refresh_matches_sync_refresh` |
| 11 | `home.py` 待读占位符改回「未运行」 | `test_home_scan_pending_does_not_claim_unrun` |
| 12 | `home.py` 同步刷新不取代分片扫描（`refresh_flows`） | `test_home_sync_refresh_supersedes_pending_scan` |
| 13 | `app.py` 接线回退到同步版 `refresh_flows` | `test_run_gui_wires_sliced_refresh_on_workbench` |
| 14 | `app.py` 首显不预热元素库 dock | `test_editor_show_prewarms_elements_dock_hidden` |
| 15 | `home.py` 历史页签「刷新」不取代分片扫描（`refresh_history`） | `test_home_sync_refresh_supersedes_pending_scan` |

> #12 与 #15 打在**同一段逻辑的两条入口**上、期望**同一个** nodeid：该用例的循环体已
> 覆盖两条同步入口，任一条丢掉 `_abort_run_scan()` 都应让它红。#15 是第二轮收尾时补的
> ——最初只改了 `refresh_flows`，「历史」页签的「刷新」按钮是同类的第二条静默覆盖入口
> （旧分片收尾会用更早的结果盖掉用户刚刷出来的）。

### 7.3 事故与加固：陈旧备份把新源码盖回去了（第二轮）

探针第一版的「起手自愈」是按**备份目录里有这个文件**还原，于是第二轮跑的时候，它把**上一轮留下的
陈旧备份**盖到了这一轮已经改好的源码上——`app.py` / `home.py` 被退回 HEAD 状态，本轮改动全丢
（靠会话里逐条 Edit 的原文重放恢复，`ast.parse` ＋ grep ＋ ruff 复核）。

反思：还原的判据必须是「**这份文件此刻是不是注入态**」，而「备份目录里有没有它」回答的是另一个问题。
改法：注入时在改动处留哨兵（`# [M40-NEGATIVE-INJECTED] idx=N`），自愈只认哨兵、按记录的
(原文, 注入后) 对反推还原——干净源码永远不会被误还原。**该自愈机制本身也做了实测**：手动注入
（模拟被硬杀）→ 确认哨兵在场、锚点已破坏 → `_heal()` → md5 逐字节一致。

### 7.1 过程中抓到的两处「我自己的判据缺陷」（本片最有价值的产出）

- **假绿灯机**：探针第一版只判 `returncode != 0`，且把第 2 条的期望 nodeid 写成
  `test_hybrid_extension_without_ack_is_marked_dead`。结果**注入后仍然绿**（`1 passed in 1.29s`）——
  无 ack 场景下腿本来就该判死，摘掉子句它照样通过。实测出的**真实分工**是：
  - `without_ack_is_marked_dead` 承重的是「**判死 + 如实标注未响应**」（对应注入 #1）；
  - `not self._extension_armed()` 的承重者**只有** `ack_keeps_leg_alive`（不对它判断，已 ack 的腿
    也会在 3 s 宽限期到点被判死 → 变成 `unavailable` 而不是 `timeout`）。
  → 期望 nodeid 按实测重设，并**新增注入 #1** 覆盖前者的真实承重点。
- **判据不可靠（挂住 ≠ 红）**：`test_capture_late_result_from_replaced_session_is_ignored` 被注入 #6
  破坏后不是干净变红，而是**永久阻塞**——迟到结果一路走到 `_confirm_element_save` 弹**真模态对话框**。
  修法：用例里 `monkeypatch.setattr(type(window), "_confirm_element_save", lambda self, result: None)`，
  破坏才表现为干净的断言失败。（同类教训：凡「删掉守卫后代码会继续往下走」的注入，都要先确认
  下游没有副作用面——对话框、真实 I/O、全局输入。）

### 7.2 踩坑：硬杀探针导致源码停留在注入态

第一版探针卡在 #6 达 5 分钟（就是上面那个对话框阻塞）。我 `Stop-Process` 硬杀后，`finally` 里的
`path.write_bytes(original)` **没跑到**，`app.py` 停留在注入态（迟到结果守卫整块缺失）。已按探针里
记录的原文恢复并 `ast.parse` + `grep` 复核；探针据此加了三道安全网：**起手锚点自检**（锚点不唯一即
中止）、**备份到系统临时目录**、**atexit 还原**。

### 7.4 收尾重跑抓到的两处「注入本身」的缺陷（第二轮）

把注入表从 14 扩到 15（补 `refresh_history` 那条入口）后重跑，**没全绿**——两处问题都在注入侧，
不在实现侧。两条都已加进探针的前置检查。

- **判据只看了终态，被兜底掩盖（#12 假绿灯）**：摘掉 `refresh_flows()` 入口的 `_abort_run_scan()`
  后用例照样绿。根因：`refresh_flows()` 结尾**无条件**调 `refresh_history(runs)`，而 `refresh_history`
  自己也有 abort——于是「函数返回时扫描已停」这个终态被别处兜住了。这跟 M38 S1.2 那条
  「收尾还会兜底做一次，所以必须有中间态断言」是同一个坑，只是这次由**调用链**而不是 `_on_finished` 兜底。
  → 判据改为**中间态**：用 spy 包住 `list_runs`，记下「这一次同步读**开始时**旧分片是否已经死了」；
  取代必须发生在新的一次同步读**之前**，`assert reads[mark:]` 全为真。改后 #12 精确变红。
- **锚点从一行中间切一刀 → 注入后语法不合法（#15 `SYNTAX-FAIL`）**：`old` 只取到
  `self._runs = list_runs`，哨兵拼在替换串后面，于是原行剩余部分
  （`(self._artifacts_root(), limit=0) if runs is None else runs`）被顶到下一行、缩进全乱。
  探针报了 `[SYNTAX-FAIL]`，但文案写着「验证没打到判据」，离现场很远。
  → 锚点整行/整块地取（`new` 自带换行）；并加前置检查 `_anchor_ends_at_line_end`——锚点末端必须落在
  行尾（纯删除型 `new == ""` 与整行型天然满足，只有「中间切一刀」会踩）。第一次写检查时误把
  「`new` 不以换行结尾」当判据，把 7 处合法的删除型/行尾型注入一起判失败，当场改成边界判据。


## 8. 落地清单

| 文件 | 改动 |
|---|---|
| `src/rpa_core/capture/extension.py` | 记 `capture_armed` ack（`_armed` Event + `armed` 属性），`_read_loop` 先分类型再分派 |
| `src/rpa_core/capture/hybrid.py` | `ARM_ACK_TIMEOUT_SECONDS`/`DEGRADED_TIMEOUT_SECONDS`/`_ARM_ACK_ERROR`；`pick()` ⓪ 段判死 + 一次性窄预算；`extension_unresponsive` |
| `src/rpa_core/gui/capture_float.py` | **新建**：捕获浮窗（两腿分列状态 + 倒计时 + 取消按钮） |
| `src/rpa_core/gui/app.py` | 浮窗接线 / 二次点击＝取消 / `work()` 保底 emit + 桥会话一对一 / 迟到结果丢弃 / `capture_web_status`·`capture_desktop_status` / `defer_refresh` |
| `src/rpa_core/gui/home.py` | `defer_refresh` 开关 / `list_runs` 结果复用 / `setUpdatesEnabled(False)` |
| `tests/contract/test_gui_capture.py` | +5 改写 1（含对话框桩） |
| `tests/contract/test_capture_hybrid.py` | +2（`isolated_endpoints` fixture 隔离真实端点前缀） |
| `tests/contract/test_gui_home.py` | +2（`list_runs` 计数桩） |
| `.harness/spike/probe_gui_startup_real.py` 等 3 个探针 | 启动/切页分段实测（真机，非 offscreen） |
| `.harness/spike/probe_m40_negative.py` | 15 处注入的负向验证探针（哨兵式自愈） |

第二轮新增/改动：

| 文件 | 改动 |
|---|---|
| `src/rpa_core/run_history.py` | `iter_run_summaries()` / `sort_runs()` 抽出，`list_runs` 改为同源组合 |
| `src/rpa_core/gui/home.py` | `start_refresh()` 分片刷新 ＋ `_arm_scan_timer` / `_step_run_scan` / `_abort_run_scan` / `_collect_flows` / `_status_text`；`refresh_flows()` 入口取代进行中的分片；待读期间显示 `…`；`closeEvent` 停表 |
| `src/rpa_core/gui/app.py` | `MainWindow.showEvent` 首显预热元素库 dock（`_prewarm_elements_dock` ＋ `_elements_prewarmed`）；`run_gui` 工作台分支改调 `start_refresh` |
| `tests/contract/test_gui_home.py` | +4（分片结果一致 / 待读占位 / 同步取代分片 / 启动路径接线） |
| `tests/contract/test_gui_panels.py` | +1（预热建好即隐藏、不改变开关语义） |
| `tests/unit/test_run_history.py` | +1（分片与同步同源、逐字段一致） |
| `.harness/spike/probe_m40_first_click.py` | **新建**：三臂 A/B 首点延迟探针（`none`/`block`/`slice`） |

## 9. FULL GATE

第二轮收尾复跑（2026-09-28，改动 `home.py::refresh_history` + 判据强化之后）：**`check_all.py` exit 0 /
末行 `FULL GATE PASSED`**。

| 检查 | 结果 |
|---|---|
| pytest | `1174 passed, 21 skipped, 2 xfailed in 240.29s` |
| ruff | `All checks passed!` |
| architecture | `ARCHITECTURE CHECK PASSED (65 python files, 86 manifests)` |
| tasks | `TASK CHECK PASSED (60 features, 1 active task)` |
| param consumption | `PARAM CONSUMPTION CHECK PASSED (77 checked / 3 exempt / 0 skipped = 80 条命令)` |
| error contract | `ERROR CONTRACT CHECK PASSED (77 checked / 3 exempt / 0 skipped = 80 条命令)` |
| command matrix | `COMMAND MATRIX CHECK PASSED（86 条命令；死参数台账 0 项，实现缺口 0 条，l2 块 109 个）` |

> 第一轮的实测数字见 `.harness/PROGRESS.md` 的 M40 条目（历史条目刻意保留原值）。

## 10. 第二轮（2026-09-28 追加报障）：第一次点击明显 / 第一次开元素库卡

维护者原话：

```text
1，内部标签切换，第一次点击的时候很明显。以及点击元素库时，第一次也会卡一下，关闭元素库重开
就好多了。不过重新运行 uv run rpa-core gui，再测试就不卡了，不知道是否跟你的修复有关
```

### 10.1 归因：第一轮把阻塞**搬了位置**，没有消除

三臂 A/B（`.harness/spike/probe_m40_first_click.py`；每臂**各自一个进程**，否则第二臂会吃到
第一臂刚读热的文件缓存，比较就偏了）。判据用**用户体感口径**：一个本该在 `show()+T ms` 被受理的
输入，实际什么时候才轮到；`none` 臂什么都不做，给出「窗口首帧」的地板价。

| 臂 | 50 ms 该触发的输入 | 第一下**点页签**被推迟 | 运行历史填好 |
|---|---|---|---|
| `none`（地板） | 80.0 ms（+30.0） | −1.4 ms | 6.3 ms |
| `block`（旧：整段 `refresh_flows`） | 285.8 ms（**+235.8**） | **+202.3 ms** | 264.4 ms |
| `slice`（新：分片 `start_refresh`） | 100.2 ms（+50.2） | **+8.9 ms** | 301.7 ms |

第一轮的修复（`defer_refresh` ＋ `show()` 后 `singleShot`）把扫描从「窗口出现**之前**」挪到了
「窗口刚出现**之后**」：原来用户点不到（窗口还没出现，感觉是「启动慢」），现在窗口先出现、紧接着
那一轮被占住，**阻塞正好压在用户第一下手的位置**。同一个阻塞，换了位置。

四条候选机制在同一份数据里逐条排除：

| 候选 | 实测 | 结论 |
|---|---|---|
| 惰性 import（`element_panel` / `capture` 边际成本） | 2.6 ms / 4.4 ms | 否 |
| 页签首次布局＋绘制 | 0.1–3.0 ms（安静事件队列下） | 否 |
| 冷磁盘读 | 进程内 `list_runs` 冷/热只差 1 ms（93.5 / 92.2） | 否 |
| `show()` 之后的阻塞段 | 第一下点击被推迟 202 ms | **是** |

### 10.2 「重新运行就不卡了」＝ OS 文件缓存（与第一轮修复无关）

`list_runs` 在本机 157 条运行 / 464 个文件下：

```text
冷（进程内首读）: 400.2 ms | 文本读 314 次 / open 自耗时 274.9 ms
热（进程内复用）: 128.2 ms | 文本读 314 次 / open 自耗时  52.1 ms
```

同一进程内的冷→热对（400.2 → 128.2）说明差异来自**文件读取**而非解析。第一轮修的是
「阻塞放在哪一轮」，**没有减少读取量**，所以它与「重启就不卡」无关；后者是第二次启动时
文件已在 OS 缓存里。（未取得管理员权限清空系统文件缓存，故冷盘数字只作为**量级**证据，不做
稳定复现承诺。）

### 10.3 修法

1. `run_history.py`：把 `list_runs` 拆成**同源**两半——`iter_run_summaries()`（逐条产出、不排序）
   ＋ `sort_runs()`；`list_runs = sort_runs(list(iter_run_summaries(...)))`。同源而不是各写一遍，
   否则界面与 CLI 的运行历史顺序会慢慢漂开；契约用例钉住两边**逐字段一致**。
2. `home.py::start_refresh()`：先**立刻**用流程库填表（本地读取，很便宜），再用零延时 `QTimer`
   链分片扫运行历史，每轮预算 `RUN_SCAN_SLICE_SECONDS = 6 ms`（**每轮至少推进一条**，保证有界
   推进，预算注入为 0 时退化成「每轮一条」——门禁据此钉住分片语义）。
3. 扫描未完成期间运行列显示 `…`，提示「正在读取运行历史…」：**「还没读到」与「确实没有运行
   记录」是两件事**；分片把这段时间拉长了（本机约 0.3 s），沿用旧文案就是把未知当已知。
4. **两条同步入口都要取代进行中的分片**：`home.py::refresh_flows()`（流程库「刷新」）与
   `home.py::refresh_history()`（历史页签「刷新」）入口都先 `_abort_run_scan()`。否则旧分片
   收尾时用**更早**的一份结果覆盖刚刷出来的新结果（「点了刷新，数字变回旧的」——比慢更坏）。
   两条入口是同一类静默覆盖，只改一条等于留一半；契约用例把两条**循环**跑（注入 #12/#15
   分别打这两条）。
5. `app.py::run_gui` 的工作台分支改调 `window.start_refresh`。
6. 编辑器 `MainWindow.showEvent` 首次显示时空闲预热元素库 dock（`_prewarm_elements_dock`），
   把首开开销从「用户第一次点元素库」挪到「编辑器刚打开」。实测 **首开 65.2 ms / 二次开
   20.3 ms**（§1.3 那次量的 26.4/0.5 ms 是因为当时没打开流程、元素库读的是空集）。
   挂 `showEvent` 而不是 `open_editor_window()`：工作台打开与 `--workflow` 直开两条入口都覆盖，
   也不用给 `open_editor_window` 的测试替身加方法。

### 10.4 验收结果（第二轮）

| 口径 | 结果 |
|---|---|
| 三臂 A/B | 第一下点页签被推迟 **202.3 ms → 8.9 ms**（`none` 地板 −1.4 ms）；运行历史填好 264.4 → 301.7 ms（分片的代价：整体晚 ~37 ms 填满，换来输入不被推迟） |
| 契约用例 | `test_gui_home.py` 13（+4：分片结果一致/待读占位/同步取代分片/接线）、`test_gui_panels.py` 末条 +1（预热建好即隐藏）、`tests/unit/test_run_history.py` +1（分片与同步同源） |
| 负向验证 | 15 处注入（第二轮新增 7 处，见 §7）全部「对照绿 → 注入精确红 → 逐字节还原」；过程中两处**注入侧**缺陷（#12 假绿灯 / #15 锚点切断行）已修，见 §7.4 |
| FULL GATE | `check_all.py` **exit 0 / `FULL GATE PASSED`**；`1174 passed / 21 skipped / 2 xfailed`（见 §9） |

### 10.5 残留（登记，不修）

- 页签**首帧本身**约 22.6 ms（`none` 臂的「切换本身」）：157 行 `QTableWidget` 的首次布局＋绘制，
  不是本次报障的承重项（分片前它被 202 ms 的阻塞盖住了），未做手术。
- 元素库**每次**打开仍有约 20 ms（dock 已缓存也要 relayout ＋ 重读元素列表）。
- 探针事故与加固：见 §7.3。

