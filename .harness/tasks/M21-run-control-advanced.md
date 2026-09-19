# M21 GUI 运行控制进阶（暂停/继续 + 恢复人工确认）

状态：`done`

关联：ADR 0005（暂停契约）、ADR 0004（检查点与恢复）、ADR 0011（运行控制 proxy：GUI/devserver 进程内不跑 runtime）、ADR 0016（GUI 为主力形态）
前置：M18（运行控制：`RunManager` 子进程宿主 + 运行/取消/事件流/悬浮窗）

## 背景与缺口（现状核实 2026-09-17）

- `runtime/orchestrator.py` 已有 `RunHandle.pause()` / `pause_and_wait()`，暂停在**节点边界**
  生效（ADR 0005），终态为持久化的 `paused`。
- 但暂停信号是**进程内** `asyncio.Event`：`rpa-core run` 子进程没有任何外部触发方式。
- `RunManager`（`devserver/runs.py`）只有 `start/cancel/status/events/close`，**无 pause/resume**；
  GUI 只有「运行 / 取消运行」。
- `rpa-core resume --run-id [--allow-indeterminate]` 是**另一个进程**从 checkpoint 恢复
  （用于崩溃/`recovery_required`/`indeterminate`），与「同一进程内暂停后继续」是两件事。
- 结论：GUI 侧暂停需要**跨进程控制通道**；恢复需要 GUI 前端化 M3/M4 的人工确认门。

## 术语区分（避免实现串味）

> 实施时修正（见文末「实施结果」）：暂停是**收口式**（ADR 0005：run task 结束、进程退出、
> `paused` 落盘），因此「继续」与「恢复」走的是同一条 `resume` 通路，区别只在**有没有人工门槛**。

| 场景 | 语义 | 机制 |
|---|---|---|
| **暂停** | 节点边界停住 → 收口落盘 `paused` → **run 进程退出** | 进程内 `pause` Event + **跨进程控制通道**（控制文件） |
| **继续** | 从 `paused` 的检查点起**新进程**续跑（不重跑已完成节点） | 同 `resume` 通路，**无人工门槛** |
| **恢复（resume）** | 从已收口 run 的检查点起新进程；`indeterminate` / `recovery_required` 需显式确认 | `rpa-core resume --run-id [--allow-indeterminate]` + GUI 确认框 |

## 任务（切片）

- [x] **S1 跨进程控制通道（暂停/继续）**
  - 设计：run 子进程在节点边界轮询一个**控制文件**（`run_artifacts/<run_id>/control.json`，
    内容 `{"pause": true|false, "requestedAt": ...}`）或本地端点；GUI/RunManager 写该文件。
    - 约束：不得让 GUI 进程承载 runtime（ADR 0011）；不得引入 web 端口（ADR 0016）。
    - 文件方案优先（零新依赖、跨平台、崩溃后可查证）；命名管道方案作为备选记录。
  - CLI：`rpa-core run` 挂控制通道；新增 `rpa-core pause --run-id` / `rpa-core continue --run-id`
    （或 `run --control-file`）以便 CLI/GUI 同权（ADR 0006 §6 通道对齐口径）。
  - 事件：`pauseRequested` / `runPaused` / `runResumed` 已在 orchestrator 产出，确认 GUI 消费。
  - 测试：合同——暂停请求在下一节点边界生效、继续后不重复执行已完成节点、取消优先于暂停
    （ADR 0005 既有优先级：取消 > 暂停 > 超时）。
- [x] **S2 RunManager + GUI 接线**
  - `RunManager.pause(run_id)` / `continue_run(run_id)`（写控制文件 + 状态回读）。
  - GUI：工具栏与悬浮窗增「暂停 / 继续」；运行面板显示「已暂停（等待继续）」；按钮态随状态切换。
  - 终态 `paused` 的 run 在流程库/事件流里可辨识，且可继续或显式放弃。
- [x] **S3 恢复人工确认（indeterminate / recovery_required）**
  - `RunManager.resume(run_id, allow_indeterminate=False)` → spawn `rpa-core resume ...`。
  - GUI：run 终态为 `recovery_required` / `indeterminate` 时弹确认对话（说明影响：可能重复执行
    未确认的副作用节点），用户显式确认后带 `--allow-indeterminate` 恢复；取消则不动作。
  - 与 S1 的「继续」在 UI 上区分文案，避免误操作。
