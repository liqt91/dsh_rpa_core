# M24 断点与单步调试（Debugger）

状态：`active`

关联：ADR 0005（暂停/继续语义，M21 增补跨进程控制通道）、ADR 0004（检查点与恢复）、ADR 0011（子进程 run host）、M21（已完成：跨进程暂停 + 浏览器会话续接）、`docs/gui-run-control.md`
前置：M21 的 `control_channel.py` + `RunHandle.resume()` + 浏览器会话跨进程续接

## 背景与现状

M21 已实现「暂停即收口」的跨进程暂停/继续：控制文件 `control.json` 下发暂停请求 → orchestrator 在**节点边界**停下 → run 进程退出、`paused` 落盘 → `resume` 从检查点起新进程续跑（浏览器会话跨进程续接已就绪）。

缺口（对标影刀调试能力）：

1. **断点**：无法指定「跑到某个节点前停下」——用户只能手动暂停，命中位置不可预期；
2. **单步**：继续后一路跑到底，无法「执行一个节点再停」；
3. 因此失败/数据不对时，只能整段重跑或加「打印日志」猜测，调试成本高。

本任务在既有暂停通道上做**最小扩展**，不引入进程内挂起（保持「暂停即收口」契约不变）。

## 关键设计（先行定案）

- **断点命中 = 一种暂停原因**：复用 `RunStatus.PAUSED` 与检查点语义，新增 `pauseReason`
  （`user` | `breakpoint` | `step`）供 UI 区分展示；不改终态集合。
- **命中时机**：节点**执行前**判定（`node_id ∈ breakpoints` → 不执行、直接收口），
  与暂停请求同一判定点（节点边界），实现上共用一处 `_should_stop()`。
- **断点集传递**：run 启动时随命令行/子进程参数下发（CLI `--breakpoints a,b`；
  `RunManager` 透传）。运行中动态增删断点**列为后续**（需要控制文件热更新语义）。
- **resume 不得原地死循环**：命中断点暂停后，resume 必须跳过「刚命中的那个断点」——
  检查点记录 `consumedBreakpoints`（随 checkpoint 持久化），resume 时把
  `pausedAtNode` 并入已消费集合，避免同一节点反复暂停。
- **单步**：`resume(step=True)` = 执行**一个**节点后在下一个边界再次暂停
  （`pauseReason: step`）；GUI「继续」与「单步」两个入口。
- **范围外**：条件断点、日志断点、变量监视/求值、调用栈、跨 run 的断点持久化。

## 任务（切片）

- [x] **S1 断点契约（runtime + CLI）**（2026-09-20 done）
  - `control_channel.py`：控制文件增 `breakpoints`（节点 id 列表）与 `step` 语义；
    `request_pause` 保持向后兼容。
  - `runtime/orchestrator.py`：节点边界判定「暂停请求 / 命中断点 / 单步」三合一；
    收口时记录 `pausedAtNode` 与 `pauseReason` 到检查点；`RunHandle.resume(step=False)`
    把 `pausedAtNode` 并入 `consumedBreakpoints`。
  - `checkpoint`（`runtime/checkpoint.py`）：新增 `consumed_breakpoints` 字段
    （版本化，旧检查点缺省为空）。
  - `cli.py`：`run` 增 `--breakpoints`、`resume` 增 `--step`；输出/结果里带 `pauseReason`。
  - 验收：给定流程与断点，run 在断点节点**执行前**停下（该节点未执行、已完成集合不含它）；
    resume 后越过该断点继续；`--step` 每次只前进一个节点。
- [ ] **S2 GUI 断点交互**
  - 画布编号栏断点标记（红点）+ 切换入口（卡片右键菜单 / 快捷键）；`FlowTreeView`
    只负责绘制与命中，状态由 app 持有（`set[int]` 节点 id）。
  - 运行前把断点集交给 `RunManager`；运行面板/浮窗显示「命中断点：<节点标题>」，
    并复用 G4 的「跳转到失败节点」定位能力跳到命中节点。
  - 验收：GUI 可设/清断点、run 命中断点暂停并高亮该节点、继续/单步按钮行为正确。
- [ ] **S3 单步与暂停原因展示**
  - 运行面板显示 `pauseReason`（用户暂停 / 断点命中 / 单步）；浮窗按钮按状态启用
    （暂停中显示「继续 / 单步」）。
  - 验收：单步在浏览器流程上亦正确（会话跨进程续接不丢）。
- [ ] **S4 测试与文档**
  - 单测：控制通道断点解析、orchestrator 命中判定与 `consumedBreakpoints` 防死循环、
    `--step` 单步边界；契约：`RunManager` 透传、GUI 断点设置与展示。
  - 文档：ADR 0005 增补（断点/单步语义与边界）或新 ADR；`docs/gui-run-control.md`
    扩章；CLI `--help` 示例。
  - 验收：full gate 通过；PROGRESS 追加记录。

## 验收

- 断点、单步、用户暂停三种「停」都可预期、可继续、不丢已完成节点、不重复执行节点。
- 浏览器流程暂停/继续/单步的会话续接不回退（M21 既有行为）。
- 每切片：GUI 合同测试（offscreen）+ `check_all.py` 全门禁通过；PROGRESS 追加一行。

## 风险 / 注意

- **死循环风险**：断点与 resume 的交互必须靠 `consumedBreakpoints` 显式消费，不能只靠
  「暂停请求已清除」（断点是声明式的，会持续命中）。
- **与暂停请求的优先级**：同一节点同时有暂停请求与断点时，`pauseReason` 取「用户暂停」
  （用户意图优先），但两者都收口，行为一致。
- **检查点兼容**：新增字段必须可选，旧检查点（M21/M23 期间产生）仍可 resume。
- 运行中动态改断点（热更新）需要控制文件版本化与合并语义，本期不做，避免与「收口式暂停」
  的原子性冲突。
