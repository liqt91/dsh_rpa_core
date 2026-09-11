# dev server 手册（M8/M9）

设计期 HTTP 工具，为编辑器（M9）与捕获（M10）提供 catalog / compile / workflow 文件 API。定位与边界见 ADR 0007：**不承载 run**，进程内无 orchestrator / registry；仅复用 `catalog` 与 `compiler`（架构检查断言不 import `runtime` / `executors`）。

## 启动

```powershell
uv run python -m rpa_core.cli devserver            # 默认 127.0.0.1:8765
uv run python -m rpa_core.cli devserver --port 9000 --workflows D:\tmp\workflows
```

**目录约定（每流程一个目录，2026-09-02）**：`workflows/` = 流程定义根，**每个流程一个目录** `<流程名>/`，主文件 `workflow.json`，可随目录带附属资产——**捕获元素作为流程资产放在 `<流程名>/elements/*.json`**（可入版本库）。`run_artifacts/` = 运行证据（gitignore）。三者职责分离：workflows 根内的 `.json` 平铺旧文件不再识别（遗留手工迁移）。

启动后浏览器打开 `http://127.0.0.1:8765/` 即编辑器单页（M9，ADR 0008：零构建 vanilla HTML/JS，无 npm/打包器/CDN；`GET /` 是唯一静态路由，不开放其他文件路径）。

编辑器用法：左侧命令面板（可过滤）点击追加节点 → 画布选中节点 → 右侧属性表单按 input_schema 生成字段（值支持 `${inputs.x}` 引用）→「编译」回显 `errors[]` → 在文件名框填**流程名**（= `workflows/<名>/`）→「保存」（草稿可保存，不要求编译通过）。「打开」下拉列出 workflows 根下含 `workflow.json` 的目录。**元素库面板只显示当前流程名的元素资产**（流程名变化即刷新；未命名时提示）。

安全边界（ADR 0007 §6）：仅监听 `127.0.0.1`、请求体上限 1 MiB（`PAYLOAD_TOO_LARGE`）、workflow 名限 `[A-Za-z][A-Za-z0-9_-]*` 且 resolve 后必须在根目录内（越界 `FORBIDDEN`）。无认证——设计期本地工具，远程/多用户需新 ADR。

## 端点速查

| 端点 | 方法 | 说明 |
|---|---|---|
| `/api/catalog` | GET | `{digest, commands[]}`，每条含 `id/version/kind/effect/input_schema/output_schema/errors` |
| `/api/compile` | POST | body `{"workflow": {...}, "capabilities": [...]?}`（缺省 = CLI 五项能力）→ `{valid, errors[], catalog_digest}` |
| `/api/workflows` | GET | 根目录内流程名列表（含 `workflow.json` 的目录） |
| `/api/workflows/{name}` | GET / PUT | 读 / 写流程（原子落盘到 `<name>/workflow.json`）；PUT 只要求合法 JSON 对象（草稿可保存），静态校验走 compile |
| `/api/workflows/{name}/elements` | GET | 该流程元素资产列表（`{elements: []}`） |
| `/api/workflows/{name}/elements/{el}` | GET / POST / DELETE | 读 / 保存（model 校验）/ 删除流程元素 |
| `/api/workflows/{name}/elements/{el}/verify` | POST | 元素结构校验（M13：ElementDescriptor 模型 + selector/locator 语义；活体验证需捕获会话内完成） |
| `/api/runs` | POST | 启动运行（body `{"workflow": <名>, "inputs": {...}?}`；spawn `rpa-core run` 子进程，返回 `{runId}`） |
| `/api/runs/{runId}` | GET | 运行状态（子进程在跑？exit code？result.json） |
| `/api/runs/{runId}/events` | GET | 运行事件流（轮询 events.jsonl） |
| `/api/runs/{runId}/cancel` | POST | 取消运行（终止子进程，run 落 cancelled） |
| `/api/capture/desktop/{start,pick,cancel}` | POST | 桌面 UIA 捕获（M10 实装，见下） |
| `/api/capture/browser/{start,pick,cancel}` | POST | 浏览器捕获；start 需 `{"transport": "persistent" \| "user-browser" \| "extension"}`（extension 为无缝跨页主路线） |
| `/api/capture/extension/pending` | GET | 扩展轮询捕获激活状态 |
| `/api/capture/extension/result` | POST | 扩展 content script 捕获结果回传 |

错误形态统一为 `{"error": <CODE>, "message": <str>}`：`BAD_REQUEST`(400)、`FORBIDDEN`(403)、`NOT_FOUND`(404)、`METHOD_NOT_ALLOWED`(405)、`PAYLOAD_TOO_LARGE`(413)、`NOT_IMPLEMENTED`(501)。

## curl 全流程

```powershell
uv run python -m rpa_core.cli devserver            # 终端 1

curl http://127.0.0.1:8765/api/catalog             # 终端 2：命令目录 + digest
curl -X POST http://127.0.0.1:8765/api/compile `
  -H "Content-Type: application/json" `
  -d "{\"workflow\": $(Get-Content examples/search-and-save/workflow.json -Raw)}"
curl http://127.0.0.1:8765/api/workflows           # 列表
curl -X PUT http://127.0.0.1:8765/api/workflows/demo `
  -H "Content-Type: application/json" `
  -d (Get-Content examples/search-and-save/workflow.json -Raw)   # 保存 → workflows/demo/workflow.json
curl http://127.0.0.1:8765/api/workflows/demo      # 读回
```

编译不过的草稿可以保存（PUT 不校验 Workflow 模型）；`POST /api/compile` 返回的 `errors[]` 逐条给出 `path` + `message`（pydantic 错误）或 `message`（编译器错误）。

