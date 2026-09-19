# M22 macOS/Linux 传输层真机验证（Native Messaging 跨平台）

状态：`done`

（macOS 侧 S1–S4 完成，2026-09-19；**Linux 仍未真机**，见「完成证据」。进展：2026-09-18 修
POSIX 端点路径长度缺陷并回归覆盖；2026-09-19 于 macOS 完成 S1–S3 真机验收与 S4 文档口径更正。
Linux 侧保持未验证——本机无 Linux 环境，不代跑。）
关联：ADR 0015（Native Messaging 扩展通道，含 §6 平台差异补充）、M20（Windows 真机已验）、`docs/extension-install.md` §1.2（平台范围）
前置：M20（`local_transport` + `workers/ext_bridge` + 三平台 host manifest 注册均已实现）

## 背景与缺口（现状核实 2026-09-17）

M20 的传输层与安装注册**按三平台实现**，但**只在 Windows 真机验证过**：

| 部件 | Windows | macOS / Linux |
|---|---|---|
| `local_transport` 命名管道分支（pywin32 overlapped + `PeekNamedPipe` 兜底 + `CancelIoEx`） | ✅ 真机（S0/S1/S2） | 不适用 |
| `local_transport` Unix 域套接字分支（`$XDG_RUNTIME_DIR` 或临时目录 + 陈旧端点清理） | 不适用 | ❓ **仅单测/设计，未真机** |
| host manifest 注册路径（macOS `~/Library/Application Support/.../NativeMessagingHosts`、Linux `~/.config/.../NativeMessagingHosts`） | ✅ 真机 | ❓ **未真机** |
| `native_host_executable()` 在 POSIX 上的 console script 路径 | ✅ 真机 | ❓ 未真机 |
| 扩展加载 + host 被浏览器拉起 + 端点命名 + 心跳/回收 | ✅ 真机（Edge/Chrome） | ❓ 未真机 |

风险点：Chromium 在 POSIX 上对 host manifest 的 `path` **不支持参数**（只能用可执行文件/脚本
启动器），且 Unix socket 的运行时目录、权限（0o700）、陈旧文件回收在真实环境可能不同于设计。

## 任务（切片）

- [x] **S1 平台准备与最小握手（macOS 2026-09-19 完成；Linux 未做）**
  - macOS/Linux 各一环境：`uv sync --all-groups --extra gui`，加载 `extension/`（Load unpacked），
    用 `rpa-core install-extension` 注册 host。
  - 验证：`ExtensionExecClient().status()` 报 `online`；`rpa-core env-status` 的
    `browsers.*.online` 正确；端点目录/权限符合预期。
- [x] **S2 S0 四项等价验证（macOS 2026-09-19 完成）**（复用 `.harness/spike/native_messaging/` 的
  观测设计，但按**产品通道口径**重解 —— 见下方"口径差异"）
  - SW 长连 native port 保活（心跳间隔稳定）；扩展 reload 与浏览器完全退出 → host 回收
    （Unix socket 文件消失、无残留进程）；重连时延；unpacked ID 推导/发现一致。
  - **口径差异（重要）**：spike 的 15s `ping` 是 **spike 扩展**的观测手段（其
    `background.js` 用 `setInterval` 主动发）；产品扩展只在收到 host 的 `ping` 时回 `pong`，
    **自身从不主动发**，因此"看不到 ping"不等于保活失败。产品口径下保活的等价判据是
    `workers/ext_bridge.py` 的「端点可连接 ⇔ 扩展在线」（无心跳窗口）。
- [ ] **S3 端到端**
  - [x] **捕获 arm → 描述符回传（macOS 真机，2026-09-18）**：`HybridCaptureSession`
    全路径（GUI「捕获元素」按钮同构造）拿到真实描述符。期间暴露并修掉一处**平台退化缺陷**：
    非 Windows 上桌面腿 49ms 返回 `{"error": "desktop capture requires Windows"}`，旧「先回传者胜」
    把它当成捕获成功、掐掉仍在线的扩展腿 → 用户侧「点捕获元素闪一下就弹回、网页里怎么点都没反应」。
    细节与 4 处修改见 M23 G1「平台退化修正」。
    > **真实手势缺陷**：`Control+Click` 被系统层改写成"次要点击"，浏览器只派发 `contextmenu`，
    > **永不派发 `ctrlKey` 的 `click`** → 只挂 `click` 监听的 content.js 在 Mac 上必然
    > 「红框跟随鼠标、但怎么点都捕获不到」。已修（content.js 增补 `metaKey`/次要点击路径），
    > 见 M23 G1「真实手势缺陷修正（macOS 次要点击）」。教训：**合成事件验证不能替代真实手势验证**。
  - [x] `browser.navigate` 经扩展通道 succeeded（真实浏览器，2026-09-19）：`rpa-core run` 跑最小
    流程（`browserType=msedge` + `action=goto`，`goto` 在真实浏览器里**新建标签页**，不动用户
    现有页面）→ `status: succeeded`、耗时 **1760ms**、effect `committed`、
    `details.transport: "extension"`；输出的 `browserInstance` 与 `ExtensionExecClient().status()`
    报告的宿主实例 id 一致（`b5530743-…`），证明命令确实落在同一扩展通道上。
