# ADR 0012：设计期扩展安装入口放 devserver，采用能力层复用而非子进程代理

- 状态：已接受
- 日期：2026-09-07
- 关联：ADR 0007（devserver 隔离）、ADR 0011（运行控制子进程代理）、M14.5（CLI 通道对齐）、docs/extension-install.md §6.5/§6.5.1

## 背景

编辑器需要"浏览器捕获插件"的安装入口：检测 Chrome/Edge 各自是否已装/已启用、装完引导去扩展页启用。
初版采用外部注册表路线（update_url 指 CWS），但真机发现**未上架本地 CRX 启用后约 30s 被异步商店
校验判损坏**（docs §6.5.1），故默认安装方式改为**开发者模式 Load unpacked 引导**（源码目录加载，
location=4，持久可用；影刀 Chrome 同款）。本 ADR 记录该入口的托管形态（放 devserver、能力层复用）。

## 决策

### 1. 放 devserver：devserver 进程内直接复用 `rpa_core.extension_installer` 能力层

- **不放独立服务进程**：编辑器页面只能经 devserver 的 HTTP 面交互；另起安装服务是过度设计（加进程/端口），
  且安装是低频一次性本机动作，无独立部署理由。
- **不用 ADR 0011 的子进程代理形态**：ADR 0011 的子进程 run host 是因为 `runtime`/`orchestrator`
  被 ADR 0007 禁止 import 进 devserver 进程。本功能的 `extension_installer` **不在 ADR 0007 禁 import 清单**
  （只禁 runtime/executors/workers/cli），且 devserver 已 import 它做 CRX 托管（`load_packed_extension`/
  `update_manifest_xml`）。既然能力可 import，就按 M14.5 原则**直接 import 复用**，不 spawn 再解析 CLI 输出
  （M14.5 明确"不 spawn 解析 CLI 输出"）。CLI 与 devserver 同为该能力层的薄通道，输出同源。
- 能力层补纯函数（`extension_installer.py`）：状态探针（注册表 + 浏览器 profile `Secure Preferences`
  只读扫描 + **开发者模式加载检测**——unpacked 扩展不存 manifest，故按 Secure Preferences 记录
  `location==4 && path==源码目录` 匹配）、打开源码目录/扩展页（跨平台）、`install_external_guided`
  （外部注册表 + 自动清卸载屏蔽，仅上架后使用）；winreg/路径均可注入，便于合同测试。

### 2. 端点契约（默认 = Load unpacked 引导）

| 端点 | 方法 | 语义 |
|---|---|---|
| `/api/extension/status` | GET | 只读：双浏览器 `{binary, registryEntry, profiles[], unpackedProfiles[], installed, enabled}` + `extensionDir`（源码目录）+ `enableHint` + `installMode` |
| `/api/extension/open-dir` | POST | 系统文件管理器打开源码目录（Load unpacked 选目录用） |
| `/api/extension/open-page` | POST | body `{browser}`：启动该浏览器扩展管理页（chrome:///edge:// 无法外部导航，前端复制地址提示粘贴） |
| `/api/extension/install` | POST | （上架后）外部注册表安装，`install_external_guided`；未上架本地 CRX 不可持久 |
| `/api/extension/unblock` | POST | 清除浏览器 external_uninstalls 卸载记忆（装不上排查用） |

- 编辑器 UI 默认走 **Load unpacked 引导**（复制/打开源码目录 → 打开扩展页 → 开发者模式 → 加载目录）；
  `--policy`（UAC/HKLM 强装）与外部注册表维持 CLI-only/上架后使用。
- 错误沿用现有码表：`BAD_REQUEST`(400)、`METHOD_NOT_ALLOWED`(405)、`NOT_FOUND`(404，
  open-page 无浏览器可执行)、打包前置失败 `503`；非 Windows 由能力层抛 `PLATFORM_UNSUPPORTED`。

### 3. 隔离与边界（不变式不破）

- devserver 仍不 import `runtime`/`executors`/`workers`/`cli`（架构检查断言继续覆盖；
  本 ADR 复用的是 `extension_installer`，非运行时）。
- 引导与状态检测是**本机用户级工具行为**（读 profile、打开文件管理器/浏览器），不依赖运行时；
  与 ADR 0007 "devserver 不承载 run/编排" 不冲突——无 orchestrator/run 状态进入 devserver 进程。

## 后果

- 编辑器内可引导安装浏览器捕获插件（Load unpacked）、看双浏览器已装/启用状态、复制路径/打开目录。
- CLI `install-extension` 默认同样输出引导步骤（自动打开源码目录），`--registry` 保留上架后路线；
  `--status` 只读检测、`--unblock` 排查，与 devserver 输出同源。
- 架构边界清晰：未来若出现"能力不可 import（runtime 类）的 devserver 功能"才套 ADR 0011 子进程代理。
