# 会话协议（本地回环）

服务只绑 `127.0.0.1`。三个客户端：编辑窗口（PPX / pywebview）、浏览器插件（MV3）、Agent 侧 CLI。

## 端点文件

`%TEMP%\workbuddy-studio\endpoint.json`（可用 `STUDIO_HOME` 改目录）：

```json
{"port": 7788, "token": "…", "pid": 1234, "root": "D:\\work", "version": "1.0.0"}
```

编辑窗口与 CLI 都从这里取端口；文件不存在时退回 `STUDIO_PORT` / 7788。

## 路由

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 服务状态、编辑窗口在线与否、等待计数、拾取数 |
| POST | `/open` | 排队一个「打开文件」请求，返回 `id` |
| GET | `/inbox?since=N&client=editor` | 编辑窗口取收件箱（`cursor` 是**收件箱自己的**游标） |
| POST | `/events` | 追加事件：`save` / `opened` / `close` / `heartbeat` / `ack` |
| GET | `/events?since=N&timeout=S&kinds=a,b&path=P` | 长轮询，最多等 S 秒 |
| POST | `/capture` | 插件上报拾取结果 |
| GET | `/captures?since=N` | 最近的拾取记录（最多 200 条） |
| POST | `/shutdown` | 停服务 |

### 事件字段

| kind | 由谁发 | 关键字段 |
|---|---|---|
| `open-request` | 服务 | `path`, `reason`, `requestId` |
| `opened` | 编辑窗口 | `path`, `sha` |
| `save` | 编辑窗口 | `path`, `sha`, `bytes`, `lines`, `validation{ok,line,col,message,kind}` |
| `heartbeat` | 编辑窗口 | `path`, `dirty` |
| `capture` | 插件 | `selector`, `strategy`, `matches`, `tag`, `text`, `attributes`, `rect`, `alternatives` |

事件 id 单调递增，`since` 传上次的 `cursor` 即可只取新增。

## 安全边界

- 只监听回环地址；不接受外网连接。
- `POST` 带 `Origin` 时必须是 `chrome-extension://` / `moz-extension://` / `http://127.0.0.1:*` / `http://localhost:*`，否则 `403 ORIGIN_DENIED`。
- 没有 `Origin` 的请求（CLI、curl）放行 —— 但它们本来就只能从本机发起。
- 插件权限只有 `activeTab` + `scripting` + `storage` + `contextMenus`，`host_permissions` 仅回环；不注入常驻脚本，点一次注入一次。

## 与 PPX 的关系

编辑窗口的 Python 侧（`editor/api/api.py`）用 `@api_method("studio.*")` 把上面几个路由包成 RPC，
前端 `ppx.call('studio.openFile', …)`。窗口关闭只是客户端消失，服务里的收件箱还在，
下一个窗口打开仍能取到未消费的请求。