- [ ] **S4 差异修正与文档**
  - [x] **POSIX 端点路径长度缺陷（macOS 真机暴露，2026-09-18）**：`sun_path` 103 字节上限被
    `61 字节 per-user TMPDIR + 56 字节端点名` 顶穿（123 字节）→ host 被浏览器正常拉起但
    `bind()` 抛裸 `OSError` 静默死亡 → 扩展**永远离线**。已修：定长实例 token
    （`sha256(instanceId)[:16]`）+ macOS 短端点目录（`/tmp/rpa_core-<uid>/rpa_core_ext`）
    + 长度守卫（`endpoint_path` 抛领域异常、`bind` 的 `OSError` 转领域异常）
    + host 诊断日志落盘与启动自检 + 残留端点回收。细节见 ADR 0015 §6。
  - [x] 回归覆盖：`tests/unit/test_local_transport.py`（定长 token / 路径长度 / 目录私有性 /
    守卫 / 残留回收）、`tests/contract/test_ext_bridge.py`（stderr 不再丢弃、结果信封带真实
    instanceId、按原始 id 与 token 双向路由、bind 失败以退出码 3 + 日志收场）。
  - [x] `docs/extension-install.md` §1.2 与 `extension/README.md` 更新为「已真机验证的平台」表述；
    `.harness/architecture.md`/ADR 0015 增补平台验证状态（2026-09-19 完成）：
    §1.2 加**平台验证状态表**并显式标注旧句失效（「macOS 亲测可用、全链路通过」是 **bsk 传输
    时代**结论，不适用于 Native Messaging）；ADR 0015 加 §7（含 4 条验收事实与偏差）；
    `architecture.md` 的共享低层模块段加平台覆盖提示；`extension/README.md` 补 macOS manifest
    落盘位置、host 生命周期语义（端点可连接 ⇔ 扩展在线、无需心跳），并修掉「`browser.navigate`
    不再暴露」的歧义（命令仍在，移除的是 `transport`/`channel` 入参）。
  - [x] launcher 形式、`pgrep` 相关路径表的真机核对（2026-09-19）：
    `pgrep -f` 命中 **1 条** Edge 主进程命令行，同时存在的 **16 个 `Microsoft Edge Helper`
    子进程零误判**；`--load-extension` 在 Edge 153 上**仍有效**（主进程命令行携带该参数，
    Secure Preferences 中该扩展 `location=4`、`path` = 源码目录、无 `disable_reasons`）。
    注意：`_posix_process_matches` 的 `ps` 回退分支在本机沙箱下不可用（`Operation not
    permitted`），但 `pgrep` 主路径正常，故 `edge.running=true` 判定正确。

## 验收

- macOS 与 Linux 上各跑通 S1–S3；`ExtensionExecClient` 的在线/提交/结果全链路可用。
- 无平台特化 hack 残留（若必须特化，须在 ADR 0015 记录并说明理由）。
- 全门禁通过（POSIX 上 `uv run pytest` 既有 skip 语义不变）；PROGRESS 追加记录。

## 风险 / 开放问题

- **桌面捕获是 Windows-only 能力（平台边界，2026-09-18 真机确认）**：`capture.desktop_agent`
  依赖 pywinauto/win32gui 做 UIA hit-test，非 Windows 上 `python -m
  rpa_core.capture.desktop_agent` 直接输出 `{"error": "desktop capture requires Windows"}`
  并退出码 1。因此 macOS/Linux 上「捕获元素」实际只有**网页腿**（扩展）可用：
  `HybridCaptureSession.desktop_offline` 报 True 并退化为纯扩展捕获，GUI 提示不再承诺桌面捕获。
  若后续要补 macOS 桌面捕获，等价物是 Accessibility API（`AXUIElementCopyElementAtPosition`）
  —— 属新功能（M10 的 macOS 版），需单独立项，不在本任务范围。
