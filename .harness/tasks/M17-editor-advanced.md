# M17 编辑器进阶（M11 后续）

状态：`done`

## 背景

M11 树形画布交付后，编辑器进阶项（变量补全、全屏编辑器、运行状态高亮）一直挂 backlog。M14/M15 收尾后立项实装。

## 目标

提升编辑器的编写效率与可观测性。

## 任务

- [x] 变量补全：属性表单输入 `${` 时提示可引用路径（inputs/各节点 outputs/loop.item/error.code），下拉点选补全
- [x] 全屏编辑器：画布可放大全屏（局部专注模式），「⛶ 全屏」切换
- [x] 运行状态高亮：devserver 新增只读端点 `/api/runs/latest-events`（读 run_artifacts 最近一次 events.jsonl），画布节点按 node_id 标 run-succeeded/failed/running
- [x] 合同/E2E 测试（补全下拉 / 全屏切换 / 运行状态高亮）
- [x] 完整门禁

## 验收标准

- [x] 变量补全在属性表单生效且不误报
- [x] 全屏切换可用且状态不丢
- [x] 运行状态高亮与 events.jsonl 一致
- [x] 完整门禁通过（184 tests）

## 完成证据

- `app.js`：`computeReferencePaths()`（inputs/steps/loop/error 引用路径）+ `attachRefCompletion()`（`${` 触发下拉点选）；`toggleFullscreen()`；`loadRunStatus()`/`clearRunStatus()`
- `devserver/app.py`：`latest_run_events()`（读 `run_artifacts/<最新>/events.jsonl`）+ server.py 路由 `/api/runs/latest-events`
- `styles.css`：ref-completion / fullscreen / run-* 状态样式
- E2E +3；full gate 184 tests
