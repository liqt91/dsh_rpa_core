# M20 扩展通道迁移 Native Messaging（取消常驻 hub）

状态：`done`
关联：ADR 0015（决策）、ADR 0013（扩展单通道）、ADR 0014 §10（被取代的 ExtHub 方案）、`docs/capture-transport.md`、`docs/extension-install.md`
决策记录：四项取舍已由维护者定案——**全量切换**（不留 HTTP 双栈）、**捕获通道一并迁移**、**三平台同步**、**host 用 pythonw 启动器**。

## 背景

现行扩展通道为扩展长轮询 `127.0.0.1:8765` 的 HTTP 队列（`ExtensionExecHub`），GUI 内嵌
`ExtLoopbackGateway`。结构性缺陷：①「插件离线」误报（hub 懒启动 / MV3 SW 休眠）；
② 8765 端口争抢（GUI 与 devserver 启动顺序成隐性契约）；③ 在线判定依赖心跳窗口时序。

目标：扩展经 `connectNative` 连本机 bridge host（**浏览器按需拉起**），host 与执行器侧经
本地 IPC 通信。在线 = IPC 可连接；无端口、无心跳窗口、无「hub 未启动」状态。

## 关键设计（已定）

- **传输**：Windows 命名管道（pywin32，经 pywinauto 传递依赖）+ POSIX Unix 域套接字。
  实测本机 Windows Python 3.12 **无 `socket.AF_UNIX`**，故必须平台分派。
- **协议**：两侧同构——4 字节小端长度前缀 + UTF-8 JSON。消息面：`hello`（host 上报
  browser/instanceId/pid/版本）、`submit`/`result`、`capture_arm`/`capture_disarm`/
  `capture_result`、best-effort `cancel`。
