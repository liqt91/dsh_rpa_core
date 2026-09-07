# ADR 0012：设计期扩展安装入口放 devserver，采用能力层复用而非子进程代理

- 状态：已接受
- 日期：2026-09-07
- 关联：ADR 0007（devserver 隔离）、ADR 0011（运行控制子进程代理）、M14.5（CLI 通道对齐）、docs/extension-install.md §6.5

## 背景

编辑器需要"浏览器捕获插件"的安装入口：检测 Chrome/Edge 各自是否已装/已启用、可单选某一浏览器安装、
装完引导去扩展页点一次启用（外部注册表路线，manifest update_url 已指向 CWS，Chrome/Edge 152 实测均
"装一次 + 手动启用"，见 docs §6.5）。安装入口只存在于编辑器，等价于一个"本机浏览器扩展引导安装"功能。

## 决策

### 1. 放 devserver：devserver 进程内直接复用 `rpa_core.extension_installer` 能力层

- **不放独立服务进程**：编辑器页面只能经 devserver 的 HTTP 面交互；另起安装服务是过度设计（加进程/端口），
  且安装是低频一次性本机动作，无独立部署理由。
- **不用 ADR 0011 的子进程代理形态**：ADR 0011 的子进程 run host 是因为 `runtime`/`orchestrator`
  被 ADR 0007 禁止 import 进 devserver 进程。本功能的 `extension_installer` **不在 ADR 0007 禁 import 清单**
  （只禁 runtime/executors/workers/cli），且 devserver 已 import 它做 CRX 托管（`load_packed_extension`/
  `update_manifest_xml`）。既然能力可 import，就按 M14.5 原则**直接 import 复用**，不 spawn 再解析 CLI 输出
  （M14.5 明确"不 spawn 解析 CLI 输出"）。CLI 与 devserver 同为该能力层的薄通道，输出同源。
- 能力层补三类纯函数（`extension_installer.py`）：状态探针（注册表 + 浏览器 profile `Secure Preferences`
  只读扫描，判 installed/enabled）、单浏览器安装参数、打包前置；winreg/路径均可注入，便于合同测试。

### 2. 端点契约

| 端点 | 方法 | 语义 |
|---|---|---|
| `/api/extension/status` | GET | 只读：双浏览器 `{binary, registryEntry, profiles[], installed, enabled}` + `enableHint`；未打包时 `packed:null`（不 503） |
| `/api/extension/install` | POST | body `{browser: chrome\|edge\|both}`：未打包先打包 → 写 HKCU 外部注册表 → 返回 `{extensionId, version, browsers, enableHint, status, note}` |

- 编辑器 UI 只提供**免管理员默认路线**（外部注册表，HKCU）；`--policy`（UAC/HKLM 强装）维持 CLI-only。
- 错误沿用现有码表：`BAD_REQUEST`(400)、`METHOD_NOT_ALLOWED`(405)、打包前置失败 `503`、
  写注册表失败 `502`（`ExtensionInstallError.code` 透传）；非 Windows 由能力层抛
  `PLATFORM_UNSUPPORTED` → `503`。

### 3. 隔离与边界（不变式不破）

- devserver 仍不 import `runtime`/`executors`/`workers`/`cli`（架构检查断言继续覆盖；
  本 ADR 复用的是 `extension_installer`，非运行时）。
- 安装是**本机用户级工具行为**（HKCU 外部扩展注册表，免管理员、免商店、不依赖运行时），
  与 ADR 0007 "devserver 不承载 run/编排" 不冲突——无 orchestrator/run 状态进入 devserver 进程。

## 后果

- 编辑器内可一键安装浏览器捕获插件、看双浏览器已装/启用状态、装后引导去扩展页启用。
- CLI `install-extension` 同步获得 `--browser` 单选与 `--status` 只读检测，与 devserver 输出同源。
- 架构边界清晰：未来若出现"能力不可 import（runtime 类）的 devserver 功能"才套 ADR 0011 子进程代理。
