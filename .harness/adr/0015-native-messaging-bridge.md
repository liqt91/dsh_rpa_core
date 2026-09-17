# ADR 0015：扩展通道改为 Native Messaging（浏览器按需拉起 bridge，取消常驻 hub）

- 状态：**已接受（2026-09-17）**，实施见 `.harness/tasks/M20-native-messaging.md`
- 日期：2026-09-17
- 关联：ADR 0007（devserver 与捕获契约）、ADR 0011（运行控制 proxy）、ADR 0014 §10（GUI 内嵌 ExtHub）
- 取代：ADR 0014 §10 中「GUI 内嵌 loopback 网关承接扩展轮询」的传输选择（`ExtLoopbackGateway` + `/api/ext/*` 长轮询）

## 1. 背景

自研 MV3 扩展是浏览器执行与捕获的唯一通道（ADR 0013）。现行传输为：扩展 `background.js`
长轮询 `127.0.0.1:8765` 的 `/api/ext/*`，宿主侧由 `ExtensionExecHub` 持队列；GUI 独立运行时
内嵌 `ExtLoopbackGateway` 承接（ADR 0014 §10）。

该设计在实践中暴露三个结构性问题：

1. **存在「扩展离线」状态且与用户直觉冲突**：hub 未启动（GUI 懒启动、devserver 未开）或
   MV3 service worker 休眠时，界面显示「插件通道：离线（浏览器指令不可用）」，而用户的
   浏览器与插件其实正常。用户感知为误报。
2. **固定端口 8765 的争抢**：`ExtLoopbackGateway` 与 `DevServer` 同用 8765 且禁用地址复用，
   「GUI 先开、devserver 后起」或反之都需探测/复用，启动顺序成为隐性契约。
3. **在线判定依赖心跳窗口**（`ONLINE_WINDOW_SECONDS`）与长轮询 hold 的时序耦合，历史上
   已因窗口小于 hold 导致误判离线（PROGRESS 2026-09-10 `ext-channel-browser-resolve`）。

## 2. 决策

扩展通道传输改为 **Native Messaging**：扩展经 `chrome.runtime.connectNative()` 连接本机
bridge host，host 由**浏览器按需拉起**（不再需要预先运行的 hub）；host 与执行器侧
（`rpa-core run` 子进程 / GUI / devserver）经**本地 IPC**（Windows 命名管道 / POSIX Unix
域套接字）通信。

```text
run 子进程 / GUI / devserver                    浏览器（Edge/Chrome）
ExtensionExecClient ──本地 IPC──▶ ext_bridge(host) ──stdio──▶ 扩展 SW
                                  浏览器按需拉起；SW 断→port 断→host 退
```

要点：

- **host 是 `workers/` 下的子进程入口**（`rpa_core.workers.ext_bridge`），只依赖 stdlib +
  平台 IPC，不依赖 catalog/runtime——符合「workers 仅依赖 model」的既有约束。
- **host 入口必须是无参数可执行文件**：新增 console script
  `rpa-core-ext-host = "rpa_core.workers.ext_bridge:main"`，host manifest 的 `path` 直接指向
  `<venv>/Scripts/rpa-core-ext-host.exe`。**不可**写 `"pythonw.exe" "script.py"` 这类带参数
  的命令行——S0 真机实测证明 Chromium 不按命令行解析该字段，会把解释器无参拉起（退化为
  读 stdin 的 REPL），表现为「扩展连上但 host 逻辑不运行且无任何日志」。
- **在线语义 = 本地 IPC 端点可连接**。不再有心跳窗口；host 随扩展 port 存活，扩展休眠
  断开即 host 退出、端点消失，不存在「浏览器在跑但显示离线」的中间态。
  - 注意「关闭浏览器窗口 ≠ 扩展离线」：Edge 的后台驻留（启动增强 / 关闭后继续运行后台
    扩展）会让扩展 SW 无窗口时继续运行并重连，host 保持存活。这是预期行为；host 回收
    只在浏览器**完全退出**（含后台驻留进程）时发生。
- **安装注册**：host manifest 写入浏览器 Native Messaging 目录（Windows HKCU 注册表 /
  macOS `~/Library/Application Support/.../NativeMessagingHosts` / Linux
  `~/.config/.../NativeMessagingHosts`），`allowed_origins` 用扩展 ID 白名单强制。
  免管理员（HKCU），三平台同步。
  - Load unpacked 的扩展 ID 由**源码目录绝对路径**派生且与浏览器无关（算法：路径的
    UTF-16LE 字节 → SHA256 前 128 位 → 十六进制 → 映射 0-9→a-j / a-f→k-p），故可**在扩展
    加载前预注册**；Secure Preferences 发现保留为权威交叉校验。
- **捕获通道一并迁移**：`/api/capture/extension/*` 的 arm/disarm/result 复用同一 port，
  扩展不再有 HTTP 轮询。
- **全量切换**：HTTP 长轮询、`ExtensionExecHub`、`ExtLoopbackGateway`、`HUB_URL_ENV`、
  `DEFAULT_HUB_URL`、`ONLINE_WINDOW_SECONDS` 全部退役（不留双栈）。

## 3. 与竞品的关系

影刀（ShadowBot 6.2.23）真机逆向显示其通信层即 native messaging host
（`shadowbot.chrome.bridge(_v2)`，Edge/Chrome 各一注册），安装走外部注册表 CRX +
开发者模式兜底（`docs/extension-install.md` §6.0/§8）。本决策与业界已验证路线同构；
差异在于我们**不复刻**其 Secure Preferences HMAC 预写手法，安装仍走 Load unpacked 引导。

## 4. 后果

**正面**

- 消除「插件离线」整类误报与 GUI 启动顺序问题；GUI 与 devserver 不再争 8765。
- 零开放 TCP 端口；扩展 ID 白名单由 host manifest 强制，比「仅绑 loopback」更严。
- 安装一次注册后跨版本更新稳定（host 路径与扩展 ID 不随 CRX 重打包变化）。

**代价 / 风险**

- 需写注册表 / 浏览器配置目录（HKCU 免管理员，但企业锁定环境需回退手动引导）。
- Load unpacked 的扩展 ID 由源码路径派生，需从 Secure Preferences 动态发现后写入
  `allowed_origins`（installer 幂等自愈）。
- **MV3 service worker 与长连 native port 的存活行为**：S0 真机实测**保活**（Edge 152，
  15s 心跳零空洞），无需心跳窗口；alarm 兜底保留为 SW 被回收时的保险。
- 扩展侧必须**串行化 `connectNative`**（连接中标志），否则并发调用会拉起多个 host
  进程（S0 实测一次 reload 起 2 个）。
- host 分发形态：开发期用 venv 内的 console script exe（注册表路径含 venv 绝对路径，
  变动需重注册）；生产分发（独立打包 exe）属后续议题。

## 5. 被否备选

- **保留 HTTP + token 配对（Playwright MCP 模式）**：不解决「hub 必须预先运行」与端口争抢。
- **双栈（native 为主 + HTTP 兜底）**：长期维护两套协议与测试，收益不明。
- **Windows 命名管道 + POSIX 各自实现**：即本方案实现层选择，非替代。
- **TCP loopback + 端口文件**：仍开放端口，收益回退。