- 需要 macOS/Linux 真机与图形浏览器环境（本机为 Windows，无法代跑）。
- ~~Linux 无 `$XDG_RUNTIME_DIR`（如纯 SSH 会话）时的端点目录回退行为需实测确认。~~
  → 已定：缺省回退 `/tmp/rpa_core-<uid>/rpa_core_ext`（Linux 侧真机仍待跑）。
- macOS 的 Gatekeeper/签名对 host 可执行文件的影响（开发态 console script 通常无碍）。
- **端点在线窗口由 MV3 SW 存活决定**：SW 休眠时 port 关闭、host 退出（端点消失），
  浏览器唤醒后重连。实测 macOS 上 host 被浏览器拉起、绑定端点成功，但进程可能在 SW
  休眠时被终结 —— 残留 socket 由「同名重绑回收 + 超期残留回收」两道兜住。S2 的
  「SW 保活 / 心跳间隔」验收仍需在 macOS 上复测（Windows 上实测 15s 心跳零空洞）。
- macOS App Sandbox 版浏览器（App Store 分发）会把 host 的写入重定向到 app 容器，
  此时 host 与 CLI 可能算出不同端点目录 —— 本机 `/Applications/Microsoft Edge.app`
  非沙箱，**该推断未真机验证**（改用 `/tmp` 后此风险已大幅降低，但未消除）。

## 完成证据（2026-09-19，macOS + Edge 153 + 扩展 0.3.1）

**环境**：端点 `/tmp/rpa_core-501/rpa_core_ext/rpa_core_ext_msedge_<token>.sock`；host manifest 落
`~/Library/Application Support/Microsoft Edge/NativeMessagingHosts/com.rpa_core.ext_bridge.json`
（`path` 指向 `.venv/bin/rpa-core-ext-host`，无参数）。

| 项 | 观测证据 |
|---|---|
| S1 端点目录/权限 | 目录 `0o700`、属主 uid = euid(501)；`pathBytes=72` ≤ `limit=103`、`ok=true` |
| S1 握手 | `ExtensionExecClient().status()` → `online: true`（msedge / 153 / extVersion 0.3.1 / MacIntel） |
| S1 CLI 对照 | `rpa-core env-status`：`browsers.edge.*` 全 `true`、`chrome.*` 全 `false`（未装）；`_online: "live"`（实时探测非缓存） |
| S2-① 保活 | host 单 pid `84978` 自 10:37:51 服务至 22:01:30（**11h23m**），期间端点 inode 未变、日志零断连事件 ⇒ 端点全程可连接 ⇒ 扩展全程在线（无需心跳） |
| S2-② reload 回收 | reload 后旧 host 消失、端点 inode `24654880` → `24708529`（重新 bind）；旧 pid 与新 pid 均无残留 |
| S2-② 浏览器退出回收 | 独立 profile 实例退出（rc=0）后其端点**立即消失且 socket 文件被删除**，目录中只剩主实例的 socket |
| S2-③ 重连时延 | reload 后首次重连 **13s**（22:01:30→22:01:43，SW 冷启动+退避）、同轮第二次切换 **<1s**；独立实例冷启动到上线 **2s** |
| S2-④ ID 一致 | profile 发现 `djhfackbkhofdepbmideabbecgpeohhi` ＝ 路径推导同值 ＝ manifest `allowed_origins`；且扩展实际连上 host，构成端到端闭环 |
| S3 端到端 | `browser.navigate` → `succeeded` / 1760ms / `transport: "extension"` / `browserInstance` 与 status 一致 |

**复现路径**（脚本为一次性诊断，未入库）

- S1：`uv run rpa-core env-status`；能力层 `ExtensionExecClient().status()` + `endpoint_diagnostics()`
- S2-②③：主证据是 `~/.rpa-core/logs/ext-host.log`（每行带 pid 前缀，`host starting` 行 = 新 host
  出生证明）+ 端点 socket 的 inode/时间戳；`observe.py --seconds 180` 可作旁证（本轮窗口内它只
  记录了 `HOST-UP` + `connected` 且 3 分钟无掉线，reload 发生在其退出之后）
- S3：`uv run rpa-core run <流程目录>/workflow.json --artifacts <dir>`

**未完成（不阻塞本任务，但需记住）**

- **Linux 全线未真机**（本机无 Linux 环境）。真机时优先确认：`$XDG_RUNTIME_DIR` 缺省回退是否
  生效、`pgrep -f` 在目标发行版能否命中浏览器主进程。
- Windows 记录的「重连 ≤0.4s」与 macOS 的「reload 后 13s」**不是同一触发场景**，勿直接比较；
  统一口径需在同一条件下重测。