## 元素捕获（M10 实装）

PowerShell 全流程（`$base = "http://127.0.0.1:8765"`）：

**桌面控件捕获**（hover 模式：鼠标移动实时高亮，F9 或 Ctrl+Click 捕获）：

```powershell
$base = "http://127.0.0.1:8765"
$s = Invoke-RestMethod -Method Post -Uri "$base/api/capture/desktop/start" `
  -ContentType "application/json"   -Body '{"hover":true,"timeoutSeconds":60}'
# → {"sessionId":"desktop-1","mode":"hover"}（hover 默认即 hybrid：mode 回 "hybrid"）；
# 移动鼠标（红色高亮框跟随），到目标控件按 F9 或 Ctrl+Click
# 混合捕获（hybrid）：鼠标进浏览器网页内容区时桌面高亮让位给扩展页内 picker，
# Ctrl+Click 走扩展捕获；浏览器 UI 骨架与桌面应用仍走 UIA。先回传者胜，另一侧自动回收。
Invoke-RestMethod -Method Post -Uri "$base/api/capture/desktop/pick" `
  -ContentType "application/json" `
  -Body (@{sessionId=$s.sessionId; saveAs="myControl"; flow="myFlow"; timeoutSeconds=90} | ConvertTo-Json)
# → 元素描述符（selector.locator 可回验命中）并存入 workflows/myFlow/elements/myControl.json
```

**桌面控件捕获**（热键模式：把鼠标移到目标控件上，按 F9，无高亮）：

```powershell
$base = "http://127.0.0.1:8765"
$s = Invoke-RestMethod -Method Post -Uri "$base/api/capture/desktop/start" `
  -ContentType "application/json" -Body '{"hotkey":"F9","timeoutSeconds":60}'
# → {"sessionId":"desktop-1","mode":"hotkey"}；把鼠标放到目标控件上按 F9
Invoke-RestMethod -Method Post -Uri "$base/api/capture/desktop/pick" `
  -ContentType "application/json" `
  -Body (@{sessionId=$s.sessionId; saveAs="myControl"; flow="myFlow"; timeoutSeconds=90} | ConvertTo-Json)
# → 元素描述符（selector.locator 可回验命中）并存入 workflows/myFlow/elements/myControl.json
```

**浏览器捕获 — persistent**（弹出专用浏览器，站点登录一次后 cookies 持久）：

```powershell
$s = Invoke-RestMethod -Method Post -Uri "$base/api/capture/browser/start" `
  -ContentType "application/json" `
  -Body (@{transport="persistent"; headless=$false; startUrl="https://www.baidu.com"} | ConvertTo-Json)
# 在弹出的浏览器里点到目标元素：
Invoke-RestMethod -Method Post -Uri "$base/api/capture/browser/pick" `
  -ContentType "application/json" `
  -Body (@{sessionId=$s.sessionId; timeoutSeconds=60; saveAs="searchBox"; flow="myFlow"} | ConvertTo-Json)
```

**浏览器捕获 — user-browser**（复用日常 Chrome/Edge 登录态；先在 `chrome://inspect/#remote-debugging` 开启"允许远程调试"，S1/重启持久性均已验证）：

```powershell
$s = Invoke-RestMethod -Method Post -Uri "$base/api/capture/browser/start" `
  -ContentType "application/json" `
  -Body (@{transport="user-browser"; browserType="chrome"} | ConvertTo-Json)
# 到你日常浏览器的目标标签页里点击元素（页面会出现高亮框）：
Invoke-RestMethod -Method Post -Uri "$base/api/capture/browser/pick" `
  -ContentType "application/json" `
  -Body (@{sessionId=$s.sessionId; timeoutSeconds=60; saveAs="loginPageEl"; flow="myFlow"} | ConvertTo-Json)
```

行为说明：

- `pick` 会**阻塞**直到你在页面里点击元素 / 按下热键 / 超时（默认 browser 60s、desktop 90s）
- 浏览器 pick 注入高亮覆盖层，鼠标悬停即高亮，点击即采集（生成 CSS selector 并当场回验命中数）；`Esc` 取消本次
- `saveAs` 可选——指定后描述符落库到该 `flow` 流程的元素资产 `<流程名>/elements/{name}.json`（需同时传 `flow`，缺省 400），可用 `GET /api/workflows/{flow}/elements/{name}` 读回
- `cancel` 结束会话：桌面会终止 agent 子进程，浏览器会清理注入并断开（不关闭你的浏览器）
- 桌面捕获优先级：`windowHandle` > 前台窗口 > 屏幕级 hit-test（窗口作用域可免疫安全软件覆盖层，见 `docs/capture-transport.md`）

**浏览器捕获 — extension**（自研 content-script 扩展，无缝跨浏览器/跨页主路线，零逐次授权）：

```powershell
# 无 token 配对：扩展安装并运行即自动可用（devserver 仅绑定 127.0.0.1）
# 捕获：捕获期间所有浏览器的所有页面 hover 高亮自动激活，Ctrl+Click 捕获：
Invoke-RestMethod -Method Post -Uri "$base/api/capture/browser/start" `
  -ContentType "application/json" -Body '{"transport":"extension"}'
Invoke-RestMethod -Method Post -Uri "$base/api/capture/browser/pick" `
  -ContentType "application/json" `
  -Body (@{sessionId=$s.sessionId; timeoutSeconds=90; saveAs="el"; flow="myFlow"} | ConvertTo-Json)
```

扩展安装与协议详见 `extension/README.md`。
