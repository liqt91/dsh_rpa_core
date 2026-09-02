# M10 元素捕获：桌面 UIA hit-test + 浏览器双通道

状态：`done`

## 目标

按 ADR 0007 捕获契约实装元素捕获：桌面走 UIA hit-test（独立子进程承载捕获会话），浏览器走持久 profile（主）与 user-browser 登录态复用（次，子类型由 S1 结论定）。产物为可入册元素描述符，供编辑器属性面板引用。

## 任务

- [x] 执行 S1 验证（`docs/capture-transport.md` §4 协议，M10 前置，自 M8 移入）：2026-09-01 于用户日常 Edge 152.0.4191.53 实测通过——WS URL 从 `DevToolsActivePort` 文件读取（页面 UI 无需交互）、`/json/*` 404 防扫描确认、`connect_over_cdp` 枚举真实标签页、picker 注入回验 + 高亮、原生 CDP session 可用；`user-browser` 子类型定案 `chrome-inspect-ws` 并补录 ADR 0007 §4。遗留项已闭环（2026-09-02 Chrome 152 重启实测）：开关跨重启持久、UUID 路径每次启动轮换（服务端每次会话重读文件，实装即为此方式）；小红书登录态断言（用户未登录，顺延至实际使用时验证，机制已在 example.com 全链路验证）
- [x] `browser.launch` manifest 扩展 `userDataDir`（持久 profile 模式，M10a 主路线前置）；执行器适配 `launch_persistent_context`（close 时 context.close）
- [x] 桌面捕获子进程协议：UIA hit-test hook、stdout JSON 通信（与 `python.worker` 同构隔离模式）、dev server 端点实装（start/pick/cancel）；**窗口作用域 hit-test**（在被测窗口 UIA 子树内找包含点且面积最小的后代）免疫安全软件覆盖层劫持；测试模式 `--point` + `--window-handle`
- [x] 浏览器捕获实装：persistent 与 user-browser（chrome-inspect-ws，读 DevToolsActivePort 构造 WS URL）传输的 picker 注入、selector 生成与回验
- [x] M10c 立项：自研捕获扩展设计记录——token 配对反连 dev server 为主传输（借鉴 Playwright MCP 的 `PLAYWRIGHT_MCP_EXTENSION_TOKEN` 机制与标签组隔离 UX）、Native Messaging Python host 为备选（借鉴 chrome-relay）；弹窗成本矩阵定稿于 `docs/capture-transport.md` §2.5。仅设计记录，不在本里程碑实装
- [x] 元素描述符模型（`model/capture.py` ElementDescriptor）与落库约定（`workflows_root/elements/` + `GET /api/elements[/name]`，编辑器元素库契约）
- [x] 合同/E2E 测试与完整门禁

## 验收标准

- [x] S1 验证结论写入 ADR 0007（传输子类型定案 chrome-inspect-ws）
- [x] 桌面与浏览器捕获各一条真实 E2E（元素描述符可回验命中：浏览器 selector 命中 1；桌面 locator 经真实 DesktopExecutor.findElement matchedCount == 1）
- [x] dev server 捕获端点从 501 占位转为实装（未配置后端时保持 501，请求体校验 400 优先）
- [x] 完整门禁通过

## 范围外

- 编辑器元素库 UI（M13 消费捕获产物）
- 自研捕获扩展（M10c）的实装——本里程碑仅完成立项设计记录
- Panerelay / Playwright MCP 作为传输链路（降为记录备查）

## 实施中发现并修复

1. **桌面捕获被覆盖层劫持**：安全软件的 `Windows.UI.Input.InputSite.WindowClass` 透明输入窗格偶发挡在目标按钮上，`ElementFromPoint` 返回覆盖层元素——引入窗口作用域 hit-test（子树内最小包含面积元素）根治。
2. **agent stdout 编码**：子进程按系统区域编码（cp936）输出中文描述符，父进程按 UTF-8 解码失败杀死读取线程——agent `sys.stdout.reconfigure(encoding="utf-8")` + 父端 `errors="replace"`。
3. **UIA 首次枚举偶发不完整**：agent 整体重试（重新 hit-test，4 次 / 0.4s 间隔），以 verifyCount == 1 为成功信号。
4. **devserver 会话注册表死锁**：`threading.Lock` 不可重入，start 流程内嵌套加锁——改 `RLock`。
5. **413 未排空 body**（M12 期间发现）：Windows 下客户端写一半收 RST——先排空再响应。

## 完成证据

- `commands/browser/launch.json`：`userDataDir` 参数
- `src/rpa_core/executors/browser.py`：persistent context 适配
- `src/rpa_core/capture/`：`browser.py`（双传输会话 + picker 注入）、`desktop.py`（agent 会话包装）、`desktop_agent.py`（UIA hit-test agent）
- `src/rpa_core/model/capture.py`：ElementDescriptor
- `src/rpa_core/devserver/`：捕获端点实装 + 元素库路由 + RLock + close 生命周期
- `tests/contract/test_capture_contract.py`：fake 会话端点流（元素落库往返、404/403、取消）
- `tests/e2e/test_capture.py`：浏览器 persistent 真实 E2E（data:URL 页 + 合成点击 + 落库）；桌面 WinForms 真实 E2E（point 捕获 + 执行器回验 matchedCount == 1）
- full gate 通过：105 tests + ruff + architecture + task check
