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
- **POSIX 端点路径长度**：macOS 曾因 `sun_path` 的 103 字节上限令扩展**永远离线**
  （host 被正常拉起但 bind 静默失败）——定长 token + 短端点目录 + 长度守卫，见 §6。

## 5. 被否备选

- **保留 HTTP + token 配对（Playwright MCP 模式）**：不解决「hub 必须预先运行」与端口争抢。
- **双栈（native 为主 + HTTP 兜底）**：长期维护两套协议与测试，收益不明。
- **Windows 命名管道 + POSIX 各自实现**：即本方案实现层选择，非替代。
- **TCP loopback + 端口文件**：仍开放端口，收益回退。

## 6. 补充：POSIX 端点路径长度（M22 真机修正，2026-09-18）

**现象**：macOS 上扩展长期显示「离线」。浏览器**确实在拉 host**（65 秒内命中 46 次进程
拉起），但端点目录全程为空 —— host 起了、死在绑定之前，且用户侧没有任何诊断信息。

**真因**：POSIX 端点名落进 `sockaddr_un.sun_path`，**104 字节含结尾 NUL（有效 103）**，
这是 BSD 时代遗留尺寸；而实际路径由三个各自合理的数字叠加而成：

| 组成 | 字节 |
|---|---|
| macOS per-user 临时区 `/var/folders/<2>/<22 位>/T/rpa_core_ext/` | 61 |
| 端点名 `rpa_core_ext_msedge_<uuid36>` | 56 |
| `.sock` | 5 |
| **合计** | **123 > 103** |

`AF_UNIX` 不解析符号链接，`tempfile.gettempdir()` 在 macOS 上就是那条 61 字节私有路径；
`XDG_RUNTIME_DIR` 在 macOS 不存在（launchd GUI 会话实测只注入 `SSH_AUTH_SOCK`）。
Windows 走内核命名空间（`\\.\pipe\`，上限 256 字符，不经路径解析），Linux 有 systemd 注入
`/run/user/<uid>`（14 字节且两端同源）—— **只有 macOS 同时满足「目录长 + 走路径 + 名字含变长 UUID」**。

**修正（四条，均为平台无关的契约收紧）**

1. **端点名实例段改定长 token**：`sha256(instanceId)[:16]`。哈希作用于**原始** id
   （先于任何截断），否则两个超长 id 截断后撞名。原始 instanceId 仍可通过 host 的
   `status` 握手与 `result` 信封补带获得（宿主是唯一知道自身真实 id 的角色），
   会话绑定与 `target_host` 路由两种形态都命中。
2. **macOS 端点目录改 `/tmp/rpa_core-<uid>/rpa_core_ext`**：短、且与用户名无关
   （`$HOME` 推导会让长用户名复现同一 bug）。Linux 保留 `$XDG_RUNTIME_DIR` 优先，
   未设时同 macOS。目录 0700 并在复用前复核「非符号链接 + 目录 + 属主为本进程 euid」。
3. **长度守卫**：`endpoint_path()` 在 bind/connect 前计算字节数，超限抛
   `LocalTransportError`（带路径、实际字节数与上限）；`_UnixServer.bind()` 的裸
   `OSError` 一并转领域异常。此前 `except LocalTransportError` 对 bind 失败**是死代码**，
   于是 host 带 traceback 静默退出（退出码 1），浏览器侧只剩「离线」。
4. **诊断落盘 + 残留回收**：host 启动自检把端点路径/字节数/平台上限写日志
   （默认 `~/.rpa-core/logs/ext-host.log`，`RPA_EXT_BRIDGE_LOG` 可覆盖）；`/tmp` 在 macOS
   不会被系统清理，故 host 启动时回收「超期且连不上」的同前缀残留端点。

**代价**

- 端点名不再可读、不可反解（需要可读性时看日志 / `status`）。
- 升级窗口内，升级前已拉起的旧 host 仍用旧命名：显式 `target_host` 可能短暂匹配不到，
  无 `target_host` 的自动路由不受影响（它按前缀枚举，不依赖命名格式）。
- 企业环境 home 不可写时日志落盘会静默降级（best-effort，不影响通道）。

## 7. 平台验证状态（M22 真机验收，2026-09-19）

传输层与安装注册**按三平台实现**，但最初只在 Windows 真机上验过（M20）。macOS 侧 2026-09-18
暴露端点路径长度缺陷（§6），修复后于 2026-09-19 完成 S1–S3 真机验收：

| 部件 | Windows | macOS | Linux |
|---|---|---|---|
| `local_transport` 命名管道分支 | ✅ 真机（M20 S0–S2） | 不适用 | 不适用 |
| `local_transport` Unix 域套接字分支 | 不适用 | ✅ 真机 | ❓ 仅单测/设计 |
| host manifest 注册路径 | ✅ 真机 | ✅ 真机 | ❓ 未真机 |
| POSIX console script 入口（`rpa-core-ext-host`） | ✅ 真机 | ✅ 真机 | ❓ 未真机 |
| host 被浏览器拉起 + 端点命名 | ✅ 真机 | ✅ 真机 | ❓ 未真机 |
| 断开回收（扩展 reload / 浏览器退出）+ 自动重连 | ✅ 真机 | ✅ 真机 | ❓ 未真机 |
| `browser.*` 命令端到端 | ✅ 真机 | ✅ 真机 | ❓ 未真机 |

**macOS 环境**：Edge 153 + 扩展 0.3.1；端点 `/tmp/rpa_core-501/rpa_core_ext/<name>.sock`
（`pathBytes=72` ≤ 103；目录 0700、属主 = euid）。

**验收事实（含与设计的偏差）**

1. **「端点可连接 ⇔ 扩展在线」不经心跳即可判定**。host 单进程连续服务 **11 小时 23 分**，
   端点 socket 自 bind 起 inode 未变、日志零断连事件 —— MV3 SW 在长连 native port 下确实
   不被回收，`workers/ext_bridge.py` docstring 陈述的"无需心跳窗口"在 macOS 同样成立。
   > spike（`.harness/spike/native_messaging/`）里的 15s `ping` 是 **spike 扩展**的观测手段，
   > 非产品行为：产品扩展仅被动回 `pong`。复验时不要把"看不到 ping"读成"保活失败"。
2. **回收即时，且端点文件被删除**（不只是进程退出）：扩展 reload 后旧 host 消失、端点
   inode 重建（`24654880` → `24708529`）；浏览器完全退出后该实例端点 socket 直接从磁盘
   移除。两条路径均无残留进程。
3. **重连时延存在平台/场景差异**：macOS 上扩展 reload 后**首次重连约 13s**（SW 冷启动 +
   3s 退避），此后 < 1s；浏览器冷启动到扩展上线约 **2s**。M20 在 Windows 记录的"重连
   ≤0.4s"与本次 13s 差异显著 —— 两处的触发条件（是否含 SW 冷启动）**需在统一条件下复核**，
   勿直接把两个数字当同一指标对比。
4. **多实例互不干扰**：`instanceId` 是 profile 级 `crypto.randomUUID()`，独立 profile 的
   浏览器实例得到不同端点名，两个 host 可并存。

**仍未验证**：Linux 侧 `$XDG_RUNTIME_DIR` 缺省回退与 `pgrep` 路径匹配；macOS App Sandbox 版
浏览器（App Store 分发）对 host 写入的重定向（改用 `/tmp` 后风险大幅降低但未消除）。
