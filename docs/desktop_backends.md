# Windows 桌面双后端分工（desktop.uia / desktop.win32）

状态：已验证（M2.2）
关联：ADR 0003、`.harness/tasks/M2.2-uia-winforms.md`

## 定位

| | `desktop.uia`（UIA 语义主线） | `desktop.win32`（Win32 消息主线） |
|---|---|---|
| 适合目标 | WinForms / WPF / 现代 UI（有 AutomationId 的语义控件） | 经典 Win32 应用、系统对话框、菜单栏 |
| 定位方式 | `automationId` / `controlType` / `name` | `title` / `className` / `handle` / `controlId` / `menuPath` |
| 独有命令 | — | `hotkey`、`menuSelect` |
| 典型场景 | 自有业务窗体：输入框、按钮、标签读回 | 记事本"打开"对话框（`#32770`）、菜单路径、键盘快捷键 |

## 共享契约

6 个同名命令（`attachWindow / findElement / click / input / getText / closeSession`）在两个后端之间保持对称：相同 `version / kind / risk / stability / effect / capabilities`，`attachWindow` 输出一致（`sessionId / processId / workWindowId`）。测试锁定：`tests/contract/test_desktop_contract.py::test_desktop_backend_manifests_share_lifecycle_contract`。

## 已验证的 E2E

- `desktop.uia`：确定性 WinForms 测试应用（`testapps/desktop/Program.cs`，运行时由 .NET Framework 自带 csc 编译）。链路：attach → 按 automationId 定位输入框/按钮 → 输入 → 提交 → 读回标签 → 打开模式对话框 → attach 对话框（第二个 session）→ 输入 → 接受 → 主窗读回 `dialog:<文本>`。测试：`tests/e2e/test_uia_desktop.py`，含连续 3 次运行一致性。
- `desktop.win32`：记事本菜单打开文件链路（M2）。

## 实现注意（已知边界）

1. **UIA 桌面枚举盲区**：部分环境（如远程会话 + 安全软件）下，UIA 桌面子树遍历会漏掉 owned 弹出窗口（`find_elements(title=...)` 返回空），但按 HWND 直连该元素的 UIA provider 完全正常。因此 `desktop.uia.attachWindow` 在树遍历 0 匹配时回落到 Win32 `EnumWindows`（按标题 + 可见性 + 进程过滤）再构造 UIA wrapper——窗口发现跨 UIA/Win32 边界是合理的，UIA 本身也以 HWND 为顶层锚点。
2. **窗口与元素的异步出现**：`attachWindow` 与 `findElement` 支持 `timeoutMs`（轮询直至唯一匹配或超时）。`desktop.win32` 的 manifest 未声明 `timeoutMs`，如需对称扩展先改 manifest。
3. **Modeless vs Modal**：测试应用使用 modeless 对话框。模态对话框在点击发起的 UIA `Invoke` 调用内部嵌套消息循环，会阻塞执行器的 COM 调用直至对话框关闭（表现为 attempt 超时）；需要驱动模态对话框时应拆分为独立 workflow 或使用 win32 后端的 `menuSelect`/`hotkey` 路径。
4. **测试应用 GC 陷阱**：modeless form 必须保留托管引用（存为字段），否则 UIA 枚举的 COM 调用会触发 GC 回收未引用的窗体——表现为"窗口短暂存在又消失"。

## 选择指引

- 目标是自有 WinForms/WPF 业务应用 → `desktop.uia`（优先 `automationId`，重命名控件不影响 RPA）。
- 目标是系统对话框、菜单操作、无 UIA 暴露的老应用 → `desktop.win32`。
- 混合场景（如记事本打开对话框）可在同一 workflow 中混用两个后端的命令，session 互不干扰。