- [x] **S4 测试与文档**
  - 合同：恢复对话仅在对应终态出现；`allow_indeterminate` 必须来自显式确认（默认拒绝）。
  - 文档：ADR 0005/0011 增补（控制通道选型与边界）；`docs/api-usage.md` 补 CLI 用法；
    GUI 手册段（`docs/editor-design.md` 或新 GUI 文档）补暂停/继续/恢复语义。

## 实施结果（2026-09-19）

### 计划修正：暂停语义照 ADR 0005，不照本文件的术语表

本文件原「术语区分」表把暂停/继续写成「同一 run 进程存活，节点边界停住，随后继续」，
与 ADR 0005 的既定契约（**暂停即收口**：run task 结束、进程退出、`paused` 落盘）冲突，
也与本文件 S3 自己的「spawn `rpa-core resume` 新进程」写法不自洽。

实施按 ADR 0005：**收口式**。「继续」= 从检查点起新进程（`resume`），不做进程内挂起、
不引入资源租约/心跳。计划里那条术语表描述已修正。

**新增的必备配套：浏览器会话跨进程续接**（计划里没有，但不做则「继续」在浏览器流程上必失败）。
核实证据（2026-09-19 实测）：

- `checkpoint.json` 已持久化 `sessionId` + `tabId` + `browserInstance`（`_open_extension` 的
  outputs 与 effect details 都写）；
- `PlaywrightExecutor.close()` 只解绑、**不关用户浏览器里的标签页** ⇒ 标签页物理跨进程存活；
- 但 `sessionId → tabId` 映射是进程内字典 ⇒ 实测新 executor 用旧 `sessionId` 直接报
  「缺少有效会话」。ADR 0013（移除 playwright、统一扩展单通道）之后，ADR 0004 那句
  「浏览器 session 不跨进程存活」已经过时，已就地修订。

### 交付

- `src/rpa_core/control_channel.py`（新，顶层零依赖）：`control_path` / `request_pause` /
  `request_continue` / `read_control` / `pause_requested` / `reset_control` / `watch_control_file`。
  **放顶层而非 `runtime/`**：三方共用（runtime 轮询、CLI pause、devserver 写），
  而 `check_devserver_isolation` 禁止 devserver import `rpa_core.runtime`。
- `runtime/orchestrator.py`：`RunHandle.resume()`（清未生效的暂停请求）；`_resume` 里调
  `executors.restore_from_scopes(scopes)`。
- `executors/registry.py`：`restore_from_scopes(scopes)` 恢复钩子（尽力而为、异常静默）。
- `executors/browser.py`：`session_bindings_from_scopes()`（纯函数，从快照提取
  `sessionId→tabId`/实例，`detach` 的会话不复活）+ `PlaywrightExecutor.restore_from_scopes()`。
- `cli.py`：`run`/`resume` 挂 `_await_with_control`（run 结束即取消 watcher）；
  `resume` 分支先 `reset_control`；新增 `pause` 子命令（`--run-id` / `--artifacts`）。
- `devserver/runs.py`：`_spawn` 抽出；entry 增 `workflow` 与 `real_run_id_ready`（Event）；
  `pause` / `continue_run` / `resume(allow_indeterminate=)`；`RunControlError`。
- `gui/app.py`：工具栏与运行菜单增「暂停 / 继续」；`_pause_run` / `_continue_run` /
  `_adopt_run_handle` / `_confirm_resume` / `_resume_confirmation`；`_poll_run` 区分
  `paused` 与两种需确认终态；事件流加 `pauseRequested`/`runPaused`/`runResumed` 可读格式化。
- `gui/run_float.py`：暂停/继续按钮、`show_pausing`、`_pause_pending`（防止一秒数次的状态
  刷新冲掉「已请求暂停」提示）、`_RESUMABLE_STATES`。

### 测试

- `tests/contract/test_run_control_channel.py`（新，7 项）：控制文件原语、原子写无残留、
  失效安全、watcher 镜像语义（含不重复调用）、**真子进程**「外部写 control.json → 节点边界停下」、
  「resume 忽略残留请求且不重跑已完成节点」。
