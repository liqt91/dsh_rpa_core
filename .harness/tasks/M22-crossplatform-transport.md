# M22 macOS/Linux 传输层真机验证（Native Messaging 跨平台）

状态：`planned`
关联：ADR 0015（Native Messaging 扩展通道）、M20（Windows 真机已验）、`docs/extension-install.md` §1.2（平台范围）
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
  - `browser.navigate` 经扩展通道 succeeded（真实浏览器）；捕获 arm → Ctrl+Click → 描述符回传。
- [ ] **S4 差异修正与文档**
  - 修正 POSIX 分支实测暴露的差异（socket 路径/权限/清理、launcher 形式、`pgrep` 相关路径表）。
  - `docs/extension-install.md` §1.2 与 `extension/README.md` 更新为「已真机验证的平台」表述；
    `.harness/architecture.md`/ADR 0015 增补平台验证状态。

## 验收

- macOS 与 Linux 上各跑通 S1–S3；`ExtensionExecClient` 的在线/提交/结果全链路可用。
- 无平台特化 hack 残留（若必须特化，须在 ADR 0015 记录并说明理由）。
- 全门禁通过（POSIX 上 `uv run pytest` 既有 skip 语义不变）；PROGRESS 追加记录。

## 风险 / 开放问题

- 需要 macOS/Linux 真机与图形浏览器环境（本机为 Windows，无法代跑）。
- Linux 无 `$XDG_RUNTIME_DIR`（如纯 SSH 会话）时的端点目录回退行为需实测确认。
- macOS 的 Gatekeeper/签名对 host 可执行文件的影响（开发态 console script 通常无碍）。
