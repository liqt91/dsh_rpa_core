# M8 设计期服务与编辑器架构决策

状态：`active`

## 目标

拖拽编辑器 + 元素捕获（影刀式工作流）已确认为下一阶段方向。本里程碑先立地基：ADR 0007 定义**设计期 dev server**（与运行时分离的新顶层包）与捕获传输契约；随后实现最小 dev server（catalog / compile / workflow 文件读写 / 捕获端点骨架），让编辑器（M9）与双捕获（M10）有稳定 API 可用。

前置事实（M7 S0 实验结论 + 后续调研，详见 `docs/capture-transport.md`）：

- Chrome 152 默认用户目录的 CDP 调试端口被上游安全策略封锁（136+），显式 `--user-data-dir` 指向默认目录同样无效。
- 新发现：`chrome://inspect/#remote-debugging` 用户授权开关开启后 9222 监听，但 `/json/*` 发现端点 404（防扫描设计），需显式 WebSocket URL——S1 验证中，若走通则为登录态页面捕获的最优载体。
- 浏览器捕获 live 复用登录态 → 传输优先级：chrome://inspect 授权开关（S1 验证）→ Panerelay → Playwright MCP 官方扩展 evaluate 链路 → 自研扩展兜底；非扩展主路线 = 专用持久 profile（与 Playwright MCP 默认方案同构）。
- 桌面捕获走 UIA hit-test，不涉及上述限制。

## 任务

- [ ] 编写 ADR 0007：dev server 定位（设计期工具，不承载 run）、新顶层包 `rpa_core.devserver` 与架构边界（只读复用 catalog/编译器，绝不共享运行进程）、workflow 文件目录约定（受限根目录）、浏览器捕获传输契约（按 `docs/capture-transport.md` 决策矩阵与 S1 结论）、桌面捕获契约
- [ ] 执行 S1 验证（`docs/capture-transport.md` §4 协议）：chrome://inspect 授权开关的 WebSocket 连接、登录态断言、picker 注入回验、开关持久性
- [ ] 实现 dev server 骨架（stdlib `http.server`，零新依赖）：`GET /api/catalog`（manifest 元数据：id/version/kind/effect/input_schema/output_schema/errors）、`POST /api/compile`（dry-run：`{valid, errors[]}`）、`GET /api/workflows`、`GET/PUT /api/workflows/{name}`（受限目录内读写）
- [ ] 捕获端点骨架：`POST /api/capture/desktop/start|pick|cancel`（UIA hit-test）与 `POST /api/capture/browser/*`（含 `transport` 参数，M10 实装，本里程碑先定义契约与 501 占位）
- [ ] dev server 安全边界：目录逃逸拒绝、仅监听 127.0.0.1、请求体大小限制
- [ ] 合同测试：catalog 端点与 `load_catalog` 一致、compile 端点对合法/非法 workflow 的响应、workflow 文件读写的包含校验
- [ ] 手册：dev server 启动命令与端点速查（`docs/devserver.md`）
- [ ] 完整 harness 门禁通过（架构检查覆盖新包）

## 验收标准

- [ ] ADR 0007 已接受，捕获传输契约含 S0 实测证据
- [ ] curl 可走通 catalog → compile → workflow 存取全流程
- [ ] dev server 不 import runtime（架构检查断言），无运行时进程依赖
- [ ] 完整门禁通过

## 范围外

- 操控型端点（run/pause/resume over HTTP——仍以 ADR 0006 推迟为准，重估另立 ADR）
- 前端页面实现（M9）
- 捕获实现细节（M10，本里程碑只定契约）
- 认证 / 多用户

## 待定问题

- workflow 目录默认放 `workflows/`（仓库根）还是用户目录？（倾向仓库根 `workflows/`，git 管理）
- 捕获会话状态（桌面 hit-test 的 hook）放 dev server 进程还是独立子进程？（倾向独立子进程，避免 UIA 污染服务进程 COM 状态）

## 完成证据

仅在全部验收标准通过后填写。
