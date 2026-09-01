# ADR 0007：设计期 dev server 与捕获传输契约

- 状态：已接受
- 日期：2026-09-01
- 关联：ADR 0006（HTTP 运行操控推迟）、`docs/capture-transport.md`（传输决策矩阵）、M8 任务单

## 背景

M9 拖拽编辑器与 M10 元素捕获需要一个稳定的设计期 API：读取命令目录、dry-run 编译 workflow、读写 workflow 文件。ADR 0006 推迟的是"承载 run 的 HTTP 服务"，与本 ADR 的设计期工具不冲突——dev server 永远不启动、暂停或恢复 run。

M7 S0 实测（Chrome 152 封锁默认 profile CDP 端口）已在 `docs/capture-transport.md` 定稿浏览器捕获传输方案；本 ADR 将其固化为 dev server 端点契约。

## 决策

### 1. 新顶层包 `rpa_core.devserver` 与架构边界

- 依赖方向：`devserver` 可 import `model`、`catalog`、`compiler`（只读复用）；**禁止** import `runtime`、`executors`、`workers`、`cli`。由架构检查脚本断言。
- 定位：设计期工具，与运行时进程隔离。dev server 进程内不存在 orchestrator、registry 或任何 run 状态。
- 实现载体：stdlib `http.server`（`ThreadingHTTPServer`），零新依赖。规则 10 的 FastAPI/框架排除维持不变。

### 2. workflow 文件目录约定（待定问题结案）

- 默认目录：**仓库根 `workflows/`**，纳入 git 管理（倾向已确认为决策）。dev server 构造参数 `workflows_root` 可覆盖，测试与多根场景使用。
- 文件名即 workflow 名，模式 `[A-Za-z][A-Za-z0-9_-]*`（无分隔符、无 `..`，从源头杜绝路径逃逸）；磁盘文件 `<name>.json`。
- 受限根目录：所有读写 `resolve()` 后必须仍位于根目录内，越界一律拒绝。
- PUT 语义：只要求 body 是合法 JSON 对象并原样落盘（编辑器需保存编译不过的草稿）；静态校验走 `POST /api/compile`。

### 3. 端点契约

统一响应：JSON；错误形态 `{"error": <CODE>, "message": <str>}`。默认监听 `127.0.0.1:8765`。

| 端点 | 方法 | 语义 |
|---|---|---|
| `/api/catalog` | GET | manifest 元数据 `{digest, commands: [{id, version, kind, effect, input_schema, output_schema, errors}]}`，与 `load_catalog` 同源同快照 |
| `/api/compile` | POST | body `{"workflow": {...}, "capabilities": [...]?}`（capabilities 缺省 = CLI 五项）；dry-run 响应 `{valid, errors[], catalog_digest}`，不产生 ExecutionPlan 副作用 |
| `/api/workflows` | GET | 列出根目录内全部 workflow 名 |
| `/api/workflows/{name}` | GET/PUT | 读/写 workflow 文件；PUT 响应 `{name, bytes}` |
| `/api/capture/desktop/{start,pick,cancel}` | POST | 桌面捕获会话（UIA hit-test），M8 仅契约 |
| `/api/capture/browser/{start,pick,cancel}` | POST | 浏览器捕获会话，M8 仅契约 |

错误码与状态码：`BAD_REQUEST`(400)、`FORBIDDEN`(403，越界/非法名)、`NOT_FOUND`(404)、`METHOD_NOT_ALLOWED`(405)、`PAYLOAD_TOO_LARGE`(413)、`NOT_IMPLEMENTED`(501，捕获端点 M10 前占位)。

### 4. 浏览器捕获传输契约

- `POST /api/capture/browser/start` body 必含 `transport`，取值 `"persistent"`（专用持久 profile，M10a 主路线）或 `"user-browser"`（复用登录态）。
- **S1 结论（2026-09-01，Edge 152.0.4191.53 实测）**：`user-browser` 子类型定案为 **`chrome-inspect-ws`**（`userBrowserType` 参数）——用户在 `edge://inspect/#remote-debugging` 手动开启授权开关后，服务端读取 User Data 目录下 `DevToolsActivePort` 文件（首行端口 + 次行浏览器 UUID 路径）构造 `ws://127.0.0.1:<port><path>` 直连；`/json/*` 发现端点 404（防扫描）不可用。`connect_over_cdp`、picker 注入、原生 CDP session 均已实测通过（证据见 `docs/capture-transport.md` §2.2/§4）。开关跨重启持久性待重启确认。
- Panerelay / Playwright MCP / 自研扩展维持降级备选记录，暂不启用。
- 持久 profile 模式所需的 `browser.launch` manifest 扩展（`userDataDir`）随 M10a 实装。

### 5. 桌面捕获契约（待定问题结案）

- 捕获路径：UIA hit-test（不涉及浏览器 CDP 限制）。
- **捕获会话状态放独立子进程**：UIA/COM 初始化会污染宿主进程 COM 状态，dev server 服务进程必须保持无 COM 状态；子进程经 stdout JSON 与 dev server 通信（与 `python.worker` 同构的隔离模式）。
- M8 仅定义端点与 501 占位，子进程协议随 M10 实装。

### 6. 安全边界

- 仅监听 `127.0.0.1`；构造参数不接受非回环 host。
- 请求体上限 1 MiB（`PAYLOAD_TOO_LARGE`）。
- workflow 名与路径越界拒绝（见 §2）。
- 无认证：设计期本地工具，配合仅回环监听；远程/多用户场景需要新 ADR。

## 后果

- M9 编辑器可依赖 catalog/compile/workflows 三组端点开发；M10 捕获按 §4/§5 契约实装。
- dev server 与运行时的隔离由架构检查机械保证；两个进程形态（设计期 HTTP、运行期进程内 API）互不依赖。
- S1 验证结论（user-browser 子类型定案）落地时仅扩充 `transport` 的取值枚举，不改端点形态。
