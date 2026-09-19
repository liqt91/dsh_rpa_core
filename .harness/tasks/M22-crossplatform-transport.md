# M22 macOS/Linux 传输层真机验证（Native Messaging 跨平台）

状态：`planned`

（进展：S4 部分完成 —— POSIX 端点路径长度缺陷已修复并回归覆盖，2026-09-18；
S1–S3 的真机逐步验收仍待补。当前 active_plan 为 M23，故此处保持 `planned`。）
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

- [ ] **S1 平台准备与最小握手**
  - macOS/Linux 各一环境：`uv sync --all-groups --extra gui`，加载 `extension/`（Load unpacked），
    用 `rpa-core install-extension` 注册 host。
  - 验证：`ExtensionExecClient().status()` 报 `online`；`rpa-core env-status` 的
    `browsers.*.online` 正确；端点目录/权限符合预期。
- [ ] **S2 S0 四项等价验证**（复用 `.harness/spike/native_messaging/`）
  - SW 长连 native port 保活（心跳间隔稳定）；扩展 reload 与浏览器完全退出 → host 回收
    （Unix socket 文件消失、无残留进程）；重连时延；unpacked ID 推导/发现一致。
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
  - [ ] `browser.navigate` 经扩展通道 succeeded（真实浏览器）
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
  - [ ] `docs/extension-install.md` §1.2 与 `extension/README.md` 更新为「已真机验证的平台」表述；
    `.harness/architecture.md`/ADR 0015 增补平台验证状态（ADR 0015 §6 已补平台差异；安装文档
    待 S1–S3 真机跑通后再改口径）。
  - [ ] launcher 形式、`pgrep` 相关路径表的真机核对（S1–S2 一并做）。

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
