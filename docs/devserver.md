# dev server 手册（M8/M9）

设计期 HTTP 工具，为编辑器（M9）与捕获（M10）提供 catalog / compile / workflow 文件 API。定位与边界见 ADR 0007：**不承载 run**，进程内无 orchestrator / registry；仅复用 `catalog` 与 `compiler`（架构检查断言不 import `runtime` / `executors`）。

## 启动

```powershell
uv run python -m rpa_core.cli devserver            # 默认 127.0.0.1:8765，workflow 目录 = 仓库根 workflows/
uv run python -m rpa_core.cli devserver --port 9000 --workflows D:\tmp\workflows
```

启动后浏览器打开 `http://127.0.0.1:8765/` 即编辑器单页（M9，ADR 0008：零构建 vanilla HTML/JS，无 npm/打包器/CDN；`GET /` 是唯一静态路由，不开放其他文件路径）。

编辑器用法：左侧命令面板（可过滤）点击追加节点 → 画布选中节点 → 右侧属性表单按 input_schema 生成字段（值支持 `${inputs.x}` 引用）→「编译」回显 `errors[]` → 填文件名「保存」（草稿可保存，不要求编译通过）。「打开」下拉列出 workflows 目录现有文件。

安全边界（ADR 0007 §6）：仅监听 `127.0.0.1`、请求体上限 1 MiB（`PAYLOAD_TOO_LARGE`）、workflow 名限 `[A-Za-z][A-Za-z0-9_-]*` 且 resolve 后必须在根目录内（越界 `FORBIDDEN`）。无认证——设计期本地工具，远程/多用户需新 ADR。

## 端点速查

| 端点 | 方法 | 说明 |
|---|---|---|
| `/api/catalog` | GET | `{digest, commands[]}`，每条含 `id/version/kind/effect/input_schema/output_schema/errors` |
| `/api/compile` | POST | body `{"workflow": {...}, "capabilities": [...]?}`（缺省 = CLI 五项能力）→ `{valid, errors[], catalog_digest}` |
| `/api/workflows` | GET | 根目录内 workflow 名列表 |
| `/api/workflows/{name}` | GET / PUT | 读 / 写（原子落盘）；PUT 只要求合法 JSON 对象（草稿可保存），静态校验走 compile |
| `/api/capture/desktop/{start,pick,cancel}` | POST | M10 实装，当前 501 `NOT_IMPLEMENTED` |
| `/api/capture/browser/{start,pick,cancel}` | POST | M10 实装；start 需 `{"transport": "persistent" \| "user-browser"}`（非法 400，合法 501） |

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
  -d (Get-Content examples/search-and-save/workflow.json -Raw)   # 保存 → workflows/demo.json
curl http://127.0.0.1:8765/api/workflows/demo      # 读回
```

编译不过的草稿可以保存（PUT 不校验 Workflow 模型）；`POST /api/compile` 返回的 `errors[]` 逐条给出 `path` + `message`（pydantic 错误）或 `message`（编译器错误）。

## 捕获契约（M10 前占位）

- `POST /api/capture/browser/start` 的 `transport` 取值与降级链见 `docs/capture-transport.md`：`persistent`（专用持久 profile，主路线）/ `user-browser`（登录态复用，子类型由 S1 结论定：chrome-inspect-ws / panerelay / mcp-extension）。
- 桌面捕获走 UIA hit-test，捕获会话状态放独立子进程（避免 COM 状态污染服务进程），子进程协议随 M10 实装。
