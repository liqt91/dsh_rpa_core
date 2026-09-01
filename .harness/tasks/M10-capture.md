# M10 元素捕获：桌面 UIA hit-test + 浏览器双通道

状态：`active`

## 目标

按 ADR 0007 捕获契约实装元素捕获：桌面走 UIA hit-test（独立子进程承载捕获会话），浏览器走持久 profile（主）与 user-browser 登录态复用（次，子类型由 S1 结论定）。产物为可入册元素描述符，供编辑器属性面板引用。

## 任务

- [ ] 执行 S1 验证（`docs/capture-transport.md` §4 协议，M10 前置，自 M8 移入）：chrome://inspect 授权开关的 WebSocket 连接、登录态断言、picker 注入回验、开关持久性；结论（user-browser 子类型定案 + 运维方式 + 降级链）补录 ADR 0007
- [ ] `browser.launch` manifest 扩展 `userDataDir`（持久 profile 模式，M10a 主路线前置）
- [ ] 桌面捕获子进程协议：UIA hit-test hook、stdout JSON 通信（与 `python.worker` 同构隔离模式）、dev server 端点实装（start/pick/cancel）
- [ ] 浏览器捕获实装：persistent 与 user-browser（按 S1 结论）传输的 picker 注入、selector 生成与回验
- [ ] 元素描述符模型与落库约定（编辑器元素库契约）
- [ ] 合同/E2E 测试与完整门禁

## 验收标准

- [ ] S1 验证结论写入 ADR 0007（传输子类型定案）
- [ ] 桌面与浏览器捕获各一条真实 E2E（元素描述符可回验命中）
- [ ] dev server 捕获端点从 501 占位转为实装
- [ ] 完整门禁通过

## 范围外

- 编辑器元素库 UI（M9/M11 按需消费）
- 自研扩展兜底路线（远期，仅在 2.2/2.3/2.4 全部不可用时启动）

## 完成证据

仅在全部验收标准通过后填写。
