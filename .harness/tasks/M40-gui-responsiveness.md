# M40 GUI 响应性：启动阻塞 / 切页无响应 / 捕获静默等待

状态：`done`

由来：维护者 2026-09-28 报障三句——「冷启动很慢，启动后，内部切换标签也很慢，捕获元素第一次
点击没有红框出现，但再次点击提示已有捕获任务进行中」。本任务单把三句拆成**可归因的三条**，
每一条都有一手实测或现场日志支撑，不做推测性归因。

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

## 5. 待维护者澄清

- 「内部切换标签」若指的不是工作台三个页签（实测 5–15 ms），请指认具体界面（编辑器内？dock？
  浏览器标签？）——本片按「启动期主线程阻塞」处理，未擅自改 QTabWidget。

## 6. 验收结果（一手实测）

| 口径 | 结果 |
|---|---|
| 三份契约测试 | `test_gui_capture.py` 16 + `test_capture_hybrid.py`/`test_capture_extension.py` 21 + `test_gui_home.py` 9 = **46 项全绿**（本轮合跑 37 项 19.6 s / exit 0） |
| 大范围回归 | `tests/contract -k "gui or editor"` **435 passed / 531 deselected / 38.92 s** |
| 静态门禁 | `check_architecture`(65 py / 86 manifest)、`check_param_consumption`(77 checked/3 exempt/0 skipped)、`check_error_contract`、`check_command_matrix`(86 条命令)、`check_tasks` 全 PASSED |
| 负向验证 | **8 处注入全部「对照绿 → 注入精确红 → 逐字节还原」**（见 §7） |
| FULL GATE | `check_all.py` 见 §9 |

## 7. 负向验证（8 处，探针 `.harness/spike/probe_m40_negative.py`）

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
| `.harness/spike/probe_m40_negative.py` | 8 处注入的负向验证探针 |

## 9. FULL GATE

见 `.harness/PROGRESS.md` 的 M40 条目（本轮实测数字以该条为准）。