- `tests/unit/test_session_restore.py`（新，9 项）：快照提取（navigate/attach/detach/垃圾输入）、
  真实 checkpoint 形态、恢复前失败 vs 恢复后可用（含省略 sessionId 的回退路由）、
  注册表钩子尽力而为。
- `tests/contract/test_gui_run_pause.py`（新，8 项）：真子进程「暂停 → paused → 继续 → succeeded」、
  请求未落地时的按钮态、句柄分派（运行中撤销 / 已收口 resume）、
  **`indeterminate` 不确认绝不恢复**、`recovery_required` 确认但不需要 flag、浮窗按钮态。
- `tests/contract/test_public_api.py`：API 面加 `RunHandle.resume`。

### 真机验收（macOS + Edge 153 + 扩展 0.3.1）

`browser.navigate(baidu)` → `workflow.sleep(2)` → 暂停 → `resume` → `browser.navigate(reload)`
→ `browser.getText(#kw)`：

- 暂停：`status=paused`、`completedSteps=['root/nap','root/open']`（进行中的 sleep 跑完才停）；
- resume：`status=succeeded`，`sessionId=9c1bc13e-…` **未变**，`reload` 与 `getText` 都作用在
  暂停前那个 `tabId=285306515` 上（`matchedCount=1`）；
- `stepStarted` 序列 = `open, nap, afterResume, readKw` —— 已完成节点**零重跑**。

### 后续项（未做，记在这里免得丢）

- **Web 编辑器未前端化**：`devserver` 的 HTTP 端点与 `static/app.js` 未加暂停/继续
  （ADR 0006 §6 的通道对齐要求）。本期范围只到 PySide6 GUI。
- **桌面会话续接**：`desktop.uia` 仍不跨进程（ADR 0004 既有约定）；若将来要做，需在
  Windows 上按进程句柄重新 attach。

## 验收

- GUI 里可对运行中的流程暂停（节点边界生效、状态可见）与继续（不重复已完成节点）。
- 崩溃/不确定终态的 run 能在 GUI 里经**显式确认**恢复；默认不自动恢复。
- 取消仍优先于暂停；暂停的 run 不占用 CPU 空转。
- 全门禁通过；PROGRESS 追加记录。

## 风险 / 开放问题（实施后定案）

规划期三条疑问，实施时均已定案——不再是开放问题，记录结论以免重复讨论：

- **控制文件轮询粒度**：定案「节点边界检查，无需高频」。watcher 默认间隔按节点粒度设
  （`watch_control_file(interval=…)`），轮询只读一个极小的 JSON，开销可忽略；
  不需要 inotify/FS 事件（跨平台且不值当）。原子写**必须**（临时文件 + `os.replace`），
  已实现并有回归（「原子写无残留」）；读取对半写/损坏**失效安全**——解析失败按「无请求」
  处理，绝不因此暂停或崩溃（有单测覆盖）。
- **`paused` 的进程存活期与超时**：随「收口式」定案自动消解——暂停时 run task 结束、
  子进程退出、`paused` 落盘，**不存在「暂停期间挂着的进程」**，故无需存活期上限、
  也无需暂停期间的资源租约/心跳。deadline 在 resume 时按全新 `workflow.timeout_seconds`
  重新计费（沿用 ADR 0005 既有约定）。
- **GUI 关闭时 `paused` run 的归属**：同样是收口式的自然结果——引擎进程早已退出，
  checkpoint 留在 `run_artifacts/<run_id>/`，任何一方（GUI 重启后、或 CLI
  `rpa-core resume --run-id`）都能接管，不依赖 GUI 存活。GUI 里表现为「继续」按钮
  对 `paused` 终态可用（`_RESUMABLE_STATES`）。

## 遗留（本期范围外，已单列）

- **Web 编辑器未对齐**：`devserver` 的 HTTP 端点与 `static/app.js` 未加暂停/继续，
  与 ADR 0006 §6 的通道对齐口径有差。范围只到 PySide6 GUI（ADR 0016 已定 GUI 为唯一
  主力形态、Web 冻结演进），故按现状记录，不视为欠账。
- **桌面会话不跨进程**：`desktop.uia` 仍不跨进程续接（ADR 0004 既有约定，修订后的
  浏览器特例不适用于桌面）。若将来要做，需在 Windows 上按进程句柄重新 attach。
