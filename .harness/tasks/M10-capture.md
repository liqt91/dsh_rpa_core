# M10 元素捕获：桌面 UIA hit-test + 浏览器双通道

状态：`active`

## 目标

按 ADR 0007 捕获契约实装元素捕获：桌面走 UIA hit-test（独立子进程承载捕获会话），浏览器走持久 profile（主）与 user-browser 登录态复用（次，子类型由 S1 结论定）。产物为可入册元素描述符，供编辑器属性面板引用。

## 任务

- [x] 执行 S1 验证（`docs/capture-transport.md` §4 协议，M10 前置，自 M8 移入）：2026-09-01 于用户日常 Edge 152.0.4191.53 实测通过——WS URL 从 `DevToolsActivePort` 文件读取（页面 UI 无需交互）、`/json/*` 404 防扫描确认、`connect_over_cdp` 枚举真实标签页、picker 注入回验 + 高亮、原生 CDP session 可用；`user-browser` 子类型定案 `chrome-inspect-ws` 并补录 ADR 0007 §4。遗留：开关跨重启持久性（待用户重启 Edge 确认）、小红书登录态断言（用户未登录，顺延至 M10b 实装时）
- [ ] `browser.launch` manifest 扩展 `userDataDir`（持久 profile 模式，M10a 主路线前置）
- [ ] 桌面捕获子进程协议：UIA hit-test hook、stdout JSON 通信（与 `python.worker` 同构隔离模式）、dev server 端点实装（start/pick/cancel）
- [ ] 浏览器捕获实装：persistent 与 user-browser（chrome-inspect-ws，按 S1 结论）传输的 picker 注入、selector 生成与回验
- [ ] M10c 立项：自研捕获扩展设计记录——token 配对反连 dev server 为主传输（借鉴 Playwright MCP 的 `PLAYWRIGHT_MCP_EXTENSION_TOKEN` 机制与标签组隔离 UX）、Native Messaging Python host 为备选（借鉴 chrome-relay）；弹窗成本矩阵定稿于 `docs/capture-transport.md` §2.5。仅设计记录，不在本里程碑实装
- [ ] 元素描述符模型与落库约定（编辑器元素库契约）
- [ ] 合同/E2E 测试与完整门禁

## 验收标准

- [ ] S1 验证结论写入 ADR 0007（传输子类型定案）
- [ ] 桌面与浏览器捕获各一条真实 E2E（元素描述符可回验命中）
- [ ] dev server 捕获端点从 501 占位转为实装
- [ ] 完整门禁通过

## 范围外

- 编辑器元素库 UI（M9/M11 按需消费）
- 自研捕获扩展（M10c）的实装——本里程碑仅完成立项设计记录
- Panerelay / Playwright MCP 作为传输链路（降为记录备查）

## 完成证据

仅在全部验收标准通过后填写。