- **发现/路由**：host 在收到扩展 `hello` 后以其 browser+instanceId 命名端点；执行器按
  browserType 枚举端点连接（Windows 列 `\\.\pipe\` 前缀 / POSIX 列运行时目录），连接后
  读 `hello` 校验实例身份。多 profile 多实例天然并存。
  （**M22 修正 2026-09-18**：POSIX 上实例段改为定长 token `sha256(instanceId)[:16]`，
  端点目录 macOS 走 `/tmp/rpa_core-<uid>` —— 原「browser+instanceId 直接拼名」顶穿
  `sun_path` 103 字节上限，见 ADR 0015 §6。）
- **生命周期**：host 由浏览器 spawn，stdin EOF（扩展 port 断/浏览器退出）即清理端点退出。

## 切片

- [x] **S0 spike（硬门槛，需真机人工协作）**：最小 host + 最小扩展实测 ✅ **通过（2026-09-17，Edge 152 真机）**
  - 脚手架：`.harness/spike/native_messaging/`（`extension/` 最小 MV3 扩展 + `observe.py`
    观测器/错误上报 + `register_host.py` 注册/注销/ID 发现 + `README.md`）
  - 结论见「S0 完成证据」；**通过 → 可按 S3/S4 继续**
  - MV3 SW 在长连 native port 下是否保活（Edge/Chrome 各验）
  - 浏览器退出 / 扩展 reload → host 是否被 stdin EOF 回收
  - 断线重连时延；是否需 alarm 兜底
  - Load unpacked 扩展 ID 从 Secure Preferences 读取的稳定性
  - **未过此关不进入 S4 之后的实装**
- [x] **S1 传输层**：`src/rpa_core/local_transport.py`
  - 长度前缀 JSON framing（读写两端、跨块、超限拒绝）
  - Windows 命名管道 server/client（多实例、`\\.\pipe\` 前缀枚举、超时连接）
  - POSIX Unix socket server/client（运行时目录、陈旧端点清理、枚举）
  - 单测：framing 边界、同进程管道往返、多实例枚举、连接超时、EOF 语义
- [x] **S2 host 子进程**：`src/rpa_core/workers/ext_bridge.py`
  - stdio 帧循环 + `hello` 握手 + 端点命名 + 多客户端中继 + 取消/EOF 清理
  - 合同测试：真实 spawn host 子进程 + 假扩展（脚本扮演 stdio 对端）+ 管道客户端往返
- [x] **S3 安装注册**：`extension_installer.py` 增 native host manifest
  - 三平台 manifest 路径（Win HKCU 注册表 / macOS / Linux）
  - unpacked 扩展 ID 发现（Secure Preferences `location==4` 且 path 匹配 → settings key）
  - `allowed_origins` 写入 + 幂等自愈（GUI 启动 / `install-extension` 时重扫重注册）
  - CLI/GUI 入口 + 卸载清理（GUI 入口归 S7）
- [x] **S4 扩展改造**：`extension/manifest.json`（+`nativeMessaging`，摘 8765 host_permissions，
  version 0.2.0→0.3.0）+ `extension/background.js`（exec/capture 轮询 → native port，断线重连）
- [x] **S5 执行器切换**：`extension_exec.py` 删 `ExtensionExecHub`、`ExtensionExecClient`
  传输换本地 IPC（公开方法签名不变，`executors/browser_ext.py` 零改动）；错误码语义保留
- [x] **S6 捕获切换**：`capture/extension.py` pending 轮询 → 端点 arm/disarm/result
- [x] **S7 接线清理**：`gui/app.py`（徽标/插件对话框探端点，删 `_ensure_ext_hub`/`_ext_gateway`/
  `_ext_hub_url`）、`devserver`（删 `/api/ext/*`、`ExtLoopbackGateway`、`extension_hub_*`）、
  `devserver/runs.py`（删 `hub_url`/`RPA_EXT_HUB_URL`）、`cli.py`（env-status 探端点、
  install-extension 联动注册）
- [x] **S8 收尾**：删 `HUB_URL_ENV`/`DEFAULT_HUB_URL`/`ONLINE_WINDOW_SECONDS`；重写受影响测试
  （`test_extension_exec_channel`、`test_gui_ext_gateway`、`test_run_control` 等）；文档
  （`docs/capture-transport.md` 标记 supersede、`docs/extension-install.md`、`architecture.md`）

## 每切片门禁

- 合同/单测补齐；`uv run python .harness/scripts/check_all.py` 全绿
- PROGRESS.md 追加一行；切片完成后在本任务单勾选

## 风险

- **S0 不通过**（SW 不保活）→ 退回 alarm 定时重连；若仍不可靠则重新评估方案
- **企业锁注册表** → 回退「手动安装 host manifest」引导文案
- **旧版 HTTP 扩展不兼容** → manifest 版本 + 权限变更触发浏览器重新确认，安装器引导重载
- **活跃外部写入**：提交按显式路径 `git add <file>`，禁用 `git add -A`（PROGRESS 2026-09-15 运维提醒）

## 完成证据

### S0（2026-09-17，Edge 152 真机）

四项全部通过（观测器 `observe.log` 原始时间戳为证）：

| # | 问题 | 实测结果 |
|---|---|---|
| 1 | MV3 SW 长连 native port 是否保活 | **保活**。连续心跳 `PING seq=371..385` 间隔稳定 15.00s，跨 3.5 分钟零空洞；reload 后新 SW `seq` 从 1 重新计数，仍稳定 15s |
| 2 | 扩展 reload / 浏览器退出 → host 回收 | **回收**。reload：`ENDPOINT closed-by-host` 与新 `HOST-UP` 同秒；关浏览器：`15:41:06.760 closed-by-host` / `15:41:07.909 HOST-DOWN`，`rpa-core-ext-host.exe` 残留 0、端点列表为空 |
| 3 | 断线重连时延 | **≈0.4s**（15:40:00.399 断开 → 15:40:00.792 新 host 就绪）；无需依赖 3s 重试，alarm 兜底保留为 SW 被回收时的保险 |
| 4 | Load unpacked 扩展 ID 从 Secure Preferences 可稳定读出 | **稳定**。`discover_extension_id` 得 `dfbjkpbeppapijmjcpppconbchmeinek`，与 `edge://extensions` 一致，`allowed_origins` 校验通过（无 forbidden 上报） |

**Chrome 152 复核（2026-09-17，同一源码目录）**：四项同样全过，且额外验证两点——

- 用**路径算出的 ID**（非从 profile 发现）预注册后 Chrome 直接接受（无 forbidden），
  Chrome 侧端点 `rpa_core_ext_chrome_2432df58-…`、心跳 `PING seq=2..6` 间隔稳定 15.00s；
- reload 回收：`15:57:10.095 closed-by-host` → `15:57:10.173 port-opened`（≈0.08s），
  **host 进程恒为 1 个**（串行化修复生效；Edge 侧修复前实测为 2 个）。

**unpacked 扩展 ID 推导（S3 可直接复用）**：Chromium 的 unpacked 扩展 ID 由**源码目录绝对
路径**派生，与浏览器无关，算法为「路径的 UTF-16LE 字节 → SHA256 前 128 位 → 十六进制 →
映射 0-9→a-j / a-f→k-p」。实测 `D:\Users\Administrator\Documents\代码\rpa_core\.harness\
spike\native_messaging\extension` 算出 `dfbjkpbeppapijmjcpppconbchmeinek`，与 Edge profile
发现值逐字一致、Chrome 也接受 → S3 可在扩展加载前预注册（Secure Preferences 发现仍保留
为权威交叉校验）。

**决定性发现（改变实现）**：

1. **host manifest 的 `path` 不能依赖带参数的命令行**（`"pythonw.exe" "script.py"`）。
   Chromium 未按命令行解析 → `pythonw` 被无参拉起，退化为读 stdin 的 REPL：扩展
   `connectNative` 看似成功（徽标 on）却无任何 host 逻辑，且**不产生任何日志**，极难定位。
   → 改为**无参数可执行入口**：pyproject 新增 console script
   `rpa-core-ext-host = "rpa_core.workers.ext_bridge:main"`，manifest `path` 直接指向
   `<venv>/Scripts/rpa-core-ext-host.exe`。这也正是 S3/S4 需要的稳定 host 入口。
2. **扩展侧必须串行化 `connectNative`**：spike 扩展的 `connect()` 因 `await ensureInstanceId()`
   让并发调用绕过 `if (port) return` 守卫，实测一次 reload 起了 **2 个 host 进程**（PID 320/2448，
   同一端点名多实例）。→ S4 真实扩展需用「连接中」标志串行化（否则每装一次扩展多一个 host）。
3. 观测手段沉淀：spike 扩展把连接尝试/断开/异常 POST 到本地观测器
   （`127.0.0.1:8799`）+ 观测器轮询端点出现/消失，使失败原因**无需人工抄 SW 控制台**即可定位。

**关于「浏览器退出」的准确语义（重要）**：

- 关闭最后一个窗口会触发扩展 port 断开 → host 被回收（实测 15:41:06，残留 0、端点空）；
- 但 Edge 的**后台驻留**（`msedge.exe --no-startup-window`，即「启动增强 / 关闭 Edge 后继续
  运行后台扩展」）会在无窗口时继续跑扩展 SW → 扩展重连 → host **再次被拉起**（实测
  15:41:40，且复现并发竞态起 2 个）。
- 结论：**「关闭浏览器窗口」≠「扩展离线」**；host 回收只在浏览器**完全退出**（含后台驻留
  进程）时发生。这是设计预期的正确语义（在线 = port 连接），但排障文案与验收必须说明，
  否则会误判为「关不掉/回收失败」。E2E 里若要断言离线，需确保浏览器进程全退或直接断 port。

### S1（2026-09-17）

- `src/rpa_core/local_transport.py`：长度前缀 JSON framing（stdio 与端点共用）；
  Windows 命名管道（pywin32 **overlapped I/O**）与 POSIX Unix 域套接字双实现；
  `endpoint_name` / `list_endpoints` / `connect` / `LocalEndpointServer`。
- 关键实现结论（均为实测踩坑，已写入模块 docstring）：
  1. Windows Python 3.12 **无 `socket.AF_UNIX`** → 必须平台分派（Windows 走命名管道）。
  2. 同步（非 overlapped）管道句柄上，一端挂起的阻塞 `ReadFile` 会阻塞另一线程的
     `WriteFile`（中继模型必然并发读写）→ 全部句柄以 `FILE_FLAG_OVERLAPPED` 打开。
  3. pywin32 的 overlapped `ReadFile`/`WriteFile`/`ConnectNamedPipe` **不抛**
     `ERROR_IO_PENDING`，而是作为返回值返回 → 需显式判断返回码。
  4. `ConnectNamedPipe` 在客户端已接入时抛 `ERROR_PIPE_CONNECTED` 且不挂 overlapped
     → 必须直接交接，否则会永远等一个不会触发的完成事件。
  5. 挂起的 `ConnectNamedPipe` 无法用 `CloseHandle` 解除 → 关闭走 ctypes `CancelIoEx`
     （pywin32 只暴露限调用线程的 `CancelIo`）。
- 单测 `tests/unit/test_local_transport.py` 15 项：framing 边界 7 + 端点往返/枚举/
  超时/多客户端/平台化双绑定 8；`ruff` 与架构检查通过。

### S2（2026-09-17）

- `src/rpa_core/workers/ext_bridge.py`：由浏览器按需拉起的 host——stdio 帧循环 +
  `hello` 握手 → 以 browser+instanceId 命名端点；`submit` 转发为 `command` 并按 id
  路由结果回发起客户端；host 侧强制超时（回 `TIMEOUT` 并 best-effort `cancel`）；
  扩展主动消息广播给全部客户端；stdin EOF → 清理端点退出（端点消失 = 离线）。
- 合同测试 `tests/contract/test_ext_bridge.py` 9 项：真实 spawn host 子进程 + 假扩展
  stdio 对端 + 管道客户端，覆盖握手/身份/status/命令往返/超时/捕获透传/广播/
  并发按 id 路由/非法消息/EOF 回收。
- 门禁：`check_all.py` 中 2 项失败（`test_run_startup_failure_surfaces_compile_reason`
  与 `test_windows_desktop_vertical_slice`）单独重跑均通过，属既有环境敏感 flake，
  与本切片无关。

### S3（2026-09-17）

- `extension_installer.py` 新增 native messaging host 注册能力（ADR 0015 落地）：
  - `NATIVE_HOST_NAME=com.rpa_core.ext_bridge` / `NATIVE_HOST_ENTRY=rpa-core-ext-host`；
  - `extension_id_from_path`（路径→ID，Windows 用 UTF-16LE）、`discover_unpacked_extension_id`
    （Secure Preferences）、`resolve_native_host_extension_id`（显式 > 发现 > 推导）；
  - `register_native_host` / `unregister_native_host` / `native_host_status` /
    `ensure_native_host`（幂等自愈）；三平台：Win 写 HKCU
    `Software\<vendor>\<browser>\NativeMessagingHosts\<name>` + manifest 落
    `~/.rpa-core/native-host/`，macOS/Linux 直接写浏览器 `NativeMessagingHosts/`；
  - host 入口缺失（未 `uv sync`）报 `NATIVE_HOST_MISSING`，不静默写坏注册。
- CLI `install-extension` 接线：默认 Load unpacked 引导**同时注册** host（配套两步，逐浏览器
  容错）；`--status` 增 `nativeHost`（只读）；`--remove` 一并注销 host。
- 真实注册实测：真实扩展目录 → ID `edklhodhmpncghhhlpabpppimjakipdi`，edge/chrome 双注册，
  `--status` 回读 registered=true / extensionId / hostExecutable 全对。
- 测试：`tests/unit/test_native_host.py` 13 项（路径→ID 形状 + Windows 真机锚点、manifest 与
  注册表写入、幂等、目录变动自愈、注销、状态、入口缺失报错、ID 解析优先级、profile 发现）；
  既有 `test_extension_installer` 两条合同测试随新行为更新（引导不再断言「完全不碰注册表」，
  改为「不写外部扩展注册表条目」；`--remove` 载荷含 nativeHost）。
- 顺带修复**传输层并发竞态**（S1 遗留，被 `test_concurrent_clients_are_routed_by_command_id`
  间歇性抓到，约 1/5）：客户端若在 `CreateNamedPipe` 与 `ConnectNamedPipe` **之间**接入，
  overlapped 的 `ConnectNamedPipe` 返回 pending 且**永不完成**（实测），导致该客户端永远不被
  `accept`（表现为第二个客户端丢回复）。修法：`PeekNamedPipe` 兜底判「已连接」（成功=已连接，
  未连接报 `ERROR_PIPE_NOT_CONNECTED=230`），`_begin_connect` 与 `accept` 两处各兜一层；
  并抽出 `_connect_instance` 供确定性回归测试
  （`test_connect_detects_client_that_connected_before_connect_named_pipe`）。
  验证：进程内探针 40/40、host 竞态探针 40/40、桥接合同测试连跑 6 轮全绿。
- 门禁：`check_all.py` 393 passed / 1 failed（`test_uia_desktop_vertical_slice` 桌面 E2E
  焦点抖动，单独重跑通过——既有环境敏感 flake）；ruff/架构/tasks 全过。

### S4（2026-09-17，Edge 152 真机验收通过）

- `extension/manifest.json`：version 0.2.0→**0.3.0**，权限 +`nativeMessaging`，摘掉
  `http://127.0.0.1:8765/*` 与 `http://localhost:8765/*`（保留 `<all_urls>`；`update_url`
  与外部注册表 CRX 路线所需的商店指向保留不变）。
- `extension/background.js` 传输层重写：HTTP 长轮询（`poll()`/`execLoop`/`schedule()`/
  `DEVSERVER`/`EXEC_HOLD_S`）全部删除 → `connectNative("com.rpa_core.ext_bridge")`
  - `connect()` 用 `connecting` 标志**串行化**（S0 教训：并发会拉起多个 host）；
    断开 3s 退避重连，SW 被回收由 30s alarm 兜底；
  - host 推 `command` → `runCommand` → `result`；推 `capture_arm/capture_disarm` → 广播标签页；
    `ping`→`pong`；`cancel` 忽略（宿主已按超时返回，迟到结果由 host 丢弃）；
  - 捕获结果经 port 回传 `capture_result`（带 `sessionId`）；`hello` 首帧上报
    browser/instanceId/extVersion/version/platform/ua/focused/focusedAt；窗口焦点变化上报 `focus`；
  - 命令执行面（tabs/scripting/cookies/domOp/pageEval/waitComplete/权限 `assertAllowed`）
    逐行保留，零行为改动。
- `extension/content.js`：新增**启动即查询捕获态**（`rpa-capture-state`）——推送模型丢掉了
  旧 HTTP「每 5s 重复广播」对**新页面**的天然覆盖，首次真机验收即暴露（重载扩展/刷新页后无红框）。
  配套 `background.js` 用 `chrome.storage.session` 持久捕获态 + `chrome.tabs.onUpdated`
  在页面加载完成时补发 arm。
- `extension/popup.js`/`popup.html`：devserver 探测 → **bridge 连接状态**（已连接/未连接 +
  `lastError`）；新增 `capture_armed`/`capture_disarmed` ack（让发起方确认 arm 已到达扩展，
  真机排障用）。
- `extension/README.md`：协议段重写为 Native Messaging 消息面；安装段补 host 注册步骤与
  「path 必须是无参数可执行文件」约束。
- 测试：`test_extension_installer` 两处版本断言 0.2.0→0.3.0；`node --check` 三个 JS 全过。
- **真机端到端验收**（`.harness/spike/native_messaging/arm_capture_probe.py`，客户端→host→
  扩展→页面→描述符回传）：
  ```
  16:47:36.028 capture_armed (extension ack)
  16:47:56.952 CAPTURE_RESULT {"descriptor":{"selector":{"css":"#chat-textarea"},
               "verifyCount":1,"metadata":{"tag":"textarea","id":"chat-textarea",...},
               "url":"https://www.baidu.com/"}}
  ```
  （探针经本地端点发 `capture_arm` → 扩展进入捕获模式（红框跟随）→ Ctrl+Click → 描述符回传。）
- 门禁：FULL GATE PASSED。

### S5 + S6 + S7（2026-09-17，一并落地：三者互相耦合，拆开会出现中间态不可用）

**S5 执行器切换**（`extension_exec.py` 重写）：

- 删 `ExtensionExecHub` / `HUB_URL_ENV` / `DEFAULT_HUB_URL` / `ONLINE_WINDOW_SECONDS` /
  `DEFAULT_POLL_SECONDS`（HTTP 长轮询与心跳窗口整体退役）。
- `ExtensionExecClient` 换本地端点：`browser`/`endpoint` 可选，`online()/status_cached()/host()/
  submit()` 签名不变（`executors/browser_ext.py` **零改动**）；`status()` 聚合在线端点
  （online/host/hosts/instances）；`targetHost` 支持浏览器名或实例 id，无匹配即
  `TARGET_HOST_OFFLINE` 快失败；`tabs.create` 结果补带端点实例 id（会话绑定）。
- 读取在守护线程里做并带截止时间（管道 recv 本身不可超时），host 卡死不会挂住执行器。
- 端点前缀改为**环境变量可覆盖**（`RPA_EXT_ENDPOINT_PREFIX`）且 host 与 client 同源
  （`local_transport.endpoint_prefix()`），测试据此隔离本机真实端点。

**S6 捕获切换**（`capture/extension.py` / `hybrid.py`）：

- `ExtensionCaptureSession` 自己连端点：`start()` 下发 `capture_arm` + 起读线程，
  收到 `capture_result` 唤醒 `pick`，`cancel/close` 下发 `capture_disarm`；
  无端点时 `pick` 明确返回 `offline`（不挂满超时）。
- `HybridCaptureSession` 内嵌扩展腿（不再依赖 devserver 的 pending/result 路由），
  「先回传者胜」语义不变；devserver 在创建 hybrid 会话后显式 `start()` 扩展腿。
  > 2026-09-18 修正：胜出判据收紧为「**有效捕获描述符**先回传者胜」（`kind` ∈ {desktop,
  > browser}）。腿的失败形态一律不抢跑 —— 原判据会把「腿不可用/崩溃」当成用户捕获结果，
  > 在非 Windows 上直接掐掉仍可用的扩展腿（见 M23 G1「平台退化修正」）。

**S7 接线清理**：

- `devserver/app.py`：删 `_extension_hub` / `set_extension_hub_url` / `extension_hub_*` /
  `extension_command_*` / `extension_pending` / `extension_result`；`env_status_view` 改用
  端点在线状态。
- `devserver/server.py`：删 `/api/ext/*` 路由与 `/api/capture/extension/*` 路由、
  `_host_report`/`_wait_seconds`/`_route_extension_exec`，删 `ExtLoopbackGateway` /
  `probe_ext_hub` / `_GatewayHTTPServer`（ADR 0014 §10 的 loopback 网关整体退役）。
- `devserver/runs.py`：删 `hub_url` 与 `RPA_EXT_HUB_URL` 注入（run 子进程自行发现端点）。
- `gui/app.py`：删 `_ensure_ext_hub`/`_ext_gateway`/`_ext_hub_url`；状态徽标与插件对话框
  改为探测 bridge 端点（`ExtensionExecClient().status()`）。
- `cli.py`：`env-status` 改用端点探测（无需 devserver 在跑）。

**测试**：删 `test_extension_exec_channel.py`（HTTP hub）与 `test_gui_ext_gateway.py`（网关）；
`test_capture_extension.py` 重写为「假 bridge 端点」驱动（4 项：arm+pick / 落库 / cancel 撤防 /
无端点 offline）；`test_capture_hybrid.py` 扩展腿改假端点（4 项：默认 hybrid / 扩展先赢 / 桌面先赢 /
落库）；`test_ext_bridge.py` 增 7 项**客户端**契约（status/online、submit 往返、错误码透传、
target 离线快失败、双宿主按 target 路由、tabs.create 带实例 id、无端点离线）；
`tests/conftest.py` 隔离改端点前缀；`test_capture.py` e2e 改假端点。

**真机端到端验收**（CLI run，真实 Edge + 已装扩展）：

```
uv run python -m rpa_core.cli run s5_check.json   # browser.navigate https://example.com
status: succeeded
outputs.open: {url: "https://example.com/", browserInstance: "ef4d3919-…",
               browserType: "msedge", tabId: "933970309"}
effects[0].details: {operation: "navigate", transport: "extension", ...}
```

- 门禁：FULL GATE PASSED（含桌面 E2E）。

### S8（2026-09-17，收尾）

- 文档：`docs/extension-execution-plan.md` 顶部加「传输层已被 ADR 0015 取代」横幅（逐项列出
  退役的 `/api/ext/*`、`RPA_EXT_HUB_URL`、长轮询、`ExtensionExecHub`、心跳窗口、
  `ExtLoopbackGateway`）；`docs/extension-install.md` §1.2 执行通道段补 Native Messaging +
  host 注册要求；`.harness/architecture.md` 增「共享低层模块」表（`local_transport` /
  `extension_exec` / `workers.ext_bridge` 的职责与使用者）。
- Web 编辑器：`devserver/static/app.js` 三处 `/api/ext/status` 改为新增只读路由
  `GET /api/extension/bridge-status`（返回端点聚合状态 + `latestVersion`，形状与旧 hub status
  兼容：instances 平铺 `extVersion` 等字段），前端徽标/浏览器置灰逻辑无需改写。
- 清理核对：`src/` 内已无 `ExtensionExecHub`/`HUB_URL_ENV`/`DEFAULT_HUB_URL`/
  `ONLINE_WINDOW_SECONDS`/`DEFAULT_POLL_SECONDS`/`ExtLoopbackGateway`/`probe_ext_hub`/
  `hub_url`/`/api/ext/*` 残留（仅 `local_transport` docstring 提及旧常量作历史对照）。
- `.harness/spike/native_messaging/` 保留为可复跑的实证工具（S0 四项 + 捕获端到端探针），
  已加入 ruff exclude。
- 门禁：FULL GATE PASSED；`native-messaging-bridge` feature 置 `passes: true`。




