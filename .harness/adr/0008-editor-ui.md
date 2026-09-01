# ADR 0008：编辑器 UI 放行与零构建形态

- 状态：已接受
- 日期：2026-09-01
- 关联：ADR 0006（UI 排除结论）、ADR 0007（dev server）、M9 任务单、规则 10

## 背景

M8 dev server 交付了 catalog / compile / workflows 三组设计期 API，编辑器（M9）成为下一个调用方。规则 10 要求 UI 在第一垂直切片通过前排除——该门槛早已通过（M1 vertical-slice）；ADR 0006 亦写明"UI 需要新的 ADR"。本 ADR 决定编辑器是否放行以及采用什么形态。

`project_state.json` 的排除清单中 "React UI" 是当时对"重型前端"的排除意向，需在此明确解释。

## 决策

### 1. 放行：设计期编辑器 UI

- 排除门槛已满足：第一垂直切片（M1）与运行时核心契约均通过验收；UI 依附的 dev server（ADR 0007）已存在且与运行时隔离。
- 放行范围仅限**设计期编辑器**，由 dev server 托管；运行期操控 UI（run/pause/resume over HTTP）仍按 ADR 0006 推迟不变。

### 2. 形态：零构建静态单页（vanilla HTML/JS/CSS）

- 无框架、无 npm、无打包器、无 CDN 外链——页面资产是仓库内静态文件，仅由 dev server 的 `GET /` 单条静态路由提供（不开放任意文件路径，维持 ADR 0007 安全边界）。
- 理由：编辑器调用面只有 5 个本地端点，vanilla JS 完全够用；零构建消除工具链与供应链依赖，与仓库"stdlib 优先、零新依赖"的取向一致；无构建产物即无缓存失效问题，刷新即最新。
- "React UI" 排除解释：排除的是 React 及同类框架/构建工具链，不是一切 UI。若未来编辑器复杂度（M11+ 树形编辑、元素库）超出 vanilla 可维护范围，重估需新 ADR。

### 3. 架构边界不变

- 编辑器页面是 dev server（设计期包）的一部分，不触碰 `runtime`/`executors`；架构检查的 devserver 隔离断言继续覆盖。
- 编辑器对 workflow 的读写一律走 HTTP API（与外部调用方同权），不读盘直连。

## 后果

- M9 可以启动，交付物为 `devserver` 托管的单页编辑器。
- 规则 10 排除清单更新：UI（设计期编辑器）放行；FastAPI、数据库、MCP、调度器、安装器维持排除。
- 零构建约束成为 M9/M11 编辑器演进的硬边界，超出时触发新 ADR。
