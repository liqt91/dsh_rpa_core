# M18 运行控制（编辑器 run/cancel/看状态）

状态：`done`

## 背景与决策

编辑器缺运行入口。ADR 0011 放行 devserver 代理型运行控制：devserver spawn 子进程跑 `rpa-core run`，只持有子进程句柄 + run_id（cancel=终止子进程，status=读证据文件），进程内仍无 orchestrator/run 状态（隔离不破）。

## 任务

- [x] 后端：`RunManager` 子进程托管（spawn `rpa-core run`，持有句柄）+ `/api/runs` 端点（start/status/events/cancel）
- [x] 前端：运行按钮 + 状态/事件流展示
- [x] 合同测试（端点 + 子进程托管）+ E2E
- [x] 完整门禁

## 验收标准

- [x] 编辑器可运行 workflow 并看到状态与事件流
- [x] cancel 终止子进程，run 落 cancelled
- [x] devserver 隔离不破（架构检查断言）
- [x] 完整门禁通过（190 tests）

## 完成证据

- ADR 0011：devserver 代理型运行控制（子进程 run host，进程内仍无 orchestrator/run 状态）
- `devserver/runs.py`：RunManager（spawn `rpa-core run` 子进程，stdout 解析真实 run_id → `run_artifacts/<uuid>/` 读证据；cancel=终止子进程）
- `/api/runs` 端点：POST 启动 / GET {runId} 状态 / GET {runId}/events / POST {runId}/cancel；CLI `run` 新增 `--inputs`
- 前端：▶ 运行 / ■ 取消 / 事件流面板（轮询状态）
- 合同 5 项 + E2E 1 项；full gate 190 tests（架构检查确认 devserver 不 import runtime/executors——隔离不破）
