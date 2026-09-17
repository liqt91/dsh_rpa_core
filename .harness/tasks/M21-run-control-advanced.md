# M21 GUI 运行控制进阶（暂停/继续 + 恢复人工确认）

状态：`planned`
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

| 场景 | 语义 | 机制 |
|---|---|---|
| **暂停 / 继续** | 同一 run 进程存活，节点边界停住，随后继续 | 进程内 `pause` Event + **新增跨进程控制通道** |
| **恢复（resume）** | 原进程已退出（崩溃/`recovery_required`/`indeterminate`），从 checkpoint 起**新进程** | `rpa-core resume --run-id`（已存在）+ GUI 人工确认 |

## 任务（切片）

- [ ] **S1 跨进程控制通道（暂停/继续）**
  - 设计：run 子进程在节点边界轮询一个**控制文件**（`run_artifacts/<run_id>/control.json`，
    内容 `{"pause": true|false, "requestedAt": ...}`）或本地端点；GUI/RunManager 写该文件。
    - 约束：不得让 GUI 进程承载 runtime（ADR 0011）；不得引入 web 端口（ADR 0016）。
    - 文件方案优先（零新依赖、跨平台、崩溃后可查证）；命名管道方案作为备选记录。
  - CLI：`rpa-core run` 挂控制通道；新增 `rpa-core pause --run-id` / `rpa-core continue --run-id`
    （或 `run --control-file`）以便 CLI/GUI 同权（ADR 0006 §6 通道对齐口径）。
  - 事件：`pauseRequested` / `runPaused` / `runResumed` 已在 orchestrator 产出，确认 GUI 消费。
  - 测试：合同——暂停请求在下一节点边界生效、继续后不重复执行已完成节点、取消优先于暂停
    （ADR 0005 既有优先级：取消 > 暂停 > 超时）。
- [ ] **S2 RunManager + GUI 接线**
  - `RunManager.pause(run_id)` / `continue_run(run_id)`（写控制文件 + 状态回读）。
  - GUI：工具栏与悬浮窗增「暂停 / 继续」；运行面板显示「已暂停（等待继续）」；按钮态随状态切换。
  - 终态 `paused` 的 run 在流程库/事件流里可辨识，且可继续或显式放弃。
- [ ] **S3 恢复人工确认（indeterminate / recovery_required）**
  - `RunManager.resume(run_id, allow_indeterminate=False)` → spawn `rpa-core resume ...`。
  - GUI：run 终态为 `recovery_required` / `indeterminate` 时弹确认对话（说明影响：可能重复执行
    未确认的副作用节点），用户显式确认后带 `--allow-indeterminate` 恢复；取消则不动作。
  - 与 S1 的「继续」在 UI 上区分文案，避免误操作。
- [ ] **S4 测试与文档**
  - 合同：恢复对话仅在对应终态出现；`allow_indeterminate` 必须来自显式确认（默认拒绝）。
  - 文档：ADR 0005/0011 增补（控制通道选型与边界）；`docs/api-usage.md` 补 CLI 用法；
    GUI 手册段（`docs/editor-design.md` 或新 GUI 文档）补暂停/继续/恢复语义。

## 验收

- GUI 里可对运行中的流程暂停（节点边界生效、状态可见）与继续（不重复已完成节点）。
- 崩溃/不确定终态的 run 能在 GUI 里经**显式确认**恢复；默认不自动恢复。
- 取消仍优先于暂停；暂停的 run 不占用 CPU 空转。
- 全门禁通过；PROGRESS 追加记录。

## 风险 / 开放问题

- 控制文件轮询粒度（节点边界检查即可，无需高频）；文件损坏/半写需原子写（临时文件 + rename）。
- `paused` 状态下的进程存活期与超时（是否设上限、超时后如何处理）需在 S1 定案。
- GUI 关闭时处于 `paused` 的 run 的归属（子进程是否随 GUI 退出、能否由 CLI 接管）。
