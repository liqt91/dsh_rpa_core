# ADR 0003：Windows 桌面驱动采用 UI Automation 语义层

- 状态：已接受
- 日期：2026-08-28

## 背景

桌面自动化不存在一种同时覆盖所有 Windows 图形界面的通用技术：

- Win32 HWND/message 面向窗口句柄和标准控件协议，适合已知经典控件，但对 owner-draw、自绘区域、跨进程指针参数、UIPI 权限边界和现代 UI 框架能力有限。
- Microsoft UI Automation（UIA）面向可访问性语义树和 Control Pattern。经典 Win32 标准控件、WinForms、WPF 以及正确暴露 provider/AutomationPeer 的应用通常适合；自绘、虚拟化、DirectX/canvas、部分 Electron 自定义区域可能缺少稳定节点。
- 图像识别/OCR 面向最终像素，能覆盖无语义树界面和远程桌面画面，但受 DPI、缩放、主题、遮挡、压缩和焦点影响，动作通常需要坐标或真实输入，风险高于语义操作。
- Office 等复杂应用的业务对象通常更适合专用 Object Model/COM adapter，而不是把所有业务语义压进 UIA。

M2 的目的不是覆盖全部桌面应用，而是验证 rpa_core 的 executor 协议能承载第二类结构化驱动。

## 技术验证

在 Windows 10、Python 3.12.12、pywinauto 0.6.9 上验证了一个确定性 WinForms 测试程序：

- UIA 能枚举稳定的 `automationId`、`controlType` 和 `name`；
- `set_edit_text` 可以在不激活窗口的情况下写入标准 Edit；
- Invoke Pattern 可以在不移动鼠标的情况下触发 Button；
- Text 控件结果可以通过 UIA 回读。

## 决策

M2 使用 `pywinauto` 的 `uia` backend 处理标准语义控件，同时补充 `win32` backend 处理经典窗口、菜单和模态对话框。

- 依赖仅在 `sys_platform == 'win32'` 时安装。
- 所有操作要求显式 `sessionId`，session 绑定 `processId + nativeWindowHandle`。
- Locator 分为两类：
  - UIA: `automationId`、`controlType`、`name`
  - Win32: `title`、`className`、`handle`、`controlId`、`menuPath`、`foundIndex`
- `desktop.attachWindow` / `desktop.win32.attachWindow` 要求显式 backend，匹配规则由 backend 决定。零匹配返回 `ELEMENT_NOT_FOUND`，多匹配返回 `AMBIGUOUS_MATCH`。
- `desktop.findElement` / `desktop.win32.findElement` 同样要求唯一匹配，并返回 session 内部的 `elementId`。Element reference 不得跨 session 使用。
- UIA 输入优先 Value Pattern/`set_edit_text`；点击优先 Invoke Pattern/`invoke`；Win32 菜单/对话框优先 `menu_select`、`click_input`、消息级方法。两条路径都不应该静默降级。
- UIA 操作由每个 executor 实例的单线程池串行执行，避免同一 session 的 COM/UIA 调用并发交错。
- asyncio task 取消不能强杀已经进入底层 COM 的线程。每次 UIA 查询使用底层 timeout；外层超时后 session 标记为丢失，后续操作返回 `SESSION_LOST`。本里程碑不宣称可以安全中止任意卡死的第三方 provider。
- `desktop.closeSession` 只释放 rpa_core session 和 element reference，不关闭目标应用。

## 分层边界

后续桌面能力按以下层次扩展：

1. 应用专用 API/Object Model（当业务语义明显更强时）；
2. UIA 语义树；
3. Win32 message 白名单 adapter；
4. 图像/OCR 与坐标动作。

禁止从 UIA 静默降级到坐标点击。降级必须由 workflow 明确选择不同 locator/driver，并在证据中记录实际使用层。

图像库、截图 provenance、DPI/坐标系、置信度、模板版本和 OCR 结果属于后续独立里程碑，不进入 M2。

## 适用边界

适合 UIA 优先：

- 经典 Win32 标准控件；
- WinForms/WPF 标准控件；
- 正确暴露 accessibility tree 的 Electron/Chromium 桌面应用；
- 具有稳定 UIA provider 的企业桌面软件。

需要专用 adapter 或视觉层：

- Office 文档、工作簿等业务对象；
- owner-draw、自绘 canvas、DirectX、游戏；
- 缺少 accessibility tree 的 Electron 自定义区域；
- RDP/Citrix 中只暴露远程画面的客户端窗口。

## 参考资料

- Microsoft UI Automation Overview: https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-uiautomationoverview
- UI Automation Providers Overview: https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-providersoverview
- UI Automation Control Patterns: https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-controlpattern-ids
- pywinauto Getting Started: https://pywinauto.readthedocs.io/en/latest/getting_started.html
- Microsoft VDI automation limitations: https://learn.microsoft.com/en-us/troubleshoot/power-platform/power-automate/desktop-flows/cannot-record-in-vdi-environments

## 后果

- M2 能覆盖一大类标准企业桌面应用和经典 Win32 菜单/对话框，但不承诺所有 Windows GUI 均可自动化。
- 图像识别/OCR 继续后置，避免在结构化驱动尚未稳定时引入元素图片库、坐标系和置信度模型。
- pywinauto/pywin32 是 Windows 专用依赖；跨平台稳定性由 manifest capability 和 executor registration 保证，而不是在非 Windows 上模拟桌面行为。
